import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support_hub.channels.bale import Bale
from support_hub.channels.base import ProviderError
from support_hub.polling import BalePolling


class BaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_methods(self):
        adapter = Bale(None)
        channel = SimpleNamespace(id="id", platform="bale", credentials={"webhook_secret":"secret"})
        adapter.call = AsyncMock(return_value=True)
        await adapter.register(channel, "https://example.com")
        adapter.call.assert_awaited_once_with(channel, "setWebhook", {"url":"https://example.com/webhook/bale/id/secret"})
        adapter.call.reset_mock()
        await adapter.unregister(channel)
        adapter.call.assert_awaited_once_with(channel, "deleteWebhook", {})
        adapter.call = AsyncMock(return_value=[{"update_id":2}, {"update_id":1}])
        self.assertEqual(await adapter.updates(channel, 1), [{"update_id":1}, {"update_id":2}])
        adapter.call.assert_awaited_once_with(channel, "getUpdates", {"offset":1,"limit":100,"timeout":10})
        adapter.call.return_value = [{"update_id":"invalid"}]
        with self.assertRaises(ProviderError):
            await adapter.updates(channel, 1)

    async def test_cursor_only_after_ingest(self):
        channel = SimpleNamespace(id="id", poll_offset=5, receive_mode="polling")
        db = SimpleNamespace(get_channel=AsyncMock(return_value=channel),
                             advance_poll_offset=AsyncMock(return_value=1))
        manager = SimpleNamespace(ingest=AsyncMock())
        worker = BalePolling(db, manager, None)
        worker.adapter.updates = AsyncMock(return_value=[{"update_id":4},{"update_id":5},{"update_id":6}])
        manager.ingest.side_effect = [None, RuntimeError("queue failure")]
        with self.assertRaises(RuntimeError):
            await worker.once(channel)
        db.advance_poll_offset.assert_awaited_once_with("id", 6)
        self.assertEqual(manager.ingest.await_count, 2)
        worker.adapter.updates.assert_awaited_once_with(channel, 5)

    async def test_private_message_zero_update_id(self):
        events = Bale(None).events(None, {"update_id":0,"message":{"message_id":1,
            "chat":{"id":9631638400,"type":"private"},"from":{"id":9631638400},"text":"سلام"}})
        self.assertEqual(events[0][0], "0")
        self.assertEqual(events[0][1]["external_id"], "9631638400")
        self.assertEqual(events[0][1]["platform"], "bale")
