"""
Google OAuth token exchange and Antigravity project discovery.
Pure urllib — no external dependencies.
"""

import json
import time
import urllib.parse
import urllib.request

from constants import (
    CLIENT_ID, CLIENT_SECRET, SCOPES,
    TOKEN_URL, USERINFO_URL,
    LOAD_CODE_ASSIST_URL, ONBOARD_URL,
    DEFAULT_USER_AGENT, GOOG_API_CLIENT,
)


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a fresh access token."""
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_user_info(access_token: str) -> dict:
    """Fetch Google user profile (email, name, picture)."""
    req = urllib.request.Request(USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _api_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
        "X-Goog-Api-Client": GOOG_API_CLIENT,
    }


def _extract_project(data: dict) -> str | None:
    project = data.get("cloudaicompanionProject", "")
    if project.startswith("projects/"):
        project = project.split("/", 1)[1]
    return project or None


def discover_project(access_token: str) -> str | None:
    """Call loadCodeAssist, fallback to onboardUser if needed."""
    meta = {
        "ideType": "ANTIGRAVITY",
        "platform": "PLATFORM_UNSPECIFIED",
        "pluginType": "GEMINI",
    }
    headers = _api_headers(access_token)

    # Try loadCodeAssist first
    body = json.dumps({"metadata": meta}).encode()
    req = urllib.request.Request(LOAD_CODE_ASSIST_URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            project = _extract_project(json.loads(resp.read()))
            if project:
                return project
    except Exception:
        pass

    # Fallback: onboard
    onboard_body = json.dumps({"tierId": "legacy-tier", "metadata": meta}).encode()
    for _ in range(3):
        req2 = urllib.request.Request(ONBOARD_URL, data=onboard_body, headers=headers)
        try:
            with urllib.request.urlopen(req2, timeout=30) as resp:
                project = _extract_project(json.loads(resp.read()))
                if project:
                    return project
        except Exception:
            pass
        time.sleep(2)
    return None
