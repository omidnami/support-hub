import os

import socketio
from aiohttp import web

from database import authenticate_project, find_source_project, open_database, register_user


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
