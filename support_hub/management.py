"""Trusted central backend only; this key grants cross-tenant provisioning."""
import hmac
import os
import secrets
from uuid import uuid4

from aiohttp import web
from pymysql.err import IntegrityError

from .channels import ADAPTERS
from .channels.base import ProviderError
from .channels.instagram import valid_account
from .config import valid_identifier
from .database import digest
from .protocol import decode, ProtocolError
from .security import Bucket


def install_management(app, db, manager, secret):
    if len(secret) < 32 or secret == "replace-with-random-secret":
        raise ValueError("HUB_MANAGEMENT_KEY requires a random secret")
    rate = Bucket(5, 20)

    def authorize(request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) > 512 or not hmac.compare_digest(
                digest(header[7:]), digest(secret)):
            raise web.HTTPUnauthorized()
        if not rate.allow():
            raise web.HTTPTooManyRequests(headers={"Retry-After": "1"})
        tenant = request.match_info["tenant"]
        if not valid_identifier(tenant):
            raise web.HTTPBadRequest()
        return tenant

    async def data(request):
        try:
            return decode(await request.text())
        except ProtocolError:
            raise web.HTTPBadRequest() from None

    async def clients(request):
        tenant = authorize(request)
        if request.method == "GET":
            return web.json_response({"clients": await db.list_clients(tenant)})
        body = await data(request)
        endpoint, peers = body.get("endpoint"), body.get("peers", [])
        if not valid_identifier(endpoint) or not isinstance(peers, list) or len(peers) > 1000:
            raise web.HTTPBadRequest()
        token = secrets.token_hex(32)
        try:
            await db.add_client(tenant, endpoint, token, peers)
        except ValueError:
            raise web.HTTPBadRequest() from None
        except IntegrityError:
            raise web.HTTPConflict() from None
        return web.json_response({"tenant": tenant, "endpoint": endpoint, "token": token}, status=201)

    async def channels(request):
        tenant = authorize(request)
        if request.method == "GET":
            return web.json_response({"channels": await db.list_channels(tenant)})
        body = await data(request)
        platform, owner, token = (body.get(k) for k in ("platform", "owner_endpoint", "token"))
        if (not isinstance(platform, str) or platform not in ADAPTERS or not valid_identifier(owner)
                or not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise web.HTTPBadRequest()
        channel_id, hook = uuid4().hex, secrets.token_urlsafe(32)
        credentials = {"token": token, "webhook_secret": hook}
        if platform == "instagram":
            account = body.get("account_id")
            if not valid_account(account):
                raise web.HTTPBadRequest()
            credentials["account_id"] = account
        try:
            await db.add_channel(channel_id, tenant, owner, platform, credentials, hook)
        except ValueError:
            raise web.HTTPBadRequest() from None
        except IntegrityError:
            raise web.HTTPConflict() from None
        return web.json_response({"id": channel_id, "endpoint": "ch-" + channel_id,
                                  "platform": platform}, status=201)

    async def disable_client(request):
        tenant = authorize(request)
        endpoint = request.match_info["endpoint"]
        if not valid_identifier(endpoint):
            raise web.HTTPBadRequest()
        changed = await db.query(
            "UPDATE clients SET enabled=0 WHERE tenant=%s AND endpoint=%s AND enabled=1",
            (tenant, endpoint), write=True)
        if not changed:
            raise web.HTTPNotFound()
        manager.clients.notify(tenant, endpoint)
        return web.json_response({"enabled": False})

    async def channel_action(request):
        tenant = authorize(request)
        channel_id = request.match_info["channel_id"]
        if len(channel_id) != 32:
            raise web.HTTPNotFound()
        channel = await db.get_channel(channel_id)
        if not channel or channel.tenant != tenant:
            raise web.HTTPNotFound()
        if request.match_info["action"] == "disable":
            await db.query("UPDATE channels SET enabled=0 WHERE tenant=%s AND id=%s",
                           (tenant, channel_id), write=True)
            return web.json_response({"enabled": False})
        if request.match_info["action"] != "register":
            raise web.HTTPNotFound()
        base = os.getenv("HUB_PUBLIC_BASE_URL", "").rstrip("/")
        if not base and channel.platform != "instagram":
            raise web.HTTPServiceUnavailable()
        try:
            await manager.adapters[channel.platform].register(channel, base)
        except NotImplementedError:
            raise web.HTTPNotImplemented() from None
        except ValueError:
            raise web.HTTPBadRequest() from None
        except ProviderError:
            raise web.HTTPBadGateway() from None
        return web.json_response({"registered": True})

    for method in ("GET", "POST"):
        app.router.add_route(method, "/v1/tenants/{tenant}/clients", clients)
        app.router.add_route(method, "/v1/tenants/{tenant}/channels", channels)
    app.router.add_post("/v1/tenants/{tenant}/clients/{endpoint}/disable", disable_client)
    app.router.add_post("/v1/tenants/{tenant}/channels/{channel_id}/{action}", channel_action)
