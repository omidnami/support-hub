"""Integration tests: dedicated disposable MariaDB database only."""
import asyncio
import os
import unittest
from uuid import uuid4

from support_hub.database import Database
from support_hub.migrations import discover


@unittest.skipUnless(os.getenv("HUB_TEST_DB") == "1", "Set HUB_TEST_DB=1 for database integration tests")
class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await Database.open()
        self.tenant = "test-" + uuid4().hex
        await self.db.init_schema()

    async def asyncTearDown(self):
        await self.db.query("DELETE FROM channels WHERE tenant=%s", (self.tenant,), write=True)
        await self.db.query("DELETE FROM clients WHERE tenant=%s", (self.tenant,), write=True)
        await self.db.close()

    async def test_migrations_are_idempotent_and_indexed(self):
        migrations = await self.db.migration_status()
        self.assertEqual([name for name, _ in migrations], [m.name for m in discover()])
        self.assertTrue(all(applied for _, applied in migrations))
        before = await self.db.query("SELECT * FROM migrations ORDER BY name")
        results = await asyncio.gather(self.db.migrate(), self.db.migrate())
        self.assertEqual(results, [[], []])
        self.assertEqual(before, await self.db.query("SELECT * FROM migrations ORDER BY name"))
        indexes = await self.db.query("SHOW INDEX FROM channels")
        self.assertTrue(any(row["Key_name"] == "unique_provider_account" and row["Non_unique"] == 0 for row in indexes))
        fk = await self.db.query(
            "SELECT REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='channels' "
            "AND REFERENCED_TABLE_NAME='clients'")
        self.assertEqual(len(fk), 2)

    async def test_auth_and_encrypted_channel(self):
        token = uuid4().hex + uuid4().hex
        other = uuid4().hex + uuid4().hex
        await self.db.add_client(self.tenant, "admin", token, ["public"])
        await self.db.add_client(self.tenant, "public", other, ["admin"])
        self.assertIsNone(await self.db.authenticate(self.tenant, "admin", other))
        self.assertIn("public", (await self.db.authenticate(self.tenant, "admin", token)).peers)
        cid = uuid4().hex
        creds = {"token": "example-provider-secret", "webhook_secret": "example-hook"}
        await self.db.add_channel(cid, self.tenant, "admin", "telegram", creds, "example-hook")
        row = await self.db.query("SELECT credentials FROM channels WHERE id=%s", (cid,))
        self.assertNotIn(b"example-provider-secret", row[0]["credentials"])
        channel = await self.db.get_channel(cid)
        self.assertEqual(channel.credentials, creds)
        self.assertIn(channel.endpoint, (await self.db.authenticate(self.tenant, "admin", token)).peers)
        self.assertNotIn(channel.endpoint, (await self.db.authenticate(self.tenant, "public", other)).peers)
        public = await self.db.list_channels(self.tenant)
        self.assertNotIn("credentials", public[0])

    async def test_instagram_account_lookup(self):
        token = uuid4().hex + uuid4().hex
        await self.db.add_client(self.tenant, "admin", token, [])
        account = str(uuid4().int)[:30]
        cid = uuid4().hex
        creds = {"token": "example-provider-secret", "webhook_secret": "example-hook",
                 "account_id": account}
        await self.db.add_channel(cid, self.tenant, "admin", "instagram", creds, "example-hook")
        channel = await self.db.instagram_channel(account)
        self.assertIsNotNone(channel)
        self.assertEqual(channel.id, cid)
        self.assertEqual(channel.credentials, creds)
        await self.db.query("UPDATE channels SET enabled=0 WHERE id=%s", (cid,), write=True)
        self.assertIsNone(await self.db.instagram_channel(account))
