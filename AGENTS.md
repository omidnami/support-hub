# AGENTS.md — Project Memory for AI Agents

> حافظه پروژه برای مدل‌ها و ایجنت‌ها. پیش از هر تغییر این فایل را بخوانید و اگر تصمیم معماری، قرارداد یا محدودیت عملیاتی تغییر کرد، همین فایل را هم به‌روزرسانی کنید.

**Last verified:** 2026-11-22 · base implementation commit `e13d931` · 151 tests passing (docs may be newer)

---

## 1. What this project is

Support Hub is a **transport-only relay** for roughly **1,000 stores**.

- **Planet is the system of record** for operators, assignments, conversation history, and offline delivery policy.
- **Hub stores no messages, no operators, no assignments, and no conversation history.**
- Every chat channel uses the **same seven-field envelope**.
- Each store is one logical Hub client identified by `project_id`.
- Hub does not know how many operators a store has.
- **Bale customers never connect to Socket.IO.** Only the independent `bale_service` talks to both Hub and the Bale API.
- Phase one supports **internal chat + private Bale chats only**.

Non-goals for now: group/channel chats, durable queues, message history in Hub, final delivery ACKs, S3/MinIO file storage, Redis adapter, multi-process Hub, WhatsApp/Telegram/Rubika adapters.

---

## 2. Non-negotiable invariants

1. **Never store message content or conversation history in Hub.** Only `users`, `projects`, and `sources` tables exist.
2. **Never accept `project_id` from a message payload.** Derive it from the authenticated project token or from the registered source.
3. **Exactly seven message fields**, no extras:
   `user_id`, `sender`, `text`, `file`, `robot_id`, `timestamp`, `source`.
4. **`sender` must match the connection:** `client`, `operator`, or bridge inbound (`client` only).
5. **Bale customers never connect to Hub.** A browser client may use only a registered `chat` source with a signed `robot_id + user_id`.
6. **One bridge per provider.** Hub rejects a second `bale` bridge, including concurrent connects.
7. **Hub and `bale_service` must each run as a single instance.** There is no Redis adapter or shared room state.
8. **Only one Bale service instance.** Multiple instances can send duplicate replies.
9. **Webhook secret is the only Bale authentication.** Never log it, never return it in public APIs, and mask it in proxy logs.
10. **Do not commit `.env`, bot tokens, project tokens, or webhook secrets.**
11. **Keep changes consistent with existing style.** Run the full test suite and compile step before finishing.
12. **Do not commit unless the user explicitly asks.** (Earlier work was committed externally; keep future changes uncommitted by default.)
13. **Do not silently ACK offline inbound.** If the project room has no operator, Hub sends a signed project webhook to Planet and only ACKs on `2xx`. If the webhook is missing or fails, the inbound message is rejected (`store_offline` / `webhook_failed`). Hub still stores no message or queue.

---

## 3. Runtime topology

```text
Planet / operator frontend
        │  REST + Socket.IO (project token)
        ▼
   Support Hub  ──────────────  project:{project_id} room
        │  bridge:bale room
        │
        ├── signed HTTPS webhook (offline fallback) ──►  Planet ingest
        ▼
   bale_service  ────────────►  Bale Bot API
        ▲
        │  HTTPS webhook
   Bale platform
```

### Socket.IO roles

| Role | Auth | Rooms | Can send |
| --- | --- | --- | --- |
| `operator` | `{token: PROJECT_TOKEN}` | `project:{id}` | `chat` as `operator` |
| `client` | `{robot_id, user_id, signature, expires?}` | `project:{id}` + `chat:[...]` conversation room | `chat` as `client`, only its own `chat` source/identity |
| `bridge` | `{role:"bridge", provider:"bale", token:HUB_BALE_SERVICE_TOKEN}` | `bridge:bale` | `chat` as `client`, only `source=bale` |

