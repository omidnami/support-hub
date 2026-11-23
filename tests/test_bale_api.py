import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from bale_service.api import BaleAPI, BaleError, BaleUnavailable, FileTooLarge


class BaleApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.responses = []

        async def handler(request):
            payload = await request.json()
            self.calls.append((request.match_info["token"],
                               request.match_info["method"], payload))
            if self.responses:
                status, data = self.responses.pop(0)
            else:
                status, data = 200, {"ok": True, "result": {"message_id": 1}}
            return web.json_response(data, status=status)

        app = web.Application()
        app.router.add_post("/bot{token}/{method}", handler)

        async def download(request):
            self.downloads.append((
                request.match_info["token"], request.match_info["path"],
            ))
            return web.Response(body=b"abc", content_type="image/jpeg")

        self.downloads = []
        app.router.add_get("/file/bot{token}/{path:.*}", download)
        self.server = TestServer(app)
        await self.server.start_server()
        self.api = BaleAPI(str(self.server.make_url("")).rstrip("/"))

    async def asyncTearDown(self):
        await self.api.close()
        await self.server.close()

    async def test_set_webhook_uses_post_and_json(self):
        await self.api.set_webhook("123:abc", "https://example.com/hook")
        self.assertEqual(
            self.calls,
            [("123:abc", "setWebhook", {"url": "https://example.com/hook"})],
        )

    async def test_long_text_is_split(self):
        text = "x" * 5000
        await self.api.send_text("123:abc", 42, text)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1], "sendMessage")
        self.assertEqual(self.calls[0][2]["chat_id"], "42")
        self.assertEqual(self.calls[0][2]["text"], "x" * 4096)
        self.assertEqual(self.calls[1][2]["text"], "x" * 904)

    async def test_photo_uses_send_photo(self):
        await self.api.send_file(
            "123:abc", 42, {"type": "photo", "file_id": "p1"}, "caption"
        )
        self.assertEqual(self.calls[0][1], "sendPhoto")
        self.assertEqual(
            self.calls[0][2],
            {"chat_id": "42", "photo": "p1", "caption": "caption"},
        )

    async def test_voice_uses_send_voice(self):
        await self.api.send_file(
            "123:abc", 42, {"type": "voice", "file_id": "v1"}
        )
        self.assertEqual(self.calls[0][1], "sendVoice")
        self.assertEqual(
            self.calls[0][2], {"chat_id": "42", "voice": "v1"}
        )

    async def test_audio_and_video_use_their_methods(self):
        await self.api.send_file(
            "123:abc", 42, {"type": "audio", "file_id": "a1"}
        )
        await self.api.send_file(
            "123:abc", 42, {"type": "video", "file_id": "vid1"}
        )
        self.assertEqual(
            [call[1] for call in self.calls], ["sendAudio", "sendVideo"]
        )
        self.assertEqual(self.calls[0][2]["audio"], "a1")
        self.assertEqual(self.calls[1][2]["video"], "vid1")

    async def test_unknown_or_sticker_type_falls_back_to_document(self):
        await self.api.send_file(
            "123:abc", 42, {"type": "sticker", "file_id": "s1"}
        )
        await self.api.send_file(
            "123:abc", 42, {"type": "unknown", "file_id": "d1"}
        )
        self.assertEqual(
            [call[1] for call in self.calls],
            ["sendDocument", "sendDocument"],
        )

    async def test_long_caption_is_split_at_caption_limit(self):
        await self.api.send_file(
            "123:abc", 42, {"type": "document", "file_id": "d1"}, "y" * 1500
        )
        self.assertEqual(self.calls[0][1], "sendDocument")
        self.assertEqual(len(self.calls[0][2]["caption"]), 1024)
        self.assertEqual(self.calls[1][1], "sendMessage")
        self.assertEqual(self.calls[1][2]["text"], "y" * 476)

    async def test_empty_file_object_falls_back_to_text(self):
        await self.api.send(
            "123:abc",
            {"user_id": 42, "text": "hello", "file": {}},
        )
        self.assertEqual(self.calls[0][1], "sendMessage")
        self.assertEqual(self.calls[0][2]["text"], "hello")

    async def test_429_is_retried(self):
        self.responses = [
            (429, {"ok": False, "parameters": {"retry_after": 0}}),
            (200, {"ok": True, "result": True}),
        ]
        result = await self.api.set_webhook("123:abc", "https://example.com/hook")
        self.assertTrue(result)
        self.assertEqual(len(self.calls), 2)

    async def test_platform_error_is_not_silently_accepted(self):
        self.responses = [(401, {"ok": False, "error_code": 401,
                                 "description": "Unauthorized"})]
        with self.assertRaises(BaleError):
            await self.api.get_me("123:abc")

    async def test_get_file_returns_path(self):
        self.responses = [(
            200,
            {"ok": True, "result": {
                "file_id": "f1", "file_path": "photos/a.jpg",
                "file_size": 3,
            }},
        )]
        result = await self.api.get_file("123:abc", "f1")
        self.assertEqual(result["file_path"], "photos/a.jpg")
        self.assertEqual(
            self.calls,
            [("123:abc", "getFile", {"file_id": "f1"})],
        )

    async def test_download_file_returns_bytes(self):
        data, content_type = await self.api.download_file(
            "123:abc", "photos/a.jpg", 10
        )
        self.assertEqual(data, b"abc")
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(self.downloads, [("123:abc", "photos/a.jpg")])

    async def test_download_file_rejects_large_files(self):
        with self.assertRaises(FileTooLarge):
            await self.api.download_file("123:abc", "photos/a.jpg", 2)

    async def test_server_error_is_temporary(self):
        self.responses = [(500, {"ok": False})]
        with self.assertRaises(BaleUnavailable):
            await self.api.get_me("123:abc")


if __name__ == "__main__":
    unittest.main()
