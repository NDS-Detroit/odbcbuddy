# ODBC Buddy

ODBC Buddy is a small web service that runs SQL queries against a SQL Server
database and hands back the results as JSON.

It exists because connecting to that database directly is fiddly — it needs a
specific Microsoft ODBC driver and some legacy TLS/OpenSSL settings. ODBC Buddy
gets all of that working once, in one place. Other programs then just send it a
query over HTTP instead of each one setting up the driver themselves.

Because it can run **any** query against a database that holds private client
information, every request to the data endpoints must carry a secret key. Requests
without the right key are turned away before the database is even opened.

## How the pieces fit together (production)

The live service runs on the `micalc.org` server and answers at
`https://flask.micalc.org`. The moving parts:

- **The app** — `app.py` (a Flask app). `wsgi.py` is the entry point.
- **The process** — run by systemd as the service `gunicorn-flask.service`, under
  your user account. It listens on a local socket (`/run/gunicorn-flask.sock`), not a
  public port.
- **The front door** — nginx receives HTTPS on `flask.micalc.org` and forwards
  requests to that socket.
- **The secrets** — `consts.py` (kept out of git) holds the database connection
  string and the API key. See [Configuration](#configuration).

## Endpoints

- **`POST /query`** — run a SQL query, get JSON back. **Requires the key.**
- **`GET /test`** — quick "can I reach the database?" check. **Requires the key.**
- **`GET /`** — health check. No key, returns no data. Safe for uptime monitors.

Example query:

```bash
curl -X POST https://flask.micalc.org/query \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: THE-SECRET-KEY" \
  -d '{"query": "SELECT TOP 10 * FROM your_table"}'
```

A successful response looks like `{"status": "success", "results": [ ... ]}`.
A request with a missing or wrong key gets back
`{"status": "error", "message": "unauthorized"}` with HTTP status **401**.

## How the key works

Every request to `/query` and `/test` must include a header:

```
X-Api-Key: THE-SECRET-KEY
```

The service compares that against the key it was configured with (see below). The
check happens **before** any database connection is opened, and the comparison is
constant-time so the key can't be guessed by timing.

There's also an on/off switch, `REQUIRE_AUTH`:

- **On (the default, and the normal state):** a missing or wrong key is rejected
  with a 401.
- **Off ("permissive"):** requests are still allowed through, but each one that
  lacks a valid key is written to the log as `PERMISSIVE: allowing ...`. This mode
  exists only for the short window while you're updating the programs that call this
  service — it lets you watch the log and confirm every caller is sending the key
  before you switch enforcement back on. **Don't leave it off**; the database is
  exposed while it is.

If the service is set to enforce but has no key configured at all, it rejects
everything (it fails safe rather than falling open).

## Configuration

Create a `consts.py` in the project root (it is gitignored, so the real values
never land in git):

```python
# Connection string for the SQL Server database.
ODB_CONN_STR = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=your-server;DATABASE=your-db;UID=your-user;PWD=your-password"

# The shared secret callers must send in the X-Api-Key header.
API_KEY = "a-long-random-string"

# Whether to enforce the key. Leave it True (or omit it) in normal operation.
# Set it to False only briefly while migrating callers (see below).
REQUIRE_AUTH = True
```

Both settings can also come from environment variables, which win over `consts.py`
if set: `DEFENDERDATA_API_KEY` and `DEFENDERDATA_REQUIRE_AUTH`.

Generate a fresh key with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(36))"
```

## Deploying an update

The service runs as your user, so you don't need root to restart it. After you
change the code or `consts.py`, tell the running service to reload:

```bash
kill -HUP "$(systemctl show gunicorn-flask.service -p MainPID --value)"
```

`-HUP` is a "reload yourself" signal — gunicorn re-reads the code and config without
dropping the service. Before reloading after a code change, it's worth confirming the
app still imports cleanly:

```bash
cd ~/odbcbuddy && .env/bin/python -c "import app; print('ok')"
```

## Turning the key requirement on (or rotating it) without downtime

If callers aren't sending the key yet, switching enforcement on all at once would
break them. Do it in three steps instead:

1. **Prepare, permissively.** Put the key in `consts.py` and set
   `REQUIRE_AUTH = False`, then reload. Nothing breaks — callers with or without the
   key both still work — and the log starts flagging callers that aren't sending the
   key yet (`PERMISSIVE: allowing ...`).
2. **Update the callers.** Point each program that uses this service at the key.
   Watch the log until the `PERMISSIVE` lines from your own callers stop.
3. **Enforce.** Set `REQUIRE_AUTH = True` and reload. From now on, anything without
   the key gets a 401.

To roll back, set `REQUIRE_AUTH = False` and reload again.

Check the log while you work:

```bash
journalctl -u gunicorn-flask.service --since "10 min ago" | grep -E "PERMISSIVE|unauthorized"
```

## Running it locally (Docker)

For local development the app can run in Docker, which bundles the ODBC driver and
TLS settings for you:

```bash
docker-compose up --build -d
```

It listens on port **54321**. Point requests at `http://localhost:54321` and send the
same `X-Api-Key` header. If your SQL Server needs legacy TLS and you run outside
Docker, you may have to add this to `/etc/ssl/openssl.cnf`:

```ini
[system_default_sect]
MinProtocol = TLSv1
CipherString = DEFAULT@SECLEVEL=1
```

## Security

This service can run arbitrary SQL against a database of private client information,
so treat it as sensitive:

- Keep the key requirement **on**. Rotate the key by generating a new one, updating
  `consts.py` here and in every caller, and reloading.
- The key is the main protection. As an extra layer you can also limit which machines
  can reach it — for example an nginx `allow`/`deny` list on the `/query` and `/test`
  locations, or a firewall rule. (That needs root and can't easily cover people
  working from changing home IP addresses, so it's a complement to the key, not a
  replacement.)
- Never commit `consts.py` or the key.
