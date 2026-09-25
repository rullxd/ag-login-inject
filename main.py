"""
ag-login-inject — Interactive CLI & batch runner.

Usage:
  python main.py                          # interactive menu
  python main.py -p "password"            # pre-set password
  python main.py -b emails.txt -p "pass"  # non-interactive batch

Password input (in order of precedence):
  1. --password / -p flag
  2. AG_PASSWORD environment variable
  3. Interactive prompt (visible)
"""

import os
import sys
import time

from constants import DEFAULT_CALLBACK_PORT
from google_auth import exchange_code, get_user_info, discover_project
from login import playwright_login, stop_virtual_display, enable_virtual_display
from router_db import (
    ROUTER_DB, RouterNotFoundError,
    check_router_installed,
    get_all_connections, get_existing_emails,
    inject_connection, delete_connection, test_connection,
    get_all_api_connections,
)

# ── ANSI colors (auto-disabled when not a terminal) ──
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

if sys.platform == "win32":
    os.system("")  # enable ANSI on Windows 10+

import re
import unicodedata

class C:
    RST = "\033[0m" if _USE_COLOR else ""
    BOLD = "\033[1m" if _USE_COLOR else ""
    DIM = "\033[2m" if _USE_COLOR else ""
    BORDER = "\033[96m" if _USE_COLOR else ""
    TITLE = "\033[93;1m" if _USE_COLOR else ""
    PRIMARY = "\033[96;1m" if _USE_COLOR else ""
    CAT = "\033[95;1m" if _USE_COLOR else ""
    NUM = "\033[93m" if _USE_COLOR else ""
    PROMPT = "\033[92m" if _USE_COLOR else ""
    MUTED = "\033[90m" if _USE_COLOR else ""
    OK = "\033[92m" if _USE_COLOR else ""
    WARN = "\033[93m" if _USE_COLOR else ""
    ERR = "\033[91m" if _USE_COLOR else ""
    BOX = {"tl": "╔", "h": "═", "tr": "╗", "v": "║",
           "ml": "╠", "mr": "╣", "bl": "╚", "br": "╝",
           "tm": "╦", "bm": "╩", "mm": "╬"}

VERSION = "1.1.0"
REPO_URL = "https://github.com/rullxd/ag-login-inject"


def _vlen(s):
    """Visible length (strip ANSI, count wide chars)."""
    plain = re.sub(r'\033\[[0-9;]*m', '', str(s))
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in plain)


def _pad(s, width):
    return str(s) + ' ' * max(0, width - _vlen(s))


def _c(text, *codes):
    return "".join(codes) + str(text) + C.RST


def clr():
    if _USE_COLOR:
        os.system("cls" if os.name == "nt" else "clear")


def banner():
    b = C.BOX
    W = 56
    title = f"ag-login-inject v{VERSION}"
    sub = f"{REPO_URL}"
    pipe = f"{C.BORDER}{b['v']}{C.RST}"
    print(f"\n  {C.BORDER}{b['tl']}{b['h'] * W}{b['tr']}{C.RST}")
    print(f"  {pipe}{_pad('  ' + _c(title, C.BOLD, C.TITLE), W)}{pipe}")
    print(f"  {pipe}{_pad('  ' + _c(sub, C.MUTED), W)}{pipe}")
    print(f"  {C.BORDER}{b['bl']}{b['h'] * W}{b['br']}{C.RST}")


def ask_password(prompt: str = "Password: ") -> str:
    """Prompt for password (visible input)."""
    return input(f"  {prompt}").strip()


def get_password(global_pw: str | None, prompt: str = "Password: ") -> str | None:
    """Resolve password from flag → env → interactive prompt."""
    pw = global_pw or os.environ.get("AG_PASSWORD", "")
    if pw:
        return pw
    return ask_password(prompt) or None


def preflight_check():
    """Check prerequisites before running. Exits with helpful message if something is wrong."""
    errors = []

    # Check 9router
    try:
        check_router_installed()
    except RouterNotFoundError as e:
        errors.append(str(e))

    # Check Playwright
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        errors.append(
            "Playwright is not installed.\n"
            "  Install it with:\n"
            "    pip install playwright\n"
            "    playwright install chromium"
        )

    if errors:
        print(f"\n{C.ERR}Preflight check failed:{C.RST}\n")
        for e in errors:
            print(f"  {C.ERR}✗{C.RST} {e}\n")
        sys.exit(1)


