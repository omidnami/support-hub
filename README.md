# Support Hub

A minimal, headless, multi-tenant client-to-client communication gateway.
First milestone (v0.1.0 development): native WebSocket, authenticated endpoints,
peer routing, Redis outbox and retry/ACK. No FastAPI, UI or permanent chat history.

## Local development

Python 3.12 recommended, and a running Redis instance.

    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    python -c 'import secrets; print(secrets.token_hex(32)); print(secrets.token_hex(32))'

Replace both placeholders in .env with different generated secrets. Then:

    chmod 600 .env
    source .env
    python -m support_hub

Connect to ws://127.0.0.1:8765/ws using a WebSocket client.
Send these JSON frames (replace tokens and generate a unique UUID per message).

    {"type":"auth","tenant":"demo","endpoint":"admin","token":"<admin secret>"}
    {"type":"auth","tenant":"demo","endpoint":"public","token":"<public secret>"}

The two endpoints connect separately. From admin send:

    {"type":"message","id":"57bd71e8-bb21-4e40-a9d9-bc8883973a4c","to":"public","payload":{"text":"سلام"}}

The sender receives accepted with a delivery_id; the recipient receives a message.
The recipient must ACK the actual delivery_id **after processing/persisting locally**:

    {"type":"ack","delivery_id":"<64-character delivery_id>"}

Messages have at-least-once semantics until ACK or expiry (5 hours by default).
The recipient must deduplicate by delivery_id. A sender retries with the same UUID
and unchanged payload if it didn't receive accepted. A dedup receipt remains until
the original TTL but contains no message payload. accepted means queued in Redis,
not delivered. ack_ok removed=false means already absent or not owned by this endpoint.
Expiry is best-effort retention; this MVP does not send expiry notifications to the sender.

## Tests

    python -m unittest discover -s tests -v
    HUB_TEST_REDIS_URL=redis://127.0.0.1:6379/15 python -m unittest discover -s tests -v

Use a dedicated disposable test Redis instance/DB. Integration tests create unique
tenant keys and clean them up. The second command exercises real Redis and WebSocket.

## Scope and deployment

Single process, one active socket per tenant endpoint; endpoints are provisioned in
environment variables for development only. Peer allowlists are server-side; a client
cannot select its source or another tenant. Do not expose privileged static credentials
in public storefront JavaScript. Browser short-lived scoped credentials, origin policy,
rate limits and MariaDB-backed tenant/channel provisioning are future work before
public deployment. Each destination has a 1000 pending-message limit; a full queue
returns destination_queue_full. Maximum inbound frame size is 64 KiB.
Use restricted Redis access, noeviction and appropriate AOF persistence if pending
messages must survive Redis restarts. Configure storage, expiry and backup policy
according to the temporary nature of message payloads.

Target: existing WHM/cPanel account planet, localhost service managed by systemd,
Apache reverse proxy and cPanel AutoSSL. No changes to the hosting server are included
here. Redis must be isolated from other applications via credentials/key ACLs as appropriate.
Do not expose this development milestone publicly without completing production controls.

## Database mode and channels (second development milestone)

Create a MariaDB database and restricted database user first using cPanel. Set the
HUB_DB_* variables in .env and generate a Fernet key with:

    python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

Keep this key private and backed up separately from the database. Never commit .env.
Database mode overrides static endpoints.

    source .env
    python -m support_hub.admin init-db
    python -m support_hub.admin add-client shop1 admin --peer public
    python -m support_hub.admin add-client shop1 public --peer admin
    python -m support_hub.admin add-channel shop1 admin telegram
    python -m support_hub

Provision with local CLI or the optional central management API below. Client tokens are
random and hashed. Channel credentials use Fernet authenticated encryption.
Each channel has a virtual destination ch-<channel ID> authorized only for its owner.
The HTTP server is on localhost:8766 and WebSocket on localhost:8765.
For incoming provider callbacks proxy /webhook/ to 8766; do not log secret callback
paths at Apache or any other proxy. /health can be checked locally.

Register a real callback only after SSL and proxy are working:

    python -m support_hub.admin set-webhook shop1 <channel-id> https://hub.example.com

This changes the registered bot webhook, so use a dedicated test bot.
Telegram verifies both secret path and provider secret header. Bale and Rubika
use unique random secret paths. Their contracts must be tested with real bots.
Eitaa is outbound text only using the application API; inbound not implemented.
WhatsApp and inbound Eitaa are not implemented. No media transfer in this milestone.

