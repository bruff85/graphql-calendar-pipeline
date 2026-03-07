#!/usr/bin/env python3
"""
LCUSD Elementary School Lunch Calendar Generator

Schedule: Runs on the last day of each month AND as a retry on the
1st, 3rd, 5th, and 7th of each month to catch late API publishing.

Logic:
  1. Try nextMonthPublished from the API chain (fastest)
  2. If null, scrape the LCUSD nutrition site for next month's menu ID
  3. If not published yet, keep current month's ICS and exit cleanly
  4. Only commit if menu data actually changed
"""

import hashlib
import uuid
import re
import requests
from datetime import datetime, date, timedelta
import os

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
GRAPHQL_URL = "https://api.schoolnutritionandfitness.com/graphql"
LCUSD_MENU_URL = "https://nutrition.lcusd.net/index.php?sid=2506080150154913&page=menus&sm={month}&sy={year}"
SEED_MENU_ID = "698b7e94cc6f3104111f19e7"
MENU_ID_FILE = "current_menu_id.txt"
OUTPUT_ICS = "docs/lunch.ics"
EXCLUDE_CATEGORIES = {"Milk", "Condiment", "Extra"}

QUERY = """
{
    menu(id: "%s") {
        id
        month
        year
        items {
            day
            month
            year
            hidden
            product {
                name
                category
                hide_on_calendars
            }
        }
        nextMonthPublished { id }
        previousMonthPublished { id }
    }
}
"""


def get_current_menu_id():
    if os.path.exists(MENU_ID_FILE):
        with open(MENU_ID_FILE, "r") as f:
            menu_id = f.read().strip()
            if menu_id:
                return menu_id
    return SEED_MENU_ID


def save_menu_id(menu_id):
    with open(MENU_ID_FILE, "w") as f:
        f.write(menu_id)


def fetch_menu(menu_id):
    response = requests.post(
        GRAPHQL_URL,
        json={"query": QUERY % menu_id},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise ValueError(f"GraphQL errors: {data['errors']}")
    return data["data"]["menu"]


def scrape_menu_id_from_website(month, year):
    """
    Scrape the LCUSD nutrition site to find the menu ID for a given month/year.
    Looks for the 'Lunch Menu' link which contains the menu ID in its href.
    Returns the menu ID string if found, or None if not published yet.
    """
    url = LCUSD_MENU_URL.format(month=month, year=year)
    print(f"  Scraping LCUSD site for {month}/{year}: {url}")

    try:
        response = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; LunchCalendarBot/1.0)"
        })
        response.raise_for_status()
        html = response.text

        # Check if the page says no menus published
        if "No menus published for this month" in html:
            print(f"  Site confirms: no menus published for {month}/{year} yet.")
            return None

        # Look for the Lunch Menu link containing the menu ID
        # Pattern: href contains webmenus2 URL with id= parameter
        # e.g. href="https://www.schoolnutritionandfitness.com/webmenus2/#/view?id=698b7e94cc6f3104111f19e7&siteCode=24701"
        patterns = [
            r'webmenus2[^"]*id=([a-f0-9]{24})[^"]*siteCode=24701[^"]*"[^>]*>(?:[^<]*<[^>]+>)*[^<]*Lunch Menu',
            r'id=([a-f0-9]{24})[^"]*siteCode=24701',
            r'open\?id=([a-f0-9]{24})',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                menu_id = matches[0]
                print(f"  Found menu ID from website: {menu_id}")
                return menu_id

        print(f"  Could not extract menu ID from page HTML.")
        return None

    except requests.RequestException as e:
        print(f"  Website scrape failed: {e}")
        return None


def get_next_month(month, year):
    """Return (month, year) for the month after the given one."""
    if month == 12:
        return 1, year + 1
    return month + 1, year


def is_last_day_of_month(today):
    tomorrow = today + timedelta(days=1)
    return tomorrow.month != today.month


def is_early_month_retry(today):
    """Returns True if today is one of our retry days (1st, 3rd, 5th, 7th)."""
    return today.day in {1, 3, 5, 7}


def build_daily_menu(menu_data):
    daily = {}
    for item in menu_data["items"]:
        if item.get("hidden"):
            continue
        product = item.get("product")
        if not product:
            continue
        if product.get("hide_on_calendars"):
            continue
        category = product.get("category") or ""
        if category in EXCLUDE_CATEGORIES:
            continue
        name = (product.get("name") or "").strip()
        if not name:
            continue
        day = item.get("day")
        month = item.get("month") or menu_data["month"]
        year = item.get("year") or menu_data["year"]
        if not all([day, month, year]):
            continue
        try:
            day_date = date(int(year), int(month), int(day))
        except ValueError:
            continue
        if day_date.weekday() >= 5:
            continue
        if day_date not in daily:
            daily[day_date] = []
        if name not in daily[day_date]:
            daily[day_date].append(name)
    return daily


def generate_ics(daily_menu, month, year):
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    month_label = datetime(year, month, 1).strftime("%B %Y")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LCUSD Elementary Lunch//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:LCUSD Elementary Lunch {month_label}",
        "X-WR-TIMEZONE:America/Los_Angeles",
        "X-PUBLISHED-TTL:PT4H",
    ]
    for day_date in sorted(daily_menu.keys()):
        items = daily_menu[day_date]
        title = " | ".join(items) if items else "Lunch Menu"
        date_str = day_date.strftime("%Y%m%d")
        end_date = (day_date + timedelta(days=1)).strftime("%Y%m%d")
        uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"lcusd-lunch-{date_str}"))
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{date_str}",
            f"DTEND;VALUE=DATE:{end_date}",
            f"SUMMARY:{title}",
            "DESCRIPTION:LCUSD Elementary School Lunch Menu",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def file_hash(filepath):
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return hashlib.md5(f.read().encode()).hexdigest()


