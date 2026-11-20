import asyncio
import time
import unittest

import socketio
from aiohttp.test_utils import TestServer

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


async def _open_database():
    return _Pool()


async def _authenticate_project(pool, token):
    return 1 if token == PROJECT_TOKEN else None


async def _find_source_project(pool, name, robot_id):
    if (name, robot_id) == ("chat", ROBOT_ID):
        return 1
    return None


async def _find_source_project_with_token(pool, name, robot_id):
    if (name, robot_id) == ("chat", ROBOT_ID):
        return 1, PROJECT_TOKEN
    return None


class SocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_functions = {
            name: getattr(server, name)
            for name in (
                "open_database", "authenticate_project", "find_source_project",
                "find_source_project_with_token",
            )
        }
        server.open_database = _open_database
        server.authenticate_project = _authenticate_project
        server.find_source_project = _find_source_project
        server.find_source_project_with_token = _find_source_project_with_token

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
        for name, function in self.original_functions.items():
            setattr(server, name, function)

    def new_client(self):
        client = socketio.AsyncClient()
        self.clients.append(client)
        return client

    async def connect_operator(self):
        operator = self.new_client()
        authenticated = asyncio.Event()

        @operator.on("authenticated")
        async def on_authenticated(data):
            authenticated.set()

        await operator.connect(
            self.url, auth={"token": PROJECT_TOKEN}, transports=["websocket"]
        )
        await asyncio.wait_for(authenticated.wait(), timeout=2)
        return operator

    async def connect_client(self, user_id="101", signature=None, expires=None):
        client = self.new_client()
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


if __name__ == "__main__":
    unittest.main()
