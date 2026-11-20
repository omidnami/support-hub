import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import env_flag, load_env_file, parse_env_line
from server import (
    CLIENT_TOKEN_SKEW,
    MAX_CLIENT_TOKEN_TTL,
    MAX_FILE_BYTES,
    MAX_TEXT_LENGTH,
    PROJECT_FIELDS,
    USER_FIELDS,
    SlidingWindowLimiter,
    client_room,
    client_signature,
    derive_client_key,
    message_payload,
    normalize_id,
    parse_patch_values,
    parse_path_id,
    validate_register,
    validate_source_token,
    verify_client_signature,
)


def valid_registration():
    return {
        "name": "Test User",
        "email": "user@example.ir",
        "phone": "09121234567",
        "password": "example-password",
        "project_name": "My Shop",
        "domain": "example.ir",
        "description": "My first project",
    }


def valid_message():
    return {
        "user_id": "101",
        "sender": "client",
        "text": "hello",
        "file": None,
        "robot_id": "my-shop-chat",
        "timestamp": 1794841200000,
        "source": "chat",
    }


class ConfigTests(unittest.TestCase):
    def test_parse_env_line(self):
        self.assertEqual(parse_env_line("FOO=bar"), ("FOO", "bar"))
        self.assertEqual(parse_env_line("export FOO='bar baz'"), ("FOO", "bar baz"))
        self.assertEqual(parse_env_line('FOO="quoted"'), ("FOO", "quoted"))
        self.assertIsNone(parse_env_line("# comment"))
        self.assertIsNone(parse_env_line(""))
        self.assertIsNone(parse_env_line("not-an-assignment"))

    def test_load_env_file_does_not_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("export SH_TEST_FOO=from-file\nSH_TEST_BAR='from file'\n")
            with mock.patch.dict(os.environ, {"SH_TEST_FOO": "existing"}, clear=False):
                load_env_file(path)
                self.assertEqual(os.environ["SH_TEST_FOO"], "existing")
                self.assertEqual(os.environ["SH_TEST_BAR"], "from file")
            os.environ.pop("SH_TEST_BAR", None)

    def test_env_flag(self):
        with mock.patch.dict(os.environ, {"SH_TEST_FLAG": "yes"}, clear=False):
            self.assertTrue(env_flag("SH_TEST_FLAG"))
        with mock.patch.dict(os.environ, {"SH_TEST_FLAG": "0"}, clear=False):
            self.assertFalse(env_flag("SH_TEST_FLAG"))
        os.environ.pop("SH_TEST_FLAG", None)
        self.assertTrue(env_flag("SH_TEST_FLAG", default=True))


class RegistrationTests(unittest.TestCase):
    def test_valid_registration(self):
        values = validate_register(valid_registration())
        self.assertEqual(values["name"], "Test User")
        self.assertEqual(values["domain"], "example.ir")
        self.assertEqual(values["description"], "My first project")

    def test_optional_fields_can_be_null_or_blank(self):
        data = valid_registration()
        data["domain"] = None
        data["description"] = "   "
        values = validate_register(data)
        self.assertIsNone(values["domain"])
        self.assertIsNone(values["description"])

    def test_required_field_is_required(self):
        data = valid_registration()
        data["name"] = "   "
        with self.assertRaisesRegex(ValueError, "Required field: name"):
            validate_register(data)

    def test_missing_field_is_reported_as_required(self):
        data = valid_registration()
        del data["email"]
        with self.assertRaisesRegex(ValueError, "Required field: email"):
            validate_register(data)

    def test_invalid_payload_type(self):
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            validate_register([])

    def test_password_minimum_length(self):
        data = valid_registration()
        data["password"] = "short"
        with self.assertRaisesRegex(ValueError, "at least 8"):
            validate_register(data)

    def test_password_is_not_trimmed(self):
        data = valid_registration()
        data["password"] = "  password  "
        self.assertEqual(validate_register(data)["password"], "  password  ")

    def test_field_length_limit(self):
        data = valid_registration()
        data["name"] = "x" * 191
        with self.assertRaisesRegex(ValueError, "Invalid field: name"):
            validate_register(data)


