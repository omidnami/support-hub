"""Forward-only, checksum-verified MariaDB schema migrations."""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import aiomysql

DIRECTORY = Path(__file__).resolve().parent.parent / "migrations"
NAME = re.compile(r"[0-9]{4,}_[a-z0-9_]+\.sql\Z")
HISTORY = """
CREATE TABLE IF NOT EXISTS migrations (
    name VARCHAR(190) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


@dataclass(frozen=True)
class Migration:
    name: str
    checksum: str
    sql: str


def discover(directory=DIRECTORY):
    migrations = []
    numbers = set()
    for path in sorted(Path(directory).glob("*.sql"), key=lambda p: p.name):
        if not NAME.fullmatch(path.name) or len(path.name) > 190:
            raise ValueError(f"Invalid migration filename: {path.name}")
        number = int(path.name.split("_", 1)[0])
        if number in numbers:
            raise ValueError(f"Duplicate migration number: {number}")
        numbers.add(number)
        data = path.read_bytes()
        sql = data.decode("utf-8").strip()
        if not sql:
            raise ValueError(f"Empty migration: {path.name}")
        migrations.append((number, Migration(path.name, hashlib.sha256(data).hexdigest(), sql)))
    if not migrations:
        raise ValueError("No migrations found")
    return [migration for _, migration in sorted(migrations)]


def pending(migrations, applied):
    known = {migration.name: migration.checksum for migration in migrations}
    for name, checksum in applied.items():
        if name not in known:
            raise ValueError(f"Applied migration missing from repository: {name}")
        if known[name] != checksum:
            raise ValueError(f"Applied migration was modified: {name}")
    # Reject inserting a migration before an already applied one.
    found_pending = False
    result = []
    for migration in migrations:
        if migration.name not in applied:
            found_pending = True
            result.append(migration)
        elif found_pending:
            raise ValueError("Migration history is out of order; append new migrations only")
    return result


async def history(cur):
    await cur.execute(
        "SELECT 1 FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='migrations'")
    if not await cur.fetchone():
        return {}
    await cur.execute("SELECT name, checksum FROM migrations")
    return {row["name"]: row["checksum"] for row in await cur.fetchall()}


async def status(pool, directory=DIRECTORY):
    migrations = discover(directory)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            applied = await history(cur)
            pending(migrations, applied)
            return [(migration.name, migration.name in applied) for migration in migrations]


async def migrate(pool, directory=DIRECTORY):
    migrations = discover(directory)
    completed = []
    # Advisory locks are connection-scoped; hold the same connection throughout.
    # The selected database scopes the lock on a shared MariaDB server.
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT DATABASE() AS db")
            database = (await cur.fetchone())["db"]
            if not database:
                raise RuntimeError("A target database is required")
            lock = "hub-migrate:" + hashlib.sha256(database.encode()).hexdigest()[:48]
            await cur.execute("SELECT GET_LOCK(%s, 30) AS acquired", (lock,))
            if (await cur.fetchone())["acquired"] != 1:
                raise RuntimeError("Could not acquire database migration lock")
            try:
                applied = await history(cur)
                remaining = pending(migrations, applied)
                if not applied:
                    await cur.execute(HISTORY)
                for migration in remaining:
                    # One SQL statement per file. MariaDB DDL may auto-commit.
                    # Migration statements must be safe to retry after interruption.
                    await cur.execute(migration.sql)
                    await cur.execute(
                        "INSERT INTO migrations (name, checksum) VALUES (%s, %s)",
                        (migration.name, migration.checksum))
                    completed.append(migration.name)
            finally:
                try:
                    await cur.execute("SELECT RELEASE_LOCK(%s)", (lock,))
                    await cur.fetchone()
                except BaseException:
                    conn.close()
                    raise
    return completed
