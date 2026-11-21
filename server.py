import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import deque
from urllib.parse import quote

import aiohttp
import socketio
from aiohttp import web
from pymysql.err import IntegrityError

from config import env_flag, load_env_file
from database import (
    authenticate_project, delete_source, find_source_project,
    find_source_project_with_token, get_project_token, get_source_name,
    open_database, register_source, register_user, rotate_project_token,
    update_project, update_source_token, update_user,
)


load_env_file()

logger = logging.getLogger("support_hub")

SOURCE_NAMES = ("rubika", "bale", "telegram", "whatsapp", "instagram", "eitaa", "chat")
MIN_PASSWORD_LENGTH = 8
MAX_TEXT_LENGTH = 10_000
MAX_FILE_BYTES = 65_536
MAX_CLIENT_TOKEN_TTL = 3600
CLIENT_TOKEN_SKEW = 60
CLIENT_KEY_CONTEXT = b"support-hub/client-key/v1"

REGISTER_FIELDS = {
    "name": 190, "email": 254, "phone": 32, "password": 255,
    "project_name": 190, "domain": 253, "description": 16383,
}
USER_FIELDS = {"name": 190, "email": 254, "phone": 32, "password": 255}
PROJECT_FIELDS = {"name": 190, "domain": 253, "description": 16383}

# (HTTP method, canonical route) -> (requests, window in seconds)
HTTP_RATE_LIMITS = {
    ("POST", "/api/register"): (5, 60),
    ("POST", "/api/sources"): (30, 60),
    ("PATCH", "/api/sources/{source_id}"): (30, 60),
    ("DELETE", "/api/sources/{source_id}"): (30, 60),
    ("PATCH", "/api/users/me"): (30, 60),
    ("PATCH", "/api/projects/{project_id}"): (30, 60),
    ("POST", "/api/projects/{project_id}/token"): (10, 60),
    ("GET", "/api/projects/{project_id}/client-key"): (30, 60),
}
MESSAGE_LIMIT = 30
MESSAGE_WINDOW = 10
BRIDGE_PROVIDERS = ("bale",)
BRIDGE_MESSAGE_LIMIT = 10_000
BRIDGE_MESSAGE_WINDOW = 10


def failure(message, status):
    return web.json_response(
        {"status": False, "message": message},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def derive_client_key(project_token):
    """Derive the per-project key used to sign client identities."""
    return hmac.new(
        project_token.encode("utf-8"), CLIENT_KEY_CONTEXT, hashlib.sha256
    ).hexdigest()


def client_signature(client_key, robot_id, user_id, expires):
    message = f"{robot_id}\n{user_id}\n{expires}".encode("utf-8")
    return hmac.new(bytes.fromhex(client_key), message, hashlib.sha256).hexdigest()


def verify_client_signature(client_key, robot_id, user_id, expires, signature,
                            now=None):
    if type(expires) is not int or expires < 0:
        return False
    if not isinstance(signature, str) or len(signature) != 64:
        return False
    current = int(time.time()) if now is None else now
    if (expires < current - CLIENT_TOKEN_SKEW
            or expires > current + MAX_CLIENT_TOKEN_TTL + CLIENT_TOKEN_SKEW):
        return False
    try:
        expected = client_signature(client_key, robot_id, user_id, expires)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, signature.lower())


