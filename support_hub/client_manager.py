import asyncio
from dataclasses import dataclass, field


@dataclass
class Client:
    tenant: str
    endpoint: str
    socket: object
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    in_flight: dict[str, float] = field(default_factory=dict)


class ClientManager:
    """Single-process registry; one connection per tenant/endpoint."""

    def __init__(self):
        self._clients = {}

    def register(self, client):
        key = (client.tenant, client.endpoint)
        if key in self._clients:
            return False
        self._clients[key] = client
        return True

    def remove(self, client):
        key = (client.tenant, client.endpoint)
        if self._clients.get(key) is client:
            self._clients.pop(key)

    def notify(self, tenant, endpoint):
        client = self._clients.get((tenant, endpoint))
        if client:
            client.wake.set()
