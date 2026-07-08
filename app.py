"""Runs main app"""
import functools
import hmac
import json
import os
import traceback
from datetime import datetime, time


from flask import Flask, jsonify, request
from flask.json.provider import DefaultJSONProvider
import pyodbc
import consts
from consts import ODB_CONN_STR


app = Flask(__name__)

class CustomJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(obj, time):
            return obj.strftime('%H:%M:%S')
        return super().default(obj)

app.json = CustomJSONProvider(app)


def _env_flag(name, default):
    """Read a boolean env var; fall back to ``default`` when unset."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


# Shared-secret auth for the data routes. The key comes from the environment
# first, then consts.py (gitignored, holds the real value on the server). This
# proxy fronts a live PII database, so it must never be reachable unauthenticated.
API_KEY = os.getenv('DEFENDERDATA_API_KEY') or getattr(consts, 'API_KEY', '') or ''
# Fail closed by default. During the caller-migration window this is set to a
# falsey value (consts.REQUIRE_AUTH = False, or DEFENDERDATA_REQUIRE_AUTH=0) so
# unauthenticated callers are allowed-but-logged; flip it back once every caller
# sends the key.
REQUIRE_AUTH = _env_flag('DEFENDERDATA_REQUIRE_AUTH', getattr(consts, 'REQUIRE_AUTH', True))


def require_api_key(view):
    """Reject requests without a valid ``X-Api-Key`` before touching the DB.

    When ``REQUIRE_AUTH`` is falsey (transition window) an invalid/missing key is
    allowed through but logged, so the access log shows which callers still need
    migrating before enforcement is turned on.
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        provided = request.headers.get('X-Api-Key', '')
        authorized = bool(API_KEY) and hmac.compare_digest(
            provided.encode('utf-8'), API_KEY.encode('utf-8'))
        if authorized:
            return view(*args, **kwargs)
        client = request.headers.get('X-Forwarded-For') or request.remote_addr
        if REQUIRE_AUTH:
            app.logger.warning(
                'unauthorized request to %s from %s (missing/invalid X-Api-Key)',
                request.path, client)
            return jsonify({'status': 'error', 'message': 'unauthorized'}), 401
        app.logger.warning(
            'PERMISSIVE: allowing %s from %s without a valid X-Api-Key',
            request.path, client)
        return view(*args, **kwargs)
    return wrapped


def get_db_connection():
    connection = pyodbc.connect(ODB_CONN_STR)
    return connection

# Define a route to test the connection
@app.route('/test', methods=['GET'])
@require_api_key
def test_connection():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(1) from case_reporting_view")
        result = cursor.fetchone()
        return jsonify({'status': 'success', 'result': result[0]})
    except Exception as e:
        return jsonify({'status': 'error',
            'message': str(e),
            'conn_str': ODB_CONN_STR})

@app.route('/query', methods=['POST'])
@require_api_key
def execute_query():
    data = request.get_json()
    query = data.get('query')
    if not query:
        return jsonify({'status': 'error', 'message': 'No query provided'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in rows]
        return jsonify({'status': 'success', 'results': results})
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.exception("query failed: %r (query=%.500s)", e, query)
        return jsonify({
            'status': 'error',
            'message': repr(e),
            'error_type': type(e).__name__,
            'args': [repr(a) for a in getattr(e, 'args', ())],
            'traceback': tb,
        })
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                app.logger.exception("cursor.close() failed")
        if conn is not None:
            try:
                conn.close()
            except Exception:
                app.logger.exception("conn.close() failed")

@app.route('/')
def index():
    return jsonify({"message": "Hello, Friend!"})

@app.route('/flask')
def flask_route():
    return jsonify({"message": "This is the Flask route."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)

