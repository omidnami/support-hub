import os
import tempfile
import time
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bale_service.api import BaleError, BaleUnavailable, FileTooLarge
from bale_service.config import BaleServiceConfig
from bale_service.files import FileCache
from bale_service.repository import BaleSource
from bale_service.service import BaleService


TOKEN = "file-service-token"
SECRET = "s" * 32


def make_config(**overrides):
    values = {
        "hub_url": "http://127.0.0.1:8765",
        "hub_service_token": TOKEN,
        "bale_api_base": "https://tapi.bale.ai",
        "public_base_url": "https://support.example.com",
        "host": "127.0.0.1",
        "port": 8766,
        "sync_interval": 30,
        "request_timeout": 15,
        "hub_ack_timeout": 5,
        "max_update_bytes": 1024 * 1024,
        "webhook_require_https": True,
        "register_webhooks": False,
        "log_level": "INFO",
    }
    values.update(overrides)
    return BaleServiceConfig(**values)


def make_source(**overrides):
    values = {
        "id": 1,
        "project_id": 2,
        "robot_id": "987654",
        "token": "123:bot-token",
        "webhook_secret": SECRET,
        "last_update_id": None,
    }
    values.update(overrides)
    return BaleSource(**values)


class FakeHub:
    connected = True

    async def close(self):
        pass


class FakeAPI:
    def __init__(self, *, error=None, download_error=None,
                 file_info=None, data=b"abc", content_type="image/jpeg"):
        self.error = error
        self.download_error = download_error
        self.file_info = file_info or {
            "file_path": "photos/a.jpg", "file_size": len(data),
        }
        self.data = data
        self.content_type = content_type
        self.get_calls = []
        self.download_calls = []

    async def get_file(self, token, file_id):
        self.get_calls.append((token, file_id))
        if self.error:
            raise self.error
        return self.file_info

    async def download_file(self, token, file_path, max_bytes):
        self.download_calls.append((token, file_path, max_bytes))
        if self.download_error:
            raise self.download_error
        return self.data, self.content_type


class BaleFileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def make_service(self, api=None, *, cache=None):
        return BaleService(
            make_config(),
            pool=object(),
            api=api or FakeAPI(),
            hub=FakeHub(),
            file_cache=cache or FileCache(
                self.temporary.name, ttl=3600, max_bytes=10 * 1024 * 1024
            ),
        )

    async def get(self, service, *, token=TOKEN, **params):
        app = web.Application()
        app.router.add_get("/internal/files", service.handle_file)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.get(
                "/internal/files",
                params={
                    "source": "bale",
                    "robot_id": "987654",
                    "file_id": "AgAC-file",
                    **params,
                },
                headers=headers,
            )
            return response.status, await response.read(), response.headers
        finally:
            await client.close()

    async def test_missing_or_wrong_token_is_rejected(self):
        api = FakeAPI()
        service = self.make_service(api)
        for token in (None, "", "wrong"):
            with self.subTest(token=token):
                status, _, _ = await self.get(service, token=token)
                self.assertEqual(status, 401)
        self.assertEqual(api.get_calls, [])

    async def test_invalid_parameters_are_rejected(self):
        service = self.make_service()
        for params in (
            {"source": "chat"},
            {"source": "bale", "robot_id": ""},
            {"source": "bale", "file_id": ""},
            {"source": "bale", "file_id": "x" * 5000},
        ):
            with self.subTest(params=params):
                status, _, _ = await self.get(service, **params)
                self.assertEqual(status, 400)

    async def test_file_is_downloaded_and_cached(self):
        api = FakeAPI(data=b"image-bytes", content_type="image/jpeg")
        source = make_source()
        cache = FileCache(self.temporary.name)
        service = self.make_service(api, cache=cache)
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=source),
        ):
            status, body, headers = await self.get(service)
            self.assertEqual(status, 200)
            self.assertEqual(body, b"image-bytes")
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            self.assertIn("inline", headers.get("Content-Disposition", "").lower())
            self.assertEqual(api.get_calls, [(source.token, "AgAC-file")])
            self.assertEqual(len(api.download_calls), 1)

            status, body, _ = await self.get(service)
            self.assertEqual(status, 200)
            self.assertEqual(body, b"image-bytes")
            self.assertEqual(len(api.get_calls), 1)
            self.assertEqual(len(api.download_calls), 1)
            self.assertTrue(cache.path(FileCache.key(source.robot_id, "AgAC-file")).is_file())

    async def test_unicode_file_name_is_header_safe(self):
        api = FakeAPI(
            file_info={"file_path": "files/عکس.jpg", "file_size": 1},
            data=b"x",
        )
        service = self.make_service(api)
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=make_source()),
        ):
            status, body, headers = await self.get(service)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"x")
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])

    async def test_unknown_source_is_not_found(self):
        service = self.make_service()
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=None),
        ):
            status, _, _ = await self.get(service)
        self.assertEqual(status, 404)

    async def test_provider_not_found_maps_to_404(self):
        service = self.make_service(
            FakeAPI(error=BaleError("bad file", status=400))
        )
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=make_source()),
        ):
            status, _, _ = await self.get(service)
        self.assertEqual(status, 404)

    async def test_provider_failure_maps_to_502(self):
        service = self.make_service(
            FakeAPI(error=BaleUnavailable("down"))
        )
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=make_source()),
        ):
            status, _, _ = await self.get(service)
        self.assertEqual(status, 502)

    async def test_oversized_file_maps_to_413(self):
        service = self.make_service(
            FakeAPI(download_error=FileTooLarge("too large"))
        )
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=make_source()),
        ):
            status, _, _ = await self.get(service)
        self.assertEqual(status, 413)


class FileCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_round_trip_and_ttl(self):
        cache = FileCache(self.temporary.name, ttl=1)
        key = FileCache.key("987654", "file")
        cache.put(key, b"data", "image/jpeg", "a.jpg")
        cached = cache.get(key)
        self.assertIsNotNone(cached)
        path, meta = cached
        self.assertEqual(path.read_bytes(), b"data")
        self.assertEqual(meta["content_type"], "image/jpeg")
        old = time.time() - 10
        os.utime(path, (old, old))
        self.assertIsNone(cache.get(key))
        self.assertFalse(path.exists())

    def test_size_cap_removes_entries(self):
        cache = FileCache(self.temporary.name, max_bytes=5)
        first = FileCache.key("987654", "one")
        second = FileCache.key("987654", "two")
        cache.put(first, b"1234", "text/plain", "one.txt")
        cache.put(second, b"5678", "text/plain", "two.txt")
        remaining = [key for key in (first, second) if cache.get(key)]
        self.assertEqual(len(remaining), 1)
