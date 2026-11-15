import os
import secrets
import unittest
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from support_hub.browser_sessions import BrowserSessions
from support_hub.redis_keys import RedisKeys, validate_prefix
from support_hub.redis_manager import RedisManager


class NamespaceTests(unittest.TestCase):
    def test_all_keys_use_configured_prefix(self):
        keys = RedisKeys("planet-hub:")
        self.assertEqual(keys.index("shop", "admin"), "planet-hub:{shop}:pending:admin")
        self.assertEqual(keys.item("shop", "admin", "id"), "planet-hub:{shop}:message:admin:id")
        self.assertEqual(keys.receipt("shop", "id"), "planet-hub:{shop}:receipt:id")
        self.assertEqual(keys.browser("shop", "web-id"), "planet-hub:{shop}:browser:web-id")
        self.assertTrue(RedisKeys().index("shop", "admin").startswith("support-hub:"))

    def test_invalid_prefix(self):
        for prefix in ("", "*:", "abc:*", "abc", "a b:", "{a}:", "a?:", "a\n:", "a"*49 + ":"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                validate_prefix(prefix)


@unittest.skipUnless(os.getenv("HUB_TEST_REDIS_ACL") == "1" and os.getenv("HUB_TEST_REDIS_URL"),
                     "ACL tests require an explicitly enabled disposable Redis instance")
class RestrictedRedisTests(unittest.IsolatedAsyncioTestCase):
    async def test_least_privilege(self):
        url = os.environ["HUB_TEST_REDIS_URL"]
        parsed = urlsplit(url)
        number = int(parsed.path.strip("/") or "0")
        if number == 0:
            self.skipTest("ACL integration requires a dedicated nonzero test database")
        admin = Redis.from_url(url, decode_responses=True, protocol=2)
        username = "hub-ci-" + uuid4().hex
        prefix = username + ":"
        password = secrets.token_hex(32)
        own = Redis(host=parsed.hostname, port=parsed.port or 6379, db=number,
                    username=username, password=password, decode_responses=True, protocol=2)
        try:
            await admin.execute_command("ACL", "SETUSER", username, "on", ">" + password,
                "~" + prefix + "*", "resetchannels", "-@all", "+ping", "+select|" + str(number),
                "+get", "+set", "+expire", "+del", "+zremrangebyscore", "+zcard",
                "+zadd", "+zrem", "+zrange", "+eval")
            self.assertTrue(await own.ping())
            store = RedisManager(own, ttl=30, prefix=prefix)
            msg = {"to":"admin", "delivery_id":"id", "payload":"test"}
            self.assertEqual(await store.enqueue("shop", msg), 1)
            self.assertEqual(await store.pending("shop", "admin"), ["id"])
            self.assertIsNotNone(await store.get("shop", "admin", "id"))
            self.assertTrue(await store.ack("shop", "admin", "id"))
            sessions = BrowserSessions(own, SimpleNamespace(), prefix=prefix)
            token = await sessions.issue("shop", "admin", uuid4().hex, "https://example.test")
            self.assertIsNotNone(await sessions.record("shop", token["endpoint"]))
            self.assertTrue(await sessions.owns("shop", "admin", token["endpoint"]))
            with self.assertRaises(ResponseError):
                await own.get("unrelated-test:" + uuid4().hex)
            with self.assertRaises(ResponseError):
                await own.eval("return redis.call('GET', KEYS[1])", 1, "unrelated-test:" + uuid4().hex)
            with self.assertRaises(ResponseError):
                await own.execute_command("SELECT", 0)
            with self.assertRaises(ResponseError):
                await own.execute_command("CONFIG", "GET", "port")
            # Inspect permissions without ever invoking a global data-deletion command.
            rules = await admin.execute_command("ACL", "GETUSER", username)
            self.assertNotIn("+flush", str(rules).lower())
        finally:
            await own.aclose()
            keys = [key async for key in admin.scan_iter(match=prefix + "*")]
            if keys:
                await admin.delete(*keys)
            await admin.execute_command("ACL", "DELUSER", username)
            await admin.aclose()
