import asyncio
import unittest

from bale_service.hub import HubBridge, HubRejected, HubUnavailable


class FakeClient:
    def __init__(self):
        self.connected = True
        self.result = {"status": True}
        self.error = None
        self.calls = []

    async def call(self, event, data, *args, timeout=None):
        if isinstance(data, tuple):
            self.calls.append((event, *data, timeout))
        else:
            self.calls.append((event, data, timeout))
        if self.error is not None:
            raise self.error
        return self.result

    async def disconnect(self):
        self.connected = False


def make_bridge(on_message=None):
    bridge = HubBridge(
        "http://127.0.0.1:8765", "token", on_message or (lambda payload: None),
        ack_timeout=3,
    )
    bridge.client = FakeClient()
    bridge._authenticated.set()
    return bridge


class HubBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_bridge_rejects_inbound(self):
        bridge = make_bridge()
        bridge._authenticated.clear()
        with self.assertRaises(HubUnavailable):
            await bridge.send_inbound({"text": "hello"})
        self.assertEqual(bridge.client.calls, [])

    async def test_accepted_inbound_returns_the_ack(self):
        bridge = make_bridge()
        payload = {"text": "hello"}
        ack = await bridge.send_inbound(payload)
        self.assertEqual(ack, {"status": True})
        self.assertEqual(
            bridge.client.calls,
            [("chat", payload, 3)],
        )

    async def test_transport_metadata_is_forwarded_separately(self):
        bridge = make_bridge()
        payload = {"text": "hello"}
        metadata = {"update_id": 10, "chat_id": "501"}
        ack = await bridge.send_inbound(payload, metadata)
        self.assertEqual(ack, {"status": True})
        self.assertEqual(
            bridge.client.calls,
            [("chat", payload, metadata, 3)],
        )

    async def test_rejected_inbound_raises_with_the_hub_message(self):
        bridge = make_bridge()
        bridge.client.result = {"status": False, "message": "Source not found."}
        with self.assertRaisesRegex(HubRejected, "Source not found."):
            await bridge.send_inbound({"text": "hello"})

    async def test_timed_out_inbound_is_temporary(self):
        bridge = make_bridge()
        bridge.client.error = asyncio.TimeoutError()
        with self.assertRaises(HubUnavailable):
            await bridge.send_inbound({"text": "hello"})

    async def test_only_operator_bale_messages_reach_the_dispatcher(self):
        received = []
        bridge = HubBridge("http://127.0.0.1:8765", "token", received.append)
        handler = bridge.client.handlers["/"]["chat"]
        await handler({"source": "bale", "sender": "operator", "text": "x"})
        await handler({"source": "bale", "sender": "client", "text": "x"})
        await handler({"source": "chat", "sender": "operator", "text": "x"})
        await handler("not a dict")
        self.assertEqual(
            received,
            [{"source": "bale", "sender": "operator", "text": "x"}],
        )


if __name__ == "__main__":
    unittest.main()
