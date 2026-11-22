"""Signed webhook delivery for inbound messages when a store is offline.

The Hub never stores messages. When a project room has no online operator, the
message is handed to Planet through the project's webhook. Planet is expected
to persist it and answer with a 2xx status; only then does the Hub acknowledge
the inbound message to its sender.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import time
from urllib.parse import urlsplit

import aiohttp

from config import env_flag


logger = logging.getLogger("support_hub.webhook")

MESSAGE_FIELDS = (
    "user_id", "sender", "text", "file", "robot_id", "timestamp", "source"
)
MAX_URL_LENGTH = 2048
MAX_DELIVERY_ID_LENGTH = 255
DEFAULT_TIMEOUT = 5.0
MAX_TIMEOUT = 30.0
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class WebhookError(RuntimeError):
    """Base class for offline webhook failures."""

    code = "webhook_failed"


class StoreOffline(WebhookError):
    """No operator is online and the project has no webhook configured."""

    code = "store_offline"


class WebhookDeliveryError(WebhookError):
    """The project webhook was configured but delivery failed."""

    code = "webhook_failed"


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


def _valid_hostname(host):
    host = host.rstrip(".").lower()
    if not host or len(host) > 253:
        return False
    return all(_HOST_LABEL.fullmatch(label) for label in host.split("."))


def _forbidden_hostname(host):
    host = host.rstrip(".").lower()
    return (
        host == "localhost"
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".internal")
        or host.endswith(".home.arpa")
    )


def validate_webhook_url(value, *, allow_http=None, allow_private=None):
    """Validate and return a project webhook URL.

    Raises ValueError for unsafe or malformed URLs. DNS is resolved and
    checked again at delivery time to reduce SSRF and DNS-rebinding risk.
    """
    if not isinstance(value, str):
        raise ValueError("Invalid webhook URL.")
    value = value.strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("Invalid webhook URL.")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ValueError("Invalid webhook URL.")

    if allow_http is None:
        allow_http = env_flag("HUB_WEBHOOK_ALLOW_HTTP")
    if allow_private is None:
        allow_private = env_flag("HUB_WEBHOOK_ALLOW_PRIVATE")

    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError("Invalid webhook URL.") from None

    scheme = parts.scheme.lower()
    allowed_schemes = ("http", "https") if allow_http else ("https",)
    if scheme not in allowed_schemes:
        raise ValueError("Webhook URL must use HTTPS.")
    if not parts.hostname:
        raise ValueError("Invalid webhook URL.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Webhook URL must not contain credentials.")
    if parts.fragment:
        raise ValueError("Webhook URL must not contain a fragment.")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Invalid webhook URL.") from None
    if port is not None and not 0 < port < 65536:
        raise ValueError("Invalid webhook URL.")

    host = parts.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        if not _valid_hostname(host):
            raise ValueError("Invalid webhook URL.")
        if not allow_private and _forbidden_hostname(host):
            raise ValueError("Webhook URL must not target a private host.")
    elif not allow_private and not _public_ip(literal):
        raise ValueError("Webhook URL must not target a private address.")
    return value


class _ValidatingResolver(aiohttp.abc.AbstractResolver):
    """Reject hostnames that resolve to private or special-purpose addresses."""

    def __init__(self, allow_private=False):
        self._resolver = aiohttp.resolver.ThreadedResolver()
        self._allow_private = allow_private

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not self._allow_private:
            for record in records:
                if not _public_ip(record.get("host", "")):
                    raise WebhookDeliveryError(
                        "Webhook host resolves to a private address."
                    )
        return records

    async def close(self):
        await self._resolver.close()


def webhook_timeout():
    try:
        value = float(os.getenv("HUB_WEBHOOK_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT
    if value <= 0:
        return DEFAULT_TIMEOUT
    return min(value, MAX_TIMEOUT)


def valid_delivery_id(value):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_DELIVERY_ID_LENGTH:
        return False
    return value.isascii() and all(32 <= ord(character) <= 126 for character in value)


def delivery_id_for(message, chat_id, update_id=None):
    """Return a stable idempotency key for an offline message."""
    if update_id is not None:
        return f"{message['source']}:{message['robot_id']}:{update_id}"
    material = json.dumps(
        {
            "source": message["source"],
            "robot_id": message["robot_id"],
            "chat_id": chat_id,
            "user_id": message["user_id"],
            "timestamp": message["timestamp"],
            "text": message["text"],
            "file": message["file"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return "msg:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_offline_payload(project_id, message, chat_id, delivery_id):
    return {
        "event": "chat.offline",
        "project_id": project_id,
        "conversation": {
            "source": message["source"],
            "robot_id": message["robot_id"],
            "chat_id": chat_id,
        },
        "message": {field: message[field] for field in MESSAGE_FIELDS},
        "delivery_id": delivery_id,
    }


def webhook_signature(secret, timestamp, body):
    digest = hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return "sha256=" + digest


async def deliver_webhook(url, secret, project_id, message, chat_id, delivery_id):
    """POST one signed offline message to a project webhook.

    Raises WebhookDeliveryError unless Planet returns a 2xx response. Redirects
    are never followed. Message content, the URL and the secret are not logged.
    """
    if not isinstance(secret, str) or not secret:
        raise WebhookDeliveryError("Project webhook is not configured.")
    if not valid_delivery_id(delivery_id):
        raise WebhookDeliveryError("Invalid webhook delivery id.")

    allow_private = env_flag("HUB_WEBHOOK_ALLOW_PRIVATE")
    try:
        url = validate_webhook_url(url, allow_private=allow_private)
    except ValueError as exc:
        raise WebhookDeliveryError(str(exc)) from None

    body = json.dumps(
        build_offline_payload(project_id, message, chat_id, delivery_id),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Event": "chat.offline",
        "X-Hub-Delivery": delivery_id,
        "X-Hub-Timestamp": str(timestamp),
        "X-Hub-Signature-256": webhook_signature(secret, timestamp, body),
    }

    connector = aiohttp.TCPConnector(
        limit=10,
        resolver=_ValidatingResolver(allow_private=allow_private),
    )
    timeout = aiohttp.ClientTimeout(total=webhook_timeout())
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as session:
            async with session.post(
                url, data=body, headers=headers, allow_redirects=False
            ) as response:
                if not 200 <= response.status < 300:
                    logger.warning(
                        "Offline webhook rejected for project %s with HTTP %s.",
                        project_id, response.status,
                    )
                    raise WebhookDeliveryError("Project webhook rejected the message.")
    except WebhookDeliveryError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        logger.warning(
            "Offline webhook delivery failed for project %s (%s).",
            project_id, type(exc).__name__,
        )
        raise WebhookDeliveryError("Project webhook delivery failed.") from exc
