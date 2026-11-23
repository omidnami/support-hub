import asyncio
import os
import time
import unittest
from unittest import mock

import socketio
from aiohttp.test_utils import TestServer

import aiohttp
import server


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


PROJECT_TOKEN = "project-token"
ROBOT_ID = "my-shop-chat"
BALE_ROBOT_ID = "bale-bot"
BALE_SERVICE_TOKEN = "bale-service-token-for-tests"
WEBHOOK_URL = "https://planet.example.ir/hooks/hub"
WEBHOOK_SECRET = "w" * 43


async def _open_database():
    return _Pool()


async def _authenticate_project(pool, token):
    return 1 if token == PROJECT_TOKEN else None


async def _get_project_webhook(pool, project_id):
    return (WEBHOOK_URL, WEBHOOK_SECRET) if project_id == 1 else None


async def _find_source_project(pool, name, robot_id):
    if (name, robot_id) in (("chat", ROBOT_ID), ("bale", BALE_ROBOT_ID)):
        return 1
    return None


async def _find_source_project_with_token(pool, name, robot_id):
    if (name, robot_id) == ("chat", ROBOT_ID):
        return 1, PROJECT_TOKEN
    return None


def bale_payload(sender="client", text="hello", user_id="501"):
    return {
        "user_id": user_id,
        "sender": sender,
        "text": text,
        "file": None,
        "robot_id": BALE_ROBOT_ID,
        "timestamp": 1794841200000,
        "source": "bale",
    }


class SocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_functions = {
            name: getattr(server, name)
            for name in (
                "open_database", "authenticate_project", "find_source_project",
                "find_source_project_with_token", "get_project_webhook",
                "deliver_webhook",
            )
        }
        server.open_database = _open_database
        server.authenticate_project = _authenticate_project
        server.find_source_project = _find_source_project
        server.find_source_project_with_token = _find_source_project_with_token
        server.get_project_webhook = _get_project_webhook
        self.webhook_calls = []

        async def _deliver_webhook(*args):
            self.webhook_calls.append(args)

        server.deliver_webhook = _deliver_webhook
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "HUB_BALE_SERVICE_TOKEN": BALE_SERVICE_TOKEN,
                "HUB_ALLOWED_ORIGINS": "",
            },
        )
        self.env_patch.start()

        self.app, self.sio, self.emit_source = server.create_app()
        self.test_server = TestServer(self.app)
        await self.test_server.start_server()
        self.url = str(self.test_server.make_url("/")).rstrip("/")
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            if client.connected:
                await client.disconnect()
        await self.test_server.close()
        self.env_patch.stop()
        for name, function in self.original_functions.items():
            setattr(server, name, function)

    def new_client(self, reconnection=True):
        client = socketio.AsyncClient(reconnection=reconnection)
        self.clients.append(client)
        return client

    async def connect_operator(self, reconnection=True):
        operator = self.new_client(reconnection=reconnection)
        authenticated = asyncio.Event()

        @operator.on("authenticated")
        async def on_authenticated(data):
            authenticated.set()

        await operator.connect(
            self.url, auth={"token": PROJECT_TOKEN}, transports=["websocket"]
        )
        await asyncio.wait_for(authenticated.wait(), timeout=2)
        return operator

    async def connect_bridge(self, token=BALE_SERVICE_TOKEN):
        bridge = self.new_client()
        authenticated = asyncio.Event()

        @bridge.on("authenticated")
        async def on_authenticated(data):
            authenticated.set()

        await bridge.connect(
            self.url,
            auth={"role": "bridge", "provider": "bale", "token": token},
            transports=["websocket"],
        )
        await asyncio.wait_for(authenticated.wait(), timeout=2)
        return bridge

    async def connect_client(self, user_id="101", signature=None, expires=None,
                             reconnection=True):
        client = self.new_client(reconnection=reconnection)
        authenticated = asyncio.Event()

        @client.on("authenticated")
        async def on_authenticated(data):
            authenticated.set()

        if expires is None:
            expires = int(time.time()) + 300
        if signature is None:
            key = server.derive_client_key(PROJECT_TOKEN)
            signature = server.client_signature(key, ROBOT_ID, user_id, expires)
        await client.connect(
            self.url,
            auth={
                "role": "client",
                "robot_id": ROBOT_ID,
                "user_id": user_id,
                "expires": expires,
                "signature": signature,
            },
            transports=["websocket"],
        )
        await asyncio.wait_for(authenticated.wait(), timeout=2)
        return client

    async def _origin_handshake(self, url, origin):
        session = aiohttp.ClientSession()
        self.addAsyncCleanup(session.close)
        ws_url = url.replace("https://", "wss://").replace("http://", "ws://")
        return await session.ws_connect(
            f"{ws_url}/socket.io/?EIO=4&transport=websocket",
            headers={"Origin": origin},
        )

    async def test_cross_origin_rejected_by_default(self):
        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self._origin_handshake(self.url, "https://store.example")

    async def test_configured_origins_allow_cross_origin(self):
        with mock.patch.dict(
            os.environ, {"HUB_ALLOWED_ORIGINS": "*"}, clear=False
        ):
            app, _, _ = server.create_app()
        other_server = TestServer(app)
        await other_server.start_server()
        self.addAsyncCleanup(other_server.close)
        other_url = str(other_server.make_url("/")).rstrip("/")

        operator = self.new_client()
        authenticated = asyncio.Event()

        @operator.on("authenticated")
        async def on_authenticated(data):
            authenticated.set()

        await operator.connect(
            other_url,
            headers={"Origin": "https://store.example"},
            auth={"token": PROJECT_TOKEN},
            transports=["websocket"],
        )
        await asyncio.wait_for(authenticated.wait(), timeout=2)

    async def test_allowed_origin_list_rejects_unknown_origin(self):
        with mock.patch.dict(
            os.environ,
            {"HUB_ALLOWED_ORIGINS": "https://panel.example"},
            clear=False,
        ):
            app, _, _ = server.create_app()
        other_server = TestServer(app)
        await other_server.start_server()
        self.addAsyncCleanup(other_server.close)
        other_url = str(other_server.make_url("/")).rstrip("/")

        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self._origin_handshake(other_url, "https://store.example")

    async def test_client_message_reaches_operator_and_conversation(self):
        operator = await self.connect_operator()
        client = await self.connect_client()

        operator_message = asyncio.Event()
        client_message = asyncio.Event()
        received = {}

        @operator.on("chat")
        async def on_operator_chat(data):
            received["operator"] = data
            operator_message.set()

        @client.on("chat")
        async def on_client_chat(data):
            received["client"] = data
            client_message.set()

        payload = {
            "user_id": "101",
            "sender": "client",
            "text": "hello",
            "file": None,
            "robot_id": ROBOT_ID,
            "timestamp": 1794841200000,
            "source": "chat",
        }
        await client.emit("chat", payload)
        await asyncio.wait_for(operator_message.wait(), timeout=2)
        await asyncio.wait_for(client_message.wait(), timeout=2)
        self.assertEqual(received["operator"], payload)
        self.assertEqual(received["client"], payload)
        self.assertEqual(self.webhook_calls, [])

    async def test_token_rotation_disconnects_open_project_sockets(self):
        operator = await self.connect_operator(reconnection=False)
        client = await self.connect_client(reconnection=False)

        operator_gone = asyncio.Event()
        client_gone = asyncio.Event()

        @operator.on("disconnect")
        async def on_operator_disconnect(*args):
            operator_gone.set()

        @client.on("disconnect")
        async def on_client_disconnect(*args):
            client_gone.set()

        rotate = mock.AsyncMock(return_value="new-project-token")
        with mock.patch.object(server, "rotate_project_token", rotate):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.url}/api/projects/1/token",
                    headers={"Authorization": f"Bearer {PROJECT_TOKEN}"},
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        (await response.json())["token"], "new-project-token"
                    )
        await asyncio.wait_for(operator_gone.wait(), timeout=2)
        await asyncio.wait_for(client_gone.wait(), timeout=2)
        rotate.assert_awaited_once()

    async def test_client_cannot_use_another_users_signature(self):
        expires = int(time.time()) + 300
        key = server.derive_client_key(PROJECT_TOKEN)
        signature_for_101 = server.client_signature(key, ROBOT_ID, "101", expires)

        with self.assertRaises(socketio.exceptions.ConnectionError):
            await self.connect_client(
                user_id="102", signature=signature_for_101, expires=expires
            )

    async def test_operator_requires_valid_project_token(self):
        with self.assertRaises(socketio.exceptions.ConnectionError):
            operator = self.new_client()
            await operator.connect(
                self.url, auth={"token": "invalid"}, transports=["websocket"]
            )

    async def test_bale_bridge_requires_service_token(self):
        with self.assertRaises(socketio.exceptions.ConnectionError):
            await self.connect_bridge(token="wrong-token")

    async def test_only_one_bale_bridge_is_active(self):
        await self.connect_bridge()
        with self.assertRaises(socketio.exceptions.ConnectionError):
            await self.connect_bridge()

    async def test_concurrent_bridge_connections_allow_only_one(self):
        results = await asyncio.gather(
            self.connect_bridge(), self.connect_bridge(), return_exceptions=True
        )
        connected = [result for result in results
                     if not isinstance(result, BaseException)]
        self.assertEqual(len(connected), 1)

    async def test_bale_bridge_inbound_reaches_operator(self):
        operator = await self.connect_operator()
        bridge = await self.connect_bridge()
        received = asyncio.Event()
        messages = []

        @operator.on("chat")
        async def on_chat(data):
            messages.append(data)
            received.set()

        payload = bale_payload()
        ack = await bridge.call("chat", payload)
        await asyncio.wait_for(received.wait(), timeout=2)
        self.assertEqual(ack, {"status": True})
        self.assertEqual(messages, [payload])

    async def test_bale_bridge_inbound_without_operator_uses_webhook(self):
        bridge = await self.connect_bridge()
        payload = bale_payload()
        ack = await bridge.call(
            "chat", (payload, {"update_id": 10, "chat_id": "501"})
        )
        self.assertEqual(ack, {"status": True})
        self.assertEqual(len(self.webhook_calls), 1)
        url, secret, project_id, message, chat_id, delivery_id = self.webhook_calls[0]
        self.assertEqual(url, WEBHOOK_URL)
        self.assertEqual(secret, WEBHOOK_SECRET)
        self.assertEqual(project_id, 1)
        self.assertEqual(message, payload)
        self.assertEqual(chat_id, "501")
        self.assertEqual(delivery_id, "bale:bale-bot:10")

    async def test_bale_bridge_inbound_with_operator_skips_webhook(self):
        operator = await self.connect_operator()
        bridge = await self.connect_bridge()
        received = asyncio.Event()

        @operator.on("chat")
        async def on_chat(_data):
            received.set()

        ack = await bridge.call("chat", bale_payload())
        await asyncio.wait_for(received.wait(), timeout=2)
        self.assertEqual(ack, {"status": True})
        self.assertEqual(self.webhook_calls, [])

    async def test_offline_bridge_without_webhook_is_rejected(self):
        bridge = await self.connect_bridge()
        server.get_project_webhook = mock.AsyncMock(return_value=None)
        ack = await bridge.call("chat", bale_payload())
        self.assertEqual(
            ack,
            {
                "status": False,
                "message": "Store is offline and no webhook is configured.",
                "code": "store_offline",
            },
        )

    async def test_offline_webhook_failure_is_rejected(self):
        bridge = await self.connect_bridge()

        async def _failed_delivery(*_args):
            raise server.WebhookDeliveryError("boom")

        server.deliver_webhook = _failed_delivery
        ack = await bridge.call("chat", bale_payload())
        self.assertEqual(
            ack,
            {
                "status": False,
                "message": "Store is offline and the project webhook failed.",
                "code": "webhook_failed",
            },
        )

    async def test_internal_client_without_operator_uses_webhook(self):
        client = await self.connect_client(user_id="101")
        payload = {
            "user_id": "101",
            "sender": "client",
            "text": "hello",
            "file": None,
            "robot_id": ROBOT_ID,
            "timestamp": 1794841200000,
            "source": "chat",
        }
        ack = await client.call("chat", payload)
        self.assertEqual(ack, {"status": True})
        self.assertEqual(len(self.webhook_calls), 1)
        self.assertEqual(self.webhook_calls[0][4], "101")
        self.assertTrue(self.webhook_calls[0][5].startswith("msg:"))

    async def test_bridge_transport_can_override_identity_metadata(self):
        bridge = await self.connect_bridge()
        ack = await bridge.call(
            "chat",
            (bale_payload(), {"chat_id": "chat-7", "delivery_id": "delivery-7"}),
        )
        self.assertEqual(ack, {"status": True})
        self.assertEqual(self.webhook_calls[0][4], "chat-7")
        self.assertEqual(self.webhook_calls[0][5], "delivery-7")

    async def test_operator_bale_reply_reaches_bridge(self):
        operator = await self.connect_operator()
        bridge = await self.connect_bridge()
        received = asyncio.Event()
        messages = []

        @bridge.on("chat")
        async def on_chat(data):
            messages.append(data)
            received.set()

        payload = bale_payload(sender="operator", text="reply")
        ack = await operator.call("chat", payload)
        await asyncio.wait_for(received.wait(), timeout=2)
        self.assertEqual(ack, {"status": True})
        self.assertEqual(messages, [payload])

    async def test_operator_bale_reply_is_rejected_when_bridge_is_offline(self):
        operator = await self.connect_operator()
        ack = await operator.call("chat", bale_payload(sender="operator"))
        self.assertEqual(
            ack,
            {"status": False, "message": "Provider bridge is offline."},
        )

    async def test_bale_bridge_cannot_send_an_internal_chat_source(self):
        bridge = await self.connect_bridge()
        payload = bale_payload()
        payload["source"] = "chat"
        payload["robot_id"] = ROBOT_ID
        ack = await bridge.call("chat", payload)
        self.assertEqual(
            ack,
            {"status": False, "message": "Bridge cannot send this source."},
        )


if __name__ == "__main__":
    unittest.main()
