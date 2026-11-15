from urllib.parse import quote

from .base import Adapter, ProviderError


class Rubika(Adapter):
    platform = "rubika"

    async def call(self, channel, method, payload):
        url = "https://botapi.rubika.ir/v3/" + quote(channel.credentials["token"], safe="") + "/" + method
        body = await self.request(url, payload)
        if body.get("status") != "OK":
            raise ProviderError(False)
        return body.get("data", {})

    async def register(self, channel, base):
        result = await self.call(channel, "updateBotEndpoints", {
            "url": self.webhook_url(channel, base), "type": "ReceiveUpdate"})
        if result.get("status") != "Done":
            raise ProviderError(False)

    def events(self, channel, body):
        update = body.get("update", body)
        if not isinstance(update, dict):
            return []
        message = update.get("new_message")
        if not isinstance(message, dict) or update.get("type") not in ("NewMessage", None):
            return []
        chat = update.get("chat_id") or message.get("chat_id")
        mid = message.get("message_id")
        if not chat or mid is None:
            return []
        text = message.get("text")
        return [(f"{chat}:{mid}", {"type": "text" if isinstance(text, str) else "unsupported",
                 "platform": self.platform, "external_id": str(chat),
                 "text": text if isinstance(text, str) else "", "provider_message_id": str(mid),
                 "profile": {"id": message.get("sender_id")}})]

    async def send(self, channel, payload):
        chat, text = self.text_payload(payload)
        return await self.call(channel, "sendMessage", {"chat_id": chat, "text": text})
