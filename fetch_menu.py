#!/usr/bin/env python3
"""
LCUSD Elementary School Lunch Calendar Generator
Fetches the monthly lunch menu from the School Nutrition and Fitness GraphQL API
and generates an ICS file with one all-day event per school day.
"""

import json
import uuid
import requests
from datetime import datetime, date
import os

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
GRAPHQL_URL = "https://api.schoolnutritionandfitness.com/graphql"
SITE_ID = "24701"

# The current month's menu ID. The script will auto-discover next month's ID
# from the API response and save it for the next run.
MENU_ID_FILE = "current_menu_id.txt"
FALLBACK_MENU_ID = "698b7e94cc6f3104111f19e7"  # March 2026 — update if needed

# Categories to EXCLUDE from the event title (ancillary items, sides, condiments)
# Edit this list to filter out items you don't want shown on the calendar
EXCLUDE_CATEGORIES = {"Milk", "Condiment", "Extra"}

# Output ICS file path
OUTPUT_ICS = "docs/lunch.ics"

# ─────────────────────────────────────────────
# GRAPHQL QUERY
# ─────────────────────────────────────────────
QUERY = """
{
    s0: site(depth: 0, id: "%s") { id name }
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
    }
}
"""


def get_menu_id():
    """Load menu ID from file, or fall back to hardcoded value."""
    if os.path.exists(MENU_ID_FILE):
        with open(MENU_ID_FILE, "r") as f:
            menu_id = f.read().strip()
            if menu_id:
                print(f"Using menu ID from file: {menu_id}")
                return menu_id
    print(f"Using fallback menu ID: {FALLBACK_MENU_ID}")
    return FALLBACK_MENU_ID


def save_next_menu_id(next_id):
    """Save the next month's menu ID for the next scheduled run."""
    if next_id:
        with open(MENU_ID_FILE, "w") as f:
            f.write(next_id)
        print(f"Saved next month's menu ID: {next_id}")


def fetch_menu(menu_id):
    """Fetch menu data from the GraphQL API."""
    query = QUERY % (SITE_ID, menu_id)
    response = requests.post(
        GRAPHQL_URL,
        json={"query": query},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    response.raise_for_status()
    data = response.json()

    if "errors" in data:
        raise ValueError(f"GraphQL errors: {data['errors']}")

    return data["data"]["menu"]


def build_daily_menu(menu_data):
    """
    Group menu items by day, filtering out hidden/excluded items.
    Returns a dict: { date_obj: [item_name, ...] }
    """
    daily = {}

    for item in menu_data["items"]:
        # Skip hidden items
        if item.get("hidden"):
            continue

        product = item.get("product")
        if not product:
            continue

        # Skip items hidden on calendars
        if product.get("hide_on_calendars"):
            continue

        # Skip excluded categories
        category = product.get("category", "") or ""
        if category in EXCLUDE_CATEGORIES:
            continue

        name = product.get("name", "").strip()
        if not name:
            continue

        # Determine the date for this item
        day = item.get("day")
        month = item.get("month") or menu_data["month"]
        year = item.get("year") or menu_data["year"]

        if not all([day, month, year]):
            continue

        try:
            day_date = date(int(year), int(month), int(day))
        except ValueError:
            continue

        # Skip weekends
        if day_date.weekday() >= 5:
            continue

        if day_date not in daily:
            daily[day_date] = []

        if name not in daily[day_date]:
            daily[day_date].append(name)

    return daily


def format_event_title(items):
    """Format the list of menu items into a clean event title."""
    if not items:
        return "Lunch Menu"
    return " | ".join(items)


def generate_ics(daily_menu, month, year):
    """Generate ICS file content from the daily menu dict."""
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LCUSD Elementary Lunch//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:LCUSD Elementary Lunch {datetime(year, month, 1).strftime('%B %Y')}",
        "X-WR-TIMEZONE:America/Los_Angeles",
        "X-PUBLISHED-TTL:PT4H",
    ]

    for day_date in sorted(daily_menu.keys()):
        items = daily_menu[day_date]
        title = format_event_title(items)
        date_str = day_date.strftime("%Y%m%d")
        # All-day event: DTEND is the next day
        next_day = date(day_date.year, day_date.month, day_date.day)
        from datetime import timedelta
        end_date = (next_day + timedelta(days=1)).strftime("%Y%m%d")

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


def main():
    print("=" * 50)
    print("LCUSD Elementary Lunch Calendar Generator")
    print("=" * 50)

    # Get the current menu ID
    menu_id = get_menu_id()

    # Fetch menu from API
    print(f"Fetching menu data...")
    menu_data = fetch_menu(menu_id)

    month = menu_data["month"]
    year = menu_data["year"]
    month_label = datetime(year, month, 1).strftime("%B %Y")
    print(f"Menu loaded: {month_label}")

    # Save next month's ID for next run
    next_month = menu_data.get("nextMonthPublished")
    if next_month and next_month.get("id"):
        save_next_menu_id(next_month["id"])
    else:
        print("Note: No next month menu published yet.")

    # Build daily menu
    daily_menu = build_daily_menu(menu_data)
    print(f"Found menu items for {len(daily_menu)} school days")

    # Generate ICS
    ics_content = generate_ics(daily_menu, month, year)

    # Write output
    os.makedirs("docs", exist_ok=True)
    with open(OUTPUT_ICS, "w", encoding="utf-8") as f:
        f.write(ics_content)

    print(f"ICS file written to: {OUTPUT_ICS}")
    print(f"Events generated: {len(daily_menu)}")
    print("Done! ✅")


if __name__ == "__main__":
    main()
