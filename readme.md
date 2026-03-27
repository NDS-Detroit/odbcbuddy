# ODBC Buddy

A lightweight Flask API that proxies SQL queries to SQL Server over ODBC. Packages the notoriously fiddly ODBC driver setup (Microsoft ODBC Driver 17, OpenSSL/TLS configuration) into a single Docker container so you only have to get it working once.

## Setup

### 1. Configure your connection string

Create a `consts.py` file in the project root:

```python
ODB_CONN_STR = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=your-server;DATABASE=your-db;UID=your-user;PWD=your-password"
```

This file is gitignored.

### 2. Run with Docker

```bash
docker-compose up --build -d
```

The service will be available on port **54321**.

### 3. OpenSSL configuration (if needed)

If your SQL Server requires legacy TLS, you may need to adjust `/etc/ssl/openssl.cnf` on the host (the Docker image handles this internally):

```ini
[system_default_sect]
MinProtocol = TLSv1
CipherString = DEFAULT@SECLEVEL=1
```

## API

**`GET /test`** — Test the database connection.

```bash
curl http://localhost:54321/test
```

**`POST /query`** — Execute a SQL query and return results as JSON.

```bash
curl -X POST http://localhost:54321/query \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT TOP 10 * FROM your_table"}'
```

**`GET /`** — Health check.

## Production deployment

For persistent deployment, you can run the app directly with a virtualenv, systemd, and nginx instead of Docker:

```bash
python3 -m venv .env
source .env/bin/activate
pip install -r requirements.txt
```

Use `wsgi:app` as the Gunicorn entry point. See the systemd and nginx documentation for configuring a reverse proxy with a Unix socket.

## Security

This service executes arbitrary SQL queries. It should **not** be exposed to the public internet. Restrict access to trusted IPs via firewall rules or an nginx allowlist.
