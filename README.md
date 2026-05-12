# Teams Presence Tracker

A lightweight CLI tool that polls your Microsoft Teams presence via the Microsoft Graph API and stores a local history of availability changes.

Unlike the local WebSocket monitor (which only knows if you're in a meeting), this tool tracks your actual Teams status: **Available**, **Busy**, **Away**, **DoNotDisturb**, **BeRightBack**, **Offline**, etc.

## Features

- **Multi-user tracking** — monitor your own presence or a list of colleagues.
- **OAuth2 Device Code flow** — no client secret needed; you authenticate interactively once and the tool caches refresh tokens.
- **CSV-based history** — all presence changes are appended to a simple CSV file (no database server required).
- **CLI commands** — `run`, `history`, and `export`.
- **Cross-platform** — built in Rust for Windows, macOS, and Linux.

## Prerequisites

1. **Rust** (1.70+) installed.
2. A **Microsoft Azure AD app registration** with the following configured:
   - **Supported account types**: Accounts in any organizational directory (multitenant) and personal Microsoft accounts
   - **Mobile and desktop applications** platform added with the redirect URI: `https://login.microsoftonline.com/common/oauth2/nativeclient`
   - **API permissions** granted:
     - `Presence.Read` (for your own presence)
     - `Presence.Read.All` (required to track other users)
     - `offline_access` (to get refresh tokens)
     - `User.Read` (to read your profile)
   - The permissions must be **consented** by the user (or admin) before first run.
   - **Note:** `Presence.Read.All` almost always requires **admin consent**. If you only track yourself, it is not strictly used, but it is requested in the scope so the app works for both modes.

> You can create the app registration at [https://portal.azure.com](https://portal.azure.com) → **App registrations** → **New registration**.

## Build

```bash
cargo build --release
```

The binary will be at:

- Windows: `target/release/teams-presence-tracker.exe`
- Linux/macOS: `target/release/teams-presence-tracker`

## Usage

All commands require your Azure AD **Client ID**:

```bash
# Using a flag
 teams-presence-tracker --client-id <YOUR_CLIENT_ID> run

# Or via environment variable
$env:TEAMS_CLIENT_ID="<YOUR_CLIENT_ID>"
 teams-presence-tracker run
```

### Commands

#### `run`

Starts polling Teams presence every **N** seconds (default: 60).

**Track yourself:**
```bash
 teams-presence-tracker --client-id <YOUR_CLIENT_ID> run --interval 30
```

**Track multiple users:**
```bash
 teams-presence-tracker --client-id <YOUR_CLIENT_ID> run \
  --users user1@company.com,user2@company.com \
  --interval 30
```

On first run you will see a device-code prompt:

```
=== Microsoft Authentication Required ===
To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code ABCD-EFGH to authenticate.
=========================================
```

Open the URL, enter the code, and sign in. The tool caches tokens locally so you only need to do this once.

While running, the tool prints changes like:

```
[2026-05-12 09:15:00] You status: Available / Available
[2026-05-12 09:22:00] user1@company.com changed: Available / Available → Busy / InACall
[2026-05-12 09:45:00] user2@company.com changed: Away / Away → Available / Available
```

#### `history`

Show recent presence records from the local CSV store.

```bash
 teams-presence-tracker --client-id <YOUR_CLIENT_ID> history --limit 20
```

#### `export`

Copy the full history CSV to another location.

```bash
 teams-presence-tracker --client-id <YOUR_CLIENT_ID> export --output my_history.csv
```

## Data Storage

Everything is stored in your operating system's user data directory:

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\teams-presence-tracker\` |
| macOS    | `~/Library/Application Support/teams-presence-tracker/` |
| Linux    | `~/.local/share/teams-presence-tracker/` |

Files:

- `presence_history.db` — the CSV file containing all recorded presence states.
- `token_cache.json` — cached OAuth2 tokens (treat this as sensitive).

## Security Notes

- The `token_cache.json` contains your refresh token. **Do not commit it** or share it.
- The `.gitignore` in this repo already excludes build artifacts and local data files.
- This tool uses **delegated permissions** (your identity) and only reads your own presence.

## Roadmap / Ideas

- Add a `serve` subcommand to expose a small web dashboard from the local CSV.
- Support Graph change notifications (webhooks) instead of polling.
- Filter history by date range.
- Aggregate daily/weekly presence statistics.
