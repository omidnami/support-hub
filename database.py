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
                    (user_id, project_name, domain, description, token.encode("ascii")),
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
            await cursor.execute("SELECT id FROM projects WHERE token = %s", (token.encode("utf-8"),))
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


async def migrate():
    pool = await open_database()
    try:
        sql = (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                for statement in sql.split(";"):
                    if statement.strip():
                        await cursor.execute(statement)
    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(migrate())
    print("Migration completed.")
