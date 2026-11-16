import asyncio
import hashlib
import os
import secrets
from pathlib import Path

import aiomysql


async def open_database():
    return await aiomysql.create_pool(
        host=os.getenv("HUB_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("HUB_DB_PORT", "3306")),
        user=os.environ["HUB_DB_USER"],
        password=os.environ["HUB_DB_PASSWORD"],
        db=os.environ["HUB_DB_NAME"],
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


async def register_user(pool, name, email, phone, password, project_name,
                        domain=None, description=None):
    token = secrets.token_hex(16)
    password_hash = await asyncio.to_thread(hash_password, password)
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
                    "INSERT INTO projects (user_id, name, domain, description, token) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user_id, project_name, domain, description, token),
                )
            await conn.commit()
        except BaseException:
            try:
                await conn.rollback()
            except BaseException:
                conn.close()
                raise
            raise
    return token


async def authenticate_project(pool, token):
    if not isinstance(token, str) or not token:
        return None
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM projects WHERE token = %s", (token,))
            row = await cursor.fetchone()
            return row[0] if row else None


async def find_source_project(pool, name, robot_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT project_id FROM sources WHERE name = %s AND robot_id = %s",
                (name, str(robot_id)),
            )
            row = await cursor.fetchone()
            return row[0] if row else None


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


async def update_source_token(pool, project_id, source_id, robot_id, token):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE sources SET token = %s, robot_id = %s WHERE id = %s AND project_id = %s",
                (token, robot_id, source_id, project_id),
            )
            if cursor.rowcount:
                return True
            # An unchanged token and robot ID are also a successful update.
            await cursor.execute(
                "SELECT id FROM sources WHERE id = %s AND project_id = %s",
                (source_id, project_id),
            )
            return (await cursor.fetchone()) is not None


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
