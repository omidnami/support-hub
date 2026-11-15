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

Next: MariaDB clients/channels/options with encrypted credentials; client and channel
lifecycle management; HTTP API/webhook receiver; shared adapters for Bale, Rubika,
Telegram, EitaaYar, Instagram and later WhatsApp. Each adapter uses the same routing
contract; no per-bot files/processes and no permanent customer directory in the hub.
