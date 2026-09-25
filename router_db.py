"""
9router SQLite database operations.

Reads/writes the 9router providerConnections table directly.
Safe for single-writer; use db_lock for concurrent access.
"""

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
import urllib.request
from datetime import datetime, timezone

from constants import SCOPES, DEFAULT_ROUTER_PORT


def _find_router_base() -> str:
    """Find 9router data directory (platform-aware)."""
    # Allow override via environment
    if os.environ.get("NINEROUTER_DIR"):
        return os.environ["NINEROUTER_DIR"]
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "9router")
    # macOS
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/9router")
    # Linux
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg, "9router")


ROUTER_BASE = _find_router_base()
ROUTER_DB = os.path.join(ROUTER_BASE, "db", "data.sqlite")
MACHINE_ID_FILE = os.path.join(ROUTER_BASE, "machine-id")
CLI_SECRET_FILE = os.path.join(ROUTER_BASE, "auth", "cli-secret")

db_lock = threading.Lock()


class RouterNotFoundError(Exception):
    """Raised when 9router installation is not found."""
    pass


def check_router_installed():
    """Verify 9router is installed. Raises RouterNotFoundError if not."""
    if not os.path.isfile(ROUTER_DB):
        raise RouterNotFoundError(
            f"9router database not found at: {ROUTER_DB}\n"
            f"  Make sure 9router is installed and has been run at least once.\n"
            f"  Install: https://github.com/9-router/9router\n"
            f"  Or set NINEROUTER_DIR env var to your 9router data directory."
        )


def get_cli_token() -> str:
    """Derive the 9router CLI auth token from machine-id + cli-secret."""
    for path, name in [(MACHINE_ID_FILE, "machine-id"), (CLI_SECRET_FILE, "cli-secret")]:
        if not os.path.isfile(path):
            raise RouterNotFoundError(
                f"9router {name} not found at: {path}\n"
                f"  9router may not be fully set up. Run it once to initialize."
            )
    mid = open(MACHINE_ID_FILE).read().strip()
    sec = open(CLI_SECRET_FILE).read().strip()
    return hashlib.sha256((mid + "9r-cli-auth" + sec).encode()).hexdigest()[:16]


def inject_connection(
    email: str,
    access_token: str,
    refresh_token: str,
    project_id: str | None,
    expires_in: int = 3599,
) -> tuple[str, str]:
    """Insert or update an Antigravity connection. Returns (id, 'inserted'|'updated')."""
    check_router_installed()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    expires_at = datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    data_obj = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
        "scope": " ".join(SCOPES) + " openid",
        "projectId": project_id or "",
        "testStatus": "active",
        "expiresIn": expires_in,
        "errorCode": None,
        "backoffLevel": 0,
        "lastUsedAt": None,
        "consecutiveUseCount": 0,
        "lastRefreshAt": now,
        "lastError": None,
        "lastErrorAt": None,
        "rateLimitedUntil": None,
    }

    with db_lock:
        conn = sqlite3.connect(ROUTER_DB)
        try:
            existing = conn.execute(
                "SELECT id FROM providerConnections WHERE provider='antigravity' AND email=?",
                (email,),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE providerConnections SET data=?, updatedAt=? WHERE id=?",
                    (json.dumps(data_obj), now, existing[0]),
                )
                conn.commit()
                return existing[0], "updated"
            else:
                conn_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO providerConnections "
                    "(id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
                    "VALUES (?, 'antigravity', 'oauth', ?, ?, 1, 1, ?, ?, ?)",
                    (conn_id, email, email, json.dumps(data_obj), now, now),
                )
                conn.commit()
                return conn_id, "inserted"
        finally:
            conn.close()


def get_all_connections() -> list[dict]:
    """List all Antigravity connections from the DB."""
    check_router_installed()
    conn = sqlite3.connect(ROUTER_DB)
    rows = conn.execute(
        "SELECT id, email, isActive, data, updatedAt "
        "FROM providerConnections WHERE provider='antigravity' ORDER BY email"
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = json.loads(r[3])
        results.append({
            "id": r[0],
            "email": r[1],
            "active": r[2],
            "project": d.get("projectId", ""),
            "status": d.get("testStatus", "?"),
            "updated": r[4],
            "has_refresh": bool(d.get("refreshToken")),
        })
    return results


def get_existing_emails() -> set[str]:
    """Return set of emails already in the DB."""
    check_router_installed()
    conn = sqlite3.connect(ROUTER_DB)
    rows = conn.execute(
        "SELECT email FROM providerConnections WHERE provider='antigravity'"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def delete_connection(conn_id: str):
    """Delete a connection by ID."""
    with db_lock:
        conn = sqlite3.connect(ROUTER_DB)
        conn.execute("DELETE FROM providerConnections WHERE id=?", (conn_id,))
        conn.commit()
        conn.close()


def _router_api(path: str, method: str = "GET") -> dict:
    """Make a request to the 9router HTTP API."""
    token = get_cli_token()
    url = f"http://localhost:{DEFAULT_ROUTER_PORT}{path}"
    req = urllib.request.Request(url, method=method, headers={"x-9r-cli-token": token})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def test_connection(conn_id: str) -> dict:
    """Test a connection via the 9router HTTP API."""
    try:
        return _router_api(f"/api/providers/{conn_id}/test", method="POST")
    except Exception as e:
        return {"valid": False, "error": str(e)}


def get_all_api_connections() -> list[dict]:
    """Fetch all provider connections from the 9router HTTP API."""
    try:
        data = _router_api("/api/providers")
        return data.get("connections", [])
    except Exception:
        return []
