import asyncio
import json
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bale_service.api import BaleError
from bale_service.config import BaleServiceConfig
from bale_service.hub import HubRejected, HubUnavailable
from bale_service.repository import BaleSource
from bale_service.service import BaleService, OutboundDispatcher


SECRET = "s" * 32


def make_config(**overrides):
    values = {
        "hub_url": "http://127.0.0.1:8765",
        "hub_service_token": "x" * 32,
        "bale_api_base": "https://tapi.bale.ai",
        "public_base_url": "https://support.example.com",
        "host": "127.0.0.1",
        "port": 8766,
        "sync_interval": 30,
        "request_timeout": 15,
        "hub_ack_timeout": 5,
        "max_update_bytes": 1024 * 1024,
        "webhook_require_https": True,
        "register_webhooks": True,
        "log_level": "INFO",
    }
    values.update(overrides)
    return BaleServiceConfig(**values)


def make_source(**overrides):
    values = {
        "id": 1,
        "project_id": 2,
        "robot_id": "42",
        "token": "bot-token",
        "webhook_secret": SECRET,
        "last_update_id": None,
    }
    values.update(overrides)
    return BaleSource(**values)


def make_update(update_id=10, text="hello"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False},
            "text": text,
        },
    }


class FakeHub:
    def __init__(self, connected=True, error=None):
        self.connected = connected
        self.error = error
        self.sent = []
        self.metadata = []

    async def send_inbound(self, payload, metadata=None):
        if self.error:
            raise self.error
        self.sent.append(payload)
        self.metadata.append(metadata)

    async def close(self):
        pass


class FakeAPI:
    def __init__(self):
        self.webhooks = []
        self.deleted = []
        self.sent = []
        self.failures = []

    async def set_webhook(self, token, url):
        self.webhooks.append((token, url))

    async def delete_webhook(self, token):
        self.deleted.append(token)

    async def send(self, token, payload):
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        self.sent.append((token, payload))
        return {"message_id": 1}

    async def close(self):
        pass


class _Cursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args):
        return None

    async def fetchone(self):
        return (1,)


class _Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def cursor(self):
        return _Cursor()


class FakePool:
    def acquire(self):
        return _Connection()


class BaleWebhookTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, hub=None, config=None):
        return BaleService(
            config or make_config(),
            pool=FakePool(),
            api=FakeAPI(),
            hub=hub or FakeHub(),
        )

    async def post(self, service, body=None, raw=None, secret=SECRET, max_size=None):
        app = web.Application(
            client_max_size=max_size or service.config.max_update_bytes
        )
        app.router.add_post("/webhooks/bale/{secret}", service.handle_webhook)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            if raw is not None:
                response = await client.post(
                    f"/webhooks/bale/{secret}", data=raw,
                    headers={"Content-Type": "application/json"},
                )
            else:
                response = await client.post(f"/webhooks/bale/{secret}", json=body)
            return response.status, await response.json()
        finally:
            await client.close()

    async def test_inbound_update_is_forwarded_and_cursor_advances(self):
        hub = FakeHub()
        service = self.make_service(hub)
        source = make_source()
        with mock.patch(
            "bale_service.service.find_source_by_secret",
            new=mock.AsyncMock(return_value=source),
        ), mock.patch(
            "bale_service.service.advance_last_update_id",
            new=mock.AsyncMock(return_value=True),
        ) as advance:
            status, body = await self.post(service, make_update())
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(len(hub.sent), 1)
        self.assertEqual(hub.sent[0]["user_id"], "42")
        self.assertEqual(hub.sent[0]["source"], "bale")
        self.assertEqual(
            hub.metadata, [{"update_id": 10, "chat_id": "42"}]
        )
        advance.assert_awaited_once_with(service.pool, source.id, 10)

    async def test_duplicate_update_is_acknowledged_without_forwarding(self):
        hub = FakeHub()
        service = self.make_service(hub)
        source = make_source(last_update_id=10)
        with mock.patch(
            "bale_service.service.find_source_by_secret",
            new=mock.AsyncMock(return_value=source),
        ), mock.patch(
            "bale_service.service.advance_last_update_id",
            new=mock.AsyncMock(return_value=True),
        ) as advance:
            status, body = await self.post(service, make_update(update_id=10))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(hub.sent, [])
        advance.assert_not_awaited()

    async def test_ignored_update_still_advances_the_cursor(self):
        hub = FakeHub()
        service = self.make_service(hub)
        source = make_source()
        update = make_update()
        update["message"]["chat"]["type"] = "group"
        with mock.patch(
            "bale_service.service.find_source_by_secret",
            new=mock.AsyncMock(return_value=source),
        ), mock.patch(
            "bale_service.service.advance_last_update_id",
            new=mock.AsyncMock(return_value=True),
        ) as advance:
            status, _ = await self.post(service, update)
        self.assertEqual(status, 200)
        self.assertEqual(hub.sent, [])
        advance.assert_awaited_once_with(service.pool, source.id, 10)

    async def test_bad_secret_json_and_update_id(self):
        service = self.make_service()
        status, _ = await self.post(service, make_update(), secret="bad")
        self.assertEqual(status, 404)
        status, _ = await self.post(service, raw="{not json")
        self.assertEqual(status, 400)
        status, _ = await self.post(service, raw=json.dumps({"update_id": "x"}))
        self.assertEqual(status, 400)

    async def test_oversized_body_returns_json_413(self):
        service = self.make_service()
        status, body = await self.post(
            service, raw=json.dumps(make_update()) + " " * 200, max_size=64
        )
        self.assertEqual(status, 413)
        self.assertEqual(body, {"ok": False})

    async def test_hub_failure_is_temporary_and_does_not_advance(self):
        service = self.make_service(
            FakeHub(error=HubUnavailable("offline"))
        )
        source = make_source()
        with mock.patch(
            "bale_service.service.find_source_by_secret",
            new=mock.AsyncMock(return_value=source),
        ), mock.patch(
            "bale_service.service.advance_last_update_id",
            new=mock.AsyncMock(return_value=True),
        ) as advance:
            status, _ = await self.post(service, make_update())
        self.assertEqual(status, 503)
        advance.assert_not_awaited()

    async def test_hub_rejection_is_temporary_and_does_not_advance(self):
        service = self.make_service(
            FakeHub(error=HubRejected("Store is offline and no webhook is configured."))
        )
        with mock.patch(
            "bale_service.service.find_source_by_secret",
            new=mock.AsyncMock(return_value=make_source()),
        ), mock.patch(
            "bale_service.service.advance_last_update_id",
            new=mock.AsyncMock(return_value=True),
        ) as advance:
            status, _ = await self.post(service, make_update())
        self.assertEqual(status, 503)
        advance.assert_not_awaited()

    async def test_unknown_source_is_not_found(self):
        service = self.make_service()
        with mock.patch(
            "bale_service.service.find_source_by_secret",
            new=mock.AsyncMock(return_value=None),
        ):
            status, _ = await self.post(service, make_update())
        self.assertEqual(status, 404)

    async def test_healthz_reports_hub_state(self):
        service = self.make_service(FakeHub(connected=True))
        response = await service.healthz(None)
        self.assertEqual(response.status, 200)
        data = json.loads(response.text)
        self.assertTrue(data["status"])
        self.assertTrue(data["hub_connected"])

        service.hub.connected = False
        response = await service.healthz(None)
        self.assertEqual(response.status, 503)


class BaleServiceBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_url_is_built_from_public_base(self):
        api = FakeAPI()
        service = BaleService(make_config(), pool=FakePool(), api=api, hub=FakeHub())
        source = make_source()
        await service._set_webhook(source)
        self.assertEqual(
            api.webhooks,
            [(
                "bot-token",
                f"https://support.example.com/webhooks/bale/{SECRET}",
            )],
        )

    async def test_operator_reply_is_sent_with_source_token(self):
        api = FakeAPI()
        service = BaleService(make_config(), pool=FakePool(), api=api, hub=FakeHub())
        source = make_source()
        service._sources_by_robot[source.robot_id] = source
        payload = {
            "user_id": "42", "sender": "operator", "text": "reply",
            "file": None, "robot_id": source.robot_id,
            "timestamp": 1, "source": "bale",
        }
        await service._send_operator_message(payload)
        self.assertEqual(api.sent, [("bot-token", payload)])

    async def test_rotated_token_is_retried_once(self):
        api = FakeAPI()
        service = BaleService(make_config(), pool=FakePool(), api=api, hub=FakeHub())
        old = make_source(token="old-token")
        fresh = make_source(token="new-token")
        service._sources_by_robot[old.robot_id] = old
        payload = {
            "user_id": "42", "sender": "operator", "text": "reply",
            "file": None, "robot_id": old.robot_id,
            "timestamp": 1, "source": "bale",
        }
        api.failures = [BaleError("Unauthorized", status=401)]
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=fresh),
        ):
            await service._send_operator_message(payload)
        self.assertEqual(api.sent, [("new-token", payload)])
        self.assertIs(service._sources_by_robot[old.robot_id], fresh)

    async def test_token_rejection_is_not_retried_without_a_change(self):
        api = FakeAPI()
        service = BaleService(make_config(), pool=FakePool(), api=api, hub=FakeHub())
        source = make_source()
        service._sources_by_robot[source.robot_id] = source
        payload = {
            "user_id": "42", "sender": "operator", "text": "reply",
            "file": None, "robot_id": source.robot_id,
            "timestamp": 1, "source": "bale",
        }
        api.failures = [BaleError("Unauthorized", status=401)]
        with mock.patch(
            "bale_service.service.find_source_by_robot",
            new=mock.AsyncMock(return_value=source),
        ) as find:
            await service._send_operator_message(payload)
        find.assert_awaited_once()
        self.assertEqual(api.sent, [])

    async def test_disabled_webhook_registration_does_not_call_bale(self):
        api = FakeAPI()
        service = BaleService(
            make_config(register_webhooks=False),
            pool=FakePool(), api=api, hub=FakeHub(),
        )
        await service._set_webhook(make_source())
        self.assertEqual(api.webhooks, [])

    async def test_sync_registers_new_sources(self):
        api = FakeAPI()
        service = BaleService(make_config(), pool=FakePool(), api=api, hub=FakeHub())
        source = make_source()
        with mock.patch.object(
            service, "_refresh_sources",
            new=mock.AsyncMock(return_value=[source]),
        ):
            await service.sync_once()
        self.assertEqual(len(api.webhooks), 1)
        self.assertEqual(api.deleted, [])

    async def test_sync_deletes_removed_and_changed_webhooks(self):
        api = FakeAPI()
        service = BaleService(make_config(), pool=FakePool(), api=api, hub=FakeHub())
        old = make_source(id=1, token="old-token", robot_id="old-robot")
        changed = make_source(id=2, token="new-token", robot_id="new-robot")
        service._sources_by_id = {
            1: old,
            2: make_source(id=2, token="old-token", robot_id="old-robot"),
        }
        with mock.patch.object(
            service, "_refresh_sources",
            new=mock.AsyncMock(return_value=[changed]),
        ):
            await service.sync_once()
        self.assertEqual(api.deleted, ["old-token", "old-token"])
        self.assertEqual(len(api.webhooks), 1)


class OutboundDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_replies_keep_order_and_failures_do_not_stop_the_queue(self):
        delivered = []

        async def send(payload):
            if payload["text"] == "fail":
                raise RuntimeError("boom")
            delivered.append(payload["text"])

        dispatcher = OutboundDispatcher(send)
        for text in ("first", "fail", "second"):
            self.assertTrue(dispatcher.submit({
                "robot_id": "bot", "user_id": "42", "text": text,
            }))
        await asyncio.wait_for(dispatcher.join(), timeout=2)
        await dispatcher.close()
        self.assertEqual(delivered, ["first", "second"])

    async def test_invalid_and_closed_submissions_are_rejected(self):
        dispatcher = OutboundDispatcher(mock.AsyncMock())
        self.assertFalse(dispatcher.submit(None))
        self.assertFalse(dispatcher.submit({"text": "no robot"}))
        await dispatcher.close()
        self.assertFalse(dispatcher.submit({"robot_id": "bot", "text": "late"}))


if __name__ == "__main__":
    unittest.main()
