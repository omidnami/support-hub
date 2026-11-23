import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import re
from dataclasses import replace
from urllib.parse import quote, unquote

from aiohttp import web

from database import open_database

from .api import BaleAPI, BaleError, BaleUnavailable, FileTooLarge
from .config import BaleServiceConfig
from .files import FileCache
from .hub import HubBridge, HubRejected, HubUnavailable
from .mapping import build_client_payload
from .repository import (
    advance_last_update_id, ensure_webhook_secret, find_source_by_robot,
    find_source_by_secret, list_bale_sources,
)

try:  # cache.py is optional at deploy time; the service also runs without it.
    from cache import CACHE_DOWN
except ImportError:  # pragma: no cover
    CACHE_DOWN = object()


logger = logging.getLogger("support_hub.bale")
WEBHOOK_PATH = "/webhooks/bale"
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
FAILED_TTL = 24 * 60 * 60
FAILED_LIMIT = 2000
BALE_FILE_LIMIT = 20 * 1024 * 1024


def _valid_text(value, limit):
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or len(value) > limit:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _safe_file_name(file_path, key):
    name = unquote(str(file_path).rsplit("/", 1)[-1]) or f"{key}.bin"
    name = "".join(
        char for char in name if char >= " " and char not in '"\\'
    )
    return name[:200] or f"{key}.bin"


