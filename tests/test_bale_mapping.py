import unittest

from bale_service.api import _file_reference, split_text
from bale_service.mapping import build_client_payload, extract_file


def make_update(text="hello", chat_type="private", **message_fields):
    message = {
        "message_id": 9,
        "date": 1_700_000_000,
        "chat": {"id": 42, "type": chat_type},
        "from": {"id": 42, "is_bot": False},
        "text": text,
    }
    message.update(message_fields)
    return {"update_id": 7, "message": message}


class BaleMappingTests(unittest.TestCase):
    def test_private_text_message(self):
        payload = build_client_payload(make_update(), "robot-1")
        self.assertEqual(payload, {
            "user_id": "42",
            "sender": "client",
            "text": "hello",
            "file": None,
            "robot_id": "robot-1",
            "timestamp": 1_700_000_000_000,
            "source": "bale",
        })

    def test_group_and_bot_messages_are_ignored(self):
        self.assertIsNone(build_client_payload(make_update(chat_type="group"), "r"))
        update = make_update()
        update["message"]["from"]["is_bot"] = True
        self.assertIsNone(build_client_payload(update, "r"))

    def test_empty_update_is_ignored(self):
        self.assertIsNone(build_client_payload({"update_id": 1}, "r"))
        update = make_update(text="   ")
        del update["message"]["text"]
        self.assertIsNone(build_client_payload(update, "r"))

    def test_photo_uses_largest_size_and_caption(self):
        update = make_update(
            text=None,
            caption="photo caption",
            photo=[
                {"file_id": "small", "file_size": 10, "width": 10, "height": 10},
                {"file_id": "big", "file_size": 100, "width": 90, "height": 90},
            ],
        )
        payload = build_client_payload(update, "r")
        self.assertEqual(payload["text"], "photo caption")
        self.assertEqual(payload["file"]["type"], "photo")
        self.assertEqual(payload["file"]["file_id"], "big")

    def test_document_metadata(self):
        update = make_update(
            text=None,
            document={
                "file_id": "doc-1",
                "file_name": "invoice.pdf",
                "mime_type": "application/pdf",
                "file_size": 1200,
            },
        )
        payload = build_client_payload(update, "r")
        self.assertEqual(payload["text"], "")
        self.assertEqual(payload["file"], {
            "provider": "bale",
            "type": "document",
            "file_id": "doc-1",
            "file_name": "invoice.pdf",
            "mime_type": "application/pdf",
            "file_size": 1200,
        })

    def test_contact_and_location_become_text(self):
        contact = make_update(
            text=None,
            contact={"first_name": "Sara", "last_name": "A",
                     "phone_number": "0912"},
        )
        self.assertEqual(
            build_client_payload(contact, "r")["text"],
            "[contact] Sara A 0912",
        )
        location = make_update(
            text=None, location={"latitude": 35.7, "longitude": 51.4}
        )
        self.assertEqual(
            build_client_payload(location, "r")["text"],
            "[location] 35.7,51.4",
        )

    def test_timestamp_fallback_and_invalid_ids(self):
        update = make_update()
        del update["message"]["date"]
        payload = build_client_payload(update, "r", now_ms=123)
        self.assertEqual(payload["timestamp"], 123)
        update["message"]["chat"]["id"] = ""
        self.assertIsNone(build_client_payload(update, "r"))

    def test_extract_file_requires_file_id(self):
        self.assertIsNone(extract_file({"photo": [{"file_size": 1}]}))
        self.assertIsNone(extract_file({"document": {"file_id": 3}}))

    def test_file_metadata_is_bounded(self):
        update = make_update(
            text=None,
            document={
                "file_id": "d",
                "file_name": "n" * 5000,
                "mime_type": "m" * 500,
            },
        )
        payload = build_client_payload(update, "r")
        self.assertEqual(len(payload["file"]["file_name"]), 1024)
        self.assertEqual(len(payload["file"]["mime_type"]), 255)

    def test_malformed_file_numbers_are_ignored(self):
        update = make_update(
            text=None,
            photo=[
                {"file_id": "a", "file_size": "big", "width": "x"},
                {"file_id": "b"},
            ],
        )
        payload = build_client_payload(update, "r")
        self.assertIn(payload["file"]["file_id"], ("a", "b"))

        update = make_update(
            text=None, document={"file_id": "d", "file_size": float("nan")}
        )
        payload = build_client_payload(update, "r")
        self.assertNotIn("file_size", payload["file"])


class BaleApiHelpersTests(unittest.TestCase):
    def test_split_text_preserves_content(self):
        text = ("line\n" * 2000).strip()
        chunks = split_text(text)
        self.assertTrue(all(0 < len(chunk) <= 4096 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_split_text_short_and_empty(self):
        self.assertEqual(split_text("hello"), ["hello"])
        self.assertEqual(split_text(""), [])

    def test_file_reference(self):
        self.assertEqual(_file_reference({"file_id": "x", "type": "photo"}),
                         ("photo", "x"))
        self.assertEqual(_file_reference("https://example.ir/a.png"),
                         ("photo", "https://example.ir/a.png"))
        self.assertEqual(_file_reference("https://example.ir/a.pdf"),
                         ("document", "https://example.ir/a.pdf"))
        with self.assertRaises(ValueError):
            _file_reference({"file_id": ""})


if __name__ == "__main__":
    unittest.main()
