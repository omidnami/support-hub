import hashlib
import hmac
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from support_hub.http_server import build_app


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = SimpleNamespace(
            list_clients=AsyncMock(return_value=[]),
            list_channels=AsyncMock(return_value=[]),
            add_client=AsyncMock(),
            add_channel=AsyncMock(),
            instagram_channel=AsyncMock(return_value=None),
            get_channel=AsyncMock(return_value=None))
        self.manager = SimpleNamespace(ingest=AsyncMock())
        self.key = "a" * 64
        with patch.dict(os.environ, {"HUB_MANAGEMENT_KEY": self.key,
                                    "HUB_META_APP_SECRET": "meta-test",
                                    "HUB_META_VERIFY_TOKEN": "verify-test"}):
            self.client = TestClient(TestServer(build_app(self.db, self.manager)))
        await self.client.start_server()
        self.headers = {"Authorization": "Bearer " + self.key}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_management_requires_secret(self):
        r = await self.client.get("/v1/tenants/shop/clients")
        self.assertEqual(r.status, 401)
        self.db.list_clients.assert_not_awaited()
        r = await self.client.get("/v1/tenants/shop/clients", headers=self.headers)
        self.assertEqual(r.status, 200)
        self.db.list_clients.assert_awaited_once_with("shop")

    async def test_create_client_and_channel(self):
        r = await self.client.post("/v1/tenants/shop/clients", headers=self.headers,
                                   json={"endpoint":"admin", "peers":["public"]})
        self.assertEqual(r.status, 201)
        data = await r.json()
        self.assertEqual(len(data["token"]), 64)
        self.db.add_client.assert_awaited_once_with("shop", "admin", data["token"], ["public"])
        r = await self.client.post("/v1/tenants/shop/channels", headers=self.headers,
            json={"owner_endpoint":"admin", "platform":"instagram", "token":"provider-secret", "account_id":"123"})
        self.assertEqual(r.status, 201)
        self.assertNotIn("token", await r.json())

    async def test_instagram_signed_callback(self):
        raw = json.dumps({"object":"instagram", "entry":[{"id":"123","messaging":[]}]}).encode()
        channel = object()
        self.db.instagram_channel.return_value = channel
        r = await self.client.post("/webhook/instagram", data=raw)
        self.assertEqual(r.status, 403)
        self.db.instagram_channel.assert_not_awaited()
        sig = "sha256=" + hmac.new(b"meta-test", raw, hashlib.sha256).hexdigest()
        r = await self.client.post("/webhook/instagram", data=raw,
                                   headers={"X-Hub-Signature-256":sig})
        self.assertEqual(r.status, 200)
        self.db.instagram_channel.assert_awaited_once_with("123")
        self.manager.ingest.assert_awaited_once()
        r = await self.client.get("/webhook/instagram", params={
            "hub.mode":"subscribe", "hub.verify_token":"verify-test", "hub.challenge":"42"})
        self.assertEqual(r.status, 200)
        self.assertEqual(await r.text(), "42")
