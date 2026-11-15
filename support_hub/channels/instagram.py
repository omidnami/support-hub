"""Instagram professional accounts, Instagram Login, text DMs only."""
import hashlib
import hmac
import os
import re

from .base import Adapter, ProviderError


def verified_signature(secret, raw, signature):
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def valid_account(account_id):
    return isinstance(account_id, str) and re.fullmatch(r"[0-9]{1,32}", account_id) is not None


class Instagram(Adapter):
    platform = "instagram"

    def endpoint(self, channel, method):
        version = os.getenv("HUB_INSTAGRAM_API_VERSION", "v27.0")
        account = channel.credentials.get("account_id")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", version) or not valid_account(account):
            raise ProviderError(False)
        return f"https://graph.instagram.com/{version}/{account}/{method}"

    def headers(self, channel):
        return {"Authorization": "Bearer " + channel.credentials["token"]}

    async def send(self, channel, payload):
        chat, text = self.text_payload(payload)
        if not valid_account(chat) or len(text) > 1000:
            raise ProviderError(False)
        body = await self.request(self.endpoint(channel, "messages"),
            {"recipient": {"id": chat}, "message": {"text": text}}, headers=self.headers(channel))
        if not body.get("message_id"):
            raise ProviderError(False)
        return body

    async def register(self, channel, base):
        # Configure the shared callback in the Meta app dashboard first.
        body = await self.request(self.endpoint(channel, "subscribed_apps"),
            {"subscribed_fields": "messages"}, headers=self.headers(channel))
        if body.get("success") is not True:
            raise ProviderError(False)

    def events(self, channel, body):
        account = channel.credentials["account_id"]
        entries = body.get("entry", [])
        if body.get("object") != "instagram" or not isinstance(entries, list):
            return []
        events = []
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("id")) != account:
                continue
            messaging = entry.get("messaging", [])
            if not isinstance(messaging, list):
                continue
            for event in messaging:
                if not isinstance(event, dict):
                    continue
                msg = event.get("message")
                if not isinstance(msg, dict) or msg.get("is_echo") or msg.get("is_self"):
                    continue
                sender, recipient = event.get("sender", {}), event.get("recipient", {})
                if not isinstance(sender, dict) or not isinstance(recipient, dict):
                    continue
                external, mid = str(sender.get("id", "")), msg.get("mid")
                if not valid_account(external) or external == account or str(recipient.get("id")) != account:
                    continue
                if not isinstance(mid, str) or not mid:
                    continue
                value = msg.get("text")
                events.append((mid, {"type": "text" if isinstance(value, str) else "unsupported",
                    "platform": self.platform, "external_id": external,
                    "text": value if isinstance(value, str) else "",
                    "provider_message_id": mid, "profile": {"id": external}}))
        return events
