import asyncio
import logging
import time
from uuid import NAMESPACE_URL, uuid5

from .channels import ADAPTERS
from .channels.base import ProviderError
from .protocol import ProtocolError, decode, normalize_message

log = logging.getLogger(__name__)


class ChannelManager:
    def __init__(self, db, store, clients, http):
        self.db, self.store, self.clients = db, store, clients
        self.adapters = {name: cls(http) for name, cls in ADAPTERS.items()}
        self.delays = {}
        self.limit = asyncio.Semaphore(20)

    async def ingest(self, channel, body):
        adapter = self.adapters[channel.platform]
        for provider_id, payload in adapter.events(channel, body):
            mid = str(uuid5(NAMESPACE_URL, f"{channel.id}:{provider_id}"))
            msg = normalize_message({"id": mid, "to": channel.owner_endpoint, "payload": payload},
                                    channel.tenant, channel.endpoint, {channel.owner_endpoint})
            result = await self.store.enqueue(channel.tenant, msg)
            if result == -2:
                raise ProtocolError("destination_queue_full")
            if result == -1:
                # A provider may redeliver an update with changed metadata; original wins.
                log.warning("channel update collision channel=%s", channel.id)
            self.clients.notify(channel.tenant, channel.owner_endpoint)

    async def status(self, channel, msg, state):
        mid = str(uuid5(NAMESPACE_URL, f"{msg['delivery_id']}:{state}"))
        event = normalize_message({"id": mid, "to": channel.owner_endpoint,
            "payload": {"type": "delivery_status", "delivery_id": msg["delivery_id"], "status": state}},
            channel.tenant, channel.endpoint, {channel.owner_endpoint})
        result = await self.store.enqueue(channel.tenant, event)
        if result == -2:
            # Best effort status only; never repeat a successful provider send just for a status.
            log.warning("delivery status queue full channel=%s", channel.id)
        self.clients.notify(channel.tenant, channel.owner_endpoint)

    async def flush(self, channel):
        if self.delays.get(channel.id, 0) > time.monotonic():
            return
        async with self.limit:
            # Re-check active configuration after waiting for a worker slot.
            channel = await self.db.get_channel(channel.id)
            if not channel:
                return
            adapter = self.adapters.get(channel.platform)
            if not adapter:
                return
            ids = await self.store.pending(channel.tenant, channel.endpoint)
            for did in ids[:10]:
                raw = await self.store.get(channel.tenant, channel.endpoint, did)
                if not raw:
                    continue
                msg = decode(raw)
                try:
                    await adapter.send(channel, msg["payload"])
                    state = "sent"
                except ProviderError as exc:
                    if exc.retry:
                        self.delays[channel.id] = time.monotonic() + max(10, exc.delay)
                        return
                    state = "rejected"
                # An upstream success followed by a Redis failure can duplicate a send.
                await self.store.ack(channel.tenant, channel.endpoint, did)
                try:
                    await self.status(channel, msg, state)
                except Exception:
                    log.warning("delivery status unavailable channel=%s", channel.id)

    async def run(self):
        while True:
            try:
                channels = await self.db.active_channels()
                active_ids = {c.id for c in channels}
                self.delays = {k: v for k, v in self.delays.items() if k in active_ids and v > time.monotonic()}
                for start in range(0, len(channels), 50):
                    results = await asyncio.gather(*(self.flush(c) for c in channels[start:start+50]),
                                                   return_exceptions=True)
                    if any(isinstance(result, Exception) for result in results):
                        log.warning("one or more channel deliveries failed")
            except Exception:
                log.warning("channel sweep failed")
            await asyncio.sleep(2)
