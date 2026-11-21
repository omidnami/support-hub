import unittest
from unittest import mock

from pymysql.err import IntegrityError

from bale_service.repository import (
    advance_last_update_id, ensure_webhook_secret, find_source_by_secret,
    list_bale_sources,
)


class _Cursor:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.queries = []
        self.failure = None
        self.always_fail = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query, params=None):
        if self.failure is not None:
            failure = self.failure
            if not self.always_fail:
                self.failure = None
            raise failure
        self.queries.append((query, params))

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


class _Pool:
    def __init__(self, cursor):
        self.cursor = cursor

    def acquire(self):
        return _Connection(self.cursor)


class BaleRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_sources_decodes_rows_and_skips_missing_tokens(self):
        cursor = _Cursor([
            (1, 2, b"robot-1", b"token-1", b"secret-1", 5),
            (2, 2, b"robot-2", b"", None, None),
            (3, 2, b"robot-3", b"token-3", None, None),
        ])
        sources = await list_bale_sources(_Pool(cursor))
        self.assertEqual([source.id for source in sources], [1, 3])
        self.assertEqual(sources[0].robot_id, "robot-1")
        self.assertEqual(sources[0].webhook_secret, "secret-1")
        self.assertEqual(sources[0].last_update_id, 5)
        self.assertEqual(sources[1].webhook_secret, "")
        self.assertIsNone(sources[1].last_update_id)

    async def test_find_by_secret_uses_the_secret_parameter(self):
        cursor = _Cursor([(1, 2, "robot", "token", "secret", None)])
        source = await find_source_by_secret(_Pool(cursor), "secret")
        self.assertEqual(source.id, 1)
        self.assertEqual(cursor.queries[0][1], ("secret",))

    async def test_advance_cursor_reports_whether_the_row_changed(self):
        cursor = _Cursor(rowcount=1)
        self.assertTrue(
            await advance_last_update_id(_Pool(cursor), 1, 10)
        )
        self.assertEqual(cursor.queries[0][1], (10, 1, 10))

        cursor = _Cursor(rowcount=0)
        self.assertFalse(
            await advance_last_update_id(_Pool(cursor), 1, 10)
        )

    async def test_ensure_secret_returns_the_stored_value(self):
        cursor = _Cursor([(b"stored-secret",)])
        secret = await ensure_webhook_secret(_Pool(cursor), 1)
        self.assertEqual(secret, "stored-secret")
        self.assertEqual(len(cursor.queries), 2)

    async def test_ensure_secret_retries_one_collision(self):
        cursor = _Cursor([(b"stored-secret",)])
        cursor.failure = IntegrityError(1062, "Duplicate entry")
        secret = await ensure_webhook_secret(_Pool(cursor), 1)
        self.assertEqual(secret, "stored-secret")

    async def test_ensure_secret_gives_up_after_repeated_collisions(self):
        cursor = _Cursor()
        cursor.failure = IntegrityError(1062, "Duplicate entry")
        cursor.always_fail = True
        with mock.patch(
            "bale_service.repository.secrets.token_urlsafe",
            return_value="same-secret",
        ):
            with self.assertRaises(RuntimeError):
                await ensure_webhook_secret(_Pool(cursor), 1)


if __name__ == "__main__":
    unittest.main()
