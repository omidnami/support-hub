# Support Hub

A minimal project-scoped Socket.IO relay. One process, live messages only.
No message history, Redis, queues, customer accounts, operators or bot adapters.

## Database

Three tables: users, projects and sources. Users own projects; projects own
sources. The project token identifies a socket connection. A source is looked
up by its type (name) and robot_id, not by the sender of a message.

Use a fresh MariaDB database for this new implementation. This migration does
not drop or convert any previous tables. User and project records are currently
created manually; there is no registration API in this step. Store password
hashes in users.password, and use distinct random tokens in projects.token.

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
