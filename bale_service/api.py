import asyncio
import json
from urllib.parse import quote, urlparse

import aiohttp


MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024


class BaleError(RuntimeError):
    def __init__(self, message, *, status=None, code=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after


class BaleUnavailable(BaleError):
    """A temporary network/platform failure."""


def split_text(text, limit=MAX_MESSAGE_LENGTH):
    """Split long text into Bale-sized chunks, preferring line boundaries."""
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut >= limit // 2:
            cut += 1
        else:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _file_reference(file):
    if isinstance(file, str):
        value = file.strip()
        if not value:
            raise ValueError("Empty file reference.")
        is_url = value.startswith("http://") or value.startswith("https://")
        kind = "document"
        if is_url:
            suffix = urlparse(value).path.lower().rsplit(".", 1)[-1]
            if suffix in ("jpg", "jpeg", "png", "gif", "webp"):
                kind = "photo"
        return kind, value
    if isinstance(file, dict):
        file_id = file.get("file_id")
        url = file.get("url")
        reference = file_id if isinstance(file_id, str) and file_id else url
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("File object needs file_id or url.")
        kind = "photo" if file.get("type") == "photo" else "document"
        return kind, reference
    raise ValueError("Unsupported file payload.")


class BaleAPI:
    def __init__(self, base_url, timeout=15, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._owns_session = session is None

    @property
    def session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        return self._session

    async def close(self):
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _call(self, token, method, payload=None):
        url = f"{self.base_url}/bot{quote(token, safe=':')}/{method}"
        body = payload or {}
        for attempt in range(2):
            try:
                async with self.session.post(url, json=body) as response:
                    raw = await response.read()
                    try:
                        data = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeError) as exc:
                        raise BaleUnavailable(
                            "Bale returned an invalid response.",
                            status=response.status,
                        ) from exc
                    retry_after = None
                    if isinstance(data, dict):
                        parameters = data.get("parameters")
                        if isinstance(parameters, dict):
                            retry_after = parameters.get("retry_after")
                    if response.status == 429 and attempt == 0:
                        delay = retry_after if isinstance(retry_after, (int, float)) else 1
                        await asyncio.sleep(max(0, min(float(delay), 30)))
                        continue
                    if response.status >= 500 or response.status in (408, 429):
                        raise BaleUnavailable(
                            "Bale API is unavailable.",
                            status=response.status,
                            retry_after=retry_after,
                        )
                    if not isinstance(data, dict) or data.get("ok") is not True:
                        description = "Bale rejected the request."
                        code = None
                        if isinstance(data, dict):
                            description = str(data.get("description") or description)
                            code = data.get("error_code")
                        raise BaleError(
                            description,
                            status=response.status,
                            code=code,
                            retry_after=retry_after,
                        )
                    return data.get("result")
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                raise BaleUnavailable("Could not reach Bale API.") from exc
        raise BaleUnavailable("Bale API rate limit was not released.")

    async def get_me(self, token):
        return await self._call(token, "getMe")

    async def set_webhook(self, token, url):
        return await self._call(token, "setWebhook", {"url": url})

    async def delete_webhook(self, token):
        return await self._call(token, "deleteWebhook", {})

    async def get_webhook_info(self, token):
        return await self._call(token, "getWebhookInfo")

    async def send_text(self, token, chat_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Text message is empty.")
        result = None
        for chunk in split_text(text):
            result = await self._call(
                token, "sendMessage", {"chat_id": str(chat_id), "text": chunk}
            )
        return result

    async def send_file(self, token, chat_id, file, text=""):
        kind, reference = _file_reference(file)
        caption = text if isinstance(text, str) else ""
        # Bale accepts shorter captions than normal messages.
        chunks = split_text(caption, MAX_CAPTION_LENGTH) if caption.strip() else []
        method = "sendPhoto" if kind == "photo" else "sendDocument"
        field = "photo" if kind == "photo" else "document"
        result = None
        if chunks:
            result = await self._call(
                token, method,
                {"chat_id": str(chat_id), field: reference, "caption": chunks[0]},
            )
            for chunk in chunks[1:]:
                result = await self._call(
                    token, "sendMessage", {"chat_id": str(chat_id), "text": chunk}
                )
        else:
            result = await self._call(
                token, method, {"chat_id": str(chat_id), field: reference}
            )
        return result

    async def send(self, token, payload):
        chat_id = payload.get("user_id")
        if not str(chat_id).strip():
            raise ValueError("Message has no user_id.")
        text = payload.get("text") or ""
        file = payload.get("file")
        if file is None or file == "" or file == {}:
            return await self.send_text(token, chat_id, text)
        return await self.send_file(token, chat_id, file, text)
