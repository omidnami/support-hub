"""Local provisioning only. No public management endpoint or secret in shell arguments."""
import argparse
import asyncio
import getpass
import secrets
from uuid import uuid4

from .database import Database
from .channels import ADAPTERS
from .channels.instagram import valid_account


async def run(args):
    db = await Database.open()
    try:
        if args.command == "init-db":
            await db.init_schema()
            print("schema initialized")
        elif args.command == "add-client":
            token = secrets.token_hex(32)
            await db.add_client(args.tenant, args.endpoint, token, args.peer)
            print("Client token (save securely; shown once):", token)
        elif args.command == "add-channel":
            token = getpass.getpass("Provider token: ").strip()
            if not token:
                raise ValueError("token required")
            channel_id, hook = uuid4().hex, secrets.token_urlsafe(32)
            credentials = {"token": token, "webhook_secret": hook}
            if args.platform == "instagram":
                account = input("Instagram professional account ID: ").strip()
                if not valid_account(account):
                    raise ValueError("invalid account ID")
                credentials["account_id"] = account
            await db.add_channel(channel_id, args.tenant, args.owner, args.platform, credentials, hook)
            print("Channel ID:", channel_id)
            print("Destination endpoint:", "ch-" + channel_id)
            print("Webhook secret (save securely):", hook)
        elif args.command == "disable-client":
            await db.query("UPDATE clients SET enabled=0 WHERE tenant=%s AND endpoint=%s",
                           (args.tenant, args.endpoint), write=True)
        elif args.command == "disable-channel":
            await db.query("UPDATE channels SET enabled=0 WHERE tenant=%s AND id=%s",
                           (args.tenant, args.id), write=True)
        elif args.command == "set-webhook":
            import aiohttp
            channel = await db.get_channel(args.id)
            if not channel or channel.tenant != args.tenant:
                raise ValueError("channel not found")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
                adapter = ADAPTERS[channel.platform](http)
                await adapter.register(channel, args.base_url.rstrip("/"))
            print("webhook registered")
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    c = sub.add_parser("add-client")
    c.add_argument("tenant")
    c.add_argument("endpoint")
    c.add_argument("--peer", action="append", default=[])
    c = sub.add_parser("add-channel")
    c.add_argument("tenant")
    c.add_argument("owner")
    c.add_argument("platform", choices=sorted(ADAPTERS))
    c = sub.add_parser("disable-client")
    c.add_argument("tenant")
    c.add_argument("endpoint")
    c = sub.add_parser("disable-channel")
    c.add_argument("tenant")
    c.add_argument("id")
    c = sub.add_parser("set-webhook")
    c.add_argument("tenant")
    c.add_argument("id")
    c.add_argument("base_url")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
