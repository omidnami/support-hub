"""Map Bale updates to the shared seven-field Support Hub envelope."""

import math
import time


FILE_FIELDS = ("file_id", "file_unique_id", "file_name", "mime_type",
               "file_size", "duration", "width", "height")
FILE_STRING_LIMITS = {"file_unique_id": 255, "file_name": 1024, "mime_type": 255}
FILE_TYPES = ("document", "audio", "voice", "video", "animation", "sticker")


def _id(value):
    if isinstance(value, bool) or type(value) not in (str, int):
        return None
    value = str(value).strip()
    if not value or len(value) > 255 or any(ord(c) < 32 for c in value):
        return None
    return value


def _text(value):
    return value if isinstance(value, str) else ""


def _number(value):
    return value if type(value) in (int, float) else 0


def _compact_file(provider, kind, raw):
    if not isinstance(raw, dict):
        return None
    file_id = raw.get("file_id")
    if not isinstance(file_id, str) or not file_id or len(file_id) > 4096:
        return None
    result = {"provider": provider, "type": kind, "file_id": file_id}
    for key in FILE_FIELDS[1:]:
        value = raw.get(key)
        if value is None or type(value) not in (str, int, float) or isinstance(value, bool):
            continue
        if type(value) is float and not math.isfinite(value):
            continue
        limit = FILE_STRING_LIMITS.get(key)
        if isinstance(value, str) and limit is not None:
            value = value[:limit]
        result[key] = value
    return result


def extract_file(message):
    """Return compact metadata for the first supported attachment."""
    photo = message.get("photo")
    if isinstance(photo, list):
        choices = [item for item in photo if isinstance(item, dict)]
        if choices:
            # Bale returns several sizes; keep the largest one only.
            best = max(
                choices,
                key=lambda item: (
                    _number(item.get("file_size")),
                    _number(item.get("width")) * _number(item.get("height")),
                ),
            )
            file = _compact_file("bale", "photo", best)
            if file:
                return file

    for kind in FILE_TYPES:
        file = _compact_file("bale", kind, message.get(kind))
        if file:
            return file
    return None


def _fallback_text(message):
    contact = message.get("contact")
    if isinstance(contact, dict):
        name = " ".join(
            part for part in (
                _text(contact.get("first_name")),
                _text(contact.get("last_name")),
            ) if part
        ).strip()
        phone = _text(contact.get("phone_number")).strip()
        if name or phone:
            return f"[contact] {' '.join(part for part in (name, phone) if part)}"

    location = message.get("location")
    if isinstance(location, dict):
        latitude, longitude = location.get("latitude"), location.get("longitude")
        if type(latitude) in (int, float) and type(longitude) in (int, float):
            return f"[location] {latitude},{longitude}"

    venue = message.get("venue")
    if isinstance(venue, dict):
        title = _text(venue.get("title")).strip()
        address = _text(venue.get("address")).strip()
        if title or address:
            return f"[venue] {' - '.join(part for part in (title, address) if part)}"
    return ""


def build_client_payload(update, robot_id, now_ms=None):
    """Build a Hub payload or return None when the update is not customer chat.

    Phase one intentionally accepts only private Bale chats. Group and channel
    messages need a separate sender/conversation model and are ignored.
    """
    if not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from")
    if isinstance(sender, dict) and sender.get("is_bot") is True:
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None

    user_id = _id(chat.get("id"))
    robot_id = _id(robot_id)
    if user_id is None or robot_id is None:
        return None

    text = _text(message.get("text")) or _text(message.get("caption"))
    file = extract_file(message)
    if not text.strip() and file is None:
        text = _fallback_text(message)
    if not text.strip() and file is None:
        return None

    date = message.get("date")
    if type(date) is int and 0 <= date <= 9_007_199_254_740:
        timestamp = date * 1000
    else:
        timestamp = int(time.time() * 1000) if now_ms is None else now_ms

    return {
        "user_id": user_id,
        "sender": "client",
        "text": text,
        "file": file,
        "robot_id": robot_id,
        "timestamp": timestamp,
        "source": "bale",
    }
