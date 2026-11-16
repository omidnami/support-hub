"""Registration only. Existing client/channel authorization is unchanged."""
import re
import secrets

from aiohttp import web
from pymysql.err import IntegrityError

from .database import digest
from .protocol import decode, ProtocolError
from .security import Bucket

DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def field(body, key, limit, required=False):
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(key)
    value = value.strip()
    if not value and not required:
        return None
    if not value or len(value) > limit or any(
            ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF
            for c in value):
        raise ValueError(key)
    return value


def registration_fields(body):
    name = field(body, "name", 190, True)
    phone = field(body, "phone", 40, True).translate(DIGITS)
    phone = re.sub(r"[ ()-]", "", phone)
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    if re.fullmatch(r"09[0-9]{9}", phone):
        phone = "+98" + phone[1:]
    elif re.fullmatch(r"989[0-9]{9}", phone):
        phone = "+" + phone
    if not re.fullmatch(r"\+?[1-9][0-9]{6,14}", phone):
        raise ValueError("phone")
    phone = "+" + phone.lstrip("+")
    email = field(body, "email", 254)
    if email:
        email = email.lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", email):
            raise ValueError("email")
    domain = field(body, "domain", 253)
    if domain:
        try:
            domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError("domain") from None
        if len(domain) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in domain.split(".")
        ):
            raise ValueError("domain")
    project_name = field(body, "project_name", 190) or (
        domain if domain and len(domain) <= 190 else name)
    return name, phone, email, domain, project_name


async def create_registration(db, name, phone, email, domain, project_name):
    api_key = secrets.token_hex(32)
    # The pool is autocommit by default: explicitly start one transaction.
    async with db.pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO users (name,phone,email,password) VALUES (%s,%s,%s,NULL)",
                    (name, phone, email))
                user_id = cur.lastrowid
                await cur.execute(
                    "INSERT INTO projects (user_id,name,apikey,domain) VALUES (%s,%s,%s,%s)",
                    (user_id, project_name, digest(api_key), domain))
            await conn.commit()
        except BaseException:
            try:
                await conn.rollback()
            except BaseException:
                conn.close()
            raise
    return api_key


def install_registration(app, db):
    rate = Bucket(2, 10)

    async def register(request):
        def failure(message, status):
            return web.json_response({"status": False, "message": message}, status=status)

        if not rate.allow():
            return failure("Too many registration attempts.", 429)
        try:
            body = decode(await request.text())
        except (ProtocolError, UnicodeError):
            return failure("Invalid JSON.", 400)
        try:
            values = registration_fields(body)
        except ValueError as exc:
            return failure("Invalid field: " + str(exc), 422)
        try:
            api_key = await create_registration(db, *values)
        except IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                # Do not issue a new key for an existing, unverified identity.
                return failure("Registration conflicts with an existing record.", 409)
            raise
        return web.json_response({
            "status": True,
            "api_key": api_key,
            "message": "Registration successful."
        }, status=201)

    app.router.add_post("/v1/register", register)