Hub emits `authenticated` after successful auth. Chat failures emit `chat_error` and also return `{status:false, message}` as the Socket.IO ACK.

### Event flow

- **Internal inbound:** client → Hub → `project:{id}` + conversation room when an operator is online.
- **Internal outbound:** operator → Hub → `project:{id}` + conversation room.
- **Bale inbound:** `bale_service` → Hub as `sender=client` → `project:{id}` or the project webhook when the room is empty.
- **Offline inbound:** if `project:{id}` has no operator socket, Hub POSTs the signed seven-field message to the project's Planet webhook; only a `2xx` permits the ACK. Without a webhook or on failure, the sender gets `store_offline` / `webhook_failed` and Bale returns `503`.
- **Bale outbound:** operator → Hub as `sender=operator` → `bridge:bale` → `bale_service` → Bale.
- If the Bale bridge is offline, Hub returns `chat_error` and does **not** echo the reply as delivered.

---

## 4. Message contract

```json
{
  "user_id": "501",
  "sender": "client",
  "text": "hello",
  "file": null,
  "robot_id": "987654",
  "timestamp": 1700000000000,
  "source": "bale"
}
```

Rules enforced by `server.message_payload`:

- `user_id`, `robot_id`: non-empty string/int, normalized to string, max 255 chars, no control chars, valid UTF-8.
- `source`: one of `rubika`, `bale`, `telegram`, `whatsapp`, `instagram`, `eitaa`, `chat`.
- `text`: string, max **10,000** chars, valid UTF-8. Empty text is allowed only when `file` is truthy.
- `file`: `null`, a string, or a JSON object. Serialized size max **65,536 bytes**; non-finite numbers rejected.
- `timestamp`: integer Unix **milliseconds**.
- Sender must match the authenticated connection.

Bale inbound always sets:

- `sender`: `"client"`
- `source`: `"bale"`
- `user_id`: `message.chat.id`
- `robot_id`: registered bot ID
- `timestamp`: `message.date * 1000`, falling back to current time
- `file`: compact metadata only (`provider`, `type`, `file_id`, and optional bounded fields)

`chat_id` and `conversation_key` are transport metadata, **not** eighth message fields. `chat_id` is the platform conversation ID (Bale: `message.chat.id`; internal chat: signed `user_id`; future adapters: chat/thread ID). `conversation_key = (project_id, source, robot_id, chat_id)`.

---

## 5. Bale integration

### Source registration

```http
POST /api/sources
Authorization: Bearer PROJECT_TOKEN

{"name":"bale","token":"BOT_TOKEN","project_id":1}
```

- Hub calls Bale `getMe`, stores `robot_id`, raw token, and `project_id` in `sources`.
- `(name, robot_id)` is unique.
- The Bale service reads `sources` **directly from MySQL**; it does not call the Hub REST API.
- The service generates `webhook_secret` (`secrets.token_urlsafe(32)`, 43 chars) and stores it once per active token/robot registration.

### Webhook lifecycle

- URL: `BALE_PUBLIC_BASE_URL + /webhooks/bale/<secret>`
- Bale requires **HTTPS on port 443 or 88**.
- `setWebhook` has **no secret/signature parameter**; the unique URL is the trust boundary.
- Sync loop runs every `BALE_SYNC_INTERVAL` (default 30s), registers new sources, removes webhooks for deleted/changed sources.
- Registration failures retry on the next sync.
- Default aiohttp access log is disabled by `main()`; error paths mask the secret.
- Reverse proxies must route only `/webhooks/bale/<secret>` and must not log that path.

### Inbound

```text
Bale → POST /webhooks/bale/<secret>
     → DB lookup by webhook_secret
     → per-source asyncio lock
     → dedup against sources.last_update_id
     → map to seven-field payload
     → Socket.IO bridge call("chat")
     → advance last_update_id only after Hub ACK
```

Status codes:

