from .base import Adapter, ProviderError


class Eitaa(Adapter):
    """Eitaa application send API; no verified inbound webhook contract yet."""
    platform = "eitaa"

    async def send(self, channel, payload):
        chat, text = self.text_payload(payload)
        result = await self.request("https://eitaayar.ir/api/app/sendMessage",
                                    {"token": channel.credentials["token"], "chat_id": chat, "text": text},
                                    form=True)
        if result.get("ok") is not True:
            raise ProviderError(False)
        return result.get("result")
