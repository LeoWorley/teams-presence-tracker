#!/usr/bin/env python3
"""
Teams Presence Scraper via Playwright
Scrapes Microsoft Teams Web (teams.microsoft.com) for presence status.
No Azure AD app registration required — uses browser automation.
"""

import argparse
import json
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_DIR = SCRIPT_DIR / "state"
STATE_PATH = STATE_DIR / "playwright_state.json"
CSV_PATH = Path.home() / "AppData/Roaming/teams-presence-tracker/presence_history.csv"

DEFAULT_CONFIG = {
    "teams_url": "https://teams.microsoft.com",
    "poll_interval_seconds": 30,
    "headless": True,
    "browser_args": [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-setuid-sandbox",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-infobars",
        "--window-size=1920,1080",
        "--start-maximized",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials"
    ],
    "viewport": {"width": 1920, "height": 1080},
    "selectors": {
        "self_presence": {
            "strategies": [
                {"type": "aria-label", "pattern": "status", "extract": "text"},
                {"type": "css", "value": "[data-tid='self-presence']", "extract": "aria-label"},
                {"type": "css", "value": "button[aria-label*='status']", "extract": "aria-label"},
            ]
        }
    },
    "status_mapping": {
        "available": ["available", "free"],
        "busy": ["busy", "in a call", "in a meeting", "do not disturb", "presenting"],
        "away": ["away", "be right back", "idle"],
        "offline": ["offline", "unknown", "presence unknown"]
    },
    "users": [],
    "aliases": {},
    "track_self": true,
    "ntfy_topic": None
}


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def ensure_csv():
    if not CSV_PATH.exists():
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "user_id", "display_name", "availability", "activity", "status_message", "recorded_at"])


def append_record(user_id: str, availability: str, activity: str, status_message: str = ""):
    ensure_csv()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        writer.writerow(["", user_id, user_id, availability, activity, status_message, now])


def parse_status(text: str, mapping: dict) -> tuple:
    """Parse raw status text into (availability, activity)."""
    text_lower = text.lower()
    for avail, keywords in mapping.items():
        for kw in keywords:
            if kw in text_lower:
                return avail, text.strip()
    return "unknown", text.strip()


def resolve_alias(name: str, aliases: dict) -> str:
    """Return the alias for a name if one exists, otherwise the original name."""
    return aliases.get(name, name)


def send_ntfy_notification(topic: str | None, title: str, message: str):
    """Send a push notification via ntfy.sh."""
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "default"},
            timeout=5,
        )
    except Exception as e:
        print(f"[WARN] ntfy notification failed: {e}")


def try_scrape_self(page, strategies: list) -> str | None:
    """Try multiple strategies to find self presence text."""
    for strat in strategies:
        try:
            if strat["type"] == "css":
                els = page.locator(strat["value"])
                if els.count() == 0:
                    continue
                el = els.first
                if strat.get("extract") == "aria-label":
                    text = el.get_attribute("aria-label", timeout=0) or ""
                else:
                    text = el.text_content(timeout=0) or ""
                if text:
                    return text
            elif strat["type"] == "aria-label":
                els = page.locator(f"[aria-label*='{strat['pattern']}']")
                count = els.count()
                for i in range(min(count, 5)):
                    text = els.nth(i).get_attribute("aria-label", timeout=0) or ""
                    if text:
                        return text
        except Exception:
            continue
    return None