| Case | Response |
| --- | --- |
| Forwarded / duplicate / ignored update | `200 {"ok":true}` |
| Bad secret / unknown source | `404` |
| Invalid JSON / bad `update_id` | `400` |
| Body over limit | `413` |
| Hub offline or rejected | `503` |
| Unexpected error | `500` |

Semantics:

- **At-least-once** delivery to Hub: if Hub processes but the ACK is lost, the update may reach operators twice.
- Duplicate `update_id <= last_update_id` is acknowledged but not forwarded.
- Unsupported/service messages advance the cursor and are not forwarded.
- Only `message` updates in **private** chats; groups/channels and bot senders ignored.
- Files are relayed as **metadata only**; no download and no S3 storage.

### Outbound

```text
operator reply → Hub → bridge:bale → bale_service per-bot queue
              → sendMessage / sendPhoto / sendDocument → Bale customer
```

- One ordered `asyncio.Queue` per `robot_id`; queue size 1000.
- Text split at **4,096** chars, preferring newlines.
- File captions split at **1,024** chars; remainder sent as normal messages.
- Bale 429 is retried once using `retry_after` (bounded to 30s).
- On Bale 401, the service re-reads the source token from DB and retries once if it changed.
- Permanent Bale errors are logged and dropped; the operator does not receive a final delivery ACK.
- Empty `file` values (`null`, `""`, `{}`) fall back to text.

### Bale limits and gotchas

- Bale keeps the last **2,000 updates for 24 hours**.
- `getFile` links expire after about **1 hour**.
- `file_id` is bot-specific.
- Bale `sendMessage` text limit is **4,096**; captions are shorter (we use 1,024).
- There is no way to put a secret in `setWebhook`; the URL is the secret.
- Update IDs are per bot. When a source token/robot changes, **clear `webhook_secret` and `last_update_id`** so old-bot state cannot leak into the new bot.
- `bale_service` handles only `message` updates; edits, callbacks, etc. are intentionally ignored in phase one.

### Offline fallback webhook (implemented)

- Per-project webhook in Planet; same rule for Bale, internal chat, Rubika, and future adapters.
- Hub checks whether `project:{id}` has any `operator` socket. If yes, current live emit; if no, Hub POSTs a signed webhook and only ACKs on `2xx`.
- Hub tracks operator presence in memory per project SID set; single-instance only. Client and bridge sockets do not count as operators.
- Hub stores no message or queue. Missing webhook returns `store_offline`; delivery failure returns `webhook_failed`; Bale returns `503` and does not advance `last_update_id`.
- URL/secret live in `projects` (`webhook_url`, `webhook_secret`, `webhook_updated_at`, migration `004`), not in a new table.
- `POST /api/register` accepts an optional `webhook_url`; Hub generates the secret and returns `webhook_url` + `webhook_secret` only once. `POST`/`DELETE /api/projects/{id}/webhook` set/rotate or remove it.
- URL must be HTTPS by default, reject credentials/fragments/private hosts, and use a per-project HMAC secret. Delivery re-resolves DNS; redirects are not followed.
- Payload keeps the seven-field message unchanged; `project_id`, `conversation`, `chat_id`, `delivery_id`, and `event` are transport metadata.
- Headers: `X-Hub-Event`, `X-Hub-Delivery`, `X-Hub-Timestamp`, `X-Hub-Signature-256` = `HMAC-SHA256(secret, timestamp + "." + raw_body)`.
- `chat_id` mapping: Bale `message.chat.id`; internal chat `user_id`; future adapters chat/thread ID. The Bale bridge sends optional `{update_id, chat_id}` as a second Socket.IO argument.
- `delivery_id` is `source:robot_id:update_id` for Bale and a stable hash otherwise. Planet must be idempotent; an ACK lost after a successful write can cause a duplicate.
- Offline-only webhook does not give Planet a complete history for messages seen live by operators. If Planet must be a full system of record, ingest every inbound message, not only offline ones.
- Internal-chat customer offline is still unsolved: storing the inbound message does not deliver the later operator reply to a disconnected client.

