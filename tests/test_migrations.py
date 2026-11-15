import tempfile
import unittest
from pathlib import Path

from support_hub.migrations import discover, pending


class MigrationTests(unittest.TestCase):
    def test_discover_and_verify_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "0001_create_table.sql"
            first.write_text("CREATE TABLE IF NOT EXISTS sample (id INT);")
            (root / "0002_add_column.sql").write_text("ALTER TABLE sample ADD COLUMN IF NOT EXISTS n INT;")
            migrations = discover(root)
            applied = {migrations[0].name: migrations[0].checksum}
            self.assertEqual(pending(migrations, applied), migrations[1:])
            self.assertEqual(pending(migrations, {}), migrations)
            with self.assertRaisesRegex(ValueError, "out of order"):
                pending(migrations, {migrations[1].name: migrations[1].checksum})
            with self.assertRaisesRegex(ValueError, "missing"):
                pending(migrations, {"0000_old.sql": "hash"})
            first.write_text("SELECT 1;")
            with self.assertRaisesRegex(ValueError, "modified"):
                pending(discover(root), applied)

    def test_duplicate_number_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("0001_one.sql", "0001_two.sql"):
                (root / name).write_text("SELECT 1;")
            with self.assertRaisesRegex(ValueError, "Duplicate migration number"):
                discover(root)
