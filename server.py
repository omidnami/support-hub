import os
import json
from urllib.parse import quote

import asyncio
import aiohttp
from pymysql.err import IntegrityError

import socketio
from aiohttp import web

from database import (
    authenticate_project, find_source_project, find_project_source, open_database, register_user, register_source,
    update_user, update_project, get_source_name, update_source_token, delete_source,
)


SOURCE_NAMES = ("rubika", "bale", "telegram", "whatsapp", "instagram", "eitaa", "chat")


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
            data = await response.json(content_type=None)
    if not isinstance(data, dict):
        raise ValueError("Invalid platform response.")
    if name == "instagram":
        robot_id = data.get("user_id") if "error" not in data else None
    elif name == "rubika":
        result = data.get("data")
        if data.get("status") != "OK" or not isinstance(result, dict):
            raise ValueError("Platform rejected the source token.")
        bot = result.get("bot", result)
        robot_id = bot.get("bot_id") if isinstance(bot, dict) else None
    else:
        result = data.get("result")
        robot_id = result.get("id") if data.get("ok") is True and isinstance(result, dict) else None
    if type(robot_id) not in (int, str) or not str(robot_id).strip() or len(str(robot_id)) > 255:
        raise ValueError("Platform did not return a valid source ID.")
    return str(robot_id)