---

## 6. Database

Migration order: `migrations/001_initial.sql`, `002_add_source_token.sql`, `003_add_bale_webhook_metadata.sql`, `004_add_project_webhook.sql`.

Core tables:

- `users`: id, name, email, phone, password.
- `projects`: id, user_id, name, domain, description, token (unique), `webhook_url`, `webhook_secret`, `webhook_updated_at`.
- `sources`: id, name enum, robot_id, project_id, token, webhook_secret, last_update_id.
  - unique `(name, robot_id)`
  - unique `webhook_secret`
  - `robot_id` is `utf8mb4_bin`

Important behavior:

- `database.py` migrates an existing database; it does **not** create the database or DB user.
- `database.open_database()` uses `autocommit=True`.
- Hub never stores message content, history, or an offline queue. Project webhook metadata is the only addition.
- `update_source_token()` runs in an explicit transaction, locks the row with `SELECT ... FOR UPDATE`, and when token/robot changes, clears `webhook_secret` and `last_update_id` atomically.
- Source tokens are stored as plaintext. DB/backup security is the installer's responsibility.
- `webhook_secret` and `last_update_id` are **transport metadata**, not message history.

Run migrations:

```bash
.venv/bin/python database.py
```

---

## 7. Repository map

| Path | Responsibility |
| --- | --- |
| `server.py` | Hub REST API + Socket.IO auth, rooms, routing, operator presence, rate limits, bridge support |
| `database.py` | DB pool, migrations, project webhook queries, source token update transaction |
| `webhooks.py` | Webhook URL validation, signed offline payload, delivery ID, HMAC delivery |
| `config.py` | `.env` loading, env flags |
| `bale_service/service.py` | Lifecycle, webhook handler, source sync, dedup, health, outbound dispatcher |
| `bale_service/api.py` | Bale Bot API client, webhook registration, text/caption splitting, send |
| `bale_service/hub.py` | Socket.IO bridge client, reconnect/backoff, inbound call, operator event filter |
| `bale_service/mapping.py` | Bale update → seven-field envelope, attachment metadata |
| `bale_service/repository.py` | Bale source queries, secret generation, dedup cursor updates |
| `bale_service/config.py` | Bale env validation (HTTPS/port/token) |
| `migrations/003_add_bale_webhook_metadata.sql` | `webhook_secret`, `last_update_id`, unique index |
| `migrations/004_add_project_webhook.sql` | project `webhook_url`, `webhook_secret`, `webhook_updated_at` |
| `docs/architecture.md` | Human-facing Persian architecture document |
| `README.md` | Public Persian API/setup/Bale runbook |
| `tests/` | Unit + mocked HTTP/Socket.IO integration tests |

---

## 8. Configuration

Hub:

```dotenv
HUB_DB_HOST=127.0.0.1
HUB_DB_PORT=3306
HUB_DB_NAME=support_hub
HUB_DB_USER=...
HUB_DB_PASSWORD=...
HUB_HOST=127.0.0.1
HUB_PORT=8765
HUB_TRUST_PROXY=0
HUB_LOG_LEVEL=INFO
HUB_BALE_SERVICE_TOKEN=<long-random-shared-secret>
HUB_WEBHOOK_ALLOW_HTTP=0
HUB_WEBHOOK_ALLOW_PRIVATE=0
HUB_WEBHOOK_TIMEOUT=5
```

- `HUB_WEBHOOK_ALLOW_HTTP` and `HUB_WEBHOOK_ALLOW_PRIVATE` are development-only. Both must be `0` in production.
- `HUB_WEBHOOK_TIMEOUT` is seconds (default 5, capped at 30).

Bale service:

```dotenv
HUB_URL=http://127.0.0.1:8765
HUB_BALE_SERVICE_TOKEN=<same-value-as-hub>
BALE_API_BASE=https://tapi.bale.ai
BALE_PUBLIC_BASE_URL=https://support.example.com
BALE_SERVICE_HOST=127.0.0.1
BALE_SERVICE_PORT=8766
BALE_SYNC_INTERVAL=30
BALE_REQUEST_TIMEOUT=15
BALE_HUB_ACK_TIMEOUT=10
BALE_MAX_UPDATE_BYTES=1048576
BALE_WEBHOOK_REQUIRE_HTTPS=1
BALE_DISABLE_WEBHOOK_REGISTRATION=0
BALE_LOG_LEVEL=INFO
```

Local `.env` status at last verification: **`HUB_BALE_SERVICE_TOKEN`, `HUB_URL`, and `BALE_PUBLIC_BASE_URL` were still missing**. The service will not start without the token; webhook registration also requires the public HTTPS URL.

For local development without a public URL:

```dotenv
BALE_DISABLE_WEBHOOK_REGISTRATION=1
# BALE_WEBHOOK_REQUIRE_HTTPS=0 only if you also need an HTTP local URL
```

This disables real Bale webhook registration; the service endpoint can still be tested manually.

---

## 9. Run and verify

```bash
# 1. Install deps (repo already has a .venv)
.venv/bin/pip install -r requirements.txt

# 2. Create the database and DB user once. `database.py` does not create them;
#    see README §1 and set HUB_DB_* in .env first.

# 3. Apply migrations (safe to rerun; MariaDB prints already-exists warnings)
.venv/bin/python database.py

# 4. Start Hub
.venv/bin/python server.py

# 5. In another terminal, start Bale service
.venv/bin/python -m bale_service

# 6. Health checks
curl -sS http://127.0.0.1:8765/healthz
curl -sS http://127.0.0.1:8766/healthz
```

Bale health returns `database`, `hub_connected`, and `sources`; it returns HTTP 503 unless both DB and bridge are healthy.

Compile and test:

```bash
.venv/bin/python -m compileall -q server.py database.py config.py webhooks.py bale_service
.venv/bin/python -m unittest discover -s tests -v
```

CI (`.github/workflows/tests.yml`) runs the same compile and test commands on Python 3.12.

### Manual end-to-end test performed

A real local E2E was run with:

- real Hub process + real MariaDB,
- real `bale_service` process,
- a fake Bale API on localhost,
- a real Socket.IO operator client and a POSTed Bale webhook.

Verified:

1. `setWebhook` called with the generated secret URL.
2. Inbound Bale text reached the operator as a seven-field payload.
3. Replaying the same `update_id` was acknowledged but not forwarded; cursor advanced.
4. Operator reply reached the fake Bale API as `sendMessage` with `chat_id` and text.

The E2E script is **not committed** because it needs a real database and process orchestration. Reproduce it manually when changing the bridge/webhook path.

A second offline-webhook E2E was run with real MariaDB, a real Hub `TestServer`, a local HTTP webhook receiver, and a real Socket.IO bridge/operator. Verified:

1. Registration with `webhook_url` stored the URL and returned a secret once.
2. With no operator, bridge inbound produced a signed `chat.offline` POST with matching HMAC and `delivery_id=bale:<robot>:<update_id>`.
3. With an operator connected, the message reached the operator and no webhook was sent.
4. After deleting the webhook and disconnecting the operator, bridge inbound was rejected with `store_offline` and no cursor advance.

That smoke script is also uncommitted; the durable coverage is in `tests/test_webhooks.py` and `tests/test_socket_integration.py`.

---

## 10. Test map

