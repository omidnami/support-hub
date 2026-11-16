import os
from urllib.parse import quote

import asyncio
import aiohttp
from pymysql.err import IntegrityError

import socketio
from aiohttp import web

from database import authenticate_project, find_source_project, open_database, register_user, register_source


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

    def room(project_id):
        return f"project:{project_id}"

    async def emit_project(project_id, event, *args):
        await sio.emit(event, args, room=room(project_id))

    async def emit_source(name, robot_id, event, *args):
        project_id = await find_source_project(pool, name, robot_id)
        if project_id is None:
            return False
        await emit_project(project_id, event, *args)
        return True

    @sio.event
    async def connect(sid, environ, auth):
        token = auth.get("token") if isinstance(auth, dict) else None
        project_id = await authenticate_project(pool, token)
        if project_id is None:
            return False
        await sio.save_session(sid, {"project_id": project_id})
        await sio.enter_room(sid, room(project_id))
        await sio.emit("authenticated", {"project_id": project_id}, to=sid)

    @sio.on("*")
    async def relay(event, sid, *args):
        if event == "authenticated":
            return
        session = await sio.get_session(sid)
        project_id = session.get("project_id")
        if project_id is not None:
            await emit_project(project_id, event, *args)

    # Socket.IO automatically removes disconnected sockets from their rooms.
    return app, sio, emit_source


if __name__ == "__main__":
    app, _, _ = create_app()
    web.run_app(app, host=os.getenv("HUB_HOST", "127.0.0.1"),
                port=int(os.getenv("HUB_PORT", "8765")))
