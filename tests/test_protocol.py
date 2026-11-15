import unittest
from uuid import uuid4

from support_hub.protocol import ProtocolError, decode, normalize_message


class ProtocolTests(unittest.TestCase):
    def test_source_is_authenticated_identity(self):
        mid = str(uuid4())
        frame = {"type": "message", "id": mid, "from": "forged", "to": "public", "payload": "hello"}
        message = normalize_message(frame, "tenant", "admin", {"public"})
        self.assertEqual(message["from"], "admin")
        self.assertEqual(message["id"], mid)
        self.assertNotEqual(message["delivery_id"], normalize_message(frame, "other", "admin", {"public"})["delivery_id"])

    def test_peer_allowlist(self):
        with self.assertRaises(ProtocolError):
            normalize_message({"id": str(uuid4()), "to": "other", "payload": {}}, "tenant", "admin", {"public"})

    def test_invalid_json(self):
        for raw in (b"{}", "[]", '{"value": NaN}', '{"value":1e400}', "{"):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                decode(raw)