def _content_disposition(file_name):
    ascii_name = "".join(
        char if 32 <= ord(char) < 127 and char not in '"\\' else "_"
        for char in str(file_name)
    )[:200] or "file"
    encoded = quote(str(file_name), safe="")
    return f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def _secret_digest(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _failed_key(secret):
    return f"hub:v1:bale:failed:{_secret_digest(secret)}"


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
    def __init__(self, config: BaleServiceConfig, *, pool=None, api=None, hub=None,
                 cache=None, file_cache=None):
        self.config = config
        self.pool = pool
        self._owns_pool = pool is None
        self.cache = cache
        self.file_cache = file_cache or FileCache(
            config.file_cache_dir,
            ttl=config.file_cache_ttl,
            max_bytes=config.file_cache_max_bytes,
        )
        self._file_locks = {}
        self.api = api or BaleAPI(
            config.bale_api_base, timeout=config.request_timeout,
            download_timeout=config.file_download_timeout,
        )
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
        # Update IDs whose delivery failed are temporarily remembered so a
        # later successful update cannot make them look like duplicates.
        self._failed_updates = {}
        self._failed_incomplete = set()

    async def start(self):
        if self.cache is not None:
            await self.cache.start()
        await asyncio.to_thread(self.file_cache.cleanup)
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
        if self.cache is not None:
            await self.cache.close()

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

    async def _failed_for(self, secret):
        """Return (key, failed IDs, whether the set is known to be complete)."""
        key = _failed_key(secret)
        failed = self._failed_updates.get(key)
        if failed is not None and key not in self._failed_incomplete:
            return key, failed, True
        if failed is None:
            failed = set()
        known = True
        if self.cache is not None and self.cache.enabled:
            stored = await self.cache.get_json(key)
            if stored is CACHE_DOWN:
                self._failed_incomplete.add(key)
                known = False
            else:
                self._failed_incomplete.discard(key)
                if isinstance(stored, list):
                    failed.update(
                        item for item in stored
                        if type(item) is int and item >= 0
                    )
        self._failed_updates[key] = failed
        return key, failed, known

    async def _save_failed(self, key, failed):
        if self.cache is None or not self.cache.enabled:
            return
        if failed:
            await self.cache.set_json(key, sorted(failed), FAILED_TTL)
        else:
            await self.cache.delete(key)

    async def _mark_failed(self, secret, update_id):
        key, failed, _ = await self._failed_for(secret)
        failed.add(update_id)
        if len(failed) > FAILED_LIMIT:
            failed.difference_update(
                sorted(failed)[:len(failed) - FAILED_LIMIT]
            )
        await self._save_failed(key, failed)

    async def _clear_failed(self, secret, update_id):
        key = _failed_key(secret)
        failed = self._failed_updates.get(key)
        if failed is None or update_id not in failed:
            return
        failed.discard(update_id)
        await self._save_failed(key, failed)

    async def process_update(self, secret, update_id, update):
        lock = self._locks.setdefault(_secret_digest(secret), asyncio.Lock())
        async with lock:
            try:
                source = await find_source_by_secret(self.pool, secret)
                if source is None:
                    raise SourceNotFound()
                if source.last_update_id is not None \
                        and update_id <= source.last_update_id:
                    _, failed, known = await self._failed_for(secret)
                    if known and update_id not in failed:
                        return "duplicate"

                payload = build_client_payload(update, source.robot_id)
                if payload is None:
                    await self._clear_failed(secret, update_id)
                    if source.last_update_id is None \
                            or update_id > source.last_update_id:
                        await advance_last_update_id(
                            self.pool, source.id, update_id
                        )
                    return "ignored"

                await self.hub.send_inbound(
                    payload,
                    {"update_id": update_id, "chat_id": payload["user_id"]},
                )
                await self._clear_failed(secret, update_id)
                if source.last_update_id is None \
                        or update_id > source.last_update_id:
                    if not await advance_last_update_id(
                            self.pool, source.id, update_id):
                        logger.warning(
                            "Bale update %s for source %s was forwarded but the dedup "
                            "cursor did not advance.", update_id, source.id,
                        )
                return "forwarded"
            except SourceNotFound:
                raise
            except Exception:
                # Remember any update we could not deliver. Otherwise a later
                # success moves the cursor past it and its retry looks like a
                # duplicate. Marking happens inside the per-secret lock.
                await self._mark_failed(secret, update_id)
                raise

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

    def _file_authorized(self, header):
        if not isinstance(header, str) or not header.startswith("Bearer "):
            return False
        token = header[7:]
        expected = self.config.hub_service_token
        if not expected or not token.isascii() or not expected.isascii():
            return False
        return hmac.compare_digest(token, expected)

    @staticmethod
    def _file_response(path, meta):
        name = meta.get("file_name") or path.name
        return web.FileResponse(
            path,
            headers={
                "Content-Type": meta.get("content_type")
                or "application/octet-stream",
                "Content-Disposition": _content_disposition(name),
                "Cache-Control": "private, max-age=300",
            },
        )

    async def handle_file(self, request):
        if not self._file_authorized(request.headers.get("Authorization", "")):
            return web.json_response({"ok": False}, status=401)
        source = request.query.get("source", "")
        robot_id = request.query.get("robot_id", "").strip()
        file_id = request.query.get("file_id", "").strip()
        if source != "bale" or not _valid_text(robot_id, 255) \
                or not _valid_text(file_id, 4096):
            return web.json_response({"ok": False}, status=400)

        source_row = await find_source_by_robot(self.pool, robot_id)
        if source_row is None:
            return web.json_response({"ok": False}, status=404)

        key = FileCache.key(robot_id, file_id)
        cached = await asyncio.to_thread(self.file_cache.get, key)
        if cached is not None:
            return self._file_response(*cached)

        lock = self._file_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                cached = await asyncio.to_thread(self.file_cache.get, key)
                if cached is not None:
                    return self._file_response(*cached)
                max_bytes = min(self.config.file_max_bytes, BALE_FILE_LIMIT)
                try:
                    info = await self.api.get_file(source_row.token, file_id)
                    data, content_type = await self.api.download_file(
                        source_row.token, info["file_path"], max_bytes
                    )
                except FileTooLarge:
                    return web.json_response({"ok": False}, status=413)
                except BaleUnavailable:
                    return web.json_response({"ok": False}, status=502)
                except BaleError as exc:
                    status = 404 if exc.status in (400, 404) else 502
                    return web.json_response({"ok": False}, status=status)

                file_name = _safe_file_name(info.get("file_path"), key)
                if content_type == "application/octet-stream":
                    guessed, _ = mimetypes.guess_type(file_name)
                    if guessed:
                        content_type = guessed
                await asyncio.to_thread(
                    self.file_cache.put, key, data, content_type, file_name
                )
                return self._file_response(
                    self.file_cache.path(key),
                    {"content_type": content_type, "file_name": file_name},
                )
        finally:
            if len(self._file_locks) > 1000:
                self._file_locks.clear()

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
    app.router.add_get("/internal/files", service.handle_file)
    app.router.add_get("/healthz", service.healthz)

    async def lifecycle(_app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app.cleanup_ctx.append(lifecycle)
    return app


def _cache_from_env():
    try:
        from cache import MetadataCache
    except ImportError:  # pragma: no cover - cache.py is optional at deploy time
        return None
    return MetadataCache.from_env()


def main():
    from .config import load_config

    config = load_config()
    level = getattr(logging, config.log_level, None)
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    service = BaleService(config, cache=_cache_from_env())
    app = create_app(service)
    # The default access log would print the webhook secret on every update.
    web.run_app(app, host=config.host, port=config.port, access_log=None)
