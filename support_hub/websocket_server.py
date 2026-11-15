import asyncio
import hmac
import logging

from redis.exceptions import RedisError
from websockets.exceptions import ConnectionClosed

from .browser_sessions import BrowserSessions, is_browser_endpoint
from .client_manager import Client, ClientManager
from .config import valid_identifier
from .message_router import MessageRouter
from .pending_messages import deliver_pending
from .protocol import ProtocolError, decode, encode
from .security import Bucket

log = logging.getLogger(__name__)


class WebSocketServer:
    def __init__(self, settings, store, db=None):
        self.settings = settings
        self.db = db
        self.store = store
        self.sessions = BrowserSessions(store.redis, db) if db else None
        self.clients = ClientManager()
        self.router = MessageRouter(store, self.clients)
        self.connection_rate = Bucket(80, 160)
        self.active = 0

    async def handle(self, ws):
        if self.active >= 2000 or not self.connection_rate.allow():
            await ws.close(1013, "server_busy")
            return
        self.active += 1
        client = None
        worker = None
        try:
            if ws.request.path != "/ws":
                await ws.close(1008, "invalid_path")
                return
            try:
                frame = decode(await asyncio.wait_for(ws.recv(), timeout=5))
            except (ProtocolError, TimeoutError):
                await ws.close(1008, "authentication_required")
                return
            tenant, endpoint, token = (frame.get(k) for k in ("tenant", "endpoint", "token"))
            origin = ws.request.headers.get("Origin")
            browser = bool(self.sessions and is_browser_endpoint(endpoint))
            if (frame.get("type") != "auth" or not valid_identifier(tenant)
                    or not valid_identifier(endpoint) or not isinstance(token, str)
                    or not 1 <= len(token) <= 256):
                await ws.close(1008, "authentication_failed")
                return

            async def authorization():
                if browser:
                    return await self.sessions.authenticate(tenant, endpoint, token, origin)
                if origin is not None or endpoint.startswith("web-"):
                    # Browser clients never use permanent server credentials.
                    return None
                if self.db:
                    return await self.db.authorization(tenant, endpoint, token)
                candidate = self.settings.endpoints.get(tenant, {}).get(endpoint)
                if candidate and hmac.compare_digest(candidate.token.encode(),
                        token.encode("utf-8", errors="surrogatepass")):
                    return candidate
                return None

            entry = await authorization()
            if entry is None:
                await ws.close(1008, "authentication_failed")
                return
            client = Client(tenant, endpoint, ws)
            if not self.clients.register(client):
                await ws.close(1008, "endpoint_already_connected")
                return
            await ws.send(encode({"type": "authenticated", "endpoint": endpoint}))
            log.info("connected tenant=%s endpoint=%s", tenant, endpoint)

            async def authorized():
                return await authorization() is not None

            worker = asyncio.create_task(self._deliver(client, authorized if self.db else None))
            rate = Bucket(30, 60)
            async for raw in ws:
                if not rate.allow():
                    await ws.close(1008, "rate_limited")
                    return
                frame = None
                try:
                    if self.db:
                        entry = await authorization()
                        if entry is None:
                            await ws.close(1008, "authorization_revoked")
                            return
                    frame = decode(raw)
                    peers = entry.peers
                    destination = frame.get("to")
                    if (self.sessions and not browser and frame.get("type") == "message"
                            and is_browser_endpoint(destination)
                            and await self.sessions.owns(tenant, endpoint, destination)):
                        peers = peers | {destination}
                    response = await self.router.handle(client, peers, frame)
                except ProtocolError as exc:
                    response = {"type": "error", "code": str(exc)}
                    if isinstance(frame, dict):
                        mid = frame.get("id")
                        if isinstance(mid, str) and len(mid) <= 64:
                            response["id"] = mid
                await asyncio.wait_for(ws.send(encode(response)), timeout=10)
        except ConnectionClosed:
            pass
        except (RedisError, TimeoutError):
            log.warning("connection interrupted by backend or send timeout")
            await ws.close(1011, "temporarily_unavailable")
        except Exception:
            log.warning("websocket backend failure")
            await ws.close(1011, "temporarily_unavailable")
        finally:
            self.active -= 1
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if client is not None:
                self.clients.remove(client)

    async def _deliver(self, client, authorized=None):
        try:
            await deliver_pending(client, self.store, self.settings.retry, authorized)
        except ConnectionClosed:
            pass
        except (RedisError, TimeoutError):
            log.warning("pending delivery interrupted")
            await client.socket.close(1011, "temporarily_unavailable")
        except Exception:
            log.warning("pending delivery worker failed")
            await client.socket.close(1011, "delivery_failed")
