"""Optional Redis cache for Hub metadata.

MySQL remains the source of truth. Only transport metadata is cached: project
webhooks, source-to-project mappings, token-to-project mappings and chat client
keys. Message content, history, operators and presence are never cached.

Every project has an ``epoch``. Project/source/webhook/token changes rotate the
epoch, so keys written under the previous epoch are no longer read and expire
on their own TTL without ``SCAN`` or manual key deletion.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

from config import env_flag

try:  # pragma: no cover - import failure is exercised by the fallback path.
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover
    Redis = None

    class RedisError(Exception):
        pass


logger = logging.getLogger("support_hub.cache")

CACHE_VERSION = "v1"
CACHE_DOWN = object()
DEFAULT_EPOCH_TTL = 30 * 24 * 60 * 60


def _digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def epoch_key(project_id):
    return f"hub:{CACHE_VERSION}:p:{project_id}:epoch"


def webhook_key(project_id, epoch):
    return f"hub:{CACHE_VERSION}:p:{project_id}:{epoch}:webhook"


def source_key(name, robot_id):
    return f"hub:{CACHE_VERSION}:src:{name}:{_digest(robot_id)}"


def token_key(token):
    return f"hub:{CACHE_VERSION}:tok:{_digest(token)}"


def chat_key(robot_id):
    return f"hub:{CACHE_VERSION}:chat:{_digest(robot_id)}"


def negative_key(kind, name, value):
    return f"hub:{CACHE_VERSION}:neg:{kind}:{name}:{_digest(value)}"


def _int_env(name, default):
    try:
        value = int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class CacheStrictError(Exception):
    """Raised when a mutation must not proceed while the cache is down."""


class MetadataCache:
    def __init__(self, url=None, *, client=None, strict=False,
                 ttl_webhook=3 * 60 * 60, ttl_source=3 * 60 * 60,
                 ttl_token=15 * 60, ttl_negative=60,
                 epoch_ttl=DEFAULT_EPOCH_TTL, down_seconds=5):
        self.url = url
        self.strict = strict
        self.ttl_webhook = ttl_webhook
        self.ttl_source = ttl_source
        self.ttl_token = ttl_token
        self.ttl_negative = ttl_negative
        self.epoch_ttl = epoch_ttl
        self.down_seconds = down_seconds
        self._client = client
        self._owns_client = client is None and bool(url)
        self._down_until = 0.0
        self._locks = {}

    @classmethod
    def from_env(cls):
        return cls(
            url=(os.getenv("HUB_REDIS_URL") or "").strip() or None,
            strict=env_flag("HUB_CACHE_STRICT", False),
            ttl_webhook=_int_env("HUB_CACHE_TTL_WEBHOOK", 3 * 60 * 60),
            ttl_source=_int_env("HUB_CACHE_TTL_SOURCE", 3 * 60 * 60),
            ttl_token=_int_env("HUB_CACHE_TTL_TOKEN", 15 * 60),
            ttl_negative=_int_env("HUB_CACHE_TTL_NEGATIVE", 60),
        )

    @property
    def enabled(self):
        return self._client is not None

    async def start(self):
        if self._client is None:
            if not self.url or Redis is None:
                return self
            self._client = Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
            )
        try:
            await self._client.ping()
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            self._mark_down()
            logger.warning(
                "Redis cache unavailable at startup (%s); using MySQL only.",
                type(exc).__name__,
            )
        return self

    async def close(self):
        if not self.enabled or not self._owns_client:
            return
        closer = getattr(self._client, "aclose", None)
        try:
            if closer is not None:
                await closer()
            else:  # redis-py < 5
                await self._client.close()
        except (RedisError, OSError):
            pass

    def _mark_down(self):
        self._down_until = time.monotonic() + self.down_seconds

    def _available(self):
        return time.monotonic() >= self._down_until

    async def _run(self, operation):
        if not self.enabled or not self._available():
            return CACHE_DOWN
        try:
            return await operation(self._client)
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            self._mark_down()
            logger.warning(
                "Redis cache error (%s); falling back to MySQL.",
                type(exc).__name__,
            )
            return CACHE_DOWN

    async def get_json(self, key):
        raw = await self._run(lambda client: client.get(key))
        if raw is CACHE_DOWN or raw is None:
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def set_json(self, key, value, ttl):
        payload = json.dumps(value, separators=(",", ":"))
        result = await self._run(
            lambda client: client.set(key, payload, ex=int(ttl))
        )
        return result is not CACHE_DOWN

    async def delete(self, *keys):
        if not keys:
            return True
        result = await self._run(lambda client: client.delete(*keys))
        return result is not CACHE_DOWN

    async def epoch(self, project_id):
        """Return the current epoch, creating a stable one on first use."""
        key = epoch_key(project_id)
        current = await self._run(lambda client: client.get(key))
        if current is CACHE_DOWN:
            return None
        if current:
            return current
        candidate = secrets.token_hex(8)
        await self._run(
            lambda client: client.set(key, candidate, nx=True, ex=self.epoch_ttl)
        )
        stored = await self._run(lambda client: client.get(key))
        if stored is CACHE_DOWN:
            return None
        return stored or candidate

    async def rotate_epoch(self, project_id):
        """Publish a new epoch; returns None when Redis is unavailable."""
        value = secrets.token_hex(8)
        result = await self._run(
            lambda client: client.set(
                epoch_key(project_id), value, ex=self.epoch_ttl
            )
        )
        return value if result is not CACHE_DOWN else None

    @asynccontextmanager
    async def lock(self, key):
        """Single-flight lock per key inside this single Hub process."""
        if not self.enabled:
            yield
            return
        entry = self._locks.get(key)
        if entry is None:
            entry = [asyncio.Lock(), 0]
            self._locks[key] = entry
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] <= 0:
                self._locks.pop(key, None)
