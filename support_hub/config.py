import json
import os
import re
from dataclasses import dataclass

IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def valid_identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


@dataclass(frozen=True)
class Endpoint:
    token: str
    peers: frozenset[str]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    redis_url: str
    ttl: int
    retry: float
    endpoints: dict[str, dict[str, Endpoint]]

    @classmethod
    def load(cls):
        raw = {} if os.getenv("HUB_DB_NAME") else json.loads(os.getenv("HUB_ENDPOINTS", "{}"))
        if not isinstance(raw, dict) or (not raw and not os.getenv("HUB_DB_NAME")):
            raise ValueError("HUB_ENDPOINTS must be a nonempty tenant mapping")
        tenants = {}
        tokens = set()
        for tenant, entries in raw.items():
            if not valid_identifier(tenant) or not isinstance(entries, dict):
                raise ValueError("Invalid tenant configuration")
            tenants[tenant] = {}
            for name, entry in entries.items():
                if not valid_identifier(name) or not isinstance(entry, dict):
                    raise ValueError("Invalid endpoint configuration")
                token, peers = entry.get("token"), entry.get("peers")
                if (not isinstance(token, str) or len(token) < 32
                        or token.startswith("REPLACE_") or token in tokens):
                    raise ValueError("Each endpoint needs a unique random token of 32+ characters")
                if (not isinstance(peers, list)
                        or not all(valid_identifier(p) and p in entries and p != name for p in peers)):
                    raise ValueError("Invalid peer allowlist")
                tokens.add(token)
                tenants[tenant][name] = Endpoint(token, frozenset(peers))
        ttl = int(os.getenv("HUB_TTL_SECONDS", "18000"))
        retry = float(os.getenv("HUB_RETRY_SECONDS", "10"))
        if not 1 <= ttl <= 18000 or not 0.1 <= retry <= 60:
            raise ValueError("Invalid TTL or retry interval")
        return cls(os.getenv("HUB_HOST", "127.0.0.1"),
                   int(os.getenv("HUB_PORT", "8765")),
                   os.getenv("HUB_REDIS_URL", "redis://127.0.0.1:6379/0"),
                   ttl, retry, tenants)
