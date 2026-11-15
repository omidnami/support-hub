"""Bale Bot API: https://docs.bale.ai/ (Update, getUpdates, setWebhook, sendMessage)."""
from .base import ProviderError
from .telegram import Telegram


class Bale(Telegram):
    platform = "bale"
    base = "https://tapi.bale.ai/bot"

    async def register(self, channel, base):
        if await self.call(channel, "setWebhook", {"url": self.webhook_url(channel, base)}) is not True:
            raise ProviderError(False)

    async def unregister(self, channel):
        if await self.call(channel, "deleteWebhook", {}) is not True:
            raise ProviderError(False)

    async def updates(self, channel, offset):
        result = await self.call(channel, "getUpdates", {"offset": offset, "limit": 100, "timeout": 10})
        if not isinstance(result, list) or any(not isinstance(u, dict) or type(u.get("update_id")) is not int
                or not 0 <= u["update_id"] < 2**63 - 1 for u in result):
            raise ProviderError()
        return sorted(result, key=lambda u: u["update_id"])
