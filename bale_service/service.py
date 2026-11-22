import asyncio
import json
import logging
import re
from dataclasses import replace
from urllib.parse import quote

from aiohttp import web

from database import open_database

from .api import BaleAPI, BaleError
from .config import BaleServiceConfig
from .hub import HubBridge, HubRejected, HubUnavailable
from .mapping import build_client_payload
from .repository import (
    advance_last_update_id, ensure_webhook_secret, find_source_by_robot,
    find_source_by_secret, list_bale_sources,
)


logger = logging.getLogger("support_hub.bale")
WEBHOOK_PATH = "/webhooks/bale"
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


class SourceNotFound(LookupError):
    pass


class OutboundDispatcher:
    """Per-bot queues keep replies to one Bale conversation ordered."""

    def __init__(self, send_callback, queue_size=1000):
        self._send = send_callback
        self._queue_size = queue_size
        self._queues = {}
        self._workers = {}
        self._closed = False

    def submit(self, payload):
        if self._closed:
            return False
        if not isinstance(payload, dict):
            return False
        robot_id = payload.get("robot_id")
        if not isinstance(robot_id, str) or not robot_id:
            logger.warning("Dropping operator reply without a robot_id.")
            return False
        queue = self._queues.get(robot_id)
        if queue is None:
            queue = self._queues[robot_id] = asyncio.Queue(maxsize=self._queue_size)
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.error("Outbound queue for Bale source %s is full.", robot_id)
            return False
        worker = self._workers.get(robot_id)
        if worker is None or worker.done():
            self._workers[robot_id] = asyncio.create_task(
                self._worker(robot_id, queue),
                name=f"bale-out-{robot_id}",
            )
        return True

    async def _worker(self, robot_id, queue):
        while True:
            payload = await queue.get()
            try:
                await self._send(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not send an operator reply to Bale source %s.",
                                 robot_id)
            finally:
                queue.task_done()

    async def close(self):
        self._closed = True
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    async def join(self):
        """Wait until every queued reply has been attempted."""
        for queue in list(self._queues.values()):
            await queue.join()


