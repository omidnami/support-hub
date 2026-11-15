import asyncio
import time


async def deliver_pending(client, store, retry_seconds, authorized=None):
    """Retry until ACK or TTL. A reconnect starts with an empty in-flight map."""
    last_check = 0.0
    while True:
        client.wake.clear()
        ids = await store.pending(client.tenant, client.endpoint)
        # Verify before delivering data and at least every five seconds while idle.
        if authorized is not None and (ids or time.monotonic() - last_check >= 5):
            if not await authorized():
                await client.socket.close(1008, "authorization_revoked")
                return
            last_check = time.monotonic()
        available = set(ids)
        for delivery_id in list(client.in_flight):
            if delivery_id not in available:
                client.in_flight.pop(delivery_id, None)

        sent = 0
        for delivery_id in ids:
            now = time.monotonic()
            if client.in_flight.get(delivery_id, 0) > now:
                continue
            payload = await store.get(client.tenant, client.endpoint, delivery_id)
            if payload is None:
                continue
            # An ACK racing with a resend can cause another delivery. Consumers dedup.
            await asyncio.wait_for(client.socket.send(payload), timeout=10)
            # ACK may have arrived while send was in progress; this entry is harmless.
            client.in_flight[delivery_id] = time.monotonic() + retry_seconds
            sent += 1
            if sent >= 100:
                break
        if sent >= 100:
            await asyncio.sleep(0)
            continue

        try:
            await asyncio.wait_for(client.wake.wait(), timeout=min(1.0, retry_seconds))
        except TimeoutError:
            pass