def scrape_users_via_js(page, users: list[str]) -> tuple[list, list[str]]:
    """Find users by visible text, then use JS to detect colored presence dot.
    Returns (results, list of users_not_found).
    """
    results = []
    not_found = []
    for user in users:
        try:
            locator = page.locator(f"text={user}")
            if locator.count() == 0:
                not_found.append(user)
                continue
            el = locator.first

            data = el.evaluate("""
                (el) => {
                    const COLOR_MAP = {
                        'rgb(19, 161, 14)': 'Available',
                        'rgb(209, 52, 56)': 'Busy',
                        'rgb(234, 163, 0)': 'Away',
                        'rgb(194, 57, 179)': 'Do not disturb',
                        'rgb(128, 128, 128)': 'Offline',
                        'rgb(176, 176, 176)': 'Offline'
                    };

                    let container = el.closest('[role=\"listitem\"]') || el.closest('li') || el.parentElement;
                    if (!container) container = el.parentElement;

                    for (let depth = 0; depth < 6 && container; depth++) {
                        const badges = container.querySelectorAll('.fui-PresenceBadge, [data-tid=\"presence-badge\"], .presence-badge');
                        for (const badge of badges) {
                            const svg = badge.querySelector('svg');
                            if (svg) {
                                const style = window.getComputedStyle(svg);
                                const fill = style.fill;
                                if (COLOR_MAP[fill]) {
                                    return { color: fill, status: COLOR_MAP[fill], source: 'svg-fill' };
                                }
                            }
                            const path = badge.querySelector('svg path');
                            if (path) {
                                const style = window.getComputedStyle(path);
                                const fill = style.fill;
                                if (COLOR_MAP[fill]) {
                                    return { color: fill, status: COLOR_MAP[fill], source: 'path-fill' };
                                }
                            }
                        }

                        const all = container.querySelectorAll('*');
                        for (const cand of all) {
                            const rect = cand.getBoundingClientRect();
                            if (rect.width > 0 && rect.width <= 20 && rect.height > 0 && rect.height <= 20) {
                                const style = window.getComputedStyle(cand);
                                const fill = style.fill;
                                const bg = style.backgroundColor;
                                if (COLOR_MAP[fill]) {
                                    return { color: fill, status: COLOR_MAP[fill], source: 'fill' };
                                }
                                if (COLOR_MAP[bg]) {
                                    return { color: bg, status: COLOR_MAP[bg], source: 'bg' };
                                }
                            }
                        }

                        container = container.parentElement;
                    }
                    return null;
                }
            """)

            if data:
                raw_status = data.get("status", "unknown")
                results.append({"name": user, "raw": raw_status, "debug": data})
            else:
                not_found.append(user)
        except Exception as e:
            print(f"[WARN] Scrape failed for {user}: {e}")
            not_found.append(user)
    return results, not_found