class PatchTests(unittest.TestCase):
    def test_user_patch(self):
        self.assertEqual(
            parse_patch_values({"name": " Updated "}, USER_FIELDS),
            {"name": "Updated"},
        )

    def test_project_nullable_fields(self):
        self.assertEqual(
            parse_patch_values(
                {"domain": None, "description": "  "},
                PROJECT_FIELDS,
                nullable=("domain", "description"),
            ),
            {"domain": None, "description": None},
        )

    def test_empty_patch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            parse_patch_values({}, USER_FIELDS)

    def test_unknown_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid field"):
            parse_patch_values({"admin": "yes"}, USER_FIELDS)

    def test_password_minimum_length_on_patch(self):
        with self.assertRaisesRegex(ValueError, "at least 8"):
            parse_patch_values({"password": "short"}, USER_FIELDS)


class IdentifierTests(unittest.TestCase):
    def test_chat_token_limits(self):
        self.assertEqual(validate_source_token("chat", "x" * 255), "x" * 255)
        with self.assertRaises(ValueError):
            validate_source_token("chat", "x" * 256)

    def test_source_token_rejects_invalid_characters(self):
        with self.assertRaises(ValueError):
            validate_source_token("telegram", "bad token")
        with self.assertRaises(ValueError):
            validate_source_token("telegram", "متن")

    def test_normalize_id(self):
        self.assertEqual(normalize_id(" 101 "), "101")
        self.assertEqual(normalize_id(101), "101")
        self.assertIsNone(normalize_id(True))
        self.assertIsNone(normalize_id(" "))
        self.assertIsNone(normalize_id("a\nb"))
        self.assertIsNone(normalize_id("x" * 256))

    def test_path_id(self):
        self.assertEqual(parse_path_id("1"), 1)
        self.assertEqual(parse_path_id("18446744073709551615"), 18446744073709551615)
        self.assertIsNone(parse_path_id("0"))
        self.assertIsNone(parse_path_id("18446744073709551616"))
        self.assertIsNone(parse_path_id("-1"))
        self.assertIsNone(parse_path_id("1.0"))
        self.assertIsNone(parse_path_id("١"))


class ClientSignatureTests(unittest.TestCase):
    def test_key_and_signature_round_trip(self):
        key = derive_client_key("project-token")
        self.assertEqual(len(key), 64)
        self.assertEqual(key, derive_client_key("project-token"))
        self.assertNotEqual(key, derive_client_key("other-token"))

        now = 1_700_000_000
        expires = now + 300
        signature = client_signature(key, "my-shop-chat", "101", expires)
        self.assertTrue(
            verify_client_signature(key, "my-shop-chat", "101", expires, signature, now=now)
        )
        self.assertTrue(
            verify_client_signature(
                key, "my-shop-chat", "101", expires, signature.upper(), now=now
            )
        )
        self.assertFalse(
            verify_client_signature(key, "my-shop-chat", "102", expires, signature, now=now)
        )
        self.assertFalse(
            verify_client_signature(key, "other-chat", "101", expires, signature, now=now)
        )
        self.assertFalse(
            verify_client_signature(key, "my-shop-chat", "101", expires, "bad", now=now)
        )
        self.assertFalse(
            verify_client_signature(
                key, "my-shop-chat", "101", True, signature, now=now
            )
        )
        self.assertFalse(
            verify_client_signature(
                key, "my-shop-chat", "101", expires, signature,
                now=expires + CLIENT_TOKEN_SKEW + 1,
            )
        )
        self.assertFalse(
            verify_client_signature(
                key, "my-shop-chat", "101", expires, signature,
                now=expires - MAX_CLIENT_TOKEN_TTL - CLIENT_TOKEN_SKEW - 1,
            )
        )

    def test_client_room_is_deterministic(self):
        room = client_room(1, "chat", "my-shop-chat", "101")
        self.assertEqual(room, client_room(1, "chat", "my-shop-chat", "101"))
        self.assertNotEqual(room, client_room(1, "chat", "my-shop-chat", "102"))
        self.assertTrue(room.isascii())


