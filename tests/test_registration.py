import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from pymysql.err import IntegrityError

from support_hub.database import Database, digest
from support_hub.http_server import build_app
from support_hub.registration import create_registration, registration_fields


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = SimpleNamespace()
        self.client = TestClient(TestServer(build_app(self.db, SimpleNamespace())))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_required_fields_and_optional_defaults(self):
        with patch("support_hub.registration.create_registration", new_callable=AsyncMock,
                   return_value="a" * 64) as create:
            response = await self.client.post("/v1/register", json={
                "name": "فروشگاه", "phone": "۰۹۱۲۱۲۳۴۵۶۷"})
            self.assertEqual(response.status, 201)
            self.assertEqual(await response.json(), {
                "status": True, "api_key": "a" * 64, "message": "Registration successful."})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            create.assert_awaited_once_with(self.db, "فروشگاه", "+989121234567",
                                            None, None, "فروشگاه")

    async def test_validation_no_database_writes(self):
        with patch("support_hub.registration.create_registration", new_callable=AsyncMock) as create:
            for body in ({}, {"name": "x", "phone": "x"}, {"name": "x", "phone": 123456789},
                         {"name": "x", "phone": "+989121234567", "email": "invalid"},
                         {"name": "x", "phone": "+989121234567", "domain": "https://example.ir/path"}):
                response = await self.client.post("/v1/register", json=body)
                self.assertEqual(response.status, 422)
                self.assertFalse((await response.json())["status"])
            response = await self.client.post("/v1/register", data="[1]")
            self.assertEqual(response.status, 400)
            create.assert_not_awaited()

    async def test_conflict_does_not_issue_key(self):
        with patch("support_hub.registration.create_registration", new_callable=AsyncMock,
                   side_effect=IntegrityError(1062, "duplicate")):
            response = await self.client.post("/v1/register", json={
                "name": "Example", "phone": "+989121234567", "domain": "Example.IR"})
            self.assertEqual(response.status, 409)
            self.assertNotIn("api_key", await response.json())

    def test_optional_fields(self):
        self.assertEqual(registration_fields({
            "name": "Owner", "phone": "00989121234567", "email": "Owner@EXAMPLE.IR",
            "domain": "Example.IR.", "project_name": "Shop"}),
            ("Owner", "+989121234567", "owner@example.ir", "example.ir", "Shop"))
        self.assertEqual(registration_fields({
            "name": "Owner", "phone": "989121234567", "domain": "example.ir"})[-1],
            "example.ir")


@unittest.skipUnless(os.getenv("HUB_TEST_DB") == "1", "Set HUB_TEST_DB=1")
class RegistrationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await Database.open()
        await self.db.migrate()
        suffix = str(uuid4().int % 10**12).zfill(12)
        self.phone = "+1" + suffix
        self.other = "+2" + suffix

    async def asyncTearDown(self):
        await self.db.query("DELETE FROM projects WHERE user_id IN "
                            "(SELECT id FROM users WHERE phone IN (%s,%s))",
                            (self.phone, self.other), write=True)
        await self.db.query("DELETE FROM users WHERE phone IN (%s,%s)",
                            (self.phone, self.other), write=True)
        await self.db.close()

    async def test_transaction_key_hash_and_duplicate_phone(self):
        key = await create_registration(self.db, "Owner", self.phone, None, None, "Shop")
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        rows = await self.db.query(
            "SELECT u.password,u.email,p.name,p.apikey,p.domain FROM users u "
            "JOIN projects p ON p.user_id=u.id WHERE u.phone=%s", (self.phone,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"password": None, "email": None, "name": "Shop",
                                   "apikey": digest(key), "domain": None})
        self.assertNotEqual(rows[0]["apikey"], key)
        with self.assertRaises(IntegrityError):
            await create_registration(self.db, "Duplicate", self.phone, None, None, "Shop 2")
        with patch("support_hub.registration.secrets.token_hex", return_value=key):
            with self.assertRaises(IntegrityError):
                await create_registration(self.db, "Rollback", self.other, None, None, "Shop")
        self.assertFalse(await self.db.query("SELECT id FROM users WHERE phone=%s", (self.other,)))
        self.assertEqual(len(await self.db.query(
            "SELECT p.id FROM projects p JOIN users u ON p.user_id=u.id WHERE u.phone=%s",
            (self.phone,))), 1)