# Module-level headless flag (set by main() from --headless arg)
_HEADLESS = False


# ── Core pipeline ──

def process_account(email: str, password: str, port: int = DEFAULT_CALLBACK_PORT,
                    idx: int = 0, total: int = 0, headless: bool | None = None,
                    retries: int = 2) -> dict | None:
    """Full pipeline: OAuth login → tokens → project → inject. Auto-retries on failure."""
    tag = f"[{idx}/{total}]" if total else ""

    for attempt in range(1, retries + 2):  # 1 try + N retries
        try:
            if attempt > 1:
                sys.stdout.write(f"  {C.WARN}{tag} {email}  retry {attempt - 1}/{retries}{C.RST}  ")
            else:
                sys.stdout.write(f"  {C.PRIMARY}{tag}{C.RST} {email}  ")
            sys.stdout.flush()

            code, redirect_uri = playwright_login(email, password, port, headless=headless if headless is not None else _HEADLESS)
            sys.stdout.write("code ✓  ")

            tokens = exchange_code(code, redirect_uri)
            access_token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                print(f"{C.ERR}no refresh_token (account may need re-consent){C.RST}")
                if attempt <= retries:
                    time.sleep(3)
                    continue
                return None
            sys.stdout.write("token ✓  ")

            info = get_user_info(access_token)
            actual_email = info.get("email", email)

            project_id = discover_project(access_token)
            sys.stdout.write(f"project={project_id or '?'}  ")

            conn_id, action = inject_connection(
                actual_email, access_token, refresh_token, project_id,
                tokens.get("expires_in", 3599),
            )
            print(f"{C.OK}{action} ✓{C.RST}")
            return {"email": actual_email, "project": project_id, "id": conn_id, "action": action}

        except Exception as e:
            print(f"{C.ERR}FAIL: {e}{C.RST}")
            if attempt <= retries:
                wait = 3 * attempt
                sys.stdout.write(f"  {C.MUTED}waiting {wait}s before retry...{C.RST}\n")
                time.sleep(wait)

    return None


# ── Menu actions ──

def menu_show():
    clr(); banner()
    conns = get_all_connections()
    if not conns:
        print(f"  {C.WARN}No Antigravity connections found.{C.RST}\n")
        input("  Press Enter..."); return

    print(f"  {C.BOLD}Antigravity Connections ({len(conns)}){C.RST}\n")

    # Auto-size email column
    max_email = max(len(c["email"]) for c in conns)
    ew = max(max_email, 20)

    print(f"  {'#':>4}  {'Email':{ew}s}  {'Status':12s}  {'Project':20s}  {'Updated':20s}")
    print(f"  {'─'*4}  {'─'*ew}  {'─'*12}  {'─'*20}  {'─'*20}")
    for i, c in enumerate(conns, 1):
        sc = CG if c["status"] == "active" else CY if c["status"] == "unavailable" else CR
        act = "●" if c["active"] else "○"
        print(f"  {i:4d}  {c['email']:{ew}s}  {sc}{act} {c['status']:10s}{C.RST}  {c['project']:20s}  {c['updated'][:19]}")
    print()
    input("  Press Enter...")


def menu_add_single(global_pw: str | None):
    clr(); banner()
    print(f"  {C.BOLD}Add Single Account{C.RST}\n")
    email = input("  Email: ").strip()
    if not email: return
    password = get_password(global_pw)
    if not password: return
    print()
    try:
        process_account(email, password)
    except Exception as e:
        print(f"  {C.ERR}FAIL: {e}{C.RST}")
    print()
    input("  Press Enter...")


