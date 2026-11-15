"""Integration tests: run against a dedicated disposable Redis DB (never production)."""
import asyncio
import json
import os
import unittest
from uuid import uuid4

from redis.asyncio import Redis
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from support_hub.config import Endpoint, Settings
from support_hub.redis_manager import RedisManager
from support_hub.websocket_server import WebSocketServer


@unittest.skipUnless(os.getenv("HUB_TEST_REDIS_URL"), "Set HUB_TEST_REDIS_URL for integration tests")
class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = Redis.from_url(os.environ["HUB_TEST_REDIS_URL"], decode_responses=True)
        self.tenant = "test-" + uuid4().hex
        self.store = RedisManager(self.redis, ttl=3, prefix='hub-delivery-test:')
        settings = Settings("127.0.0.1", 0, "", 3, 0.2, {self.tenant: {
            "admin": Endpoint("a" * 32, frozenset({"public"})),
            "public": Endpoint("b" * 32, frozenset({"admin"})),
        }})
        self.server = await serve(WebSocketServer(settings, self.store).handle, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/ws"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        keys = [k async for k in self.redis.scan_iter(match=self.store.keys.tenant(self.tenant) + "*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.aclose()

    async def login(self, ws, endpoint, token):
        await ws.send(json.dumps({"type": "auth", "tenant": self.tenant, "endpoint": endpoint, "token": token}))
        self.assertEqual((await self.receive(ws))["type"], "authenticated")

    async def receive(self, ws):
        return json.loads(await asyncio.wait_for(ws.recv(), 2))

    async def test_offline_retry_ack_and_dedup(self):
        async with connect(self.url) as admin:
            await self.login(admin, "admin", "a" * 32)
            message = {"type": "message", "id": str(uuid4()), "to": "public", "payload": "hello"}
            await admin.send(json.dumps(message))
            accepted = await self.receive(admin)
            self.assertEqual(accepted["type"], "accepted")
            delivery = accepted["delivery_id"]
            self.assertTrue(await self.store.get(self.tenant, "public", delivery))

            # Sender cannot acknowledge a destination's message.
            await admin.send(json.dumps({"type": "ack", "delivery_id": delivery}))
            self.assertFalse((await self.receive(admin))["removed"])

            async with connect(self.url) as public:
                await self.login(public, "public", "b" * 32)
                self.assertEqual((await self.receive(public))["delivery_id"], delivery)
                self.assertEqual((await self.receive(public))["delivery_id"], delivery)
                await public.send(json.dumps({"type": "ack", "delivery_id": delivery}))
                self.assertTrue((await self.receive(public))["removed"])
                self.assertIsNone(await self.store.get(self.tenant, "public", delivery))
            await admin.send(json.dumps(message))
            self.assertTrue((await self.receive(admin))["duplicate"])
            message["payload"] = "changed"
            await admin.send(json.dumps(message))
            self.assertEqual((await self.receive(admin))["code"], "message_id_conflict")

    async def test_expiry(self):
        async with connect(self.url) as admin:
            await self.login(admin, "admin", "a" * 32)
            await admin.send(json.dumps({"type": "message", "id": str(uuid4()), "to": "public", "payload": {}}))
            accepted = await self.receive(admin)
            await asyncio.sleep(3.2)
            self.assertIsNone(await self.store.get(self.tenant, "public", accepted["delivery_id"]))
            self.assertEqual(await self.store.pending(self.tenant, "public"), [])
