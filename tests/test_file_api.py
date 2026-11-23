import os
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import server


PROJECT_TOKEN = "project-token"
BALE_TOKEN = "file-service-token"
ROBOT_ID = "987654"


class _Pool:
    def close(self):
        pass

    async def wait_closed(self):
        pass


async def _open_database():
    return _Pool()


async def _authenticate_project(pool, token):
    return 1 if token == PROJECT_TOKEN else None


async def _find_source_project(pool, name, robot_id):
    if name != "bale":
        return None
    return {"987654": 1, "other-bot": 2}.get(robot_id)


class FakeAdapter:
    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = b"file-bytes"
        self.content_type = "image/jpeg"

    async def handle(self, request):
        self.requests.append({
            "query": dict(request.query),
            "authorization": request.headers.get("Authorization"),
        })
        return web.Response(
            status=self.status,
            body=self.body,
            headers={"Content-Type": self.content_type},
        )


class FileApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = FakeAdapter()
        adapter_app = web.Application()
        adapter_app.router.add_get("/internal/files", self.adapter.handle)
        self.adapter_server = TestServer(adapter_app)
        await self.adapter_server.start_server()
        adapter_url = str(self.adapter_server.make_url("")).rstrip("/")

        self.env = mock.patch.dict(
            os.environ,
            {
                "HUB_BALE_SERVICE_TOKEN": BALE_TOKEN,
                "BALE_SERVICE_URL": adapter_url,
                "HUB_FILE_TIMEOUT": "5",
                "HUB_REDIS_URL": "",
                "HUB_ALLOWED_ORIGINS": "*",
            },
        )
        self.env.start()

        self.original_functions = {
            "open_database": server.open_database,
            "authenticate_project": server.authenticate_project,
            "find_source_project": server.find_source_project,
        }
        server.open_database = _open_database
        server.authenticate_project = _authenticate_project
        server.find_source_project = _find_source_project

        self.app, _, _ = server.create_app()
        self.hub_server = TestServer(self.app)
        await self.hub_server.start_server()
        self.client = TestClient(self.hub_server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.hub_server.close()
        await self.adapter_server.close()
        self.env.stop()
        for name, function in self.original_functions.items():
            setattr(server, name, function)

    async def get(self, *, token=PROJECT_TOKEN, origin=None, **params):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if origin:
            headers["Origin"] = origin
        return await self.client.get(
            "/api/files",
            params={
                "source": "bale",
                "robot_id": ROBOT_ID,
                "file_id": "AgAC-file",
                **params,
            },
            headers=headers,
        )

    async def test_requires_project_token(self):
        response = await self.get(token=None)
        self.assertEqual(response.status, 401)
        response = await self.get(token="wrong")
        self.assertEqual(response.status, 401)
        self.assertEqual(self.adapter.requests, [])

    async def test_streams_file_and_forwards_service_token(self):
        response = await self.get()
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"file-bytes")
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(len(self.adapter.requests), 1)
        request = self.adapter.requests[0]
        self.assertEqual(request["authorization"], f"Bearer {BALE_TOKEN}")
        self.assertEqual(request["query"], {
            "source": "bale",
            "robot_id": ROBOT_ID,
            "file_id": "AgAC-file",
        })

    async def test_rejects_source_from_another_project(self):
        response = await self.get(robot_id="other-bot")
        self.assertEqual(response.status, 403)
        self.assertEqual(self.adapter.requests, [])

    async def test_rejects_unknown_source(self):
        response = await self.get(robot_id="missing")
        self.assertEqual(response.status, 404)
        self.assertEqual(self.adapter.requests, [])

    async def test_rejects_invalid_parameters(self):
        for params in (
            {"robot_id": ""},
            {"file_id": ""},
            {"file_id": "x" * 5000},
        ):
            with self.subTest(params=params):
                response = await self.get(**params)
                self.assertEqual(response.status, 400)

    async def test_rejects_sources_without_a_file_adapter(self):
        for source in ("chat", "telegram"):
            with self.subTest(source=source):
                response = await self.get(source=source)
                self.assertEqual(response.status, 404)
        self.assertEqual(self.adapter.requests, [])

    async def test_maps_adapter_errors(self):
        cases = {404: 404, 410: 410, 413: 413, 500: 502, 401: 502}
        for adapter_status, expected in cases.items():
            with self.subTest(adapter_status=adapter_status):
                self.adapter.status = adapter_status
                response = await self.get()
                self.assertEqual(response.status, expected)

    async def test_cors_preflight_for_store_origin(self):
        response = await self.client.options(
            "/api/files",
            headers={
                "Origin": "https://store.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"], "*"
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Methods"], "GET, OPTIONS"
        )
        self.assertIn(
            "Authorization", response.headers["Access-Control-Allow-Headers"]
        )

    async def test_cors_headers_on_stream_and_errors(self):
        response = await self.get(origin="https://store.example")
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"], "*"
        )
        self.assertIn(
            "Content-Disposition",
            response.headers["Access-Control-Expose-Headers"],
        )

        response = await self.get(
            token=None, origin="https://store.example"
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"], "*"
        )

    async def test_cors_reflects_configured_allowlist(self):
        with mock.patch.dict(
            os.environ, {"HUB_ALLOWED_ORIGINS": "https://store.example"},
            clear=False,
        ):
            app, _, _ = server.create_app()
        other_server = TestServer(app)
        await other_server.start_server()
        self.addAsyncCleanup(other_server.close)
        client = TestClient(other_server)
        await client.start_server()
        self.addAsyncCleanup(client.close)

        params = {
            "source": "bale",
            "robot_id": ROBOT_ID,
            "file_id": "AgAC-file",
        }
        response = await client.get(
            "/api/files",
            params=params,
            headers={
                "Authorization": f"Bearer {PROJECT_TOKEN}",
                "Origin": "https://store.example",
            },
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "https://store.example",
        )
        self.assertEqual(response.headers["Vary"], "Origin")

        response = await client.get(
            "/api/files",
            params=params,
            headers={
                "Authorization": f"Bearer {PROJECT_TOKEN}",
                "Origin": "https://other.example",
            },
        )
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