def apply_stealth(page):
    """Remove automation indicators."""
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = { runtime: {} };
    """)


def wait_for_teams_ready(page, timeout_ms: int = 30000):
    """Wait until Teams appears to be fully loaded."""
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            if page.locator("input[placeholder*='search' i], input[placeholder*='buscar' i]").first.is_visible(timeout=2000):
                return True
            if page.locator("text=Chat, div:has-text('Chat'), div:has-text('Chats')").first.is_visible(timeout=1000):
                return True
            if page.locator("[role='listitem']").first.is_visible(timeout=1000):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def navigate_to_chat_list(page):
    """Click the Chat button to ensure we are on the chat list view."""
    try:
        # Try aria-label first
        chat_btn = page.locator('[aria-label="Chat (Ctrl+Shift+2)"]').first
        if chat_btn.count() > 0:
            chat_btn.click()
            print("  Clicked Chat button to show chat list.")
            time.sleep(10)
            return True
    except Exception:
        pass
    return False


def scroll_chat_list_to_top(page):
    """Scroll the chat list to the top so pinned/favorite contacts are visible."""
    try:
        # Find the chat list container and scroll to top
        # Common selectors for the chat list scrollable area
        list = page.locator('[role="list"]').first
        if list.count() > 0:
            list.evaluate("el => el.scrollTop = 0")
            return True
        # Fallback: find any scrollable container in the left sidebar
        scrollables = page.locator('div').all()
        for div in scrollables:
            try:
                is_scrollable = div.evaluate("""
                    el => el.scrollHeight > el.clientHeight && el.clientHeight > 100
                """)
                if is_scrollable:
                    div.evaluate("el => el.scrollTop = 0")
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def save_screenshot(page, name: str):
    """Save a screenshot for debugging."""
    path = STATE_DIR / f"{name}_{datetime.now().strftime('%H%M%S')}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"📸 Screenshot saved: {path}")
    return path


def login_and_save_state(config):
    """Interactive setup: open browser, let user login manually, save state."""
    print("=" * 60)
    print("SETUP MODE")
    print("=" * 60)
    print("A browser window will open. Please:")
    print("1. Log into Microsoft Teams (teams.microsoft.com)")
    print("2. Complete MFA if prompted")
    print("3. Wait until Teams fully loads and you see your chats")
    print("4. Press ENTER here to save the session")
    print("=" * 60)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=config.get("browser_args", [])
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
        )
        page = context.new_page()
        apply_stealth(page)

        print("\nLoading Teams...")
        page.goto(config["teams_url"], wait_until="networkidle")

        print("Waiting for Teams to finish loading (up to 30 seconds)...")
        if not wait_for_teams_ready(page, timeout_ms=30000):
            print("\n⚠️  Teams seems stuck on the loading screen.")
            save_screenshot(page, "setup_stuck")
            print("Common fixes:")
            print("  - Try reloading the page (F5) in the browser window")
            print("  - Check if your company blocks Teams Web (conditional access)")
            print("  - Try logging in with a personal Microsoft account instead")
            input("\n>>> If Teams is now loaded, press ENTER to save. Otherwise Ctrl+C to exit.")
        else:
            print("✅ Teams appears ready.")

        context.storage_state(path=str(STATE_PATH))
        print(f"\n✅ Session saved to {STATE_PATH}")
        browser.close()


def run_scraper(config, users: list[str], use_config_users: bool = False):
    """Main polling loop."""
    ensure_csv()

    if not STATE_PATH.exists():
        print("❌ No saved session found. Run with --setup first.")
        sys.exit(1)

    print(f"Starting scraper...")
    print(f"CSV: {CSV_PATH}")
    print(f"Poll interval: {config['poll_interval_seconds']}s")
    print(f"Users: {users if users else 'self only'}")
    if use_config_users:
        print("(Users loaded from config.json — edits will be picked up automatically)\n")
    else:
        print("Press Ctrl+C to stop\n")

    last_status: dict[str, str] = {}
    status_confirm: dict[str, tuple[str, int]] = {}  # key -> (pending_status, count)
    STARTUP_GRACE_POLLS = 3  # skip notifications for first ~90s while page stabilizes

    def check_status_change(key: str, current: str, in_grace: bool):
        """Debounce status changes: require 2 consecutive polls with the
        same new status before confirming it. Returns (should_notify,
        notify_type, prev_status) or (False, None, None)."""
        confirmed = last_status.get(key, "")
        if current == confirmed:
            status_confirm.pop(key, None)
            return False, None, None

        pending, count = status_confirm.get(key, ("", 0))
        if pending == current:
            count += 1
        else:
            count = 1
        status_confirm[key] = (current, count)

        if count >= 2:
            last_status[key] = current
            status_confirm.pop(key, None)
            # Skip notifications during startup grace period
            if in_grace:
                return False, None, None
            return True, ("changed" if confirmed else "status"), confirmed
        return False, None, None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=config.get("headless", True),
            args=config.get("browser_args", [])
        )
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        context = browser.new_context(
            storage_state=str(STATE_PATH),
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
        )
        page = context.new_page()
        apply_stealth(page)

        print("Loading Teams...")
        page.goto(config["teams_url"], wait_until="networkidle")

        print("Waiting for Teams to be ready...")
        if not wait_for_teams_ready(page, timeout_ms=30000):
            print("⚠️  Teams not ready after 30 seconds. Taking screenshot...")
            save_screenshot(page, "run_not_ready")
            print("Trying to continue anyway...")
        print("Teams layout ready. Navigating to chat list...")
        navigate_to_chat_list(page)
        print("Teams loaded. Starting poll loop.\n")

        poll_count = 0
        missing_streak: dict[str, int] = {}
        while True:
            try:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                poll_count += 1

                # Hot-reload config users and aliases if not overridden by CLI
                if use_config_users:
                    fresh_config = load_config()
                    users = fresh_config.get("users", [])
                    config["aliases"] = fresh_config.get("aliases", {})
                    # Allow poll interval changes too
                    config["poll_interval_seconds"] = fresh_config.get("poll_interval_seconds", config["poll_interval_seconds"])

                # --- Periodic refresh: re-navigate to chat list every 20 polls (~10 min at 30s interval) ---
                if poll_count % 20 == 0:
                    try:
                        navigate_to_chat_list(page)
                    except Exception:
                        pass

                in_grace = poll_count <= STARTUP_GRACE_POLLS

                # --- Scrape self presence ---
                if config.get("track_self", True):
                    strategies = config.get("selectors", {}).get("self_presence", {}).get("strategies", [])
                    raw = try_scrape_self(page, strategies)
                else:
                    raw = None

                if raw:
                    avail, activity = parse_status(raw, config.get("status_mapping", {}))
                    key = "me"
                    current = f"{avail}/{activity}"
                    should_notify, notify_type, prev = check_status_change(key, current, in_grace)
                    if should_notify:
                        display = resolve_alias("Self", config.get("aliases", {}))
                        if notify_type == "changed":
                            print(f"[{now_str}] {display} changed: {prev} -> {avail}/{activity}")
                            send_ntfy_notification(config.get("ntfy_topic"), f"Teams Status - {display}", f"{display} is now {avail}")
                        else:
                            print(f"[{now_str}] {display} status: {avail}/{activity}")
                            send_ntfy_notification(config.get("ntfy_topic"), f"Teams Status - {display}", f"{display} is now {avail}")
                        append_record("me", avail, activity, raw)
                else:
                    print(f"[{now_str}] Could not find self presence.")

                # --- Scrape other users via JS DOM walking ---
                if users:
                    # Scroll chat list to top so favorites/pinned contacts are visible
                    scroll_chat_list_to_top(page)
                    time.sleep(1)

                    contacts, not_found = scrape_users_via_js(page, users)

                    # Clear pending confirmations for users we couldn't find this poll
                    for user in not_found:
                        status_confirm.pop(user, None)

                    # Log warnings for missing users
                    aliases = config.get("aliases", {})
                    for user in not_found:
                        missing_streak[user] = missing_streak.get(user, 0) + 1
                        if missing_streak[user] == 1:
                            display = resolve_alias(user, aliases)
                            print(f"[{now_str}] ⚠️  {display} not found in chat list (may have scrolled out of view)")
                        elif missing_streak[user] % 10 == 0:
                            display = resolve_alias(user, aliases)
                            print(f"[{now_str}] ⚠️  {display} still missing after {missing_streak[user]} polls")
                    # Reset streak for found users
                    for contact in contacts:
                        user = contact["name"]
                        if user in missing_streak and missing_streak[user] > 0:
                            display = resolve_alias(user, aliases)
                            print(f"[{now_str}] ✓  {display} found again")
                        missing_streak[user] = 0

                    for contact in contacts:
                        name = contact["name"]
                        display = resolve_alias(name, config.get("aliases", {}))
                        raw = contact["raw"]
                        avail, activity = parse_status(raw, config.get("status_mapping", {}))
                        key = name
                        current = f"{avail}/{activity}"
                        should_notify, notify_type, prev = check_status_change(key, current, in_grace)
                        if should_notify:
                            if notify_type == "changed":
                                print(f"[{now_str}] {display} changed: {prev} -> {avail}/{activity}")
                                send_ntfy_notification(config.get("ntfy_topic"), f"Teams Status - {display}", f"{display} is now {avail}")
                            else:
                                print(f"[{now_str}] {display} status: {avail}/{activity}")
                                send_ntfy_notification(config.get("ntfy_topic"), f"Teams Status - {display}", f"{display} is now {avail}")
                            append_record(name, avail, activity, raw)

                # Keep page alive
                try:
                    page.evaluate("window.scrollBy(0, 1)")
                except Exception:
                    pass

            except Exception as e:
                print(f"[ERROR] Poll cycle failed: {e}")

            time.sleep(config["poll_interval_seconds"])


def inspect_page(config):
    """Save rendered DOM and test selectors for debugging."""
    print("INSPECT MODE: loading Teams and saving debug info...")
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=config.get("browser_args", []))
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        context = browser.new_context(
            storage_state=str(STATE_PATH) if STATE_PATH.exists() else None,
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
        )
        page = context.new_page()
        apply_stealth(page)
        page.goto(config["teams_url"], wait_until="networkidle")
        if not wait_for_teams_ready(page, timeout_ms=30000):
            save_screenshot(page, "inspect_not_ready")
        print("Navigating to chat list...")
        navigate_to_chat_list(page)

        # Save rendered DOM (not just static HTML)
        rendered = page.evaluate("() => document.documentElement.outerHTML")
        debug_path = STATE_DIR / "page_debug.html"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"Saved rendered DOM to: {debug_path}")

        # Self-presence results
        strategies = config.get("selectors", {}).get("self_presence", {}).get("strategies", [])
        print("\nSelf-presence strategy results:")
        for i, strat in enumerate(strategies):
            try:
                if strat["type"] == "css":
                    count = page.locator(strat["value"]).count()
                    print(f"  [{i}] CSS '{strat['value']}': found {count} elements")
                    if count > 0:
                        el = page.locator(strat["value"]).first
                        text = el.text_content(timeout=0) or ""
                        aria = el.get_attribute("aria-label", timeout=0) or ""
                        print(f"      text='{text[:60]}' aria-label='{aria[:60]}'")
                elif strat["type"] == "aria-label":
                    els = page.locator(f"[aria-label*='{strat['pattern']}']")
                    count = els.count()
                    print(f"  [{i}] aria-label*='{strat['pattern']}': found {count} elements")
                    for j in range(min(count, 3)):
                        val = els.nth(j).get_attribute("aria-label", timeout=0) or ""
                        print(f"      [{j}] aria-label='{val[:80]}'")
            except Exception as e:
                print(f"  [{i}] FAILED: {e}")

        # JS contact scraper demo
        print("\nJS contact scraper demo (searching sidebar for contacts with colored dots)...")
        demo = page.evaluate("""
            () => {
                const COLOR_MAP = {
                    'rgb(19, 161, 14)': 'Available',
                    'rgb(209, 52, 56)': 'Busy',
                    'rgb(234, 163, 0)': 'Away',
                    'rgb(194, 57, 179)': 'Do not disturb',
                    'rgb(128, 128, 128)': 'Offline',
                    'rgb(176, 176, 176)': 'Offline'
                };
                const names = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = walker.nextNode()) {
                    const text = node.textContent.trim();
                    if (text.length > 5 && text.length < 35 && text.includes(' ') && !/[0-9]/.test(text) && !text.includes('notification') && !text.includes('menu')) {
                        const el = node.parentElement;
                        let container = el.closest('[role="listitem"]') || el.closest('li') || el.parentElement;
                        if (container) {
                            const badges = container.querySelectorAll('.fui-PresenceBadge, [data-tid="presence-badge"], .presence-badge');
                            for (const badge of badges) {
                                const svg = badge.querySelector('svg');
                                if (svg) {
                                    const fill = window.getComputedStyle(svg).fill;
                                    if (COLOR_MAP[fill]) {
                                        names.push({name: text, status: COLOR_MAP[fill], color: fill});
                                        break;
                                    }
                                }
                                const path = badge.querySelector('svg path');
                                if (path) {
                                    const fill = window.getComputedStyle(path).fill;
                                    if (COLOR_MAP[fill]) {
                                        names.push({name: text, status: COLOR_MAP[fill], color: fill});
                                        break;
                                    }
                                }
                            }
                        }
                    }
                }
                return names.slice(0, 15);
            }
        """)
        if demo:
            for item in demo:
                print(f"  Found: '{item.get('name')}' -> {item.get('status')} ({item.get('color')})")
        else:
            print("  No contacts with colored dots found.")

        try:
            input("\nPress ENTER to close browser...")
        except EOFError:
            pass
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Teams Presence Scraper via Playwright")
    parser.add_argument("--setup", action="store_true", help="Interactive login and session save")
    parser.add_argument("--inspect", action="store_true", help="Debug mode: save DOM and test selectors")
    parser.add_argument("--users", nargs="+", help="Users to track (exact names as shown in Teams sidebar)")
    args = parser.parse_args()

    config = load_config()

    if args.setup:
        login_and_save_state(config)
        return

    if args.inspect:
        inspect_page(config)
        return

    # If CLI --users is provided, use it exclusively (no hot-reload).
    # Otherwise fall back to config.json users and allow hot-reloading.
    cli_users = args.users or []
    if cli_users:
        run_scraper(config, cli_users, use_config_users=False)
    else:
        config_users = config.get("users", [])
        run_scraper(config, config_users, use_config_users=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