class MessagePayloadTests(unittest.TestCase):
    def test_valid_payload_normalizes_ids(self):
        data = valid_message()
        data["user_id"] = 101
        data["robot_id"] = 42
        payload = message_payload(data, "client")
        self.assertEqual(payload["user_id"], "101")
        self.assertEqual(payload["robot_id"], "42")
        self.assertEqual(payload["sender"], "client")

    def test_extra_field_is_rejected(self):
        data = valid_message()
        data["extra"] = True
        with self.assertRaisesRegex(ValueError, "Expected"):
            message_payload(data, "client")

    def test_sender_must_match_connection(self):
        with self.assertRaisesRegex(ValueError, "Sender"):
            message_payload(valid_message(), "operator")

    def test_invalid_source_is_rejected(self):
        data = valid_message()
        data["source"] = "unknown"
        with self.assertRaisesRegex(ValueError, "Invalid source"):
            message_payload(data, "client")

    def test_empty_message_is_rejected(self):
        data = valid_message()
        data["text"] = "   "
        with self.assertRaisesRegex(ValueError, "empty"):
            message_payload(data, "client")

    def test_file_can_carry_a_message(self):
        data = valid_message()
        data["text"] = ""
        data["file"] = "https://example.ir/file.png"
        self.assertEqual(message_payload(data, "client")["file"], data["file"])

    def test_invalid_file_type_is_rejected(self):
        data = valid_message()
        data["file"] = []
        with self.assertRaisesRegex(ValueError, "File must be"):
            message_payload(data, "client")

    def test_non_finite_file_value_is_rejected(self):
        data = valid_message()
        data["file"] = {"size": float("nan")}
        with self.assertRaisesRegex(ValueError, "Invalid file object"):
            message_payload(data, "client")

    def test_file_too_large_after_json_escaping_is_rejected(self):
        data = valid_message()
        data["file"] = "😀" * (MAX_FILE_BYTES // 4)
        with self.assertRaisesRegex(ValueError, "File payload exceeds"):
            message_payload(data, "client")

    def test_text_limit(self):
        data = valid_message()
        data["text"] = "x" * (MAX_TEXT_LENGTH + 1)
        with self.assertRaisesRegex(ValueError, "Text exceeds"):
            message_payload(data, "client")

    def test_file_size_limit(self):
        data = valid_message()
        data["file"] = {"data": "x" * MAX_FILE_BYTES}
        with self.assertRaisesRegex(ValueError, "File payload exceeds"):
            message_payload(data, "client")

    def test_timestamp_must_be_integer_milliseconds(self):
        data = valid_message()
        data["timestamp"] = 1.5
        with self.assertRaisesRegex(ValueError, "Timestamp"):
            message_payload(data, "client")
        data["timestamp"] = True
        with self.assertRaisesRegex(ValueError, "Timestamp"):
            message_payload(data, "client")

    def test_invalid_utf8_is_rejected(self):
        data = valid_message()
        data["text"] = "\ud800"
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            message_payload(data, "client")


class LimiterTests(unittest.TestCase):
    def test_sliding_window(self):
        limiter = SlidingWindowLimiter()
        self.assertTrue(limiter.allow("key", 2, 10, now=0))
        self.assertTrue(limiter.allow("key", 2, 10, now=1))
        self.assertFalse(limiter.allow("key", 2, 10, now=2))
        self.assertTrue(limiter.allow("key", 2, 10, now=11))
        self.assertTrue(limiter.allow("other", 2, 10, now=11))

    def test_clear(self):
        limiter = SlidingWindowLimiter()
        limiter.allow("key", 1, 10, now=0)
        self.assertFalse(limiter.allow("key", 1, 10, now=1))
        limiter.clear("key")
        self.assertTrue(limiter.allow("key", 1, 10, now=2))


if __name__ == "__main__":
    unittest.main()