def normalize_id(value):
    if type(value) not in (str, int):
        return None
    value = str(value).strip()
    if not value or len(value) > 255 or any(ord(c) < 32 for c in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def client_room(project_id, source, robot_id, user_id):
    return "chat:" + json.dumps(
        [project_id, source, robot_id, user_id], ensure_ascii=True, separators=(",", ":")
    )


def bridge_room(provider):
    return f"bridge:{provider}"


def valid_bridge_token(provider, token, expected=None):
    if provider not in BRIDGE_PROVIDERS:
        return False
    if expected is None:
        expected = os.getenv("HUB_BALE_SERVICE_TOKEN", "")
    if not expected or not expected.isascii():
        return False
    if not isinstance(token, str) or not token.isascii():
        return False
    return hmac.compare_digest(token, expected)


def parse_path_id(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 20:
        return None
    number = int(value)
    return number if 0 < number <= 18446744073709551615 else None


def validate_register(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid JSON.")
    values = {}
    for key, limit in REGISTER_FIELDS.items():
        value = data.get(key)
        optional = key in ("domain", "description")
        if value is None:
            if optional:
                values[key] = None
                continue
            raise ValueError(f"Required field: {key}")
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"Invalid field: {key}")
        if key != "password":
            value = value.strip()
        if not value.strip() and not optional:
            raise ValueError(f"Required field: {key}")
        if key == "password" and len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        values[key] = value if value or not optional else None
    return values


def parse_patch_values(data, fields, nullable=()):
    if not isinstance(data, dict) or not data:
        raise ValueError("Provide at least one field.")
    values = {}
    for key, value in data.items():
        if key not in fields:
            raise ValueError(f"Invalid field: {key}")
        if value is None and key in nullable:
            values[key] = None
            continue
        if not isinstance(value, str) or len(value) > fields[key]:
            raise ValueError(f"Invalid field: {key}")
        if key != "password":
            value = value.strip()
        if not value.strip() and key not in nullable:
            raise ValueError(f"Required field: {key}")
        if key == "password" and len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        values[key] = value if value or key not in nullable else None
    return values


def validate_source_token(name, token):
    if not isinstance(token, str) or not token:
        raise ValueError("Invalid source token.")
    limit = 255 if name == "chat" else 4096
    if len(token) > limit or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Invalid source token.")
    return token


def message_payload(data, sender):
    fields = {"user_id", "sender", "text", "file", "robot_id", "timestamp", "source"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(
            "Expected user_id, sender, text, file, robot_id, timestamp and source."
        )
    user_id = normalize_id(data["user_id"])
    robot_id = normalize_id(data["robot_id"])
    if user_id is None or robot_id is None:
        raise ValueError("Invalid user_id or robot_id.")
    if data["source"] not in SOURCE_NAMES:
        raise ValueError("Invalid source.")
    if data["sender"] != sender:
        raise ValueError("Sender does not match the connection.")
    if not isinstance(data["text"], str):
        raise ValueError("Text must be a string.")
    if len(data["text"]) > MAX_TEXT_LENGTH:
        raise ValueError(f"Text exceeds {MAX_TEXT_LENGTH} characters.")
    try:
        data["text"].encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Text must be valid UTF-8.") from None
    file = data["file"]
    if file is not None and not isinstance(file, (str, dict)):
        raise ValueError("File must be null, a string or a JSON object.")
    if file is not None:
        try:
            # Validate the value first, then measure its JSON wire size.
            json.dumps(file, ensure_ascii=False, allow_nan=False).encode("utf-8")
            file_size = len(json.dumps(
                file, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError("Invalid file object.") from None
    else:
        file_size = 0
    if file_size > MAX_FILE_BYTES:
        raise ValueError(f"File payload exceeds {MAX_FILE_BYTES} bytes.")
    if type(data["timestamp"]) is not int or not 0 <= data["timestamp"] <= 9007199254740991:
        raise ValueError("Timestamp must be Unix milliseconds.")
    if not data["text"].strip() and not file:
        raise ValueError("Message is empty.")
    return {**data, "user_id": user_id, "robot_id": robot_id, "sender": sender}


class SlidingWindowLimiter:
    def __init__(self, cleanup_threshold=10_000, stale_after=3600):
        self._buckets = {}
        self._cleanup_threshold = cleanup_threshold
        self._stale_after = stale_after

    def allow(self, key, limit, window, now=None):
        current = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = deque()
        cutoff = current - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(current)
        if len(self._buckets) > self._cleanup_threshold:
            self._prune(current)
        return True

    def clear(self, key):
        self._buckets.pop(key, None)

    def _prune(self, now):
        cutoff = now - self._stale_after
        self._buckets = {
            key: bucket for key, bucket in self._buckets.items()
            if bucket and bucket[-1] > cutoff
        }


async def resolve_robot_id(name, token):
    if name == "chat":
        return token
    encoded = quote(token, safe=":")
    urls = {
        "bale": f"https://tapi.bale.ai/bot{encoded}/getMe",
        "telegram": f"https://api.telegram.org/bot{encoded}/getMe",
        "rubika": f"https://botapi.rubika.ir/v3/{encoded}/getMe",
        "eitaa": f"https://eitaayar.ir/api/{encoded}/getMe",
        "instagram": "https://graph.instagram.com/me",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
        if name == "instagram":
            response = await http.get(urls[name], params={"fields": "user_id"},
                                      headers={"Authorization": f"Bearer {token}"},
                                      allow_redirects=False)
        else:
            response = await http.post(urls[name], allow_redirects=False)
        async with response:
            if response.status >= 500 or response.status == 429:
                raise RuntimeError("Platform API unavailable.")
            if response.status != 200:
                raise ValueError("Platform rejected the source token.")
            try:
                data = await response.json(content_type=None)
            except ValueError as exc:
                raise RuntimeError("Invalid platform response.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Invalid platform response.")
    if name == "instagram":
        if "error" in data:
            raise ValueError("Platform rejected the source token.")
        robot_id = data.get("user_id")
    elif name == "rubika":
        result = data.get("data")
        if data.get("status") != "OK" or not isinstance(result, dict):
            raise ValueError("Platform rejected the source token.")
        bot = result.get("bot", result)
        robot_id = bot.get("bot_id") if isinstance(bot, dict) else None
    else:
        result = data.get("result")
        if data.get("ok") is not True or not isinstance(result, dict):
            raise ValueError("Platform rejected the source token.")
        robot_id = result.get("id")
    if type(robot_id) not in (int, str) or not str(robot_id).strip() or len(str(robot_id)) > 255:
        raise RuntimeError("Platform did not return a valid source ID.")
    return str(robot_id)


def create_app():
    trust_proxy = env_flag("HUB_TRUST_PROXY")
    http_limiter = SlidingWindowLimiter()
    message_limiter = SlidingWindowLimiter()

    def request_ip(request):
        if trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.remote or "unknown"

    @web.middleware
    async def error_middleware(request, handler):
        try:
            return await handler(request)
        except web.HTTPException as exc:
            if request.path.startswith("/api/"):
                return web.json_response(
                    {"status": False, "message": exc.reason or "HTTP error."},
                    status=exc.status,
                    headers={"Cache-Control": "no-store"},
                )
            raise
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.path)
            return web.json_response(
                {"status": False, "message": "Internal server error."},
                status=500,
                headers={"Cache-Control": "no-store"},
            )

    @web.middleware
    async def rate_limit_middleware(request, handler):
        route = getattr(request.match_info, "route", None)
        canonical = getattr(getattr(route, "resource", None), "canonical", request.path)
        rule = HTTP_RATE_LIMITS.get((request.method, canonical))
        if rule:
            limit, window = rule
            key = (request_ip(request), request.method, canonical)
            if not http_limiter.allow(key, limit, window):
                return web.json_response(
                    {"status": False, "message": "Too many requests."},
                    status=429,
                    headers={
                        "Retry-After": str(window),
                        "Cache-Control": "no-store",
                    },
                )
        return await handler(request)

    app = web.Application(middlewares=[error_middleware, rate_limit_middleware])
    sio = socketio.AsyncServer(async_mode="aiohttp", transports=["websocket"])
    sio.attach(app)

    bridge_sids = {provider: set() for provider in BRIDGE_PROVIDERS}
    bridge_connecting = {provider: False for provider in BRIDGE_PROVIDERS}
    pool = None

    async def database_context(_app):
        nonlocal pool
        pool = await open_database()
        try:
            yield
        finally:
            pool.close()
            await pool.wait_closed()

    app.cleanup_ctx.append(database_context)

    async def require_project(request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None, failure("Project token required.", 401)
        project_id = await authenticate_project(pool, header[7:])
        if project_id is None:
            return None, failure("Invalid project token.", 401)
        return project_id, None

    async def register(request):
        try:
            data = await request.json()
        except (ValueError, TypeError):
            return failure("Invalid JSON.", 400)
        if not isinstance(data, dict):
            return failure("Invalid JSON.", 400)
        try:
            values = validate_register(data)
        except ValueError as exc:
            return failure(str(exc), 422)
        try:
            token, project_id = await register_user(pool, **values)
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                return failure("Registration conflicted. Try again.", 409)
            raise
        return web.json_response(
            {
                "status": True,
                "token": token,
                "project_id": project_id,
                "client_key": derive_client_key(token),
            },
            status=201,
            headers={"Cache-Control": "no-store"},
        )

    async def healthz(_request):
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()
        except Exception:
            logger.exception("Health check failed")
            return web.json_response({"status": False}, status=503)
        return web.json_response({"status": True})

    async def add_source(request):
        project_id, error = await require_project(request)
        if error:
            return error
        try:
            data = await request.json()
        except (ValueError, TypeError):
            return failure("Invalid JSON.", 400)
        if not isinstance(data, dict):
            return failure("Invalid JSON.", 400)
        if type(data.get("project_id")) is not int:
            return failure("Invalid project_id.", 422)
        if data["project_id"] != project_id:
            return failure("Project does not match token.", 403)

        name = data.get("name")
        if name not in SOURCE_NAMES:
            return failure("Invalid source name.", 422)
        try:
            token = validate_source_token(name, data.get("token"))
        except ValueError as exc:
            return failure(str(exc), 422)
        if name == "whatsapp":
            return failure(
                "WhatsApp requires a verified phone number ID; registration not implemented.",
                501,
            )
        try:
            robot_id = await resolve_robot_id(name, token)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
            return failure("Platform API unavailable.", 502)
        except ValueError:
            return failure("Platform did not validate the source.", 422)
        try:
            source_id = await register_source(pool, project_id, name, robot_id, token)
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                return failure("Source already registered.", 409)
            raise
        return web.json_response({
            "status": True, "id": source_id, "name": name,
            "robot_id": robot_id, "project_id": project_id,
        }, status=201)

    async def edit_user(request):
        project_id, error = await require_project(request)
        if error:
            return error
        try:
            data = await request.json()
        except (ValueError, TypeError):
            return failure("Invalid JSON.", 400)
        try:
            values = parse_patch_values(data, USER_FIELDS)
        except ValueError as exc:
            return failure(str(exc), 422)
        await update_user(pool, project_id, values)
        return web.json_response({"status": True})

    async def edit_project(request):
        project_id, error = await require_project(request)
        if error:
            return error
        target = parse_path_id(request.match_info["project_id"])
        if target is None:
            return failure("Invalid project_id.", 422)
        if target != project_id:
            return failure("Project does not match token.", 403)
        try:
            data = await request.json()
        except (ValueError, TypeError):
            return failure("Invalid JSON.", 400)
        try:
            values = parse_patch_values(
                data, PROJECT_FIELDS, nullable=("domain", "description")
            )
        except ValueError as exc:
            return failure(str(exc), 422)
        await update_project(pool, project_id, values)
        return web.json_response({"status": True})

    async def rotate_token(request):
        project_id, error = await require_project(request)
        if error:
            return error
        target = parse_path_id(request.match_info["project_id"])
        if target is None:
            return failure("Invalid project_id.", 422)
        if target != project_id:
            return failure("Project does not match token.", 403)
        token = await rotate_project_token(pool, project_id)
        if token is None:
            return failure("Project not found.", 404)
        return web.json_response(
            {
                "status": True,
                "token": token,
                "client_key": derive_client_key(token),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def client_key(request):
        project_id, error = await require_project(request)
        if error:
            return error
        target = parse_path_id(request.match_info["project_id"])
        if target is None:
            return failure("Invalid project_id.", 422)
        if target != project_id:
            return failure("Project does not match token.", 403)
        token = await get_project_token(pool, project_id)
        if token is None:
            return failure("Project not found.", 404)
        return web.json_response(
            {"status": True, "client_key": derive_client_key(token)},
            headers={"Cache-Control": "no-store"},
        )

    async def edit_source(request):
        project_id, error = await require_project(request)
        if error:
            return error
        source_id = parse_path_id(request.match_info["source_id"])
        if source_id is None:
            return failure("Invalid source_id.", 422)
        name = await get_source_name(pool, project_id, source_id)
        if name is None:
            return failure("Source not found.", 404)
        try:
            # Token must be revalidated without silently trimming it.
            data = await request.json()
        except (ValueError, TypeError):
            return failure("Invalid JSON.", 400)
        if not isinstance(data, dict) or set(data) != {"token"}:
            return failure("Provide the source token.", 422)
        try:
            token = validate_source_token(name, data["token"])
        except ValueError as exc:
            return failure(str(exc), 422)
        if name == "whatsapp":
            return failure(
                "WhatsApp requires a verified phone number ID; registration not implemented.",
                501,
            )
        try:
            robot_id = await resolve_robot_id(name, token)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
            return failure("Platform API unavailable.", 502)
        except ValueError:
            return failure("Platform did not validate the source.", 422)
        try:
            updated = await update_source_token(pool, project_id, source_id, robot_id, token)
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                return failure("Source already registered.", 409)
            raise
        if not updated:
            return failure("Source not found.", 404)
        return web.json_response({
            "status": True, "id": source_id, "name": name,
            "robot_id": robot_id, "project_id": project_id,
        })

    async def remove_source(request):
        project_id, error = await require_project(request)
        if error:
            return error
        source_id = parse_path_id(request.match_info["source_id"])
        if source_id is None:
            return failure("Invalid source_id.", 422)
        if not await delete_source(pool, project_id, source_id):
            return failure("Source not found.", 404)
        return web.json_response({"status": True})

    app.router.add_post("/api/register", register)
    app.router.add_get("/healthz", healthz)
    app.router.add_patch("/api/users/me", edit_user)
    app.router.add_patch("/api/projects/{project_id}", edit_project)
    app.router.add_post("/api/projects/{project_id}/token", rotate_token)
    app.router.add_get("/api/projects/{project_id}/client-key", client_key)
    app.router.add_post("/api/sources", add_source)
    app.router.add_patch("/api/sources/{source_id}", edit_source)
    app.router.add_delete("/api/sources/{source_id}", remove_source)

    def room(project_id):
        return f"project:{project_id}"

    async def emit_project(project_id, event, *args):
        # Only authenticated operators belong to the project room.
        await sio.emit(event, args, room=room(project_id))

    async def emit_source(name, robot_id, event, *args):
        # Entry point for future provider adapters, not a public endpoint.
        if name not in SOURCE_NAMES or event != "chat" or len(args) != 1:
            return False
        try:
            payload = message_payload(args[0], "client")
        except ValueError:
            return False
        if payload["source"] != name or payload["robot_id"] != normalize_id(robot_id):
            return False
        project_id = await find_source_project(pool, name, payload["robot_id"])
        if project_id is None:
            return False
        await emit_project(project_id, "chat", payload)
        await sio.emit(
            "chat", payload,
            room=client_room(project_id, name, payload["robot_id"], payload["user_id"]),
        )
        return True

    @sio.event
    async def connect(sid, environ, auth):
        if not isinstance(auth, dict):
            return False
        role = auth.get("role", "operator" if "token" in auth else None)
        if role == "operator":
            project_id = await authenticate_project(pool, auth.get("token"))
            if project_id is None:
                return False
            await sio.save_session(sid, {"role": role, "project_id": project_id})
            await sio.enter_room(sid, room(project_id))
            await sio.emit("authenticated", {"role": role, "project_id": project_id}, to=sid)
        elif role == "client":
            robot_id = normalize_id(auth.get("robot_id"))
            user_id = normalize_id(auth.get("user_id"))
            if robot_id is None or user_id is None:
                return False
            # Browser clients can use only registered chat sources, never bot tokens.
            source = await find_source_project_with_token(pool, "chat", robot_id)
            if source is None:
                return False
            project_id, project_token = source
            client_key_value = derive_client_key(project_token)
            if not verify_client_signature(
                client_key_value, robot_id, user_id,
                auth.get("expires"), auth.get("signature"),
            ):
                return False
            await sio.save_session(sid, {
                "role": role, "project_id": project_id,
                "source": "chat", "robot_id": robot_id, "user_id": user_id,
            })
            await sio.enter_room(sid, client_room(project_id, "chat", robot_id, user_id))
            await sio.emit("authenticated", {
                "role": role, "robot_id": robot_id, "user_id": user_id,
            }, to=sid)
        elif role == "bridge":
            provider = auth.get("provider")
            if not valid_bridge_token(provider, auth.get("token")):
                return False
            # Duplicate bridges would send every operator reply twice. Reserve
            # the provider slot before the first await so two concurrent
            # connections cannot both pass this check.
            if bridge_sids[provider] or bridge_connecting[provider]:
                logger.warning("Rejected a duplicate bridge connection for %s.", provider)
                return False
            bridge_connecting[provider] = True
            try:
                await sio.save_session(sid, {"role": role, "provider": provider})
                await sio.enter_room(sid, bridge_room(provider))
                bridge_sids[provider].add(sid)
                await sio.emit("authenticated", {
                    "role": role, "provider": provider,
                }, to=sid)
            except BaseException:
                bridge_sids[provider].discard(sid)
                raise
            finally:
                bridge_connecting[provider] = False
        else:
            return False

    @sio.event
    async def disconnect(sid, reason=None):
        message_limiter.clear(("chat", sid))
        for provider, sids in bridge_sids.items():
            if sid in sids:
                sids.discard(sid)
                message_limiter.clear(("bridge", provider))

    async def chat_failure(sid, message):
        result = {"status": False, "message": message}
        await sio.emit("chat_error", result, to=sid)
        return result

    @sio.on("chat")
    async def chat(sid, *args):
        session = await sio.get_session(sid)
        role = session.get("role")
        project_id = session.get("project_id")
        if role == "bridge":
            provider = session.get("provider")
            if provider not in BRIDGE_PROVIDERS:
                return await chat_failure(sid, "Not authenticated.")
        elif role in ("operator", "client"):
            provider = None
            if project_id is None:
                return await chat_failure(sid, "Not authenticated.")
        else:
            return await chat_failure(sid, "Not authenticated.")

        if role == "bridge":
            limit_key = ("bridge", provider)
            limit = BRIDGE_MESSAGE_LIMIT
            window = BRIDGE_MESSAGE_WINDOW
        else:
            limit_key = ("chat", sid)
            limit = MESSAGE_LIMIT
            window = MESSAGE_WINDOW
        if not message_limiter.allow(limit_key, limit, window):
            return await chat_failure(sid, "Too many messages. Please slow down.")
        if len(args) != 1:
            return await chat_failure(sid, "Expected one message object.")

        expected_sender = "client" if role == "bridge" else role
        try:
            payload = message_payload(args[0], expected_sender)
        except ValueError as exc:
            return await chat_failure(sid, str(exc))
        source, robot_id, user_id = payload["source"], payload["robot_id"], payload["user_id"]
        if role == "client" and (
            source != session["source"] or robot_id != session["robot_id"]
            or user_id != session["user_id"]
        ):
            return await chat_failure(sid, "Message does not match the connected client.")

        # The source identifies the project and cannot override the session.
        source_project = await find_source_project(pool, source, robot_id)
        if role == "bridge":
            if source != provider:
                return await chat_failure(sid, "Bridge cannot send this source.")
            if source_project is None:
                return await chat_failure(sid, "Source not found.")
            await emit_project(source_project, "chat", payload)
            return {"status": True}

        if source_project != project_id:
            return await chat_failure(sid, "Source not found in project.")

        if source == "chat":
            # Echo to the project's operators and the target conversation only.
            await emit_project(project_id, "chat", payload)
            await sio.emit(
                "chat", payload,
                room=client_room(project_id, source, robot_id, user_id),
            )
            return {"status": True}

        if source not in BRIDGE_PROVIDERS:
            return await chat_failure(sid, "Provider messaging is not implemented yet.")
        if not bridge_sids.get(source):
            return await chat_failure(sid, "Provider bridge is offline.")

        # Operators see their own reply; the provider bridge sends it to the platform.
        await emit_project(project_id, "chat", payload)
        await sio.emit("chat", payload, room=bridge_room(source))
        return {"status": True}

    # Socket.IO automatically removes disconnected sockets from their rooms.
    return app, sio, emit_source


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("HUB_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app, _, _ = create_app()
    web.run_app(app, host=os.getenv("HUB_HOST", "127.0.0.1"),
                port=int(os.getenv("HUB_PORT", "8765")))
