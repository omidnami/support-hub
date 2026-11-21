import os
import unittest
from unittest import mock

from bale_service.config import ConfigurationError, load_config


BASE_ENV = {
    "HUB_BALE_SERVICE_TOKEN": "x" * 32,
    "BALE_PUBLIC_BASE_URL": "https://support.example.com",
}


def load(**overrides):
    env = {**BASE_ENV, **overrides}
    with mock.patch.dict(os.environ, env, clear=True):
        return load_config()


class BaleConfigTests(unittest.TestCase):
    def test_valid_configuration(self):
        config = load()
        self.assertEqual(config.public_base_url, "https://support.example.com")
        self.assertTrue(config.register_webhooks)
        self.assertTrue(config.webhook_require_https)
        self.assertEqual(config.sync_interval, 30)

    def test_bridge_token_is_required(self):
        with self.assertRaises(ConfigurationError):
            load(HUB_BALE_SERVICE_TOKEN="")

    def test_public_url_must_be_https(self):
        with self.assertRaises(ConfigurationError):
            load(BALE_PUBLIC_BASE_URL="http://support.example.com")

    def test_public_url_port_must_be_allowed_by_bale(self):
        with self.assertRaises(ConfigurationError):
            load(BALE_PUBLIC_BASE_URL="https://support.example.com:8443")

    def test_disabling_registration_allows_a_missing_public_url(self):
        config = load(
            BALE_DISABLE_WEBHOOK_REGISTRATION="1",
            BALE_PUBLIC_BASE_URL="",
        )
        self.assertFalse(config.register_webhooks)
        self.assertEqual(config.public_base_url, "")

    def test_invalid_numbers_are_rejected(self):
        with self.assertRaises(ConfigurationError):
            load(BALE_SYNC_INTERVAL="0")
        with self.assertRaises(ConfigurationError):
            load(BALE_SERVICE_PORT="abc")


if __name__ == "__main__":
    unittest.main()
