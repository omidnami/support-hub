import os
from pathlib import Path


def parse_env_line(line):
    """Parse one dotenv-style line into a (key, value) pair."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    key, separator, value = line.partition("=")
    key = key.strip()
    if not separator or not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_env_file(path=None):
    """Load .env without overwriting variables already present in the environment."""
    path = Path(path) if path else Path(__file__).with_name(".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            os.environ.setdefault(key, value)


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_origins(name="HUB_ALLOWED_ORIGINS"):
    """Parse a comma-separated Socket.IO Origin allowlist from the environment.

    Unset or empty keeps engine.io's same-origin default. ``*`` allows every
    Origin, and any other value is split into exact origin strings.
    """
    value = (os.getenv(name) or "").strip()
    if not value:
        return None
    if value == "*":
        return "*"
    origins = [part.strip() for part in value.split(",") if part.strip()]
    return origins or None