def menu_batch_file(global_pw: str | None):
    clr(); banner()
    print(f"  {C.BOLD}Batch Login from File{C.RST}\n")
    filepath = input("  Email list file (.txt): ").strip().strip('"')
    if not filepath or not os.path.isfile(filepath):
        print(f"  {C.ERR}File not found.{C.RST}")
        input("  Press Enter..."); return

    with open(filepath) as f:
        emails = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not emails:
        print(f"  {C.WARN}No emails in file.{C.RST}")
        input("  Press Enter..."); return

    existing = get_existing_emails()
    new = [e for e in emails if e not in existing]
    old = [e for e in emails if e in existing]

    print(f"\n  Total: {C.BOLD}{len(emails)}{C.RST}  Already in 9router: {C.WARN}{len(old)}{C.RST}  New: {C.OK}{len(new)}{C.RST}\n")
    target = emails if (old and input("  Re-auth existing too? (y/N): ").strip().lower() == "y") else new
    if not target:
        print(f"  {C.WARN}Nothing to process.{C.RST}")
        input("  Press Enter..."); return

    password = get_password(global_pw, f"Password (shared for {len(target)} accounts): ")
    if not password: return

    print(f"\n  {C.BOLD}Starting: {len(target)} accounts{C.RST}\n")
    ok, fails = [], []
    for i, email in enumerate(target, 1):
        r = process_account(email, password, idx=i, total=len(target))
        if r:
            ok.append(r)
        else:
            fails.append(email)
        time.sleep(1)

    print(f"\n  {C.OK}OK: {len(ok)}{C.RST}  {C.ERR}FAIL: {len(fails)}{C.RST}")
    if fails: print(f"  Failed: {', '.join(fails)}")
    print()
    input("  Press Enter...")


def menu_test():
    clr(); banner()
    conns = get_all_connections()
    if not conns:
        print(f"  {C.WARN}No connections.{C.RST}")
        input("  Press Enter..."); return

    print(f"  {C.BOLD}Testing {len(conns)} connections via 9router API...{C.RST}\n")
    ok = fail = 0
    for c in conns:
        sys.stdout.write(f"  {c['email']:30s}  ")
        sys.stdout.flush()
        result = test_connection(c["id"])
        if result.get("valid"):
            print(f"{C.OK}valid ✓{C.RST}"); ok += 1
        else:
            err = result.get("error", "unknown")
            if "Connection refused" in err or "URLError" in err:
                err = "9router not running?"
            print(f"{C.ERR}invalid ✗  {err}{C.RST}"); fail += 1
    print(f"\n  Valid: {C.OK}{ok}{C.RST}  Invalid: {C.ERR}{fail}{C.RST}\n")
    input("  Press Enter...")


def menu_delete():
    clr(); banner()
    conns = get_all_connections()
    if not conns:
        print(f"  {C.WARN}No connections.{C.RST}")
        input("  Press Enter..."); return

    print(f"  {C.BOLD}Delete Connection{C.RST}\n")
    for i, c in enumerate(conns, 1):
        print(f"  {i:4d}  {c['email']}")
    print(f"  {'─'*40}")
    print(f"  {C.MUTED}Enter number, range (5-10), comma list (1,3,5), 'all', or 0 to cancel{C.RST}")

    choice = input("\n  Delete: ").strip().lower()
    if not choice or choice == "0": return

    to_del = []
    if choice == "all":
        to_del = conns
    elif "-" in choice:
        try:
            s, e = choice.split("-")
            to_del = conns[int(s)-1:int(e)]
        except Exception:
            print(f"  {C.ERR}Invalid range.{C.RST}"); input("  Press Enter..."); return
    else:
        try:
            nums = [int(x.strip()) for x in choice.split(",")]
            to_del = [conns[n-1] for n in nums if 0 < n <= len(conns)]
        except Exception:
            print(f"  {C.ERR}Invalid input.{C.RST}"); input("  Press Enter..."); return

    if not to_del: return
    print(f"\n  Will delete {C.ERR}{len(to_del)}{C.RST} connection(s):")
    for c in to_del: print(f"    - {c['email']}")
    if input(f"\n  Confirm? (y/N): ").strip().lower() != "y": return

    for c in to_del:
        delete_connection(c["id"])
        print(f"  {C.ERR}Deleted{C.RST} {c['email']}")
    print()
    input("  Press Enter...")


