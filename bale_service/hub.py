import asyncio
import logging

import socketio


logger = logging.getLogger("support_hub.bale.hub")


class HubUnavailable(RuntimeError):
    """The Hub bridge is offline or did not acknowledge the message."""


class HubRejected(RuntimeError):
    """Hub rejected a bridge message."""


class HubBridge:
    def __init__(self, hub_url, service_token, on_operator_message,
                 ack_timeout=10):
        self.hub_url = hub_url
        self.service_token = service_token
        self.on_operator_message = on_operator_message
        self.ack_timeout = ack_timeout
        self._authenticated = asyncio.Event()
        self._disconnected = asyncio.Event()
        self.client = socketio.AsyncClient(reconnection=False)
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def connect():
            logger.info("Connected to Support Hub; waiting for bridge authentication.")

        @self.client.event
        async def authenticated(data):
            if not isinstance(data, dict) or data.get("role") != "bridge" \
                    or data.get("provider") != "bale":
                logger.error("Hub returned an unexpected bridge authentication payload.")
                return
            self._authenticated.set()
            logger.info("Bale bridge authenticated with Support Hub.")

        @self.client.event
        async def disconnect(reason=None):
            self._authenticated.clear()
            self._disconnected.set()
            logger.warning("Bale bridge disconnected from Support Hub: %s", reason)

        @self.client.event
        async def connect_error(data=None):
            logger.warning("Bale bridge could not connect to Support Hub: %s", data)

        @self.client.on("chat")
        async def chat(data):
            if not isinstance(data, dict):
                return
            if data.get("source") != "bale" or data.get("sender") != "operator":
                return
            try:
                self.on_operator_message(data)
            except Exception:
                logger.exception("Could not enqueue an operator reply.")

    @property
    def connected(self):
        return bool(self.client.connected and self._authenticated.is_set())

    async def run(self, stop_event):
        backoff = 1
        while not stop_event.is_set():
            self._authenticated.clear()
            self._disconnected.clear()
            try:
                await self.client.connect(
                    self.hub_url,
                    auth={"role": "bridge", "provider": "bale",
                          "token": self.service_token},
                    transports=["websocket"],
                    wait_timeout=10,
                )
                await asyncio.wait_for(self._authenticated.wait(), timeout=10)
                backoff = 1
                stop_task = asyncio.create_task(stop_event.wait())
                disconnected_task = asyncio.create_task(self._disconnected.wait())
                done, pending = await asyncio.wait(
                    {stop_task, disconnected_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.warning("Hub bridge authentication timed out.")
            except Exception as exc:
                logger.warning("Hub bridge connection failed: %s", exc)
            finally:
                if self.client.connected:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        logger.debug("Error while closing Hub bridge.", exc_info=True)
            if stop_event.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def send_inbound(self, payload):
        if not self.connected:
            raise HubUnavailable("Hub bridge is disconnected.")
        try:
            ack = await self.client.call("chat", payload, timeout=self.ack_timeout)
        except asyncio.TimeoutError as exc:
            raise HubUnavailable("Hub did not acknowledge the message.") from exc
        except Exception as exc:
            raise HubUnavailable("Hub bridge call failed.") from exc
        if not isinstance(ack, dict) or ack.get("status") is not True:
            message = ack.get("message") if isinstance(ack, dict) else "Hub rejected message."
            raise HubRejected(str(message or "Hub rejected message."))
        return ack

    async def close(self):
        if self.client.connected:
            try:
                await self.client.disconnect()
            except Exception:
                logger.debug("Error while closing Hub bridge.", exc_info=True)
