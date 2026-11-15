import asyncio
import logging
import os
import signal

import aiohttp
from aiohttp import web
from redis.asyncio import Redis
from websockets.asyncio.server import serve

from .config import Settings
from .redis_manager import RedisManager
from .websocket_server import WebSocketServer


async def main():
    settings = Settings.load()
    redis = Redis.from_url(settings.redis_url, decode_responses=True,
                           socket_connect_timeout=5, socket_timeout=5)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    db = runner = task = None
    try:
        await redis.ping()
        if os.getenv("HUB_DB_NAME"):
            from .database import Database
            db = await Database.open()
        store = RedisManager(redis, settings.ttl)
        server = WebSocketServer(settings, store, db)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                         connector=aiohttp.TCPConnector(limit=30)) as http:
            try:
                if db:
                    from .channel_manager import ChannelManager
                    from .http_server import build_app
                    manager = ChannelManager(db, store, server.clients, http)
                    runner = web.AppRunner(build_app(db, manager), access_log=None)
                    await runner.setup()
                    await web.TCPSite(runner, settings.host, int(os.getenv("HUB_HTTP_PORT", "8766"))).start()
                    task = asyncio.create_task(manager.run())
                async with serve(server.handle, settings.host, settings.port, max_size=64 * 1024,
                                 max_queue=16, ping_interval=20, ping_timeout=20,
                                 compression=None):
                    logging.info("Support Hub listening on %s:%s", settings.host, settings.port)
                    await stop.wait()
            finally:
                if runner:
                    await runner.cleanup()
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    finally:
        if db:
            await db.close()
        await redis.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
