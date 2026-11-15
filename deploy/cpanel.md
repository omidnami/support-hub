# cPanel / WHM pilot deployment

This assumes AlmaLinux 9, Python 3.12, Redis 6.2+, MariaDB, and the existing
cPanel account planet. No project code runs as root. No deployment step
automatically changes Redis global configuration or existing websites.

## 1. Shared Redis preflight (root / authorized Redis admin)

Inspect without printing passwords or full ACL lists:

    redis-cli INFO keyspace
    redis-cli ACL USERS
    redis-cli CONFIG GET aclfile

Confirm DB 1 is not allocated elsewhere (an empty DB is not proof). Choose a
unique namespace and new ACL username; the examples are support-hub: and
supporthub. If that user already exists, inspect and coordinate rather than reset.
Generate a random secret locally; do not paste it in chat or commit it.
Use the command in redis-acl.rules.example with a secure interactive Redis
admin connection. Its >password, ~pattern and +select|1 are literal Redis ACL
arguments; quote them if using a shell. The application's credentials do not
need admin commands. Test PING, SET/GET/DEL under the namespace, and denial
of GET for another namespace and FLUSHDB permission (inspect ACL rules;
never run a flush command on a shared server).

Persist the ACL using existing Redis administration practice: ACL SAVE only
if an external aclfile is already configured; otherwise CONFIG REWRITE writes
the effective configuration and ACLs back to the server configuration file.
Review/back up the existing config and confirm no unrelated runtime changes
would be persisted before doing either. No restart is necessary to add the user.
Do not change the default user, eviction policy or persistence on a shared
instance without coordinating other workloads.

The Hub's restricted user cannot access other prefixes, but Redis ACL key
patterns apply across logical databases. Other applications using an unrestricted
default/admin user could still access Hub keys. Use a separate instance or
coordinate restricting all other users for strict mutual isolation. This pilot
does not reconfigure other clients.

## 2. Install as planet

    runuser -u planet -- bash -c 'cd /home/planet && git clone https://github.com/omidnami/support-hub.git && cd support-hub && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip check'

If already cloned, use git pull --ff-only from that directory instead.

## 3. Database (cPanel user interface)

Use Manage My Databases / Database Wizard to create supporthub and a dedicated
supporthub user. cPanel may prefix both with planet_. Assign the user to the
database. Use ALL PRIVILEGES scoped to this dedicated database for the
migration/install step; no server-global privileges are required. Configure
.env first. Migrations create tables, indexes and foreign keys automatically;
no manual phpMyAdmin import is required. Do not run migrations at each start.

## 4. Configuration and checks

As planet, copy deploy/cpanel.env.example to .env (outside the public document
root), chmod 600, and replace placeholders. Database password must be safely
shell-quoted. Generate a new Fernet encryption key using the venv and preserve
it securely; generate a separate random management key. Keep the Redis ACL
password and prefix synchronized with the URL and ACL rule.

    cd /home/planet/support-hub
    source .env
    .venv/bin/python -m support_hub.admin migrate
    .venv/bin/python -m support_hub.admin migrate-status
    .venv/bin/python -m support_hub

From another terminal, check curl --fail http://127.0.0.1:8766/health.
Do not expose the public proxy or register provider webhooks until this passes.
Stop foreground process before installing the systemd unit.

## 5. Service and proxy

Review deploy/support-hub.service.example. Install under /etc/systemd/system/
as root only after the foreground test, then daemon-reload and enable --now.
Check journalctl -u support-hub without exposing secrets. Create the Hub
subdomain and SSL in cPanel; use supported per-vhost Apache userdata includes
and deploy/apache-ssl.conf.example. Check config before reload. Keep internal
ports on localhost and management private. Do not log webhook secret paths.
Provider live tests and load tests are still needed before public production.