def menu_quota():
    from datetime import datetime as dt, timezone
    clr(); banner()
    print(f"  {C.BOLD}Antigravity Quota & Model Locks{C.RST}\n")

    all_conns = get_all_api_connections()
    conns = [c for c in all_conns if c.get("provider") == "antigravity"]
    if not conns:
        print(f"  {C.WARN}No Antigravity connections found.{C.RST}")
        print(f"  {C.MUTED}Make sure 9router is running (check with: curl http://localhost:20128/health){C.RST}")
        input("\n  Press Enter..."); return

    now = dt.now(timezone.utc)
    lock_keys = sorted({k for c in conns for k in c if k.startswith("modelLock_")})

    for i, c in enumerate(conns, 1):
        email = c.get("email") or c.get("name") or c.get("id", "?")[:20]
        ts = c.get("testStatus", "?")
        sc = CG if ts == "active" else CR if ts == "unavailable" else CY
        af = f"{C.OK}ON{C.RST}" if c.get("isActive") else f"{C.ERR}OFF{C.RST}"
        print(f"  {C.BOLD}{i:3d}{C.RST}  {email:40s}  {af}  {sc}{'●' if ts=='active' else '✗'} {ts}{C.RST}")

        if c.get("errorCode"):
            print(f"       {C.ERR}Error {c['errorCode']}: {(c.get('lastError') or '')[:80]}{C.RST}")

        active_locks = []
        for lk in lock_keys:
            val = c.get(lk)
            if val is None: continue
            model = lk.replace("modelLock_", "")
            try:
                until = dt.fromisoformat(val.replace("Z", "+00:00"))
                if until > now:
                    rem = int((until - now).total_seconds() // 60)
                    h, m = divmod(rem, 60)
                    active_locks.append(f"{model} ({h}h{m}m)" if h else f"{model} ({m}m)")
            except Exception:
                active_locks.append(f"{model} ({val})")
        if active_locks:
            print(f"       {C.WARN}Locked: {', '.join(active_locks)}{C.RST}")

    # Summary
    total = len(conns)
    ok = sum(1 for c in conns if c.get("testStatus") == "active")
    bad = sum(1 for c in conns if c.get("testStatus") == "unavailable")
    enabled = sum(1 for c in conns if c.get("isActive"))

    if lock_keys:
        print(f"\n  {C.BOLD}Model Lock Summary:{C.RST}")
        for lk in lock_keys:
            model = lk.replace("modelLock_", "")
            free = sum(1 for c in conns if _is_free(c.get(lk), now))
            bar_len = 30
            fb = int(free / total * bar_len) if total else 0
            bar = f"{C.OK}{'█' * fb}{C.RST}{C.ERR}{'█' * (bar_len - fb)}{C.RST}"
            fc = CG if free > 0 else CR
            print(f"    {model:35s}  {bar}  {fc}{free:3d}{C.RST}/{total} free")

    print(f"\n  {C.BOLD}Totals:{C.RST} {C.OK}{ok} OK{C.RST}  {C.ERR}{bad} unavail{C.RST}  |  {enabled}/{total} enabled\n")
    input("  Press Enter...")


def _is_free(val, now):
    from datetime import datetime as dt
    if val is None: return True
    try:
        return dt.fromisoformat(val.replace("Z", "+00:00")) <= now
    except Exception:
        return False


# ── Main ──

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Antigravity Google OAuth → 9router token injector",
        epilog=(
            "Password resolution order: --password flag → AG_PASSWORD env var → interactive prompt.\n"
            "The password is never stored on disk."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-p", "--password",
                        help="Google account password (or set AG_PASSWORD env var)")
    parser.add_argument("-b", "--batch",
                        help="Non-interactive batch mode: path to email list file (.txt)")
    parser.add_argument("--no-skip", action="store_true",
                        help="In batch mode: re-auth accounts already in 9router")
    parser.add_argument("--port", type=int, default=None,
                        help="9router port (default: 20128, or NINEROUTER_PORT env var)")
    parser.add_argument("--headless", action="store_true",
                        help="Run on headless Linux server (uses Xvfb virtual display)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Retry failed accounts N times before giving up (default: 2)")
    args = parser.parse_args()

    # Preflight
    preflight_check()

    # Override 9router port if specified
    if args.port:
        import constants
        constants.DEFAULT_ROUTER_PORT = args.port

    # Set module-level headless flag
    global _HEADLESS
    _HEADLESS = args.headless
    if args.headless:
        enable_virtual_display()

    # Resolve password from flag or env
    pw = args.password or os.environ.get("AG_PASSWORD", "")

    # Non-interactive batch mode
    if args.batch:
        if not pw:
            pw = ask_password("Password (shared for all accounts): ")
        if not pw:
            print(f"{C.ERR}Password required for batch mode.{C.RST}")
            print(f"{C.MUTED}Use --password, AG_PASSWORD env var, or enter at prompt.{C.RST}")
            sys.exit(1)

        if not os.path.isfile(args.batch):
            print(f"{C.ERR}File not found: {args.batch}{C.RST}")
            sys.exit(1)

        with open(args.batch) as f:
            emails = [l.strip() for l in f if l.strip() and not l.startswith("#")]

        if not emails:
            print(f"{C.WARN}No emails in file.{C.RST}")
            sys.exit(0)

        existing = get_existing_emails()
        todo = emails if args.no_skip else [e for e in emails if e not in existing]
        skipped = len(emails) - len(todo)

        print(f"\n  {C.PRIMARY}{C.BOLD}ag-login-inject — batch{C.RST}")
        print(f"  {C.MUTED}Total: {len(emails)} | Skipped (existing): {skipped} | To process: {len(todo)}{C.RST}\n")

        if not todo:
            print(f"  {C.MUTED}All accounts already in 9router. Use --no-skip to re-auth.{C.RST}")
            sys.exit(0)

        ok, fails = 0, []
        for i, email in enumerate(todo, 1):
            r = process_account(email, pw, idx=i, total=len(todo), retries=args.retries)
            if r:
                ok += 1
            else:
                fails.append(email)
            time.sleep(1)

        print(f"\n  {C.OK}OK: {ok}{C.RST}  {C.ERR}FAIL: {len(fails)}{C.RST}")
        if fails: print(f"  Failed: {', '.join(fails)}")
        stop_virtual_display()
        sys.exit(1 if fails else 0)

    # Interactive menu
    if pw:
        print(f"  {C.MUTED}Password pre-set via {'--password' if args.password else 'AG_PASSWORD'}{C.RST}")

    while True:
        clr(); banner()
        try:
            conns = get_all_connections()
        except Exception as e:
            print(f"  {C.ERR}Error reading 9router DB: {e}{C.RST}\n")
            input("  Press Enter to retry...")
            continue

        active = sum(1 for c in conns if c["active"])

        print(f"\n  {_c('Connections', C.BOLD, C.CAT)}: {_c(f'{active} active', C.OK)} / {len(conns)} total")
        print(f"  {_c('DB', C.MUTED)}: {_c(ROUTER_DB, C.DIM)}")

        b = C.BOX
        w1 = 30
        w2 = 30
        pipe = f"{C.BORDER}{b['v']}{C.RST}"
        top = f"  {C.BORDER}{b['tl']}{b['h'] * w1}{b['tm']}{b['h'] * w2}{b['tr']}{C.RST}"
        mid = f"  {C.BORDER}{b['ml']}{b['h'] * w1}{b['mm']}{b['h'] * w2}{b['mr']}{C.RST}"
        bot = f"  {C.BORDER}{b['bl']}{b['h'] * w1}{b['bm']}{b['h'] * w2}{b['br']}{C.RST}"

        h1 = f"  {_c('ACCOUNTS', C.BOLD, C.CAT)}"
        h2 = f"  {_c('MANAGE', C.BOLD, C.CAT)}"

        col1 = [
            f" {_c('1', C.NUM, C.BOLD)} Show connections",
            f" {_c('2', C.NUM, C.BOLD)} Add single account",
            f" {_c('3', C.NUM, C.BOLD)} Batch from file",
        ]
        col2 = [
            f" {_c('4', C.NUM, C.BOLD)} Test connections",
            f" {_c('5', C.NUM, C.BOLD)} Delete connection(s)",
            f" {_c('6', C.NUM, C.BOLD)} Quota & model locks",
        ]

        print(f"\n{top}")
        print(f"  {pipe}{_pad(h1, w1)}{pipe}{_pad(h2, w2)}{pipe}")
        print(mid)
        for c1, c2 in zip(col1, col2):
            print(f"  {pipe}{_pad(c1, w1)}{pipe}{_pad(c2, w2)}{pipe}")
        print(bot)
        print(f"  {_c('0', C.ERR, C.BOLD)} Exit\n")

        choice = input(f"  {_c('▶', C.PROMPT)} Pilihan: ").strip()
        if choice == "1": menu_show()
        elif choice == "2": menu_add_single(pw)
        elif choice == "3": menu_batch_file(pw)
        elif choice == "4": menu_test()
        elif choice == "5": menu_delete()
        elif choice == "6": menu_quota()
        elif choice == "0": print(f"\n  {C.MUTED}Bye.{C.RST}\n"); stop_virtual_display(); break


if __name__ == "__main__":
    main()