An incoming webhook returns success only after Redis accepts the normalized event.
The owner replies with a message to ch-<channel ID>, payload:
    {"type":"text","external_id":"123456","text":"hello"}

Provider sends retry on temporary errors and honor HTTP 429 Retry-After when
present. Permanent rejection is removed and reported via a best-effort delivery_status
event; sent denotes provider acceptance, not customer read/delivery. A provider may
receive a duplicate if its send succeeded but the Redis ACK failed. The sweep is
single-process, bounded concurrency, and checks all active channels; load testing,
durable delivery receipts and scoped browser credentials remain required before public deployment. No permanent customer
directory, bot-per-process model, or UI. Provider access from the hosting network
must be verified separately.

Disable using local commands:
    python -m support_hub.admin disable-channel shop1 <channel-id>
    python -m support_hub.admin disable-client shop1 admin

Disabled channels stop being swept; their pending data expire normally. This does
not unregister an upstream webhook. Disabled clients cannot send further frames; the delivery loop also checks authorization.


## Central management API

Run init-db on existing installations to apply the idempotent MariaDB migration.
Set HUB_MANAGEMENT_KEY to a unique random secret (32+ characters); routes are
disabled if absent. Only the trusted central backend may possess this key.
It grants access across tenants; never ship it to stores or browsers.
Use HTTPS through Apache or loopback, with Authorization: Bearer <management-key>.

| Method | Path | JSON body |
| --- | --- | --- |
| GET | /v1/tenants/{tenant}/clients | — |
| POST | /v1/tenants/{tenant}/clients | {"endpoint":"admin","peers":["public"]} |
| GET | /v1/tenants/{tenant}/channels | — |
| POST | /v1/tenants/{tenant}/channels | {"owner_endpoint":"admin","platform":"telegram","token":"..."} |
| POST | /v1/tenants/{tenant}/clients/{endpoint}/disable | — |
| POST | /v1/tenants/{tenant}/channels/{id}/disable | — |
| POST | /v1/tenants/{tenant}/channels/{id}/register | — |

New client secrets are returned once; list routes never return secrets.
Set HUB_PUBLIC_BASE_URL before webhook registration. Disable does not unregister
a provider webhook. Proxy /v1/ to the HTTP service only when needed; prefer
calling from a trusted central backend over loopback.

## Instagram text messaging

Official Instagram Login, professional accounts, one Meta app with a shared
callback /webhook/instagram. Configure HUB_META_APP_SECRET and HUB_META_VERIFY_TOKEN;
configure the same verification token and subscribe to messages in the Meta
dashboard. POST callbacks are checked using HMAC-SHA256 on the raw request
before routing by the unique professional account ID. Register the individual
account after app-level setup. Add the channel with an account_id (string) and
Instagram user access token. Permissions: instagram_business_basic and
instagram_business_manage_messages. App review/advanced access may be needed.
API version is configurable (default v27.0); real account validation pending.
Manual token provisioning only, no OAuth login or automatic token refresh.
Follow platform messaging windows and permissions; no unsolicited messages.

## Initial security limits

Process-local limits: 2000 concurrent WebSocket handlers, 80 new connections/s
(burst 160), 30 frames/s/socket (burst 60), HTTP 200/s (burst 400), management
5/s (burst 20). These defaults are not capacity measurements or DDoS protection.
Configure Apache access logs to exclude secret callback paths and verify-token
query strings. App HTTP access logging is disabled; errors are redacted.
Browser origins are denied by default; HUB_ALLOWED_ORIGINS permits specified
origins but never exposes a privileged token. Scoped short-lived browser
credentials and multi-user browser routing remain future work.
No public production deployment until further testing and hardening.

## CI and cPanel deployment templates

The GitHub Actions test job uses disposable Redis and MariaDB service containers.
It does not use live provider credentials. Provider callbacks and sends still
need explicit tests with real bots/accounts.

For a local integration run, export HUB_TEST_DB=1 and HUB_DB_* for a dedicated
test database, HUB_ENCRYPTION_KEY for a disposable Fernet key, and
HUB_TEST_REDIS_URL for test Redis. Never point integration tests at production.

Example files are in deploy/. They are not installed automatically. Adapt the
systemd unit to the actual Python venv and keep .env mode 600 outside the public
document root. Use cPanel's supported Apache userdata SSL vhost include mechanism
for the chosen subdomain; validate the generated configuration before reloading.
Do not replace the PlanetScripts virtual host or change global PHP settings.
Keep management loopback-only unless there is a verified reason to expose it.
Check both /health on localhost:8766 and the WS handshake before registering a bot.
