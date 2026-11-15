import hmac
import json

from aiohttp import web
from redis.exceptions import RedisError

from .database import digest
from .protocol import ProtocolError, decode


def build_app(db, manager):
    async def health(request):
        try:
            await db.query("SELECT 1")
            await manager.store.redis.ping()
        except Exception:
            return web.json_response({"status": "unavailable"}, status=503)
        return web.json_response({"status": "ok"})

    async def webhook(request):
        channel_id = request.match_info["channel_id"]
        platform = request.match_info["platform"]
        secret = request.match_info["secret"]
        if len(channel_id) != 32 or len(secret) > 128:
            raise web.HTTPNotFound()
        channel = await db.get_channel(channel_id)
        if (not channel or channel.platform != platform or platform == "eitaa"
                or not hmac.compare_digest(channel.webhook_hash, digest(secret))):
            raise web.HTTPNotFound()
        if platform == "telegram" and not hmac.compare_digest(
            digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")), channel.webhook_hash):
            raise web.HTTPForbidden()
        try:
            body = decode(await request.text())
            await manager.ingest(channel, body)
        except ProtocolError as exc:
            if str(exc) == "destination_queue_full":
                raise web.HTTPServiceUnavailable()
            raise web.HTTPBadRequest() from None
        except RedisError:
            raise web.HTTPServiceUnavailable() from None
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/webhook/{platform}/{channel_id}/{secret}", webhook)
    return app