def main():
    print("=" * 50)
    print("LCUSD Elementary Lunch Calendar Generator")
    print("=" * 50)

    today = date.today()
    print(f"Today: {today}")

    # FORCE_RUN=true is set by the workflow when triggered manually
    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"
    last_day = is_last_day_of_month(today)
    early_retry = is_early_month_retry(today)

    if not last_day and not early_retry and not force_run:
        print(f"Today (day {today.day}) is not a scheduled run day. Skipping.")
        print("Tip: Use 'Run workflow' in GitHub Actions to force a run anytime.")
        return

    if force_run:
        print("Manual trigger detected — forcing run regardless of date.")

    if last_day:
        print("Running end-of-month update — looking for next month's menu...")
        target_month, target_year = get_next_month(today.month, today.year)
    else:
        print(f"Targeting current month: {today.month}/{today.year}")
        target_month, target_year = today.month, today.year

    target_label = datetime(target_year, target_month, 1).strftime("%B %Y")
    print(f"Target month: {target_label}")

    # ── Step 1: Try the API chain ──────────────────────────
    current_id = get_current_menu_id()
    print(f"\nStep 1: Checking API chain from menu ID: {current_id}")
    current_menu = fetch_menu(current_id)
    print(f"  Current menu in file: {current_menu['month']}/{current_menu['year']}")

    target_menu = None

    # Check if current menu is already the target month
    if current_menu["month"] == target_month and current_menu["year"] == target_year:
        print(f"  Current menu is already {target_label}.")
        target_menu = current_menu
    else:
        next_info = current_menu.get("nextMonthPublished")
        if next_info:
            candidate = fetch_menu(next_info["id"])
            if candidate["month"] == target_month and candidate["year"] == target_year:
                print(f"  Found via API nextMonthPublished!")
                target_menu = candidate
            else:
                print(f"  nextMonthPublished exists but is {candidate['month']}/{candidate['year']}, not target.")

        # If API month field is wrong but items exist, trust today's date
        # The school sometimes updates menu content without updating the month field
        if not target_menu and not next_info:
            print(f"  API month field says {current_menu['month']}/{current_menu['year']} but no next month exists.")
            print(f"  Checking if current menu items match target month {target_month}/{target_year}...")
            item_days = set(item.get("day") for item in current_menu["items"] if item.get("day"))
            if item_days:
                print(f"  Menu has items for days: {sorted(item_days)[:5]}... — treating as {target_label}")
                # Override the month/year so ICS is generated for the correct month
                current_menu["month"] = target_month
                current_menu["year"] = target_year
                target_menu = current_menu

    # ── Step 2: Scrape LCUSD website as fallback ───────────
    if not target_menu:
        print(f"\nStep 2: API didn't have {target_label} — scraping LCUSD website...")
        scraped_id = scrape_menu_id_from_website(target_month, target_year)
        if scraped_id:
            try:
                candidate = fetch_menu(scraped_id)
                if candidate["month"] == target_month and candidate["year"] == target_year:
                    print(f"  Found via website scrape!")
                    target_menu = candidate
                else:
                    print(f"  Scraped ID returned wrong month: {candidate['month']}/{candidate['year']}")
            except Exception as e:
                print(f"  Failed to fetch scraped menu ID: {e}")

    # ── Step 3: Give up gracefully ─────────────────────────
    if not target_menu:
        print(f"\n{target_label} menu not available yet — keeping existing ICS unchanged.")
        print("Will retry on the next scheduled run day.")
        return

    # ── Generate and save ICS ──────────────────────────────
    month = target_menu["month"]
    year = target_menu["year"]
    print(f"\nGenerating ICS for: {datetime(year, month, 1).strftime('%B %Y')}")

    save_menu_id(target_menu["id"])

    daily_menu = build_daily_menu(target_menu)
    print(f"Menu items found for {len(daily_menu)} school days")

    ics_content = generate_ics(daily_menu, month, year)

    os.makedirs("docs", exist_ok=True)
    old_hash = file_hash(OUTPUT_ICS)
    new_hash = hashlib.md5(ics_content.encode()).hexdigest()

    if old_hash == new_hash:
        print("No changes detected — ICS file is already up to date.")
    else:
        with open(OUTPUT_ICS, "w", encoding="utf-8") as f:
            f.write(ics_content)
        print(f"ICS file updated with {len(daily_menu)} events.")

    print("\nDone! ✅")


if __name__ == "__main__":
    main()