class BaleService:
    def __init__(self, config: BaleServiceConfig, *, pool=None, api=None, hub=None):
        self.config = config
        self.pool = pool
        self._owns_pool = pool is None
        self.api = api or BaleAPI(config.bale_api_base, timeout=config.request_timeout)
        self._owns_api = api is None
        self.hub = hub or HubBridge(
            config.hub_url, config.hub_service_token,
            self._enqueue_operator_message, ack_timeout=config.hub_ack_timeout,
        )
        self.dispatcher = OutboundDispatcher(self._send_operator_message)
        self._stop = asyncio.Event()
        self._hub_task = None
        self._sync_task = None
        self._sources_by_secret = {}
        self._sources_by_robot = {}
        self._sources_by_id = {}
        self._registered = {}
        self._locks = {}

    async def start(self):
        if self.pool is None:
            self.pool = await open_database()
        await self._refresh_sources()
        self._hub_task = asyncio.create_task(
            self.hub.run(self._stop), name="bale-hub-bridge"
        )
        self._sync_task = asyncio.create_task(
            self._sync_loop(), name="bale-source-sync"
        )
        logger.info("Bale service started with %d registered source(s).",
                    len(self._sources_by_robot))

    async def stop(self):
        self._stop.set()
        for task in (self._sync_task, self._hub_task):
            if task is not None:
                task.cancel()
        tasks = [task for task in (self._sync_task, self._hub_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sync_task = None
        self._hub_task = None
        await self.dispatcher.close()
        await self.hub.close()
        if self._owns_api:
            await self.api.close()
        if self._owns_pool and self.pool is not None:
            self.pool.close()
            await self.pool.wait_closed()
            self.pool = None

    async def _refresh_sources(self):
        rows = await list_bale_sources(self.pool)
        by_secret = {}
        by_robot = {}
        by_id = {}
        for source in rows:
            secret = source.webhook_secret
            if not secret:
                secret = await ensure_webhook_secret(self.pool, source.id)
                if not secret:
                    logger.error("Could not create a webhook secret for Bale source %s.",
                                 source.id)
                    continue
                source = replace(source, webhook_secret=secret)
            by_secret[secret] = source
            by_robot[source.robot_id] = source
            by_id[source.id] = source
        self._sources_by_secret = by_secret
        self._sources_by_robot = by_robot
        self._sources_by_id = by_id
        return list(by_id.values())

    async def _sync_loop(self):
        while not self._stop.is_set():
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Bale source synchronization failed.")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.config.sync_interval
                )
            except asyncio.TimeoutError:
                pass

    async def sync_once(self):
        old_sources = dict(self._sources_by_id)
        sources = await self._refresh_sources()
        new_by_id = {source.id: source for source in sources}

        for source_id, old in old_sources.items():
            new = new_by_id.get(source_id)
            if new is None or new.token != old.token or new.robot_id != old.robot_id:
                await self._delete_webhook(old)
        self._sources_by_id = new_by_id

        if not self.config.register_webhooks:
            return
        semaphore = asyncio.Semaphore(5)

        async def register(source):
            async with semaphore:
                await self._set_webhook(source)

        await asyncio.gather(
            *(register(source) for source in sources),
            return_exceptions=True,
        )

    async def _set_webhook(self, source):
        if not self.config.register_webhooks:
            return
        if not source.webhook_secret:
            logger.error("Bale source %s has no webhook secret.", source.id)
            return
        url = (
            f"{self.config.public_base_url}{WEBHOOK_PATH}/"
            f"{quote(source.webhook_secret, safe='')}"
        )
        key = (source.token, source.robot_id, url)
        if self._registered.get(source.id) == key:
            return
        try:
            await self.api.set_webhook(source.token, url)
        except BaleError as exc:
            logger.warning(
                "Could not register Bale webhook for source %s (robot %s): %s",
                source.id, source.robot_id, exc,
            )
            return
        self._registered[source.id] = key
        logger.info("Registered Bale webhook for source %s (robot %s).",
                    source.id, source.robot_id)

    async def _delete_webhook(self, source):
        self._registered.pop(source.id, None)
        if not self.config.register_webhooks or not source.token:
            return
        try:
            await self.api.delete_webhook(source.token)
        except BaleError as exc:
            logger.warning(
                "Could not remove Bale webhook for source %s (robot %s): %s",
                source.id, source.robot_id, exc,
            )

    def _source_for_robot(self, robot_id):
        return self._sources_by_robot.get(str(robot_id))

    def _cache_source(self, source):
        self._sources_by_robot[source.robot_id] = source
        self._sources_by_id[source.id] = source
        if source.webhook_secret:
            self._sources_by_secret[source.webhook_secret] = source

    @staticmethod
    def _log_send_failure(source, payload, error):
        logger.warning(
            "Bale rejected a reply for source %s (user %s): %s",
            source.id, payload.get("user_id"), error,
        )

    async def _send_operator_message(self, payload):
        robot_id = str(payload.get("robot_id"))
        source = self._source_for_robot(robot_id)
        if source is None:
            # A new source can be registered between two sync runs.
            source = await find_source_by_robot(self.pool, robot_id)
            if source is None:
                logger.warning("Dropping reply for unknown Bale source %s.", robot_id)
                return
            self._cache_source(source)
        try:
            await self.api.send(source.token, payload)
        except BaleError as exc:
            if exc.status == 401:
                # The bot token may have been rotated since the last sync.
                fresh = await find_source_by_robot(self.pool, robot_id)
                if fresh is None or fresh.token == source.token:
                    self._log_send_failure(source, payload, exc)
                    return
                source = fresh
                self._cache_source(source)
                try:
                    await self.api.send(source.token, payload)
                except BaleError as retry_exc:
                    self._log_send_failure(source, payload, retry_exc)
                    return
            else:
                self._log_send_failure(source, payload, exc)
                return
        logger.info(
            "Delivered operator reply to Bale source %s (user %s).",
            source.id, payload.get("user_id"),
        )

    def _enqueue_operator_message(self, payload):
        if self.dispatcher.submit(payload):
            logger.debug("Queued operator reply for Bale source %s.", payload.get("robot_id"))
        else:
            logger.warning("Could not queue operator reply for Bale source %s.",
                           payload.get("robot_id"))

    async def process_update(self, secret, update_id, update):
        source = await find_source_by_secret(self.pool, secret)
        if source is None:
            raise SourceNotFound()
        lock = self._locks.setdefault(source.id, asyncio.Lock())
        async with lock:
            source = await find_source_by_secret(self.pool, secret)
            if source is None:
                raise SourceNotFound()
            if source.last_update_id is not None \
                    and update_id <= source.last_update_id:
                return "duplicate"

            payload = build_client_payload(update, source.robot_id)
            if payload is None:
                await advance_last_update_id(self.pool, source.id, update_id)
                return "ignored"

            await self.hub.send_inbound(
                payload,
                {"update_id": update_id, "chat_id": payload["user_id"]},
            )
            if not await advance_last_update_id(self.pool, source.id, update_id):
                logger.warning(
                    "Bale update %s for source %s was forwarded but the dedup cursor "
                    "did not advance.", update_id, source.id,
                )
            return "forwarded"

    async def handle_webhook(self, request):
        secret = request.match_info.get("secret", "")
        if not SECRET_PATTERN.fullmatch(secret):
            return web.json_response({"ok": False}, status=404)
        try:
            body = await request.read()
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"ok": False}, status=413)
        except web.HTTPException:
            raise
        if len(body) > self.config.max_update_bytes:
            return web.json_response({"ok": False}, status=413)
        try:
            update = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            return web.json_response({"ok": False}, status=400)
        if not isinstance(update, dict):
            return web.json_response({"ok": False}, status=400)
        update_id = update.get("update_id")
        if type(update_id) is not int or update_id < 0:
            return web.json_response({"ok": False}, status=400)

        try:
            outcome = await self.process_update(secret, update_id, update)
        except SourceNotFound:
            logger.warning("Webhook received for an unknown Bale source.")
            return web.json_response({"ok": False}, status=404)
        except HubUnavailable:
            logger.warning("Bale update %s could not be delivered to Hub.", update_id)
            return web.json_response({"ok": False}, status=503)
        except HubRejected as exc:
            logger.warning("Hub rejected Bale update %s: %s", update_id, exc)
            return web.json_response({"ok": False}, status=503)
        except Exception:
            logger.exception("Could not process Bale update %s.", update_id)
            return web.json_response({"ok": False}, status=500)
        logger.debug("Bale update %s handled: %s.", update_id, outcome)
        return web.json_response({"ok": True})

    async def healthz(self, _request):
        database_ok = True
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()
        except Exception:
            database_ok = False
        healthy = database_ok and self.hub.connected
        return web.json_response(
            {
                "status": healthy,
                "database": database_ok,
                "hub_connected": self.hub.connected,
                "sources": len(self._sources_by_robot),
            },
            status=200 if healthy else 503,
        )


def create_app(service):
    def safe_path(request):
        # The webhook secret is the only authentication factor; keep it out of logs.
        if request.path.startswith(f"{WEBHOOK_PATH}/"):
            return f"{WEBHOOK_PATH}/***"
        return request.path

    @web.middleware
    async def error_middleware(request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled Bale service error on %s %s.",
                             request.method, safe_path(request))
            return web.json_response({"status": False}, status=500)

    app = web.Application(
        middlewares=[error_middleware],
        client_max_size=service.config.max_update_bytes,
    )
    app.router.add_post(f"{WEBHOOK_PATH}/{{secret}}", service.handle_webhook)
    app.router.add_get("/healthz", service.healthz)

    async def lifecycle(_app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    from .config import load_config

    config = load_config()
    level = getattr(logging, config.log_level, None)
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    service = BaleService(config)
    app = create_app(service)
    # The default access log would print the webhook secret on every update.
    web.run_app(app, host=config.host, port=config.port, access_log=None)
