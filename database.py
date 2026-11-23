import asyncio
import hashlib
import os
import secrets
from pathlib import Path

import aiomysql
from pymysql.err import IntegrityError

from config import load_env_file


load_env_file()


def required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def open_database():
    return await aiomysql.create_pool(
        host=os.getenv("HUB_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("HUB_DB_PORT", "3306")),
        user=required_env("HUB_DB_USER"),
        password=required_env("HUB_DB_PASSWORD"),
        db=required_env("HUB_DB_NAME"),
        charset="utf8mb4",
        autocommit=True,
    )


def hash_password(password):
    salt = secrets.token_hex(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def _decode_token(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("ascii")
    return str(value)


async def register_user(pool, name, email, phone, password, project_name,
                        domain=None, description=None, webhook_url=None,
                        webhook_secret=None):
    if webhook_url and not webhook_secret:
        raise ValueError("A webhook secret is required with a webhook URL.")
    token = secrets.token_hex(16)
    password_hash = await asyncio.to_thread(hash_password, password)
    updated_at = "CURRENT_TIMESTAMP" if webhook_url else "NULL"
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO users (name, email, phone, password) VALUES (%s, %s, %s, %s)",
                    (name, email, phone, password_hash),
                )
                user_id = cursor.lastrowid
                await cursor.execute(
                    "INSERT INTO projects (user_id, name, domain, description, token, "
                    "webhook_url, webhook_secret, webhook_updated_at) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, {updated_at})",
                    (user_id, project_name, domain, description, token,
                     webhook_url, webhook_secret),
                )
                project_id = cursor.lastrowid
            await conn.commit()
        except BaseException:
            try:
                await conn.rollback()
            except BaseException:
                conn.close()
                raise
            raise
    return token, project_id


async def authenticate_project(pool, token):
    if not isinstance(token, str) or not token:
        return None
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM projects WHERE token = %s", (token,))
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_project_token(pool, project_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT token FROM projects WHERE id = %s", (project_id,))
            row = await cursor.fetchone()
            return _decode_token(row[0]) if row else None


async def rotate_project_token(pool, project_id):
    for _ in range(5):
        token = secrets.token_hex(16)
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "UPDATE projects SET token = %s WHERE id = %s",
                        (token, project_id),
                    )
                    if cursor.rowcount:
                        return token
                    # An unchanged token is also a successful update.
                    await cursor.execute(
                        "SELECT id FROM projects WHERE id = %s", (project_id,)
                    )
                    return token if await cursor.fetchone() else None
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                continue
            raise
    raise RuntimeError("Could not generate a unique project token.")


async def get_project_webhook(pool, project_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT webhook_url, webhook_secret FROM projects WHERE id = %s",
                (project_id,),
            )
            row = await cursor.fetchone()
            if not row or not row[0] or not row[1]:
                return None
            return _decode_token(row[0]), _decode_token(row[1])


async def set_project_webhook(pool, project_id, url, secret):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE projects SET webhook_url = %s, webhook_secret = %s, "
                "webhook_updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (url, secret, project_id),
            )
            return bool(cursor.rowcount)


async def delete_project_webhook(pool, project_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE projects SET webhook_url = NULL, webhook_secret = NULL, "
                "webhook_updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (project_id,),
            )
            if cursor.rowcount:
                return True
            await cursor.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            return bool(await cursor.fetchone())


async def find_source_project(pool, name, robot_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT project_id FROM sources WHERE name = %s AND robot_id = %s",
                (name, str(robot_id)),
            )
            row = await cursor.fetchone()
            return row[0] if row else None


async def find_source_project_with_token(pool, name, robot_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT p.id, p.token FROM sources s "
                "JOIN projects p ON p.id = s.project_id "
                "WHERE s.name = %s AND s.robot_id = %s",
                (name, str(robot_id)),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return row[0], _decode_token(row[1])


async def register_source(pool, project_id, name, robot_id, token):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO sources (project_id, name, robot_id, token) VALUES (%s, %s, %s, %s)",
                (project_id, name, robot_id, token),
            )
            return cursor.lastrowid


async def update_user(pool, project_id, values):
    values = {key: values[key] for key in ("name", "email", "phone", "password") if key in values}
    if not values:
        return
    if "password" in values:
        values["password"] = await asyncio.to_thread(hash_password, values["password"])
    assignments = ", ".join(f"u.{key} = %s" for key in values)
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE users u JOIN projects p ON p.user_id = u.id SET {assignments} WHERE p.id = %s",
                (*values.values(), project_id),
            )


async def update_project(pool, project_id, values):
    values = {key: values[key] for key in ("name", "domain", "description") if key in values}
    if not values:
        return
    assignments = ", ".join(f"{key} = %s" for key in values)
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE projects SET {assignments} WHERE id = %s",
                (*values.values(), project_id),
            )


async def get_source_name(pool, project_id, source_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT name FROM sources WHERE id = %s AND project_id = %s",
                (source_id, project_id),
            )
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_source_identity(pool, project_id, source_id):
    """Return ``(name, robot_id)`` for cache invalidation before a mutation."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT name, robot_id FROM sources "
                "WHERE id = %s AND project_id = %s",
                (source_id, project_id),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return _decode_token(row[0]), _decode_token(row[1])



async def update_source_token(pool, project_id, source_id, robot_id, token):
    async with pool.acquire() as conn:
        # Token changes invalidate the Bale webhook secret and dedup cursor:
        # both belong to the previous bot. Lock the row while comparing so a
        # concurrent update cannot leave stale Bale state behind.
        await conn.begin()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT token, robot_id FROM sources "
                    "WHERE id = %s AND project_id = %s FOR UPDATE",
                    (source_id, project_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    await conn.rollback()
                    return False
                changed = (
                    _decode_token(row[0]) != token or str(row[1]) != str(robot_id)
                )
                if changed:
                    await cursor.execute(
                        "UPDATE sources SET token = %s, robot_id = %s, "
                        "webhook_secret = NULL, last_update_id = NULL "
                        "WHERE id = %s AND project_id = %s",
                        (token, robot_id, source_id, project_id),
                    )
                else:
                    await cursor.execute(
                        "UPDATE sources SET token = %s, robot_id = %s "
                        "WHERE id = %s AND project_id = %s",
                        (token, robot_id, source_id, project_id),
                    )
            await conn.commit()
            return True
        except BaseException:
            try:
                await conn.rollback()
            except BaseException:
                pass
            raise


async def delete_source(pool, project_id, source_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM sources WHERE id = %s AND project_id = %s",
                (source_id, project_id),
            )
            return bool(cursor.rowcount)


async def migrate():
    pool = await open_database()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                    for statement in path.read_text(encoding="utf-8").split(";"):
                        if statement.strip():
                            await cursor.execute(statement)
    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(migrate())
    print("Migration completed.")
