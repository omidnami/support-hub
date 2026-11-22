import os
import unittest
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

import server


PROJECT_ID = 7
PROJECT_TOKEN = "good-token"


class _Cursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args):
        return None

    async def fetchone(self):
        return (1,)


class _Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def cursor(self):
        return _Cursor()


class _Pool:
    def acquire(self):
        return _Connection()

    def close(self):
        pass

    async def wait_closed(self):
        pass


async def _open_database():
    return _Pool()


async def _authenticate_project(pool, token):
    return PROJECT_ID if token == PROJECT_TOKEN else None


async def _register_user(pool, **values):
    return "a" * 32, PROJECT_ID


async def _set_project_webhook(pool, project_id, url, secret):
    return project_id == PROJECT_ID


async def _delete_project_webhook(pool, project_id):
    return project_id == PROJECT_ID


async def _get_project_token(pool, project_id):
    return PROJECT_TOKEN if project_id == PROJECT_ID else None


async def _rotate_project_token(pool, project_id):
    return "b" * 32 if project_id == PROJECT_ID else None


def registration_payload():
    return {
        "name": "Test User",
        "email": "user@example.ir",
        "phone": "09121234567",
        "password": "example-password",
        "project_name": "My Shop",
        "domain": "example.ir",
        "description": "My first project",
    }


class HttpApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_functions = {
            name: getattr(server, name)
            for name in (
                "open_database", "authenticate_project", "register_user",
                "get_project_token", "rotate_project_token",
                "set_project_webhook", "delete_project_webhook",
            )
        }
        server.open_database = _open_database
        server.authenticate_project = _authenticate_project
        server.register_user = _register_user
        server.get_project_token = _get_project_token
        server.rotate_project_token = _rotate_project_token
        server.set_project_webhook = _set_project_webhook
        server.delete_project_webhook = _delete_project_webhook

        self.app, self.sio, self.emit_source = server.create_app()

        async def boom(_request):
            raise RuntimeError("boom")

        self.app.router.add_get("/api/boom", boom)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for name, function in self.original_functions.items():
            setattr(server, name, function)

    def auth_headers(self):
        return {"Authorization": f"Bearer {PROJECT_TOKEN}"}

    async def test_health(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"status": True})

    async def test_register_returns_project_id_and_client_key(self):
        response = await self.client.post(
            "/api/register", json=registration_payload()
        )
        self.assertEqual(response.status, 201)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = await response.json()
        self.assertTrue(data["status"])
        self.assertEqual(data["project_id"], PROJECT_ID)
        self.assertEqual(data["token"], "a" * 32)
        self.assertEqual(
            data["client_key"], server.derive_client_key("a" * 32)
        )

    async def test_register_with_webhook_returns_a_one_time_secret(self):
        payload = registration_payload()
        payload["webhook_url"] = "https://planet.example.ir/hooks/hub"
        with mock.patch.dict(
            os.environ,
            {"HUB_WEBHOOK_ALLOW_HTTP": "0", "HUB_WEBHOOK_ALLOW_PRIVATE": "0"},
        ):
            response = await self.client.post("/api/register", json=payload)
        self.assertEqual(response.status, 201)
        data = await response.json()
        self.assertEqual(data["webhook_url"], payload["webhook_url"])
        self.assertGreaterEqual(len(data["webhook_secret"]), 40)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_register_with_webhook_passes_the_secret_to_storage(self):
        payload = registration_payload()
        payload["webhook_url"] = "https://planet.example.ir/hooks/hub"
        register = mock.AsyncMock(return_value=("a" * 32, PROJECT_ID))
        with mock.patch.object(server, "register_user", register), mock.patch.dict(
            os.environ,
            {"HUB_WEBHOOK_ALLOW_HTTP": "0", "HUB_WEBHOOK_ALLOW_PRIVATE": "0"},
        ):
            response = await self.client.post("/api/register", json=payload)
        self.assertEqual(response.status, 201)
        kwargs = register.await_args.kwargs
        self.assertEqual(kwargs["webhook_url"], payload["webhook_url"])
        self.assertGreaterEqual(len(kwargs["webhook_secret"]), 40)

    async def test_register_rejects_an_unsafe_webhook_url(self):
        payload = registration_payload()
        payload["webhook_url"] = "http://planet.example.ir/hooks/hub"
        with mock.patch.dict(
            os.environ,
            {"HUB_WEBHOOK_ALLOW_HTTP": "0", "HUB_WEBHOOK_ALLOW_PRIVATE": "0"},
        ):
            response = await self.client.post("/api/register", json=payload)
        self.assertEqual(response.status, 422)
        self.assertIn("HTTPS", (await response.json())["message"])

    async def test_registration_without_webhook_has_no_secret(self):
        response = await self.client.post(
            "/api/register", json=registration_payload()
        )
        self.assertEqual(response.status, 201)
        data = await response.json()
        self.assertNotIn("webhook_secret", data)
        self.assertNotIn("webhook_url", data)

    async def test_project_webhook_can_be_set_and_removed(self):
        payload = {"webhook_url": "https://planet.example.ir/hooks/hub"}
        with mock.patch.dict(
            os.environ,
            {"HUB_WEBHOOK_ALLOW_HTTP": "0", "HUB_WEBHOOK_ALLOW_PRIVATE": "0"},
        ):
            response = await self.client.post(
                f"/api/projects/{PROJECT_ID}/webhook",
                json=payload,
                headers=self.auth_headers(),
            )
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["webhook_url"], payload["webhook_url"])
        self.assertGreaterEqual(len(data["webhook_secret"]), 40)

        response = await self.client.delete(
            f"/api/projects/{PROJECT_ID}/webhook",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"status": True})

    async def test_project_webhook_requires_matching_project(self):
        response = await self.client.post(
            f"/api/projects/{PROJECT_ID + 1}/webhook",
            json={"webhook_url": "https://planet.example.ir/hooks/hub"},
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status, 403)


        response = await self.client.post(
            "/api/register",
            data="{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(
            await response.json(),
            {"status": False, "message": "Invalid JSON."},
        )

    async def test_project_token_is_required(self):
        response = await self.client.get(
            f"/api/projects/{PROJECT_ID}/client-key"
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(
            await response.json(),
            {"status": False, "message": "Project token required."},
        )

    async def test_project_must_match_token(self):
        response = await self.client.get(
            f"/api/projects/{PROJECT_ID + 1}/client-key",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(
            await response.json(),
            {"status": False, "message": "Project does not match token."},
        )

    async def test_client_key_is_derived_from_project_token(self):
        response = await self.client.get(
            f"/api/projects/{PROJECT_ID}/client-key",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            await response.json(),
            {
                "status": True,
                "client_key": server.derive_client_key(PROJECT_TOKEN),
            },
        )

    async def test_project_token_can_be_rotated(self):
        response = await self.client.post(
            f"/api/projects/{PROJECT_ID}/token", headers=self.auth_headers()
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            await response.json(),
            {
                "status": True,
                "token": "b" * 32,
                "client_key": server.derive_client_key("b" * 32),
            },
        )

    async def test_unknown_api_route_returns_json(self):
        response = await self.client.get("/api/does-not-exist")
        self.assertEqual(response.status, 404)
        self.assertEqual(
            await response.json(),
            {"status": False, "message": "Not Found"},
        )

    async def test_unhandled_error_returns_json(self):
        with self.assertLogs("support_hub", level="ERROR"):
            response = await self.client.get("/api/boom")
        self.assertEqual(response.status, 500)
        self.assertEqual(
            await response.json(),
            {"status": False, "message": "Internal server error."},
        )

    async def test_registration_is_rate_limited(self):
        for _ in range(server.HTTP_RATE_LIMITS[("POST", "/api/register")][0]):
            response = await self.client.post(
                "/api/register", json=registration_payload()
            )
            self.assertEqual(response.status, 201)
        response = await self.client.post(
            "/api/register", json=registration_payload()
        )
        self.assertEqual(response.status, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertEqual(
            await response.json(),
            {"status": False, "message": "Too many requests."},
        )


if __name__ == "__main__":
    unittest.main()
