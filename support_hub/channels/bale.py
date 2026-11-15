from .telegram import Telegram


class Bale(Telegram):
    platform = "bale"
    base = "https://tapi.bale.ai/bot"

    async def register(self, channel, base):
        # Separate random secret in the callback path, not the provider token.
        await self.call(channel, "setWebhook", {"url": self.webhook_url(channel, base)})
