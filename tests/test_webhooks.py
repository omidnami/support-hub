import asyncio
import hashlib
import hmac
import json
import os
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestServer

from webhooks import (
    MESSAGE_FIELDS,
    WebhookDeliveryError,
    build_offline_payload,
    deliver_webhook,
    delivery_id_for,
    valid_delivery_id,
    validate_webhook_url,
    webhook_signature,
)


SECRET = "s" * 43


def message(source="bale", text="hello"):
    return {
        "user_id": "501",
        "sender": "client",
        "text": text,
        "file": None,
        "robot_id": "987654",
        "timestamp": 1794841200000,
        "source": source,
    }


class WebhookUrlTests(unittest.TestCase):
    def test_valid_https_urls(self):
        self.assertEqual(
            validate_webhook_url("https://planet.example.ir/hooks/hub?x=1"),
            "https://planet.example.ir/hooks/hub?x=1",
        )
        self.assertEqual(
            validate_webhook_url("https://8.8.8.8/hook"),
            "https://8.8.8.8/hook",
        )

    def test_http_is_rejected_by_default(self):
        with mock.patch.dict(os.environ, {"HUB_WEBHOOK_ALLOW_HTTP": "0"}):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                validate_webhook_url("http://planet.example.ir/hook")

    def test_private_targets_are_rejected_by_default(self):
        with mock.patch.dict(
            os.environ,
            {
                "HUB_WEBHOOK_ALLOW_HTTP": "0",
                "HUB_WEBHOOK_ALLOW_PRIVATE": "0",
            },
        ):
            for url in (
                "https://localhost/hook",
                "https://127.0.0.1/hook",
                "https://[::1]/hook",
                "https://10.0.0.1/hook",
                "https://169.254.169.254/latest/meta-data",
                "https://metadata.google.internal/hook",
            ):
                with self.subTest(url=url):
                    with self.assertRaises(ValueError):
                        validate_webhook_url(url)

    def test_credentials_fragments_and_bad_ports_are_rejected(self):
        for url in (
            "https://user:pass@example.ir/hook",
            "https://user@example.ir/hook",
            "https://example.ir/hook#fragment",
            "https://example.ir:99999/hook",
            "not-a-url",
            "https://example.ir/hook with space",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_webhook_url(url)

    def test_local_development_flags_allow_http_and_private(self):
        self.assertEqual(
            validate_webhook_url(
                "http://127.0.0.1:8765/hook",
                allow_http=True,
                allow_private=True,
            ),
            "http://127.0.0.1:8765/hook",
        )


class DeliveryIdTests(unittest.TestCase):
    def test_update_id_is_used_when_available(self):
        self.assertEqual(
            delivery_id_for(message(), "501", update_id=10),
            "bale:987654:10",
        )

    def test_hash_is_stable_and_identity_sensitive(self):
        first = delivery_id_for(message(), "501")
        second = delivery_id_for(message(), "501")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("msg:"))
        self.assertNotEqual(first, delivery_id_for(message(text="other"), "501"))
        self.assertNotEqual(first, delivery_id_for(message(), "502"))

    def test_delivery_id_validation(self):
        self.assertTrue(valid_delivery_id("delivery-1"))
        self.assertFalse(valid_delivery_id(""))
        self.assertFalse(valid_delivery_id("x" * 256))
        self.assertFalse(valid_delivery_id("bad\nid"))
        self.assertFalse(valid_delivery_id(1))


class PayloadAndSignatureTests(unittest.TestCase):
    def test_payload_keeps_exactly_seven_message_fields(self):
        payload = build_offline_payload(7, message(), "501", "delivery-1")
        self.assertEqual(payload["event"], "chat.offline")
        self.assertEqual(payload["project_id"], 7)
        self.assertEqual(
            payload["conversation"],
            {"source": "bale", "robot_id": "987654", "chat_id": "501"},
        )
        self.assertEqual(tuple(payload["message"]), MESSAGE_FIELDS)
        self.assertEqual(payload["delivery_id"], "delivery-1")

    def test_signature_matches_hmac_contract(self):
        body = b'{"hello":"world"}'
        signature = webhook_signature(SECRET, 1700000000, body)
        expected = hmac.new(
            SECRET.encode(), b"1700000000." + body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(signature, "sha256=" + expected)


class WebhookDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def start_server(self, handler):
        app = web.Application()
        app.router.add_post("/hook", handler)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return str(server.make_url("/hook"))

    def env(self, **extra):
        values = {
            "HUB_WEBHOOK_ALLOW_HTTP": "1",
            "HUB_WEBHOOK_ALLOW_PRIVATE": "1",
            "HUB_WEBHOOK_TIMEOUT": "5",
        }
        values.update(extra)
        return mock.patch.dict(os.environ, values)

    async def test_signed_delivery_contains_all_metadata(self):
        received = {}

        async def handler(request):
            received["headers"] = dict(request.headers)
            received["body"] = await request.read()
            return web.json_response({"ok": True})

        url = await self.start_server(handler)
        with self.env():
            await deliver_webhook(
                url, SECRET, 7, message(), "501", "delivery-1"
            )

        body = received["body"]
        headers = received["headers"]
        self.assertEqual(headers["X-Hub-Event"], "chat.offline")
        self.assertEqual(headers["X-Hub-Delivery"], "delivery-1")
        self.assertEqual(
            headers["X-Hub-Signature-256"],
            webhook_signature(SECRET, int(headers["X-Hub-Timestamp"]), body),
        )
        payload = json.loads(body)
        self.assertEqual(payload["project_id"], 7)
        self.assertEqual(payload["conversation"]["chat_id"], "501")
        self.assertEqual(payload["message"], message())

    async def test_non_2xx_response_is_a_failure(self):
        async def handler(_request):
            return web.json_response({"ok": False}, status=500)

        url = await self.start_server(handler)
        with self.env():
            with self.assertRaises(WebhookDeliveryError):
                await deliver_webhook(
                    url, SECRET, 7, message(), "501", "delivery-1"
                )

    async def test_redirects_are_not_followed(self):
        calls = []

        async def redirect(_request):
            return web.Response(status=302, headers={"Location": "/target"})

        async def target(_request):
            calls.append(True)
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_post("/hook", redirect)
        app.router.add_post("/target", target)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        url = str(server.make_url("/hook"))

        with self.env():
            with self.assertRaises(WebhookDeliveryError):
                await deliver_webhook(
                    url, SECRET, 7, message(), "501", "delivery-1"
                )
        self.assertEqual(calls, [])

    async def test_timeout_is_a_failure(self):
        async def handler(_request):
            await asyncio.sleep(0.5)
            return web.json_response({"ok": True})

        url = await self.start_server(handler)
        with self.env(HUB_WEBHOOK_TIMEOUT="0.05"):
            with self.assertRaises(WebhookDeliveryError):
                await deliver_webhook(
                    url, SECRET, 7, message(), "501", "delivery-1"
                )

    async def test_invalid_secret_or_delivery_id_is_rejected(self):
        url = "https://planet.example.ir/hook"
        with self.assertRaises(WebhookDeliveryError):
            await deliver_webhook(url, "", 7, message(), "501", "delivery-1")
        with self.assertRaises(WebhookDeliveryError):
            await deliver_webhook(url, SECRET, 7, message(), "501", "bad\nid")


if __name__ == "__main__":
    unittest.main()
