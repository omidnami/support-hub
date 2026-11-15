import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from redis.asyncio import Redis
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from support_hub.browser_sessions import BrowserSessions, valid_origin
from support_hub.config import Endpoint, Settings
from support_hub.http_server import build_app
from support_hub.redis_manager import RedisManager
from support_hub.websocket_server import WebSocketServer


class OriginTests(unittest.TestCase):
    def test_origin(self):
        for value in ("https://shop.example.com", "http://localhost:8080"):
            self.assertTrue(valid_origin(value))
        for value in ("null", "http://shop.example.com", "https://example.com/path",
                      "https://user@example.com", "https://example.com?x=1", None):
            self.assertFalse(valid_origin(value))


@unittest.skipUnless(os.getenv("HUB_TEST_REDIS_URL"), "Set HUB_TEST_REDIS_URL")
class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = Redis.from_url(os.environ["HUB_TEST_REDIS_URL"], decode_responses=True)
        self.tenant = "test-" + uuid4().hex
        self.store = RedisManager(self.redis, ttl=30, prefix='hub-browser-test:')
        self.origin = "https://shop.example.com"

        async def verify(tenant, endpoint, token):
            return tenant == self.tenant and endpoint in ("admin", "other") and token == endpoint + "-test"

        async def enabled(tenant, endpoint):
            return tenant == self.tenant and endpoint in ("admin", "other")

        async def authorization(tenant, endpoint, token):
            return Endpoint("", frozenset()) if await verify(tenant, endpoint, token) else None

        self.db = SimpleNamespace(verify_client=verify, client_enabled=enabled, authorization=authorization)
        self.gateway = WebSocketServer(Settings("127.0.0.1", 0, "", 30, 2, {}), self.store, self.db)
        self.server = await serve(self.gateway.handle, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/ws"
        self.http = TestClient(TestServer(build_app(self.db, SimpleNamespace(store=self.store))))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()
        self.server.close()
        await self.server.wait_closed()
        keys = [k async for k in self.redis.scan_iter(match=self.store.keys.tenant(self.tenant) + "*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.aclose()

    async def receive(self, ws):
        return json.loads(await asyncio.wait_for(ws.recv(), 3))

    async def send(self, ws, body):
        await ws.send(json.dumps(body))

    async def login(self, ws, endpoint, token):
        await self.send(ws, {"type":"auth", "tenant":self.tenant, "endpoint":endpoint, "token":token})
        self.assertEqual((await self.receive(ws))["type"], "authenticated")

    async def issue(self, session_id):
        url = f"/v1/tenants/{self.tenant}/clients/admin/browser-sessions"
        response = await self.http.post(url, headers={"Authorization":"Bearer admin-test"},
                         json={"session_id":session_id, "origin":self.origin})
        self.assertEqual(response.status, 201)
        return await response.json()

    async def test_owner_auth_and_rotation(self):
        url = f"/v1/tenants/{self.tenant}/clients/admin/browser-sessions"
        reply = await self.http.post(url, headers={"Authorization":"Bearer other-test"},
                                     json={"session_id":uuid4().hex, "origin":self.origin})
        self.assertEqual(reply.status, 401)
        sid = uuid4().hex
        first, second = await self.issue(sid), await self.issue(sid)
        self.assertEqual(first["endpoint"], second["endpoint"])
        self.assertNotEqual(first["token"], second["token"])
        sessions = BrowserSessions(self.redis, self.db, prefix=self.store.keys.prefix)
        raw = await self.redis.get(sessions.key(self.tenant, first["endpoint"]))
        self.assertNotIn(second["token"], raw)
        self.assertIsNone(await sessions.authenticate(self.tenant, first["endpoint"], first["token"], self.origin))
        self.assertIsNone(await sessions.authenticate(self.tenant, second["endpoint"], second["token"], "https://other.example.com"))
        self.assertIsNotNone(await sessions.authenticate(self.tenant, second["endpoint"], second["token"], self.origin))
        self.assertFalse(await sessions.owns(self.tenant, "other", second["endpoint"]))
        self.assertFalse(await sessions.owns("other-tenant", "admin", second["endpoint"]))

    async def test_two_visitors_reply_and_offline_delivery(self):
        one, two = await self.issue(uuid4().hex), await self.issue(uuid4().hex)
        self.assertNotEqual(one["endpoint"], two["endpoint"])
        async with connect(self.url) as admin:
            await self.login(admin, "admin", "admin-test")
            async with connect(self.url, origin=self.origin) as visitor:
                await self.login(visitor, one["endpoint"], one["token"])
                await self.send(visitor, {"type":"message", "id":str(uuid4()), "to":"admin", "payload":"سلام"})
                self.assertEqual((await self.receive(visitor))["type"], "accepted")
                msg = await self.receive(admin)
                self.assertEqual(msg["from"], one["endpoint"])
                await self.send(admin, {"type":"ack", "delivery_id":msg["delivery_id"]})
                self.assertEqual((await self.receive(admin))["type"], "ack_ok")
                await self.send(visitor, {"type":"message", "id":str(uuid4()), "to":two["endpoint"], "payload":"forbidden"})
                self.assertEqual((await self.receive(visitor))["code"], "destination_not_allowed")
            await self.send(admin, {"type":"message", "id":str(uuid4()), "to":one["endpoint"], "payload":"پاسخ"})
            self.assertEqual((await self.receive(admin))["type"], "accepted")
            async with connect(self.url, origin=self.origin) as visitor2:
                await self.login(visitor2, two["endpoint"], two["token"])
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(visitor2.recv(), 0.15)
            async with connect(self.url, origin=self.origin) as visitor:
                await self.login(visitor, one["endpoint"], one["token"])
                msg = await self.receive(visitor)
                self.assertEqual(msg["payload"], "پاسخ")
                await self.send(visitor, {"type":"ack", "delivery_id":msg["delivery_id"]})
                self.assertEqual((await self.receive(visitor))["type"], "ack_ok")

    async def test_browser_cannot_use_permanent_token(self):
        async with connect(self.url, origin=self.origin) as ws:
            await self.send(ws, {"type":"auth","tenant":self.tenant,"endpoint":"admin","token":"admin-test"})
            with self.assertRaises(ConnectionClosed):
                await self.receive(ws)
