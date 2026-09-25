"""
Antigravity OAuth constants and API endpoints.

These are public OAuth credentials — the same ones used by Google's
Gemini CLI, opencode-antigravity-auth, and the Cloud Code extension.
They are not secret and are safe to include in source code.
"""

import os

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
LOAD_CODE_ASSIST_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
ONBOARD_URL = "https://cloudcode-pa.googleapis.com/v1internal:onboardUser"

DEFAULT_CALLBACK_PORT = 8121
DEFAULT_ROUTER_PORT = int(os.environ.get("NINEROUTER_PORT", "20128"))
DEFAULT_USER_AGENT = "antigravity/1.15.8 windows/amd64"
GOOG_API_CLIENT = "google-cloud-sdk vscode_cloudshelleditor/0.1"
