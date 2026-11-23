import os
from dataclasses import dataclass
from urllib.parse import urlparse

from config import env_flag, load_env_file


class ConfigurationError(RuntimeError):
    """Raised when the Bale service cannot start safely."""


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value


def _positive_int(name, default):
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive.")
    return value


def _positive_float(name, default):
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive.")
    return value


def _url(name, value, require_https=False):
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigurationError(f"{name} must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{name} must not contain credentials.")
    if require_https:
        if parsed.scheme != "https":
            raise ConfigurationError(f"{name} must use HTTPS for Bale webhooks.")
        if parsed.port not in (None, 443, 88):
            raise ConfigurationError(
                f"{name} must use port 443 or 88 for Bale webhooks."
            )
    return value.rstrip("/")


@dataclass(frozen=True)
class BaleServiceConfig:
    hub_url: str
    hub_service_token: str
    bale_api_base: str
    public_base_url: str
    host: str
    port: int
    sync_interval: float
    request_timeout: float
    hub_ack_timeout: float
    max_update_bytes: int
    webhook_require_https: bool
    register_webhooks: bool
    log_level: str
    file_cache_dir: str = ""
    file_cache_ttl: int = 3600
    file_cache_max_bytes: int = 512 * 1024 * 1024
    file_max_bytes: int = 20 * 1024 * 1024
    file_download_timeout: float = 30

    @property
    def hub_configured(self):
        return bool(self.hub_service_token)


def load_config():
    load_env_file()
    webhook_require_https = env_flag("BALE_WEBHOOK_REQUIRE_HTTPS", default=True)
    register_webhooks = not env_flag("BALE_DISABLE_WEBHOOK_REGISTRATION")
    public_base_url = os.getenv("BALE_PUBLIC_BASE_URL", "").strip()
    if register_webhooks and not public_base_url:
        raise ConfigurationError(
            "BALE_PUBLIC_BASE_URL is required unless webhook registration is disabled."
        )
    if public_base_url:
        public_base_url = _url(
            "BALE_PUBLIC_BASE_URL", public_base_url,
            require_https=webhook_require_https and register_webhooks,
        )
    hub_service_token = _required("HUB_BALE_SERVICE_TOKEN")

    return BaleServiceConfig(
        hub_url=_url("HUB_URL", os.getenv("HUB_URL", "http://127.0.0.1:8765")),
        hub_service_token=hub_service_token,
        bale_api_base=_url(
            "BALE_API_BASE", os.getenv("BALE_API_BASE", "https://tapi.bale.ai")
        ),
        public_base_url=public_base_url,
        host=os.getenv("BALE_SERVICE_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_positive_int("BALE_SERVICE_PORT", 8766),
        sync_interval=_positive_float("BALE_SYNC_INTERVAL", 30),
        request_timeout=_positive_float("BALE_REQUEST_TIMEOUT", 15),
        hub_ack_timeout=_positive_float("BALE_HUB_ACK_TIMEOUT", 10),
        max_update_bytes=_positive_int("BALE_MAX_UPDATE_BYTES", 1_048_576),
        webhook_require_https=webhook_require_https,
        register_webhooks=register_webhooks,
        log_level=os.getenv("BALE_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        file_cache_dir=os.getenv("BALE_FILE_CACHE_DIR", "").strip(),
        file_cache_ttl=_positive_int("BALE_FILE_CACHE_TTL", 3600),
        file_cache_max_bytes=_positive_int(
            "BALE_FILE_CACHE_MAX_BYTES", 512 * 1024 * 1024
        ),
        file_max_bytes=_positive_int(
            "BALE_FILE_MAX_BYTES", 20 * 1024 * 1024
        ),
        file_download_timeout=_positive_float("BALE_FILE_DOWNLOAD_TIMEOUT", 30),
    )
