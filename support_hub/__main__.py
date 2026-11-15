import asyncio
import logging
import signal

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
    try:
        await redis.ping()
        server = WebSocketServer(settings, RedisManager(redis, settings.ttl))
        async with serve(server.handle, settings.host, settings.port, max_size=64 * 1024,
                         max_queue=16, ping_interval=20, ping_timeout=20,
                         compression=None):
            logging.info("Support Hub listening on %s:%s", settings.host, settings.port)
            await stop.wait()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
