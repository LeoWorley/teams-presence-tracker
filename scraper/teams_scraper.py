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
    }
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


def scrape_users_via_js(page, users: list[str]) -> list:
    """Find users by visible text, then use JS to detect colored presence dot."""
    results = []
    for user in users:
        try:
            locator = page.locator(f"text={user}")
            if locator.count() == 0:
                continue
            el = locator.first

            data = page.evaluate("""
                (el) => {
                    let container = el.closest('[role=\"listitem\"]') || el.closest('li') || el.parentElement;
                    if (!container) return null;
                    for (let depth = 0; depth < 4 && container; depth++) {
                        const dots = container.querySelectorAll('div, span, svg, i');
                        for (const cand of dots) {
                            const rect = cand.getBoundingClientRect();
                            if (rect.width > 0 && rect.width <= 20 && rect.height > 0 && rect.height <= 20) {
                                const style = window.getComputedStyle(cand);
                                const bg = style.backgroundColor || style.color || cand.getAttribute('fill');
                                if (bg && bg !== 'rgba(0, 0, 0, 0)' && !bg.includes('255, 255, 255')) {
                                    return {
                                        color: bg,
                                        ariaLabel: cand.getAttribute('aria-label') || '',
                                        containerText: container.textContent.trim().substring(0, 80)
                                    };
                                }
                            }
                        }
                        container = container.parentElement;
                    }
                    return null;
                }
            """, el)

            if data:
                color = data.get("color", "")
                aria = data.get("ariaLabel", "")
                raw_status = aria or color
                color_lower = color.lower()
                if any(g in color_lower for g in ["rgb(16,", "rgb(107,", "green", "#0f7", "#107", "rgb(15,"]):
                    raw_status = "Available"
                elif any(r in color_lower for r in ["rgb(196,", "rgb(234,", "red", "#c43", "rgb(192,"]):
                    raw_status = "Busy"
                elif any(y in color_lower for y in ["rgb(255,", "yellow", "orange", "#ffc", "rgb(234, 185"]):
                    raw_status = "Away"
                elif any(gry in color_lower for gry in ["rgb(128,", "gray", "grey", "#808"]):
                    raw_status = "Offline"
                results.append({"name": user, "raw": raw_status, "debug": data})
        except Exception as e:
            print(f"[WARN] Scrape failed for {user}: {e}")
    return results


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


def run_scraper(config, users: list[str]):
    """Main polling loop."""
    ensure_csv()

    if not STATE_PATH.exists():
        print("❌ No saved session found. Run with --setup first.")
        sys.exit(1)

    print(f"Starting scraper...")
    print(f"CSV: {CSV_PATH}")
    print(f"Poll interval: {config['poll_interval_seconds']}s")
    print(f"Users: {users if users else 'self only'}")
    print("Press Ctrl+C to stop\n")

    last_status: dict[str, str] = {}

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
            save_screenshot(page, "run_stuck")
            print("Trying to continue anyway...")
        time.sleep(3)

        print("Teams loaded. Starting poll loop.\n")

        while True:
            try:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # --- Scrape self presence ---
                strategies = config.get("selectors", {}).get("self_presence", {}).get("strategies", [])
                raw = try_scrape_self(page, strategies)

                if raw:
                    avail, activity = parse_status(raw, config.get("status_mapping", {}))
                    key = "me"
                    prev = last_status.get(key, "")
                    if f"{avail}/{activity}" != prev:
                        if prev:
                            print(f"[{now_str}] Self changed: {prev} -> {avail}/{activity}")
                        else:
                            print(f"[{now_str}] Self status: {avail}/{activity}")
                        append_record("me", avail, activity, raw)
                        last_status[key] = f"{avail}/{activity}"
                else:
                    print(f"[{now_str}] Could not find self presence.")

                # --- Scrape other users via JS DOM walking ---
                if users:
                    contacts = scrape_users_via_js(page, users)
                    for contact in contacts:
                        name = contact["name"]
                        raw = contact["raw"]
                        avail, activity = parse_status(raw, config.get("status_mapping", {}))
                        key = name
                        prev = last_status.get(key, "")
                        if f"{avail}/{activity}" != prev:
                            if prev:
                                print(f"[{now_str}] {name} changed: {prev} -> {avail}/{activity}")
                            else:
                                print(f"[{now_str}] {name} status: {avail}/{activity}")
                            append_record(name, avail, activity, raw)
                            last_status[key] = f"{avail}/{activity}"

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
            save_screenshot(page, "inspect_stuck")
        time.sleep(5)

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
                const names = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = walker.nextNode()) {
                    const text = node.textContent.trim();
                    // Heuristic: looks like a person name (2+ words, no digits, reasonable length)
                    if (text.length > 5 && text.length < 35 && text.includes(' ') && !/\\d/.test(text) && !text.includes('notification') && !text.includes('menu')) {
                        const el = node.parentElement;
                        let container = el.closest('[role=\"listitem\"]') || el.closest('li') || el.parentElement;
                        if (container) {
                            const dots = container.querySelectorAll('div, span, svg, i');
                            for (const dot of dots) {
                                const rect = dot.getBoundingClientRect();
                                if (rect.width > 0 && rect.width <= 20 && rect.height > 0 && rect.height <= 20) {
                                    const style = window.getComputedStyle(dot);
                                    const bg = style.backgroundColor || style.color;
                                    if (bg && bg !== 'rgba(0, 0, 0, 0)' && !bg.includes('255, 255, 255')) {
                                        names.push({name: text, color: bg});
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
                print(f"  Found: '{item.get('name')}' with color {item.get('color')}")
        else:
            print("  No contacts with colored dots found.")

        input("\nPress ENTER to close browser...")
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

    run_scraper(config, args.users or [])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
