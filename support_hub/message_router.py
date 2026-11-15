from .protocol import ProtocolError, ack_id, normalize_message


class MessageRouter:
    def __init__(self, store, clients):
        self.store = store
        self.clients = clients

    async def handle(self, client, peers, frame):
        kind = frame.get("type")
        if kind == "message":
            msg = normalize_message(frame, client.tenant, client.endpoint, peers)
            result = await self.store.enqueue(client.tenant, msg)
            if result == -1:
                raise ProtocolError("message_id_conflict")
            if result == -2:
                raise ProtocolError("destination_queue_full")
            self.clients.notify(client.tenant, msg["to"])
            return {"type": "accepted", "id": msg["id"],
                    "delivery_id": msg["delivery_id"], "duplicate": result == 0}
        if kind == "ack":
            delivery_id = ack_id(frame.get("delivery_id"))
            removed = await self.store.ack(client.tenant, client.endpoint, delivery_id)
            client.in_flight.pop(delivery_id, None)
            return {"type": "ack_ok", "delivery_id": delivery_id, "removed": removed}
        raise ProtocolError("unknown_frame_type")
