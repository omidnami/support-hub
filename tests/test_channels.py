import unittest
from support_hub.channels import ADAPTERS
from support_hub.channels.base import ProviderError


class AdapterTests(unittest.TestCase):
    def test_telegram_private_text(self):
        events = ADAPTERS["telegram"](None).events(None, {"update_id": 8, "message": {
            "message_id": 2, "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "first_name": "User"}, "text": "سلام"}})
        self.assertEqual(events[0][0], "8")
        self.assertEqual(events[0][1]["external_id"], "42")
        self.assertEqual(events[0][1]["text"], "سلام")

    def test_rubika_text(self):
        events = ADAPTERS["rubika"](None).events(None, {"update": {"type": "NewMessage",
            "chat_id": "u123", "new_message": {"message_id": "42", "text": "hello"}}})
        self.assertEqual(events[0][1]["external_id"], "u123")

    def test_invalid_outgoing(self):
        for payload in ({}, {"type": "text", "external_id": "https://foo", "text": "x"}):
            with self.assertRaises(ProviderError):
                ADAPTERS["telegram"](None).text_payload(payload)