| File | Covers |
| --- | --- |
| `tests/test_helpers.py` | identifiers, payload validation, bridge metadata, rate limiter, config |
| `tests/test_http_api.py` | registration, webhook registration/set/delete, tokens, client key, JSON errors, rate limits |
| `tests/test_socket_integration.py` | operator/client routing, bridge auth, inbound/outbound, offline webhook fallback, bridge metadata, duplicate/concurrent bridge |
| `tests/test_bale_mapping.py` | Bale update mapping, text/file metadata, splitting, file references |
| `tests/test_bale_api.py` | `setWebhook`, sendMessage/sendPhoto, 429 retry, errors, caption limit |
| `tests/test_bale_webhook.py` | webhook status codes, dedup, ignored updates, Hub failures, health, sync, token rotation, outbound queue |
| `tests/test_bale_repository.py` | row decoding, secret generation, dedup cursor SQL |
| `tests/test_bale_hub.py` | bridge inbound ACK/rejection/timeout, operator event filter |
| `tests/test_bale_config.py` | required token, HTTPS/port rules, registration disable |
| `tests/test_database_source.py` | source token transaction, project webhook queries, Bale state reset, missing source |
| `tests/test_webhooks.py` | URL/SSRF validation, delivery IDs, payload/signature, signed delivery, redirects, timeouts |

**Not in the repo:** integration tests against real MariaDB or the real Bale API. The manual E2E above is the substitute.

---

## 11. Operational runbook

1. Apply migrations `003` and `004` before starting Hub / the Bale service.
2. Set `HUB_BALE_SERVICE_TOKEN` identically in Hub and Bale service.
3. Set `BALE_PUBLIC_BASE_URL` to a public HTTPS origin on port 443/88.
4. Reverse proxy only `/webhooks/bale/<secret>` to `BALE_SERVICE_HOST:BALE_SERVICE_PORT`.
5. Mask/remove the webhook path from proxy access logs.
6. Start exactly one Hub process and one Bale service process.
7. Confirm `/healthz` on both returns HTTP 200.
8. Register a `bale` source; within `BALE_SYNC_INTERVAL` the webhook should be set on Bale.
9. Rotate a source token with `PATCH /api/sources/{id}`; the service clears old webhook state and re-registers on the next sync.
10. Delete a source with `DELETE /api/sources/{id}`; the next sync deletes its Bale webhook.

---

## 12. Known limitations and open decisions

- **Planet persistence/offline policy is not confirmed** beyond "Hub stores nothing." Planet must define retry/history behavior.
- **No durable queue**: if Bale service is down, webhook delivery depends on Bale retries; if Hub is down, webhook returns 503. No Redis/queue yet. The signed offline webhook is implemented, but Hub itself has no retry.
- **Offline-only webhook is not a complete history**: if an operator is online, Planet does not receive the message unless the operator UI also persists it. Ingest all inbound messages if Planet must be the full system of record.
- **Internal-chat customer offline**: the inbound message can be stored by Planet, but a later operator reply to a disconnected client has no Hub history/backlog path yet.
- **No final delivery ACK**: ACK means Hub received the message, not that Bale delivered it.
- **Attachments are metadata only**: no S3/MinIO, no stable download links.
- **Private Bale chats only**: group/channel support needs a separate sender/conversation model.
- **No Redis adapter**: Hub and bridge are single-instance.
- **No operator identity/assignment in Hub**: Planet owns this.
- **No source list GET endpoint**; source management is register/update/delete only.
- **WhatsApp/Telegram/Rubika adapters** are not implemented beyond source-token validation.
- **Bale webhook drift** is not periodically verified with `getWebhookInfo`. If a webhook is deleted outside the service, `_registered` still caches it, so the service will not restore it until restart or a token/robot/URL change. Add periodic verification in a future phase.
- **Long-running idle workers**: one task per bot with outbound traffic; acceptable at ~1,000 stores, revisit if scale grows.

---

## 13. Gotchas and hard-won fixes

