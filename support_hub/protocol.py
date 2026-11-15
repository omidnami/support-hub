import hashlib
import json
from uuid import UUID

from .config import valid_identifier


class ProtocolError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
                      sort_keys=True)


def decode(raw):
    if not isinstance(raw, str):
        raise ProtocolError("text_json_required")
    try:
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        encode(value)
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ProtocolError("invalid_json") from exc
    return value


def message_id(value):
    if not isinstance(value, str):
        raise ProtocolError("invalid_message_id")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ProtocolError("invalid_message_id") from exc


def normalize_message(frame, tenant, source, peers):
    mid = message_id(frame.get("id"))
    destination = frame.get("to")
    if not valid_identifier(destination) or destination not in peers:
        raise ProtocolError("destination_not_allowed")
    if "payload" not in frame:
        raise ProtocolError("payload_required")
    delivery_id = hashlib.sha256(encode([tenant, source, mid]).encode()).hexdigest()
    return {"type": "message", "id": mid, "delivery_id": delivery_id,
            "from": source, "to": destination, "payload": frame["payload"]}


def ack_id(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise ProtocolError("invalid_delivery_id")
    return value
