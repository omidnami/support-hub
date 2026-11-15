import hashlib
import hmac
import json
import os
from dataclasses import dataclass

import aiomysql
from cryptography.fernet import Fernet

from .config import Endpoint, valid_identifier
from .migrations import migrate, status


def digest(secret):
    return hashlib.sha256(secret.encode("utf-8", errors="surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Channel:
    id: str
    tenant: str
    endpoint: str
    owner_endpoint: str
    platform: str
    credentials: dict
    webhook_hash: str
    receive_mode: str = 'webhook'
    poll_offset: int = 0


class Database:
    def __init__(self, pool, cipher):
        self.pool, self.cipher = pool, cipher

    @classmethod
    async def open(cls):
        cipher = Fernet(os.environ["HUB_ENCRYPTION_KEY"].encode())
        pool = await aiomysql.create_pool(
            host=os.getenv("HUB_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("HUB_DB_PORT", "3306")),
            user=os.environ["HUB_DB_USER"], password=os.environ["HUB_DB_PASSWORD"],
            db=os.environ["HUB_DB_NAME"], charset="utf8mb4",
            minsize=1, maxsize=8, autocommit=True, connect_timeout=5)
        return cls(pool, cipher)

    async def close(self):
        self.pool.close()
        await self.pool.wait_closed()

    async def query(self, sql, args=(), write=False):
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return cur.rowcount if write else await cur.fetchall()

    async def migrate(self):
        return await migrate(self.pool)

    async def migration_status(self):
        return await status(self.pool)

    async def init_schema(self):
        # Backward-compatible CLI/test entry point.
        return await self.migrate()

    async def client_enabled(self, tenant, endpoint):
        rows = await self.query(
            "SELECT 1 FROM clients WHERE tenant=%s AND endpoint=%s AND enabled=1",
            (tenant, endpoint))
        return bool(rows)

    async def verify_client(self, tenant, endpoint, token):
        rows = await self.query(
            "SELECT token_hash FROM clients WHERE tenant=%s AND endpoint=%s AND enabled=1",
            (tenant, endpoint))
        return bool(rows and hmac.compare_digest(rows[0]["token_hash"], digest(token)))

    async def authenticate(self, tenant, endpoint, token):
        rows = await self.query(
            "SELECT token_hash FROM clients WHERE tenant=%s AND endpoint=%s AND enabled=1",
            (tenant, endpoint))
        if not rows or not hmac.compare_digest(rows[0]["token_hash"], digest(token)):
            return None
        return await self.authorization(tenant, endpoint, token)

    async def authorization(self, tenant, endpoint, token):
        rows = await self.query(
            "SELECT token_hash, peers FROM clients WHERE tenant=%s AND endpoint=%s AND enabled=1",
            (tenant, endpoint))
        if not rows or not hmac.compare_digest(rows[0]["token_hash"], digest(token)):
            return None
        allowed = set(json.loads(rows[0]["peers"]))
        active = await self.query("SELECT endpoint FROM clients WHERE tenant=%s AND enabled=1", (tenant,))
        allowed.intersection_update(r["endpoint"] for r in active)
        channels = await self.query(
            "SELECT endpoint FROM channels WHERE tenant=%s AND owner_endpoint=%s AND enabled=1",
            (tenant, endpoint))
        allowed.update(r["endpoint"] for r in channels)
        return Endpoint("", frozenset(allowed))

    async def add_client(self, tenant, endpoint, token, peers):
        if not valid_identifier(tenant) or not valid_identifier(endpoint) or endpoint.startswith(("ch-", "web-")):
            raise ValueError("invalid tenant or reserved endpoint")
        if not all(valid_identifier(p) and p != endpoint and not p.startswith(("ch-", "web-")) for p in peers):
            raise ValueError("invalid peers")
        await self.query(
            "INSERT INTO clients (tenant,endpoint,token_hash,peers) VALUES (%s,%s,%s,%s)",
            (tenant, endpoint, digest(token), json.dumps(peers)), write=True)

    async def add_channel(self, channel_id, tenant, owner, platform, credentials, hook):
        if not await self.query(
                "SELECT endpoint FROM clients WHERE tenant=%s AND endpoint=%s AND enabled=1",
                (tenant, owner)):
            raise ValueError("owner_not_found")
        provider_account = credentials.get("account_id") if platform == "instagram" else None
        encrypted = self.cipher.encrypt(json.dumps(credentials).encode()).decode("ascii")
        await self.query(
            "INSERT INTO channels (id,tenant,endpoint,owner_endpoint,platform,credentials,webhook_hash,provider_account_id)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (channel_id, tenant, "ch-" + channel_id, owner, platform, encrypted, digest(hook), provider_account),
            write=True)

    def channel(self, row):
        return Channel(row["id"], row["tenant"], row["endpoint"], row["owner_endpoint"],
                       row["platform"], json.loads(self.cipher.decrypt(row["credentials"].encode("ascii") if isinstance(row["credentials"], str) else row["credentials"])),
                       row["webhook_hash"], row["receive_mode"], row["poll_offset"])

    async def get_channel(self, channel_id):
        rows = await self.query(
            "SELECT c.* FROM channels c JOIN clients t ON t.tenant=c.tenant "
            "AND t.endpoint=c.owner_endpoint WHERE c.id=%s AND c.enabled=1 AND t.enabled=1",
            (channel_id,))
        return self.channel(rows[0]) if rows else None

    async def active_channels(self):
        rows = await self.query(
            "SELECT c.* FROM channels c JOIN clients t ON t.tenant=c.tenant "
            "AND t.endpoint=c.owner_endpoint WHERE c.enabled=1 AND t.enabled=1")
        return [self.channel(row) for row in rows]

    async def polling_channels(self):
        rows = await self.query(
            "SELECT c.* FROM channels c JOIN clients t ON t.tenant=c.tenant "
            "AND t.endpoint=c.owner_endpoint WHERE c.enabled=1 AND t.enabled=1 "
            "AND c.platform='bale' AND c.receive_mode='polling' ORDER BY c.id")
        return [self.channel(row) for row in rows]

    async def set_receive_mode(self, channel_id, mode):
        if mode not in ("polling", "webhook"):
            raise ValueError("invalid receive mode")
        return await self.query(
            "UPDATE channels SET receive_mode=%s WHERE id=%s AND platform='bale' AND enabled=1",
            (mode, channel_id), write=True)

    async def advance_poll_offset(self, channel_id, offset):
        return await self.query(
            "UPDATE channels SET poll_offset=%s WHERE id=%s AND enabled=1 "
            "AND receive_mode='polling' AND poll_offset<%s",
            (offset, channel_id, offset), write=True)

    async def instagram_channel(self, account_id):
        rows = await self.query(
            "SELECT c.* FROM channels c JOIN clients t ON t.tenant=c.tenant "
            "AND t.endpoint=c.owner_endpoint WHERE c.platform='instagram' "
            "AND c.provider_account_id=%s AND c.enabled=1 AND t.enabled=1",
            (account_id,))
        return self.channel(rows[0]) if rows else None

    async def list_clients(self, tenant):
        rows = await self.query(
            "SELECT tenant,endpoint,peers,enabled FROM clients WHERE tenant=%s ORDER BY endpoint", (tenant,))
        return [{**r, "enabled": bool(r["enabled"]), "peers": json.loads(r["peers"])} for r in rows]

    async def list_channels(self, tenant):
        rows = await self.query(
            "SELECT id,tenant,endpoint,owner_endpoint,platform,enabled,provider_account_id,receive_mode "
            "FROM channels WHERE tenant=%s ORDER BY id", (tenant,))
        return [{**r, "enabled": bool(r["enabled"])} for r in rows]
