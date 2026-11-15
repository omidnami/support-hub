import asyncio
import hmac
import logging

from redis.exceptions import RedisError
from websockets.exceptions import ConnectionClosed

from .client_manager import Client, ClientManager
from .config import valid_identifier
from .message_router import MessageRouter
from .pending_messages import deliver_pending
from .protocol import ProtocolError, decode, encode

log = logging.getLogger(__name__)


class WebSocketServer:
    def __init__(self, settings, store, db=None):
        self.settings = settings
        self.db = db
        self.store = store
        self.clients = ClientManager()
        self.router = MessageRouter(store, self.clients)

    async def handle(self, ws):
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
            entry = None
            if (frame.get("type") == "auth" and valid_identifier(tenant)
                    and valid_identifier(endpoint) and isinstance(token, str) and len(token) <= 256):
                if self.db:
                    entry = await self.db.authenticate(tenant, endpoint, token)
                else:
                    candidate = self.settings.endpoints.get(tenant, {}).get(endpoint)
                    if candidate and hmac.compare_digest(candidate.token.encode(), token.encode("utf-8", errors="surrogatepass")):
                        entry = candidate
            if entry is None:
                await ws.close(1008, "authentication_failed")
                return

            client = Client(tenant, endpoint, ws)
            if not self.clients.register(client):
                await ws.close(1008, "endpoint_already_connected")
                return
            await ws.send(encode({"type": "authenticated", "endpoint": endpoint}))
            log.info("connected tenant=%s endpoint=%s", tenant, endpoint)
            worker = asyncio.create_task(self._deliver(client))

            async for raw in ws:
                try:
                    frame = None
                    if self.db:
                        entry = await self.db.authorization(tenant, endpoint, token)
                        if entry is None:
                            await ws.close(1008, "authorization_revoked")
                            return
                    frame = decode(raw)
                    response = await self.router.handle(client, entry.peers, frame)
                except ProtocolError as exc:
                    response = {"type": "error", "code": str(exc)}
                    if isinstance(locals().get("frame"), dict):
                        mid = frame.get("id")
                        if isinstance(mid, str) and len(mid) <= 64:
                            response["id"] = mid
                await asyncio.wait_for(ws.send(encode(response)), timeout=10)
        except ConnectionClosed:
            pass
        except (RedisError, TimeoutError):
            log.warning("connection interrupted by backend or send timeout")
            await ws.close(1011, "temporarily_unavailable")
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if client is not None:
                self.clients.remove(client)

    async def _deliver(self, client):
        try:
            await deliver_pending(client, self.store, self.settings.retry)
        except ConnectionClosed:
            pass
        except (RedisError, TimeoutError):
            log.warning("pending delivery interrupted")
            await client.socket.close(1011, "temporarily_unavailable")
        except Exception:
            log.exception("pending delivery worker failed")
            await client.socket.close(1011, "delivery_failed")
