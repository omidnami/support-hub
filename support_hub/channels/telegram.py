from .base import Adapter, ProviderError
from urllib.parse import quote


class Telegram(Adapter):
    platform = "telegram"
    base = "https://api.telegram.org/bot"

    async def call(self, channel, method, payload):
        body = await self.request(self.base + quote(channel.credentials["token"], safe=":") + "/" + method, payload)
        if body.get("ok") is not True:
            code = body.get("error_code")
            delay = body.get("parameters", {}).get("retry_after", 0)
            raise ProviderError(code == 429 or isinstance(code, int) and code >= 500,
                                delay if isinstance(delay, int) else 0)
        return body.get("result")

    async def register(self, channel, base):
        await self.call(channel, "setWebhook", {
            "url": self.webhook_url(channel, base),
            "allowed_updates": ["message"], "max_connections": 5,
            "secret_token": channel.credentials["webhook_secret"],
        })

    def events(self, channel, body):
        message = body.get("message")
        if not isinstance(message, dict):
            return []
        chat = message.get("chat", {})
        sender = message.get("from", {})
        mid = body.get("update_id")
        if chat.get("type") != "private" or mid is None or chat.get("id") is None:
            return []
        text = message.get("text")
        payload = {"type": "text" if isinstance(text, str) else "unsupported",
                   "platform": self.platform, "external_id": str(chat["id"]),
                   "text": text if isinstance(text, str) else "",
                   "profile": {k: sender[k] for k in ("id", "first_name", "last_name", "username") if k in sender},
                   "provider_message_id": str(message.get("message_id", ""))}
        return [(str(mid), payload)]

    async def send(self, channel, payload):
        chat, text = self.text_payload(payload)
        return await self.call(channel, "sendMessage", {"chat_id": chat, "text": text})
