"""Ephemeral public endpoints, issued only by the authenticated tenant backend."""
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from .config import Endpoint, valid_identifier
from .protocol import encode

TOKEN_TTL = 900
ROUTE_TTL = 18000
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
WEB_ENDPOINT = re.compile(r"web-[a-f0-9]{48}\Z")


def digest(value):
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def is_browser_endpoint(value):
    return isinstance(value, str) and WEB_ENDPOINT.fullmatch(value) is not None


def valid_origin(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        return False
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        return False
    try:
        u = urlsplit(value)
        _ = u.port
    except ValueError:
        return False
    return bool(u.hostname and not u.username and not u.password
                and not u.path and not u.query and not u.fragment
                and value == f"{u.scheme}://{u.netloc}"
                and (u.scheme == "https" or (
                    u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"))))


class BrowserSessions:
    def __init__(self, redis, db):
        self.redis, self.db = redis, db

    @staticmethod
    def key(tenant, endpoint):
        return f"support-hub:{{{tenant}}}:browser:{endpoint}"

    async def issue(self, tenant, owner, session_id, origin):
        if (not valid_identifier(tenant) or not valid_identifier(owner)
                or owner.startswith(("web-", "ch-"))
                or not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id)
                or not valid_origin(origin)):
            raise ValueError("invalid_session")
        # Stable across token refreshes; the tenant backend must generate, retain
        # and authorize a random session_id. It is NOT supplied by an anonymous user.
        endpoint = "web-" + digest(encode([tenant, owner, session_id]))[:48]
        token = secrets.token_urlsafe(32)
        record = {"owner": owner, "origin": origin, "token_hash": digest(token),
                  "expires": int(time.time()) + TOKEN_TTL}
        await self.redis.set(self.key(tenant, endpoint), encode(record), ex=ROUTE_TTL)
        return {"tenant": tenant, "endpoint": endpoint, "token": token,
                "expires_in": TOKEN_TTL, "owner_endpoint": owner}

    async def record(self, tenant, endpoint):
        if not is_browser_endpoint(endpoint):
            return None
        raw = await self.redis.get(self.key(tenant, endpoint))
        return json.loads(raw) if raw else None

    async def authenticate(self, tenant, endpoint, token, origin):
        row = await self.record(tenant, endpoint)
        if (not row or not origin or origin != row["origin"] or row["expires"] <= time.time()
                or not hmac.compare_digest(row["token_hash"], digest(token))
                or not await self.db.client_enabled(tenant, row["owner"])):
            return None
        return Endpoint("", frozenset({row["owner"]}))

    async def owns(self, tenant, owner, endpoint):
        row = await self.record(tenant, endpoint)
        if not row or row["owner"] != owner:
            return False
        # Retain routing metadata while a reply may still be waiting in Redis.
        # This does not extend the browser credential's expiry.
        return bool(await self.redis.expire(self.key(tenant, endpoint), ROUTE_TTL))
