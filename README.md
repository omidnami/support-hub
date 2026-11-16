# Support Hub

A minimal project-scoped Socket.IO relay. One process, live messages only.
No message history, Redis, queues, customer accounts, operators or bot adapters.

## Database

Three tables: users, projects and sources. Users own projects; projects own
sources. The project token identifies a socket connection. A source is looked
up by its type (name) and robot_id, not by the sender of a message.

Use a fresh MariaDB database for this new implementation. This migration does
not drop or convert any previous tables. Users and their first project can be created together through the registration API.
Passwords are hashed; project tokens are stored in projects.token.

## Run

Python 3.12, with MariaDB running:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export HUB_DB_HOST=127.0.0.1
export HUB_DB_PORT=3306
export HUB_DB_NAME=support_hub
export HUB_DB_USER=hub_user
# Set HUB_DB_PASSWORD to the database password in your environment.

python database.py
python server.py
```

The migration is repeatable for this initial schema. The server listens on
127.0.0.1:8765 by default; HUB_HOST and HUB_PORT are optional overrides.
Use a Socket.IO v4 JavaScript client (not a raw WebSocket client).
Socket.IO path: /socket.io/. WebSocket transport only.

```js
const socket = io("http://127.0.0.1:8765", {
    transports: ["websocket"],
    auth: { token: "YOUR_PROJECT_TOKEN" },
    autoConnect: false
});
socket.on("authenticated", ({ project_id }) => console.log(project_id));
socket.on("connect_error", error => console.log(error.message));
socket.on("chat", data => console.log(data));
socket.connect();
// Run after the connect event:
socket.emit("chat", { text: "Hello" });
```

The example assumes a same-origin browser or a trusted Socket.IO client.
The server uses Socket.IO's default origin policy. Invalid project tokens are
rejected; valid connections join their project's room. Custom events are sent
unchanged to all online sockets in that room, including the sender. The routing
project is taken from the authenticated socket, not from event data.
The server reserves the authenticated event for the connection response.

For future source adapters, create_app() returns the app, Socket.IO server and
an async emit_source(name, robot_id, event, *args) function. It resolves the
project from sources and emits only to that project's room. Unknown sources
return False; this is not a delivery receipt. Offline connections do not
receive missed events. No provider integration or server deployment is included.

## Initial registration

POST /api/register on the same HTTP server as Socket.IO.

```json
{
  "name": "Test User",
  "email": "user@example.ir",
  "phone": "09121234567",
  "password": "example-password",
  "project_name": "My Shop",
  "domain": "example.ir",
  "description": "My first project"
}
```

Only domain and description are optional. The user and first project are created
in one database transaction. The response (HTTP 201) contains the generated
project token:

```json
{"status": true, "token": "<32 hex characters>"}
```

Use this token directly in the existing Socket.IO auth object. No separate
HTTP process, dependency or migration is required. This endpoint creates an
initial user/project only; login, duplicate-account policy and additional
project management are not part of this step.

## Source registration

Apply the additional migration with `python database.py` before starting the updated server.

POST /api/sources

Authorization: Bearer <project token>

```json
{"name":"bale","token":"<bot token>","project_id":1}
```

The project ID must match the project token. The server calls the platform to
retrieve the actual robot ID: Bale, Telegram, Rubika and Eitaa (EitaaYar) use
getMe; Instagram Login uses the professional account user_id from /me.
For chat, token and robot_id are identical (no external request).
The new sources.token column stores the supplied source token.
Existing sources are not changed by the migration; duplicate source identities
return 409. Responses never echo the source token. WhatsApp is reserved in the
ENUM but returns 501 until its phone-number-ID registration flow is defined.

```json
{"status":true,"id":1,"name":"bale","robot_id":"123456","project_id":1}
```

Registration only; it does not start polling, register a webhook or change the
Socket.IO relay. Live provider validation has not yet been tested.

## Update and delete

All four endpoints below require the existing header:

Authorization: Bearer <project token>

- PATCH /api/users/me — any subset of name, email, phone, password. The
  account is the owner of the authenticated project; a project token is
  trusted to edit that owner. Password is hashed again when provided.
- PATCH /api/projects/{project_id} — any subset of name, domain, description.
  The ID must match the token. Domain and description can be null. Ownership
  and project token are unchanged.
- PATCH /api/sources/{source_id} — body {"token":"<new source token>"}.
  The ID is the local sources.id, not robot_id. The source must belong to
  the authenticated project. The platform is determined by its existing name.
  The server resolves the new robot ID before updating both fields in one
  SQL statement. Failed validation or duplicate IDs leave the old record
  unchanged. For chat, robot_id is the new token, without an external request.
  WhatsApp is not implemented.
- DELETE /api/sources/{source_id} — deletes only a source belonging to the
  authenticated project. No request body is needed.

Successful user/project changes and source deletion return {"status":true}.
Source token updates return the same fields as source registration, with the
updated robot_id and without a separate token field. No user or project deletion,
project-token rotation, provider webhook changes or polling are included.