def create_app():
    app = web.Application()
    sio = socketio.AsyncServer(async_mode="aiohttp", transports=["websocket"])
    sio.attach(app)

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

    async def register(request):
        try:
            data = await request.json()
        except (ValueError, TypeError):
            return web.json_response({"status": False, "message": "Invalid JSON."}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"status": False, "message": "Invalid JSON."}, status=400)

        values = {}
        fields = {
            "name": 190, "email": 254, "phone": 32, "password": 255,
            "project_name": 190, "domain": 253, "description": 16383,
        }
        for key, limit in fields.items():
            value = data.get(key)
            optional = key in ("domain", "description")
            if optional and value is None:
                values[key] = None
                continue
            if not isinstance(value, str) or len(value) > limit:
                return web.json_response(
                    {"status": False, "message": f"Invalid field: {key}"}, status=422
                )
            if key != "password":
                value = value.strip()
            if not value.strip() and not optional:
                return web.json_response(
                    {"status": False, "message": f"Required field: {key}"}, status=422
                )
            values[key] = value if value or not optional else None

        token = await register_user(pool, **values)
        return web.json_response({"status": True, "token": token}, status=201)

    app.router.add_post("/api/register", register)

    async def add_source(request):
        def failure(message, status):
            return web.json_response({"status": False, "message": message}, status=status)

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return failure("Project token required.", 401)
        project_id = await authenticate_project(pool, header[7:])
        if project_id is None:
            return failure("Invalid project token.", 401)
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
        token = data.get("token")
        if name not in SOURCE_NAMES:
            return failure("Invalid source name.", 422)
        if not isinstance(token, str) or not token or len(token) > (255 if name == "chat" else 4096):
            return failure("Invalid source token.", 422)
        if any(ord(c) < 33 or ord(c) > 126 for c in token):
            return failure("Invalid source token.", 422)
        if name == "whatsapp":
            return failure("WhatsApp requires a verified phone number ID; registration not implemented.", 501)
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

    app.router.add_post("/api/sources", add_source)

    def failure(message, status):
        return web.json_response({"status": False, "message": message}, status=status)

    async def request_project(request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return await authenticate_project(pool, header[7:])

    async def patch_values(request, fields, nullable=()):
        try:
            data = await request.json()
        except (ValueError, TypeError):
            raise ValueError("Invalid JSON.") from None
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
            values[key] = value if value or key not in nullable else None
        return values

    def path_id(request, key):
        value = request.match_info[key]
        if not value.isascii() or not value.isdecimal() or len(value) > 20:
            return None
        number = int(value)
        return number if 0 < number <= 18446744073709551615 else None

    async def edit_user(request):
        project_id = await request_project(request)
        if project_id is None:
            return failure("Invalid project token.", 401)
        try:
            values = await patch_values(request, {
                "name": 190, "email": 254, "phone": 32, "password": 255,
            })
        except ValueError as exc:
            return failure(str(exc), 422)
        await update_user(pool, project_id, values)
        return web.json_response({"status": True})

    async def edit_project(request):
        project_id = await request_project(request)
        if project_id is None:
            return failure("Invalid project token.", 401)
        target = path_id(request, "project_id")
        if target is None:
            return failure("Invalid project_id.", 422)
        if target != project_id:
            return failure("Project does not match token.", 403)
        try:
            values = await patch_values(request, {
                "name": 190, "domain": 253, "description": 16383,
            }, nullable=("domain", "description"))
        except ValueError as exc:
            return failure(str(exc), 422)
        await update_project(pool, project_id, values)
        return web.json_response({"status": True})

    async def edit_source(request):
        project_id = await request_project(request)
        if project_id is None:
            return failure("Invalid project token.", 401)
        source_id = path_id(request, "source_id")
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
        token = data["token"]
        if (not isinstance(token, str) or not token
                or len(token) > (255 if name == "chat" else 4096)
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            return failure("Invalid source token.", 422)
        if name == "whatsapp":
            return failure("WhatsApp requires a verified phone number ID; registration not implemented.", 501)
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
        project_id = await request_project(request)
        if project_id is None:
            return failure("Invalid project token.", 401)
        source_id = path_id(request, "source_id")
        if source_id is None:
            return failure("Invalid source_id.", 422)
        if not await delete_source(pool, project_id, source_id):
            return failure("Source not found.", 404)
        return web.json_response({"status": True})

    app.router.add_patch("/api/users/me", edit_user)
    app.router.add_patch("/api/projects/{project_id}", edit_project)
    app.router.add_patch("/api/sources/{source_id}", edit_source)
    app.router.add_delete("/api/sources/{source_id}", remove_source)

    def room(project_id):
        return f"project:{project_id}"

    def client_room(project_id, robot_id, user_id):
        return "chat:" + json.dumps(
            [project_id, robot_id, user_id], ensure_ascii=True, separators=(",", ":")
        )

    def normalize_id(value):
        if type(value) not in (str, int):
            return None
        value = str(value)
        return value if value.strip() and len(value) <= 255 else None

    def message_payload(data, sender):
        fields = {"user_id", "sender", "text", "file", "robot_id", "timestamp"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("Expected user_id, sender, text, file, robot_id and timestamp.")
        user_id = normalize_id(data["user_id"])
        robot_id = normalize_id(data["robot_id"])
        if user_id is None or robot_id is None:
            raise ValueError("Invalid user_id or robot_id.")
        if data["sender"] != sender:
            raise ValueError("Sender does not match the connection.")
        if not isinstance(data["text"], str):
            raise ValueError("Text must be a string.")
        if data["file"] is not None and not isinstance(data["file"], (str, dict)):
            raise ValueError("File must be null, a string or a JSON object.")
        if type(data["timestamp"]) is not int or not 0 <= data["timestamp"] <= 9007199254740991:
            raise ValueError("Timestamp must be Unix milliseconds.")
        if not data["text"].strip() and not data["file"]:
            raise ValueError("Message is empty.")
        return {**data, "user_id": user_id, "robot_id": robot_id, "sender": sender}

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
        if payload["robot_id"] != normalize_id(robot_id):
            return False
        project_id = await find_source_project(pool, name, payload["robot_id"])
        if project_id is None:
            return False
        await emit_project(project_id, "chat", payload)
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
            project_id = await find_source_project(pool, "chat", robot_id)
            if project_id is None:
                return False
            await sio.save_session(sid, {
                "role": role, "project_id": project_id,
                "robot_id": robot_id, "user_id": user_id,
            })
            await sio.enter_room(sid, client_room(project_id, robot_id, user_id))
            await sio.emit("authenticated", {
                "role": role, "robot_id": robot_id, "user_id": user_id,
            }, to=sid)
        else:
            return False

    async def chat_failure(sid, message):
        result = {"status": False, "message": message}
        await sio.emit("chat_error", result, to=sid)
        return result

    @sio.on("chat")
    async def chat(sid, *args):
        session = await sio.get_session(sid)
        role = session.get("role")
        project_id = session.get("project_id")
        if role not in ("operator", "client") or project_id is None:
            return await chat_failure(sid, "Not authenticated.")
        if len(args) != 1:
            return await chat_failure(sid, "Expected one message object.")
        try:
            payload = message_payload(args[0], role)
        except ValueError as exc:
            return await chat_failure(sid, str(exc))
        robot_id, user_id = payload["robot_id"], payload["user_id"]
        if role == "client":
            if robot_id != session["robot_id"] or user_id != session["user_id"]:
                return await chat_failure(sid, "Message does not match the connected client.")
            # Recheck the source in case it was removed or its ID changed.
            if await find_source_project(pool, "chat", robot_id) != project_id:
                return await chat_failure(sid, "Chat source is no longer available.")
        else:
            name = await find_project_source(pool, project_id, robot_id)
            if name is None:
                return await chat_failure(sid, "Source not found in project or robot_id is ambiguous.")
            if name != "chat":
                return await chat_failure(sid, "Provider messaging is not implemented yet.")

        # Echo to the project's operators and the target conversation only.
        await emit_project(project_id, "chat", payload)
        await sio.emit("chat", payload, room=client_room(project_id, robot_id, user_id))
        return {"status": True}

    # Socket.IO automatically removes disconnected sockets from their rooms.
    return app, sio, emit_source


if __name__ == "__main__":
    app, _, _ = create_app()
    web.run_app(app, host=os.getenv("HUB_HOST", "127.0.0.1"),
                port=int(os.getenv("HUB_PORT", "8765")))
