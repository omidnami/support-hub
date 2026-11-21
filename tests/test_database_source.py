import unittest

from database import update_source_token


class _Cursor:
    def __init__(self, row):
        self.row = row
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


if __name__ == "__main__":
    unittest.main()
