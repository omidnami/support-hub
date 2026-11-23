"""Tests for the optional Redis metadata cache and its cache-aside helpers."""

import os
import unittest
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

import server
from cache import (
    CACHE_DOWN, CacheStrictError, MetadataCache, chat_key, epoch_key,
    negative_key, source_key,
)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}
        self.fail = False

    async def ping(self):
        if self.fail:
            raise ConnectionError("down")
        return True

    async def get(self, key):
        if self.fail:
            raise ConnectionError("down")
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if self.fail:
            raise ConnectionError("down")
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys):
        if self.fail:
            raise ConnectionError("down")
        removed = 0
        for key in keys:
            removed += 1 if self.data.pop(key, None) is not None else 0
        return removed


def make_cache(**kwargs):
    return MetadataCache(client=FakeRedis(), down_seconds=0, **kwargs)


class CachePrimitiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_epoch_is_stable_and_rotates(self):
        cache = make_cache()
        first = await cache.epoch(1)
        self.assertIsNotNone(first)
        self.assertEqual(await cache.epoch(1), first)
        second = await cache.rotate_epoch(1)
        self.assertNotEqual(first, second)
        self.assertEqual(await cache.epoch(1), second)

    async def test_json_values_and_ttls(self):
        cache = make_cache()
        await cache.set_json("key", {"project_id": 3}, 120)
        self.assertEqual(await cache.get_json("key"), {"project_id": 3})
        self.assertEqual(cache._client.ttls["key"], 120)
        await cache.delete("key")
        self.assertIsNone(await cache.get_json("key"))

    async def test_down_cache_reports_unavailable(self):
        cache = make_cache()
        cache._client.fail = True
        self.assertIs(await cache.get_json("key"), CACHE_DOWN)
        self.assertIsNone(await cache.rotate_epoch(1))

    async def test_disabled_cache_is_not_used(self):
        cache = MetadataCache()
        self.assertFalse(cache.enabled)
        self.assertIs(await cache.get_json("key"), CACHE_DOWN)

    async def test_from_env_reads_url_and_ttls(self):
        with mock.patch.dict(
            os.environ,
            {
                "HUB_REDIS_URL": "redis://127.0.0.1:6379/3",
                "HUB_CACHE_STRICT": "1",
                "HUB_CACHE_TTL_WEBHOOK": "10800",
                "HUB_CACHE_TTL_TOKEN": "45",
                "HUB_CACHE_TTL_NEGATIVE": "7",
            },
        ):
            cache = MetadataCache.from_env()
        self.assertEqual(cache.url, "redis://127.0.0.1:6379/3")
        self.assertTrue(cache.strict)
        self.assertEqual(cache.ttl_token, 45)
        self.assertEqual(cache.ttl_negative, 7)
        self.assertEqual(cache.ttl_webhook, 3 * 60 * 60)


class ServerCacheAsideTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_token_is_cached_until_epoch_rotation(self):
        cache = make_cache()
        authenticate = mock.AsyncMock(return_value=9)
        with mock.patch.object(server, "authenticate_project", authenticate):
            self.assertEqual(
                await server.cached_authenticate_project(
                    object(), cache, "project-token"
                ),
                9,
            )
            self.assertEqual(
                await server.cached_authenticate_project(
                    object(), cache, "project-token"
                ),
                9,
            )
            authenticate.assert_awaited_once()

            await cache.rotate_epoch(9)
            self.assertEqual(
                await server.cached_authenticate_project(
                    object(), cache, "project-token"
                ),
                9,
            )
            self.assertEqual(authenticate.await_count, 2)

    async def test_unknown_token_is_negatively_cached(self):
        cache = make_cache()
        authenticate = mock.AsyncMock(return_value=None)
        with mock.patch.object(server, "authenticate_project", authenticate):
            self.assertIsNone(
                await server.cached_authenticate_project(
                    object(), cache, "bad-token"
                )
            )
            self.assertIsNone(
                await server.cached_authenticate_project(
                    object(), cache, "bad-token"
                )
            )
            authenticate.assert_awaited_once()

    async def test_source_lookup_cache_hit_and_epoch_invalidation(self):
        cache = make_cache()
        lookup = mock.AsyncMock(return_value=4)
        with mock.patch.object(server, "find_source_project", lookup):
            self.assertEqual(
                await server.cached_source_project(
                    object(), cache, "bale", "bot-1"
                ),
                4,
            )
            self.assertEqual(
                await server.cached_source_project(
                    object(), cache, "bale", "bot-1"
                ),
                4,
            )
            lookup.assert_awaited_once()

            await cache.rotate_epoch(4)
            self.assertEqual(
                await server.cached_source_project(
                    object(), cache, "bale", "bot-1"
                ),
                4,
            )
            self.assertEqual(lookup.await_count, 2)

    async def test_missing_source_is_negatively_cached_and_cleared(self):
        cache = make_cache()
        lookup = mock.AsyncMock(return_value=None)
        with mock.patch.object(server, "find_source_project", lookup):
            self.assertIsNone(
                await server.cached_source_project(
                    object(), cache, "bale", "missing"
                )
            )
            self.assertIsNone(
                await server.cached_source_project(
                    object(), cache, "bale", "missing"
                )
            )
            lookup.assert_awaited_once()

            await server.invalidate_source_cache(
                cache, 4, "bale", "missing"
            )
            self.assertIsNone(
                await server.cached_source_project(
                    object(), cache, "bale", "missing"
                )
            )
            self.assertEqual(lookup.await_count, 2)

    async def test_chat_source_caches_client_key_without_raw_token(self):
        cache = make_cache()
        token = "project-token"
        expected_key = server.derive_client_key(token)
        lookup = mock.AsyncMock(return_value=(5, token))
        with mock.patch.object(
            server, "find_source_project_with_token", lookup
        ):
            self.assertEqual(
                await server.cached_chat_source(object(), cache, "shop"),
                (5, expected_key),
            )
            self.assertEqual(
                await server.cached_chat_source(object(), cache, "shop"),
                (5, expected_key),
            )
            lookup.assert_awaited_once()
            cached = await cache.get_json(chat_key("shop"))
            self.assertNotIn(token, str(cached))

    async def test_webhook_cache_and_negative_cache(self):
        cache = make_cache()
        first = mock.AsyncMock(
            return_value=("https://planet.example.ir/hook", "secret")
        )
        with mock.patch.object(server, "get_project_webhook", first):
            self.assertEqual(
                await server.cached_project_webhook(object(), cache, 3),
                ("https://planet.example.ir/hook", "secret"),
            )
            self.assertEqual(
                await server.cached_project_webhook(object(), cache, 3),
                ("https://planet.example.ir/hook", "secret"),
            )
            first.assert_awaited_once()

        empty = mock.AsyncMock(return_value=None)
        with mock.patch.object(server, "get_project_webhook", empty):
            self.assertIsNone(
                await server.cached_project_webhook(object(), cache, 4)
            )
            self.assertIsNone(
                await server.cached_project_webhook(object(), cache, 4)
            )
            empty.assert_awaited_once()

    async def test_strict_invalidation_fails_closed(self):
        client = FakeRedis()
        cache = MetadataCache(client=client, strict=True, down_seconds=0)
        client.fail = True
        with self.assertRaises(CacheStrictError):
            await server.invalidate_project_cache(cache, 1)
        self.assertIsNone(client.data.get(epoch_key(1)))

    async def test_non_strict_invalidation_tolerates_outage(self):
        client = FakeRedis()
        cache = MetadataCache(client=client, strict=False, down_seconds=0)
        client.fail = True
        await server.invalidate_project_cache(cache, 1)

    async def test_source_invalidation_removes_source_and_negative_keys(self):
        cache = make_cache()
        for key in (
            source_key("bale", "old"),
            negative_key("src", "bale", "old"),
            chat_key("old"),
            negative_key("chat", "chat", "old"),
        ):
            await cache.set_json(key, {"x": 1}, 60)
        await server.invalidate_source_cache(cache, 1, "chat", "old")
        self.assertIsNone(await cache.get_json(source_key("chat", "old")))
        self.assertIsNone(
            await cache.get_json(negative_key("src", "chat", "old"))
        )
        self.assertIsNone(await cache.get_json(chat_key("old")))
        self.assertIsNone(
            await cache.get_json(negative_key("chat", "chat", "old"))
        )
        # Source entries for other channels are untouched.
        self.assertEqual(
            await cache.get_json(source_key("bale", "old")), {"x": 1}
        )


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


async def _open_database():
    return _Pool()


async def _get_project_token(pool, project_id):
    return "project-token"


class ServerCacheWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cache = make_cache()
        self.app, self.sio, self.emit_source = server.create_app(cache=self.cache)
        self.patches = [
            mock.patch.object(server, "open_database", _open_database),
            mock.patch.object(server, "get_project_token", _get_project_token),
        ]
        for patch in self.patches:
            patch.start()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for patch in reversed(self.patches):
            patch.stop()

    def auth_headers(self):
        return {"Authorization": "Bearer project-token"}

    async def test_token_auth_uses_cache_between_requests(self):
        authenticate = mock.AsyncMock(return_value=7)
        with mock.patch.object(server, "authenticate_project", authenticate):
            first = await self.client.get(
                "/api/projects/7/client-key", headers=self.auth_headers()
            )
            second = await self.client.get(
                "/api/projects/7/client-key", headers=self.auth_headers()
            )
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        authenticate.assert_awaited_once()

    async def test_token_rotation_invalidates_cached_auth(self):
        authenticate = mock.AsyncMock(return_value=7)
        rotate = mock.AsyncMock(return_value="new-token")
        with mock.patch.object(server, "authenticate_project", authenticate), \
                mock.patch.object(server, "rotate_project_token", rotate):
            await self.client.get(
                "/api/projects/7/client-key", headers=self.auth_headers()
            )
            response = await self.client.post(
                "/api/projects/7/token", headers=self.auth_headers()
            )
            await self.client.get(
                "/api/projects/7/client-key", headers=self.auth_headers()
            )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["token"], "new-token")
        self.assertEqual(authenticate.await_count, 2)
        rotate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
