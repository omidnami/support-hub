"""Bounded shared Bale long-poll worker; no separate bot processes."""
import asyncio
import logging
import time

from .channels.bale import Bale
from .channels.base import ProviderError

log = logging.getLogger(__name__)


class BalePolling:
    def __init__(self, db, manager, http, concurrency=20):
        self.db, self.manager = db, manager
        self.adapter = Bale(http)
        self.concurrency = concurrency
        self.delays = {}

    async def once(self, channel):
        # Only one worker per channel (run waits for its in-flight task).
        offset = channel.poll_offset
        updates = await self.adapter.updates(channel, offset)
        for update in updates:
            uid = update["update_id"]
            if uid < offset:
                continue
            current = await self.db.get_channel(channel.id)
            if not current or current.receive_mode != "polling":
                return
            # Same normalization and Redis delivery/dedup as the webhook.
            # Persist the cursor only after the update is accepted by the outbox.
            await self.manager.ingest(current, update)
            offset = uid + 1
            if not await self.db.advance_poll_offset(channel.id, offset):
                return

    async def guarded(self, channel):
        try:
            await self.once(channel)
            # Avoid a hot loop if the upstream returns immediately.
            self.delays[channel.id] = time.monotonic() + 1
        except ProviderError as exc:
            self.delays[channel.id] = time.monotonic() + max(10 if exc.retry else 60, exc.delay)
            log.warning("bale polling provider failure channel=%s", channel.id)
        except Exception:
            self.delays[channel.id] = time.monotonic() + 10
            log.warning("bale polling ingestion failure channel=%s", channel.id)

    async def run(self):
        running = {}
        last_started = {}
        try:
            while True:
                try:
                    channels = await self.db.polling_channels()
                    active = {c.id for c in channels}
                    for cid, task in list(running.items()):
                        if cid not in active:
                            task.cancel()
                        if task.done():
                            await asyncio.gather(task, return_exceptions=True)
                            running.pop(cid)
                    self.delays = {cid: until for cid, until in self.delays.items()
                                   if cid in active and until > time.monotonic()}
                    last_started = {cid: started for cid, started in last_started.items() if cid in active}
                    # Least recently scheduled first, even when all polls fill the pool.
                    for channel in sorted(channels, key=lambda c: last_started.get(c.id, 0)):
                        if len(running) >= self.concurrency:
                            break
                        if channel.id not in running and channel.id not in self.delays:
                            last_started[channel.id] = time.monotonic()
                            running[channel.id] = asyncio.create_task(self.guarded(channel))
                except Exception:
                    log.warning("bale polling scheduler failed")
                await asyncio.sleep(1)
        finally:
            for task in running.values():
                task.cancel()
            await asyncio.gather(*running.values(), return_exceptions=True)
