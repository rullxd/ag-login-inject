"""
Playwright-based Google OAuth login flow.

Handles email entry, password, intermediary screens
(Workspace welcome, ToS), and OAuth consent automatically.

Headless mode:
  Google blocks real headless Chromium. On Linux servers without a display,
  we use Xvfb (virtual framebuffer) — Playwright runs "headed" but renders
  to a virtual screen. Install: apt install xvfb / pip install xvfbwrapper.
  On Windows/macOS with a display, headless mode is not needed.
"""

import http.server
import os
import sys
import threading
import time
import urllib.parse

from constants import (
    CLIENT_ID, SCOPES, AUTH_URL,
    DEFAULT_CALLBACK_PORT,
)

# Whether to use virtual display (set by enable_virtual_display)
_vdisplay = None


def enable_virtual_display():
    """Start Xvfb virtual display for headless Linux servers.

    Call once before any playwright_login(). No-op on Windows/macOS or
    if DISPLAY is already set. Requires: pip install xvfbwrapper
    """
    global _vdisplay
    if _vdisplay is not None:
        return  # already started

    # Only needed on Linux without a display
    if sys.platform != "linux":
        return
    if os.environ.get("DISPLAY"):
        return

    try:
        from xvfbwrapper import Xvfb
        _vdisplay = Xvfb(width=1280, height=800, colordepth=24)
        _vdisplay.start()
    except ImportError:
        raise RuntimeError(
            "No display found and xvfbwrapper is not installed.\n"
            "  On a headless Linux server, install a virtual display:\n"
            "    sudo apt install xvfb\n"
            "    pip install xvfbwrapper\n"
            "  Or run with a real display (X11/Wayland)."
        )


def stop_virtual_display():
    """Stop the virtual display if one was started."""
    global _vdisplay
    if _vdisplay is not None:
        _vdisplay.stop()
        _vdisplay = None


def build_auth_url(redirect_uri: str, login_hint: str | None = None) -> str:
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _make_handler(code_holder: dict):
    """Create a single-use HTTP handler that captures the OAuth callback code."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/callback":
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    code_holder["code"] = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h2>Login successful! You can close this tab.</h2>")
                    return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_):
            pass

    return Handler


def _check_url_for_code(page, code_holder: dict) -> bool:
    """Check if the browser landed on the callback URL with a code."""
    if code_holder.get("code"):
        return True
    try:
        url = page.url
    except Exception:
        return False
    if "localhost" in url and "code=" in url:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "code" in params:
            code_holder["code"] = params["code"][0]
            return True
    return False


# Button labels for intermediary and consent screens (EN + ID)
_INTERSTITIAL_BUTTONS = [
    "Saya mengerti", "I understand", "Terima", "Accept",
    "Login", "Sign in", "Setuju", "I agree", "Berikutnya", "Next",
]
_CONSENT_BUTTONS = [
    "Continue", "Lanjutkan", "Allow", "Izinkan", "I agree", "Setuju",
]


def playwright_login(email: str, password: str, port: int = DEFAULT_CALLBACK_PORT,
                     headless: bool = False) -> tuple[str, str]:
    """
    Launch a headed Chromium browser, complete Google OAuth login,
    and return (authorization_code, redirect_uri).

    Args:
        headless: If True on a headless Linux server, auto-starts Xvfb
                  virtual display. Google blocks real headless mode, so
                  the browser still runs "headed" inside the virtual display.

    Raises RuntimeError on failure.
    """
    from playwright.sync_api import sync_playwright

    if headless:
        enable_virtual_display()

    redirect_uri = f"http://localhost:{port}/callback"
    auth_url = build_auth_url(redirect_uri, login_hint=email)

    code_holder: dict = {}
    handler_cls = _make_handler(code_holder)
    server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # Always headed — Google blocks headless
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1024, "height": 700},
        )
        page = context.new_page()

        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(
                'input[type="email"]:visible, input[type="password"]:visible',
                timeout=15000,
            )

            # Step 1: Email
            ei = page.locator('input[type="email"]')
            if ei.count() > 0:
                ei.fill(email)
                page.locator("#identifierNext button").click()
                page.wait_for_selector('input[type="password"]:visible', timeout=15000)
            else:
                another = page.locator('text="Use another account"')
                if another.count() > 0:
                    another.click()
                    page.wait_for_selector('input[type="email"]', timeout=10000)
                    page.locator('input[type="email"]').fill(email)
                    page.locator("#identifierNext button").click()
                    page.wait_for_selector('input[type="password"]:visible', timeout=15000)

            # Step 2: Password
            page.locator('input[type="password"]:visible').fill(password)
            page.locator("#passwordNext button").click()
            time.sleep(2)
            page.wait_for_load_state("networkidle", timeout=15000)

            # Step 3: Intermediary screens
            for _ in range(5):
                if _check_url_for_code(page, code_holder):
                    break
                time.sleep(0.5)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                clicked = False
                for txt in _INTERSTITIAL_BUTTONS:
                    btn = page.locator(f'button:has-text("{txt}")')
                    if btn.count() > 0:
                        try:
                            btn.first.scroll_into_view_if_needed()
                            btn.first.click()
                            page.wait_for_load_state("networkidle", timeout=10000)
                            clicked = True
                        except Exception:
                            pass
                        break
                if not clicked:
                    break

            # Step 4: OAuth consent
            for _ in range(5):
                if _check_url_for_code(page, code_holder):
                    break
                # Select all scopes checkbox
                sa = page.locator(
                    'input[type="checkbox"]#selectAll, label:has-text("Select all")'
                )
                if sa.count() > 0:
                    try:
                        sa.first.click()
                    except Exception:
                        pass
                for txt in _CONSENT_BUTTONS:
                    btn = page.locator(f'button:has-text("{txt}")')
                    if btn.count() > 0:
                        try:
                            btn.first.click()
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        break
                sub = page.locator("#submit_approve_access")
                if sub.count() > 0:
                    try:
                        sub.click()
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                if _check_url_for_code(page, code_holder):
                    break
                time.sleep(1)

            # Final wait
            deadline = time.time() + 15
            while time.time() < deadline and not _check_url_for_code(page, code_holder):
                time.sleep(0.3)

        finally:
            context.close()
            browser.close()

    server.server_close()

    code = code_holder.get("code")
    if not code:
        raise RuntimeError("No authorization code received (timeout or login failed)")
    return code, redirect_uri
