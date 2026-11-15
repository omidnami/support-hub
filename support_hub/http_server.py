import hmac
import logging
import os

from aiohttp import web
from redis.exceptions import RedisError

from .browser_sessions import BrowserSessions
from .config import valid_identifier
from .channels.instagram import valid_account, verified_signature
from .database import digest
from .management import install_management
from .protocol import ProtocolError, decode
from .security import Bucket

log = logging.getLogger(__name__)


def build_app(db, manager):
    incoming = Bucket(200, 400)
    meta_secret = os.getenv("HUB_META_APP_SECRET", "")
    verify_token = os.getenv("HUB_META_VERIFY_TOKEN", "")
    management_key = os.getenv("HUB_MANAGEMENT_KEY", "")
    session_rate = Bucket(20, 40)

    @web.middleware
    async def protection(request, handler):
        if not incoming.allow():
            return web.json_response({"error": "rate_limited"}, status=429, headers={"Retry-After": "1"})
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            exc.headers["Cache-Control"] = "no-store"
            raise
        except Exception:
            log.warning("HTTP request failed")
            response = web.json_response({"error": "temporarily_unavailable"}, status=503)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def health(request):
        try:
            await db.query("SELECT 1")
            await manager.store.redis.ping()
        except Exception:
            return web.json_response({"status": "unavailable"}, status=503)
        return web.json_response({"status": "ok"})

    async def webhook(request):
        channel_id, platform, secret = (request.match_info[k] for k in ("channel_id", "platform", "secret"))
        if len(channel_id) != 32 or len(secret) > 128:
            raise web.HTTPNotFound()
        channel = await db.get_channel(channel_id)
        if (not channel or channel.platform != platform or platform in ("eitaa", "instagram")
                or channel.receive_mode != "webhook"
                or not hmac.compare_digest(channel.webhook_hash, digest(secret))):
            raise web.HTTPNotFound()
        if platform == "telegram" and not hmac.compare_digest(
            digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")), channel.webhook_hash):
            raise web.HTTPForbidden()
        try:
            await manager.ingest(channel, decode(await request.text()))
        except ProtocolError as exc:
            if str(exc) == "destination_queue_full":
                raise web.HTTPServiceUnavailable()
            raise web.HTTPBadRequest() from None
        except RedisError:
            raise web.HTTPServiceUnavailable() from None
        return web.json_response({"ok": True})

    async def instagram_verify(request):
        if not meta_secret or not verify_token:
            raise web.HTTPNotFound()
        query = request.query
        if (query.get("hub.mode") != "subscribe"
                or not hmac.compare_digest(digest(query.get("hub.verify_token", "")), digest(verify_token))
                or "hub.challenge" not in query):
            raise web.HTTPForbidden()
        return web.Response(text=query["hub.challenge"])

    async def instagram_webhook(request):
        raw = await request.read()
        if not meta_secret or not verified_signature(meta_secret, raw, request.headers.get("X-Hub-Signature-256", "")):
            raise web.HTTPForbidden()
        try:
            body = decode(raw.decode("utf-8"))
            if body.get("object") != "instagram" or not isinstance(body.get("entry"), list):
                raise web.HTTPBadRequest()
            for entry in body["entry"]:
                if not isinstance(entry, dict) or not valid_account(str(entry.get("id", ""))):
                    continue
                channel = await db.instagram_channel(str(entry["id"]))
                if channel:
                    await manager.ingest(channel, {"object": "instagram", "entry": [entry]})
        except (ProtocolError, RedisError) as exc:
            if isinstance(exc, RedisError) or str(exc) == "destination_queue_full":
                raise web.HTTPServiceUnavailable() from None
            raise web.HTTPBadRequest() from None
        except UnicodeError:
            raise web.HTTPBadRequest() from None
        return web.json_response({"ok": True})

    async def browser_session(request):
        # Planet's backend issues this for the authorized visitor. No browser
        # origin or tenant-wide management key is accepted as a substitute.
        tenant, owner = (request.match_info[k] for k in ("tenant", "owner"))
        header = request.headers.get("Authorization", "")
        if (request.headers.get("Origin") is not None or not valid_identifier(tenant)
                or not valid_identifier(owner) or not header.startswith("Bearer ")
                or len(header) > 512 or not await db.verify_client(tenant, owner, header[7:])):
            raise web.HTTPUnauthorized()
        if not session_rate.allow():
            raise web.HTTPTooManyRequests(headers={"Retry-After": "1"})
        try:
            body = decode(await request.text())
            sessions = BrowserSessions(manager.store.redis, db, prefix=manager.store.keys.prefix)
            result = await sessions.issue(tenant, owner, body.get("session_id"), body.get("origin"))
        except (ValueError, ProtocolError):
            raise web.HTTPBadRequest() from None
        return web.json_response(result, status=201)

    app = web.Application(client_max_size=64 * 1024, middlewares=[protection])
    app.router.add_get("/health", health)
    app.router.add_post("/v1/tenants/{tenant}/clients/{owner}/browser-sessions", browser_session)
    app.router.add_get("/webhook/instagram", instagram_verify)
    app.router.add_post("/webhook/instagram", instagram_webhook)
    app.router.add_post("/webhook/{platform}/{channel_id}/{secret}", webhook)
    if management_key:
        install_management(app, db, manager, management_key)
    return app
