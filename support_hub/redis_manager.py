"""Redis is the temporary outbox; ACK removes payload, not the dedup receipt."""
import hashlib
import time

from .protocol import encode
from .redis_keys import DEFAULT_PREFIX, RedisKeys

# Atomic check + store. All three keys share a tenant hash tag.
ENQUEUE = """
local old = redis.call('GET', KEYS[3])
if old then
    if old == ARGV[1] then return 0 else return -1 end
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[5])
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[6]) then return -2 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[3])
redis.call('ZADD', KEYS[2], ARGV[4], ARGV[7])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return 1
"""

ACK = """
local removed = redis.call('ZREM', KEYS[2], ARGV[1])
if removed == 1 then redis.call('DEL', KEYS[1]) end
return removed
"""


class RedisManager:
    def __init__(self, redis, ttl=18000, max_pending=1000, prefix=DEFAULT_PREFIX):
        self.redis = redis
        self.ttl = ttl
        self.max_pending = max_pending
        self.keys = RedisKeys(prefix)

    async def enqueue(self, tenant, message):
        destination = message["to"]
        delivery_id = message["delivery_id"]
        payload = encode(message)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        now = int(time.time() * 1000)
        expiry = now + self.ttl * 1000
        return await self.redis.eval(
            ENQUEUE, 3, self.keys.item(tenant, destination, delivery_id),
            self.keys.index(tenant, destination), self.keys.receipt(tenant, delivery_id),
            fingerprint, payload, self.ttl, expiry, now, self.max_pending, delivery_id,
        )

    async def pending(self, tenant, endpoint):
        index = self.keys.index(tenant, endpoint)
        await self.redis.zremrangebyscore(index, "-inf", int(time.time() * 1000))
        return await self.redis.zrange(index, 0, self.max_pending - 1)

    async def get(self, tenant, endpoint, delivery_id):
        return await self.redis.get(self.keys.item(tenant, endpoint, delivery_id))

    async def ack(self, tenant, endpoint, delivery_id):
        return bool(await self.redis.eval(
            ACK, 2, self.keys.item(tenant, endpoint, delivery_id),
            self.keys.index(tenant, endpoint), delivery_id))
