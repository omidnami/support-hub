import secrets
from dataclasses import dataclass

from pymysql.err import IntegrityError


@dataclass(frozen=True)
class BaleSource:
    id: int
    project_id: int
    robot_id: str
    token: str
    webhook_secret: str
    last_update_id: int | None


def _decode(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


def _source(row):
    if not row:
        return None
    source = BaleSource(
        id=int(row[0]),
        project_id=int(row[1]),
        robot_id=_decode(row[2]),
        token=_decode(row[3]),
        webhook_secret=_decode(row[4]) if row[4] else "",
        last_update_id=int(row[5]) if row[5] is not None else None,
    )
    return source if source.robot_id and source.token else None


SOURCE_COLUMNS = "id, project_id, robot_id, token, webhook_secret, last_update_id"


async def list_bale_sources(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"SELECT {SOURCE_COLUMNS} FROM sources "
                "WHERE name = 'bale' AND token IS NOT NULL AND token <> '' "
                "ORDER BY id"
            )
            rows = await cursor.fetchall()
    return [source for source in (_source(row) for row in rows) if source]


async def ensure_webhook_secret(pool, source_id):
    """Create a webhook secret once, then return the stored value."""
    for _ in range(5):
        generated = secrets.token_urlsafe(32)
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "UPDATE sources SET webhook_secret = %s "
                        "WHERE id = %s AND name = 'bale' "
                        "AND (webhook_secret IS NULL OR webhook_secret = '')",
                        (generated, source_id),
                    )
                    await cursor.execute(
                        "SELECT webhook_secret FROM sources "
                        "WHERE id = %s AND name = 'bale'",
                        (source_id,),
                    )
                    row = await cursor.fetchone()
            if row and row[0]:
                return _decode(row[0])
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                continue
            raise
    raise RuntimeError("Could not generate a unique Bale webhook secret.")


async def find_source_by_secret(pool, secret):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"SELECT {SOURCE_COLUMNS} FROM sources "
                "WHERE name = 'bale' AND webhook_secret = %s",
                (secret,),
            )
            return _source(await cursor.fetchone())


async def find_source_by_robot(pool, robot_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"SELECT {SOURCE_COLUMNS} FROM sources "
                "WHERE name = 'bale' AND robot_id = %s",
                (str(robot_id),),
            )
            return _source(await cursor.fetchone())


async def advance_last_update_id(pool, source_id, update_id):
    """Atomically advance the dedup cursor; False means another worker won."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE sources SET last_update_id = %s "
                "WHERE id = %s AND name = 'bale' "
                "AND (last_update_id IS NULL OR last_update_id < %s)",
                (update_id, source_id, update_id),
            )
            return bool(cursor.rowcount)
