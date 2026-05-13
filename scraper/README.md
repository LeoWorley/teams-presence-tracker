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

The scraper polls every 30 seconds and appends changes to the same CSV file the Rust tool uses:
```
%APPDATA%\teams-presence-tracker\presence_history.csv
```

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
