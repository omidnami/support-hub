import hashlib
import hmac
import unittest
from types import SimpleNamespace

from support_hub.channels.instagram import Instagram, verified_signature
from support_hub.security import Bucket


class InstagramTests(unittest.TestCase):
    def test_signature(self):
        raw = b'{"object":"instagram"}'
        signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        self.assertTrue(verified_signature("secret", raw, signature))
        self.assertFalse(verified_signature("secret", raw + b" ", signature))

    def test_isolation_and_echo(self):
        channel = SimpleNamespace(credentials={"account_id": "123"})
        event = {"sender": {"id": "42"}, "recipient": {"id": "123"},
                 "message": {"mid": "m1", "text": "hello"}}
        echo = {**event, "message": {"mid": "m2", "is_echo": True}}
        body = {"object": "instagram", "entry": [
            {"id": "999", "messaging": [event]},
            {"id": "123", "messaging": [event, echo]}]}
        events = Instagram(None).events(channel, body)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["external_id"], "42")

    def test_bucket(self):
        bucket = Bucket(0, 2)
        self.assertTrue(bucket.allow())
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())
