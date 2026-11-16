import os

import socketio
from aiohttp import web

from database import authenticate_project, find_source_project, open_database


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
