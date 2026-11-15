"""One validated Redis namespace for all Hub data."""

import re

DEFAULT_PREFIX = "support-hub:"
PREFIX_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}:\Z")


def validate_prefix(prefix):
    if not isinstance(prefix, str) or not PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError("HUB_REDIS_PREFIX must be 1-48 ASCII letters/digits/hyphens/underscores, followed by ':'")
    return prefix


class RedisKeys:
    def __init__(self, prefix=DEFAULT_PREFIX):
        self.prefix = validate_prefix(prefix)

    def tenant(self, tenant):
        return f"{self.prefix}{{{tenant}}}:"

    def index(self, tenant, endpoint):
        return f"{self.tenant(tenant)}pending:{endpoint}"

    def item(self, tenant, endpoint, delivery_id):
        return f"{self.tenant(tenant)}message:{endpoint}:{delivery_id}"

    def receipt(self, tenant, delivery_id):
        return f"{self.tenant(tenant)}receipt:{delivery_id}"

    def browser(self, tenant, endpoint):
        return f"{self.tenant(tenant)}browser:{endpoint}"
