"""
Parallel batch injector — multiple concurrent Playwright browsers.
Each worker gets a unique callback port.

Usage:
  python parallel.py -p "password" -b emails.txt [-w 5]
  AG_PASSWORD="pass" python parallel.py -b emails.txt -w 3
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from constants import DEFAULT_CALLBACK_PORT
from google_auth import exchange_code, get_user_info, discover_project
from login import playwright_login, stop_virtual_display, enable_virtual_display
from router_db import get_existing_emails, inject_connection, check_router_installed, RouterNotFoundError

import threading
_print_lock = threading.Lock()


def tprint(msg):
    with _print_lock:
        print(msg, flush=True)


# ANSI colors (auto-disabled when piped)
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
if _USE_COLOR:
    C = "\033[0m"; CG = "\033[92m"; CR = "\033[91m"; CC = "\033[96m"; CB = "\033[1m"; CD = "\033[2m"
else:
    C = CG = CR = CC = CB = CD = ""


def worker(email: str, password: str, port: int, headless: bool = False, retries: int = 2) -> dict:
    """Single account: login → exchange → discover → inject. Auto-retries."""
    for attempt in range(1, retries + 2):
        try:
            code, redirect_uri = playwright_login(email, password, port, headless=headless)

            tokens = exchange_code(code, redirect_uri)
            access_token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                if attempt <= retries:
                    time.sleep(3)
                    continue
                raise RuntimeError("no refresh_token — try re-running this account")

            info = get_user_info(access_token)
            actual_email = info.get("email", email)
            project_id = discover_project(access_token)
            conn_id, action = inject_connection(
                actual_email, access_token, refresh_token, project_id,
                tokens.get("expires_in", 3599),
            )
            return {"email": actual_email, "project": project_id, "id": conn_id, "action": action}

        except Exception:
            if attempt <= retries:
                time.sleep(3 * attempt)
                continue
            raise


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Parallel Antigravity batch injector (multiple concurrent browsers)",
        epilog="Password: --password flag → AG_PASSWORD env var → interactive prompt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-p", "--password", help="Google account password (or set AG_PASSWORD)")
    parser.add_argument("-b", "--batch", required=True, help="Email list file (.txt)")
    parser.add_argument("-w", "--workers", type=int, default=5,
                        help="Concurrent browsers (default: 5, recommended: 3)")
    parser.add_argument("--no-skip", action="store_true",
                        help="Re-auth accounts already in 9router")
    parser.add_argument("--port", type=int, default=None,
                        help="9router port (default: 20128, or NINEROUTER_PORT env var)")
    parser.add_argument("--headless", action="store_true",
                        help="Run on headless Linux server (uses Xvfb virtual display)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Retry failed accounts N times before giving up (default: 2)")
    args = parser.parse_args()

    # Preflight
    if args.port:
        import constants
        constants.DEFAULT_ROUTER_PORT = args.port

    try:
        check_router_installed()
    except RouterNotFoundError as e:
        print(f"\n{CR}✗ {e}{C}")
        sys.exit(1)

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print(f"\n{CR}✗ Playwright not installed. Run: pip install playwright && playwright install chromium{C}")
        sys.exit(1)

    # Resolve password
    pw = args.password or os.environ.get("AG_PASSWORD", "")
    if not pw:
        pw = input("  Password (shared for all accounts): ").strip()
    if not pw:
        print(f"{CR}Password required.{C}")
        sys.exit(1)

    # Load emails
    if not os.path.isfile(args.batch):
        print(f"{CR}File not found: {args.batch}{C}")
        sys.exit(1)

    with open(args.batch) as f:
        emails = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    if not emails:
        print(f"{CR}No emails in file.{C}")
        sys.exit(1)

    existing = get_existing_emails()
    todo = emails if args.no_skip else [e for e in emails if e not in existing]
    skipped = len(emails) - len(todo)

    print(f"\n  {CC}{CB}ag-login-inject — parallel{C}")
    print(f"  {CD}Workers: {args.workers} | Total: {len(emails)} | "
          f"Skipped: {skipped} | To process: {len(todo)}{C}\n")

    if not todo:
        print(f"  {CD}All accounts already in 9router. Use --no-skip to re-auth.{C}")
        return

    # Start virtual display if headless
    if args.headless:
        enable_virtual_display()

    ok = fail = 0
    fails = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for i, email in enumerate(todo):
            port = DEFAULT_CALLBACK_PORT + i
            ft = pool.submit(worker, email, pw, port, headless=args.headless, retries=args.retries)
            futures[ft] = email

        for ft in as_completed(futures):
            email = futures[ft]
            try:
                r = ft.result()
                ok += 1
                tprint(f"  {CG}✓{C} [{ok+fail}/{len(todo)}] {r['email']} → {r['action']} (project={r['project']})")
            except Exception as e:
                fail += 1
                fails.append(email)
                tprint(f"  {CR}✗{C} [{ok+fail}/{len(todo)}] {email} → {e}")

    print(f"\n  {'═'*50}")
    print(f"  {CG}OK: {ok}{C}  {CR}FAIL: {fail}{C}  Total: {len(todo)}")
    if fails:
        print(f"  Failed: {', '.join(fails)}")
    print()
    stop_virtual_display()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
