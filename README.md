# ag-login-inject

Bulk-provision Google accounts into [9router](https://github.com/9-router/9router) for Antigravity AI model access (`ag/claude-*`, `ag/gemini-*`).

Automates the same Google OAuth flow used by [Antigravity CLI](https://github.com/nichochar/antigravity), Gemini CLI, and Cloud Code extensions — but headlessly handles login, consent screens, and token injection at scale.

## What it does

```
Playwright (headed Chromium)
  → Google OAuth login (email + password + consent)
  → Localhost callback captures auth code
  → Exchange for access_token + refresh_token
  → Discover/provision Antigravity project (loadCodeAssist API)
  → Insert/update into 9router's providerConnections SQLite DB
```

After injection, the accounts are immediately available as `ag/` model connections in 9router.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | Uses `str \| None` union syntax |
| [9router](https://github.com/9-router/9router) | Must be installed and run at least once |
| Google account(s) | Workspace or personal Gmail |
| Display server | Google blocks headless login — needs a visible browser (or `--headless` with Xvfb on Linux) |

## Installation

```bash
git clone https://github.com/rullxd/ag-login-inject.git
cd ag-login-inject
pip install -r requirements.txt
playwright install chromium
```

## Quick start

```bash
# Interactive menu — prompts for everything
python main.py

# Batch: inject 20 accounts from a file
python main.py -b emails.txt

# Parallel: 3 browsers at once (~12s/account vs ~30s sequential)
python parallel.py -b emails.txt -w 3
```

## Password handling

Passwords are resolved in this order (first wins):

1. `--password` / `-p` flag
2. `AG_PASSWORD` environment variable
3. Interactive prompt (visible while typing)

The password is **never stored on disk**. For batch mode with many accounts sharing the same Workspace password:

```bash
# Option 1: flag (visible in shell history — use for testing only)
python main.py -b emails.txt -p "password"

# Option 2: env var (recommended for scripts)
AG_PASSWORD="password" python main.py -b emails.txt

# Option 3: prompted (most secure)
python main.py -b emails.txt
# → Password (shared for all accounts): mypassword
```

## Usage

### Interactive menu

```bash
python main.py
```

| # | Action |
|---|--------|
| 1 | Show all Antigravity connections |
| 2 | Add single account |
| 3 | Batch login from .txt file |
| 4 | Test all connections via 9router API |
| 5 | Delete connection(s) — single, range, comma list, or all |
| 6 | Check quota & model locks per account |

### Sequential batch

```bash
python main.py -b emails.txt           # skip existing accounts
python main.py -b emails.txt --no-skip # re-auth all
```

### Parallel batch

```bash
python parallel.py -b emails.txt -w 3
```

- `-w` = concurrent workers (browsers). **3 recommended**, 5 max on most machines.
- Each worker uses a unique callback port (8121, 8122, 8123, ...).
- Approximately 3x faster than sequential.

### Email list format

```
user1@example.com
user2@example.com
# commented-out@example.com  (skipped)
```

See `emails.example.txt` for a template.

## How Google login is automated

The script launches a **headed** Chromium browser (Google blocks headless) and handles:

1. **Email input** → clicks `#identifierNext`
2. **Password input** → clicks `#passwordNext`
3. **Intermediary screens** — Workspace welcome page, Terms of Service, security prompts. Scrolls down and clicks through common button labels (English + Indonesian).
4. **OAuth consent** — checks "Select all" scopes, clicks "Continue" / "Allow"
5. **Callback** — captures the authorization code from `localhost:8121/callback`

## Configuration

| Env variable | Default | Description |
|---|---|---|
| `AG_PASSWORD` | — | Password for all accounts |
| `NINEROUTER_DIR` | Auto-detected | Path to 9router data directory |
| `NINEROUTER_PORT` | `20128` | 9router HTTP API port |

### Headless mode (Linux servers)

Google blocks real headless Chromium. On Linux servers without a display, use `--headless` to auto-start a virtual framebuffer (Xvfb):

```bash
# Install Xvfb
sudo apt install xvfb
pip install xvfbwrapper

# Run headless
python main.py -b emails.txt --headless
python parallel.py -b emails.txt -w 3 --headless
```

The browser still runs "headed" internally, but renders to a virtual screen — invisible to the user, but appears real to Google.

### 9router data directory auto-detection

| OS | Default path |
|----|-------------|
| Windows | `%APPDATA%\9router` |
| macOS | `~/Library/Application Support/9router` |
| Linux | `$XDG_CONFIG_HOME/9router` or `~/.config/9router` |

## File structure

```
constants.py       OAuth credentials & API endpoints
google_auth.py     Token exchange, user info, project discovery
login.py           Playwright Google login automation
router_db.py       9router SQLite read/write operations
main.py            Interactive CLI + sequential batch
parallel.py        Parallel batch injector
```

## FAQ

**Q: Why does it open a browser window?**
Google blocks automated login from headless browsers. The script needs a visible Chromium instance. On Linux servers, use `--headless` to run inside a virtual display (Xvfb).

**Q: Are the OAuth credentials safe to have in source code?**
Yes. These are the same **public** OAuth credentials used by Antigravity CLI, [opencode-antigravity-auth](https://github.com/NoeFabris/opencode-antigravity-auth), and Google Cloud Code extensions. They are not secret.

**Q: What if an account shows "unavailable" status?**
Use menu option 4 ("Test all connections") to trigger a token refresh via the 9router API.

**Q: Can I use this with 2FA-enabled accounts?**
Not automatically. The script handles password-only login. For 2FA accounts, you'll need to manually complete the challenge in the browser window.

**Q: Port 8121 is in use — what do I do?**
The parallel mode auto-assigns unique ports (8121 + worker index). For single mode, you can modify `DEFAULT_CALLBACK_PORT` in `constants.py`.

## License

MIT — see [LICENSE](LICENSE).
