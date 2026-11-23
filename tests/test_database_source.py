import unittest

from database import (
    delete_project_webhook, get_project_webhook, get_source_identity,
    set_project_webhook, update_source_token,
)


class _Cursor:
    def __init__(self, row, rowcount=1):
        self.row = row
        self.rowcount = rowcount
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query, params=None):
        self.queries.append((query, params))

    async def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def begin(self):
        pass

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rollbacks += 1

    def cursor(self):
        return self._cursor


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return self.connection


class SourceTokenUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_bot_clears_webhook_state(self):
        cursor = _Cursor((b"old-token", "old-robot"))
        connection = _Connection(cursor)
        updated = await update_source_token(
            _Pool(connection), 2, 1, "new-robot", "new-token"
        )
        self.assertTrue(updated)
        self.assertTrue(connection.committed)
        self.assertEqual(connection.rollbacks, 0)
        update_query, update_params = cursor.queries[1]
        self.assertIn("webhook_secret = NULL", update_query)
        self.assertIn("last_update_id = NULL", update_query)
        self.assertEqual(update_params, ("new-token", "new-robot", 1, 2))

    async def test_unchanged_bot_keeps_webhook_state(self):
        cursor = _Cursor((b"same-token", "same-robot"))
        connection = _Connection(cursor)
        updated = await update_source_token(
            _Pool(connection), 2, 1, "same-robot", "same-token"
        )
        self.assertTrue(updated)
        self.assertTrue(connection.committed)
        update_query, _ = cursor.queries[1]
        self.assertNotIn("webhook_secret", update_query)

    async def test_missing_source_rolls_back(self):
        cursor = _Cursor(None)
        connection = _Connection(cursor)
        updated = await update_source_token(
            _Pool(connection), 2, 1, "robot", "token"
        )
        self.assertFalse(updated)
        self.assertFalse(connection.committed)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(len(cursor.queries), 1)


class SourceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_decodes_stored_values(self):
        cursor = _Cursor((b"bale", b"987654"))
        identity = await get_source_identity(
            _Pool(_Connection(cursor)), 2, 1
        )
        self.assertEqual(identity, ("bale", "987654"))

    async def test_missing_identity_returns_none(self):
        identity = await get_source_identity(
            _Pool(_Connection(_Cursor(None))), 2, 1
        )
        self.assertIsNone(identity)


class ProjectWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_project_webhook(self):
        cursor = _Cursor(("https://planet.example.ir/hook", "secret"))
        webhook = await get_project_webhook(_Pool(_Connection(cursor)), 2)
        self.assertEqual(webhook, ("https://planet.example.ir/hook", "secret"))

    async def test_get_project_webhook_returns_none_when_missing(self):
        for row in (None, (None, None), ("https://planet.example.ir/hook", None)):
            with self.subTest(row=row):
                cursor = _Cursor(row)
                webhook = await get_project_webhook(_Pool(_Connection(cursor)), 2)
                self.assertIsNone(webhook)

    async def test_set_project_webhook_reports_missing_project(self):
        cursor = _Cursor(None, rowcount=0)
        updated = await set_project_webhook(
            _Pool(_Connection(cursor)), 2, "https://planet.example.ir/hook", "secret"
        )
        self.assertFalse(updated)
        self.assertIn("webhook_url", cursor.queries[0][0])

    async def test_delete_project_webhook_checks_project_existence(self):
        cursor = _Cursor((2,), rowcount=0)
        deleted = await delete_project_webhook(_Pool(_Connection(cursor)), 2)
        self.assertTrue(deleted)
        self.assertEqual(len(cursor.queries), 2)

        cursor = _Cursor(None, rowcount=0)
        deleted = await delete_project_webhook(_Pool(_Connection(cursor)), 2)
        self.assertFalse(deleted)


if __name__ == "__main__":
    unittest.main()
