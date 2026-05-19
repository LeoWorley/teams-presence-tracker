# Teams Presence Scraper (Playwright)

A browser-automation alternative to the Graph API approach. No Azure AD app registration required — it logs into `teams.microsoft.com` via a real browser and scrapes presence status from the UI.

## Why this exists

If your organization blocks `Presence.Read.All` (or you can't create Azure AD app registrations), this scraper bypasses both issues by using the same web browser you already use to access Teams.

## Trade-offs vs Graph API

| | Graph API (Rust) | Playwright Scraper |
|---|---|---|
| Setup | Azure AD app + admin consent | Browser login once |
| Reliability | High (official API) | Medium (UI can change) |
| Real-time | Polling every 30-60s | Polling every 30-60s |
| Resource use | Low (lightweight CLI) | Medium (Chromium browser) |
| Multi-user | Easy with `Presence.Read.All` | Harder (must match chat list names) |

## Installation

```bash
cd scraper
pip install -r requirements.txt
python -m playwright install chromium
```

## First run — login & save session

```bash
python teams_scraper.py --setup
```

1. A Chrome window opens with Teams.
2. Log in with your work account.
3. Complete MFA if prompted.
4. Wait until you see your chat list fully loaded.
5. Press **ENTER** in the terminal to save the session.

Your login cookies are saved to `scraper/state/playwright_state.json`. You only need to do this once.

## Run the scraper

**Track yourself:**
```bash
python teams_scraper.py
```

**Track specific users (match names shown in Teams chat list):**
```bash
python teams_scraper.py --users "Diego Zepeda" "Another Colleague"
```

**Track users via config file (no restart needed):**

Edit `scraper/config.json` and add the `users` array:
```json
{
  "users": ["Diego Zepeda", "Another Colleague"],
  ...
}
```

Then run the scraper without `--users`:
```bash
python teams_scraper.py
```

The scraper reloads `config.json` every poll cycle, so you can add or remove users at any time — changes are picked up within ~30 seconds without restarting.

**Aliases for privacy:**

You can map real names to display aliases in `config.json`. The scraper still searches for the real name in Teams, but prints and notifies using the alias:

```json
{
  "aliases": {
    "Ramon Diaz": "?",
    "Diego Zepeda": "Colleague A"
  }
}
```

Output example:
```
[2026-05-15 10:00:00] ? status: busy / busy
[2026-05-15 10:05:00] ? changed: busy / busy -> available / available
```

Aliases apply to CLI output and ntfy notifications. The real name is still stored in the CSV history.

**Disable self-tracking:**

If you only want to track other users and not your own presence, set in `config.json`:
```json
{
  "track_self": false
}
```

Changes are picked up automatically on the next poll cycle.

The scraper polls every 30 seconds and appends changes to:
```
%APPDATA%\teams-presence-tracker\presence_history.csv
```

## Run at startup (Windows Task Scheduler)

You can run the scraper automatically when Windows boots, even if no user is logged in, using the provided setup script.

**Prerequisites:**
1. Run `--setup` once so the browser session is saved:
   ```bash
   python teams_scraper.py --setup
   ```

**Option A: Double-click the batch file (easiest)**

Navigate to `scripts/` and double-click:
```
Setup Scraper Startup.bat
```

A UAC prompt will appear — click **Yes** to grant Administrator rights. The script handles the rest.

**Option B: Run the PowerShell script directly**

```powershell
.\scripts\setup-scraper-task.ps1
```

If you forgot to open PowerShell as Administrator, the script will automatically restart itself with a UAC prompt — no need to do it manually.

**Track multiple users at startup:**

```powershell
.\scripts\setup-scraper-task.ps1 -Users "Diego Zepeda","Another Colleague"
```

**Optional parameters:**
- `-Users "Name1","Name2"` — list of users to track
- `-PythonPath "C:\Path\To\python.exe"` — specify Python executable
- `-ScraperDir "C:\Path\To\scraper"` — specify scraper directory

**Manage the task:**
```powershell
Start-ScheduledTask -TaskName "TeamsPresenceScraper"
Stop-ScheduledTask  -TaskName "TeamsPresenceScraper"
Get-ScheduledTask   -TaskName "TeamsPresenceScraper"
```

**Manually edit the task (e.g., to add users to the command line):**

1. Open Task Scheduler (`taskschd.msc`).
2. Find `TeamsPresenceScraper` in the root of the Task Scheduler Library.
3. Right-click → **Properties** → **Actions** tab → **Edit**.
4. In the "Add arguments" field you can add `--users "Name1" "Name2"` after the script path.

However, the easiest way to change users is to edit `scraper/config.json` (see "Track users via config file" above). The scheduled task does not need to be touched — just edit the JSON file and the running scraper picks up the change automatically within one poll interval.

> **Note:** The scraper runs in **headless** mode by default, so it does not need a visible desktop. If you experience issues running without a logged-in user, an alternative is to enable [Windows auto-logon](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/regini) for your account and place the scraper in the Startup folder instead.

## Debug selectors

If the scraper can't find your presence status, Teams might have updated its UI.

```bash
python teams_scraper.py --inspect
```

This opens a visible browser, loads Teams, and prints what each selector strategy found. It also saves the full page HTML to `scraper/state/page_debug.html` so you can inspect it.

Update `scraper/config.json` with better selectors if needed.

## Tips

- **Headless mode** is enabled by default. Use `--setup` or `--inspect` to see the browser.
- The scraper simulates tiny scroll movements to keep Teams from going idle.
- If Teams shows a "Keep me signed in" checkbox during `--setup`, check it to extend session life.
- If session expires, just run `--setup` again.