- **Bridge connection race:** reserve the provider slot before the first `await`, otherwise two bridges can connect concurrently. Hub also keeps `bridge_connecting` and only routes to fully joined bridges.
- **Bridge offline race:** do not route operator replies to a bridge sid that is still connecting; `bridge_sids` is populated only after joining `bridge:bale`.
- **Source token update:** clear `webhook_secret` and `last_update_id` when token/robot changes. Otherwise old-bot updates are misattributed and new-bot update IDs may be dropped by the old cursor.
- **Do not log the webhook URL:** it contains the only authentication secret. Aiohttp access log is off; error middleware masks the path.
- **Dedup cursor advances only after Hub ACK.** If ACK is lost, duplicates are possible. Planet must tolerate duplicate messages.
- **Bale caption limit is 1,024**, not 4,096. Split captions separately.
- **Bale `file_id` is bot-specific.** Do not reuse across bots.
- **`update_id` is per bot.** Reset it on bot token/robot changes.
- **Empty file objects** (`{}`, `""`) must be treated as no file, otherwise text-only replies fail.
- **`sender` on bridge inbound must be `client`.** Hub derives the project from `source + robot_id`, never from the payload.
- **Offline fallback check is presence-only:** Hub can see whether `project:{id}` has any operator socket, not identity/assignment/count. Any connected operator suppresses the webhook; Planet still owns assignment.
- **Never ACK offline inbound on an empty room.** Only a successful Planet webhook `2xx` permits the ACK; otherwise Bale must keep its cursor unchanged.
- **Offline webhook needs SSRF checks, per-project HMAC, and stable `delivery_id`.** Do not log the URL, secret, or message body. Planet must deduplicate retries.
- **`chat_id` is transport metadata, not an eighth field.** Keep the seven-field payload unchanged; use `conversation_key = (project_id, source, robot_id, chat_id)`.
- **Operator reply does not create a conversation room for Bale**; `user_id` is the Bale chat ID and the bridge is the only delivery path.
- **Queue ordering is per bot**, not global. Cross-bot ordering is irrelevant.
- **429 handling:** one retry only, bounded by `retry_after`; do not loop forever.

---

## 14. Change checklist

Before finishing any change:

- [ ] Did I preserve the seven-field contract and sender/source checks?
- [ ] Did I avoid storing messages/operators/history in Hub?
- [ ] Did I derive project identity from token/source, never payload?
- [ ] Did I keep bridge/models single-instance assumptions?
- [ ] Did I avoid logging tokens or webhook secrets?
- [ ] For offline webhook changes: signed payload, SSRF validation, idempotency key, `chat_id` metadata, and no ACK before Planet `2xx`?
- [ ] Did I update `.env.example`, README, `docs/architecture.md`, and this file if behavior changed?
- [ ] Did I add/update tests for success, rejection, timeout, and duplicate paths?
- [ ] Did I run `compileall` and the full unittest suite?
- [ ] Did I leave changes uncommitted unless asked?

---

## 15. Handoff snapshot (2026-11-21)

- Phase one (internal chat + private Bale) plus the offline Planet webhook are implemented and covered by **151 tests**.
- A manual E2E with real Hub + MariaDB + `bale_service` + fake Bale API passed: webhook registration, inbound relay, dedup, outbound reply.
- Migrations `003` and `004` are applied to the local development database.
- Base code is committed as `8f99611`; later commits include `e13d931`. Current changes are uncommitted.
- Local `.env` still needs `HUB_BALE_SERVICE_TOKEN`, `HUB_URL`, and `BALE_PUBLIC_BASE_URL` before the Bale service can run.
- Real Bale verification is blocked on a real bot token and a public HTTPS endpoint on port 443/88.
- The offline Planet webhook is implemented in `webhooks.py`, `server.py`, and `database.py`; docs are updated in `docs/architecture.md` §8 and README §6.
- Next decisions: Planet persistence/offline policy, whether to ingest all inbound messages or only offline ones, internal-chat offline reply delivery, S3/MinIO attachments, final delivery ACK, and periodic `getWebhookInfo` verification.
