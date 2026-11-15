import asyncio
import re
from urllib.parse import quote, urlsplit

import aiohttp

from ..protocol import encode


class ProviderError(Exception):
    def __init__(self, retry=True, delay=0):
        self.retry, self.delay = retry, delay
        super().__init__("provider_temporarily_unavailable" if retry else "provider_rejected")


class Adapter:
    platform = ""

    def __init__(self, http):
        self.http = http

    async def request(self, url, data, form=False, headers=None):
        try:
            kwargs = {"data" if form else "json": data}
            async with self.http.post(url, allow_redirects=False, headers=headers, **kwargs) as response:
                if response.status == 429:
                    try:
                        delay = float(response.headers.get("Retry-After", "10"))
                    except ValueError:
                        delay = 10
                    raise ProviderError(True, min(max(delay, 1), 18000))
                if response.status >= 500 or response.status in (408, 425):
                    raise ProviderError()
                if response.status != 200:
                    raise ProviderError(False)
                body = await response.json(content_type=None)
                if not isinstance(body, dict):
                    raise ProviderError()
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            # Never include credential-bearing URLs in logs or error frames.
            raise ProviderError() from None

    @staticmethod
    def text_payload(payload):
        if not isinstance(payload, dict) or payload.get("type") != "text":
            raise ProviderError(False)
        chat, text = payload.get("external_id"), payload.get("text")
        if (not isinstance(chat, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chat)
                or not isinstance(text, str) or not 1 <= len(text) <= 4096):
            raise ProviderError(False)
        return chat, text

    @staticmethod
    def webhook_url(channel, base):
        u = urlsplit(base)
        if u.scheme != "https" or not u.netloc or u.path or u.query or u.fragment or u.username:
            raise ValueError("webhook base must be an HTTPS origin")
        return f"{base}/webhook/{channel.platform}/{channel.id}/{quote(channel.credentials['webhook_secret'], safe='')}"

    async def register(self, channel, base):
        raise NotImplementedError("provider registration unavailable")

    def events(self, channel, body):
        return []
