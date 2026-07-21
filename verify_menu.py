#!/usr/bin/env python3
"""
Verify menu discovery against the live LCUSD nutrition site.

Every failure mode in this pipeline is SILENT — the ICS stays valid, the food is
just wrong. So verification is manual and deliberate rather than a passing build.

USAGE
    python verify_menu.py              # check the current + next month
    python verify_menu.py 8 2026       # check a specific month
    python verify_menu.py --regress    # re-run the known-good May/June cases

WHAT IT CHECKS
    1. Does the site say the month is published?
    2. Does discovery resolve a menu id, from the right section and site code?
    3. Does that id come back from the GraphQL API with items?
    4. Does the API's month field line up with the month requested?

Step 4 is the one to watch. The API reports months 0-INDEXED (May = 4), but
fetch_menu() now normalises that at the boundary, so by the time a menu reaches
here its month should match what you asked for. A mismatch means the
normalisation broke or the API changed convention — either way, investigate
before trusting the feed. See lunchlook-backend/docs/MENU_PIPELINE.md.
"""

import calendar
import sys
from datetime import date

from fetch_menu import fetch_menu, scrape_menu_id_from_website

# Verified 2026-07-21 by hand against the district site and the GraphQL API.
KNOWN_GOOD = {
    (5, 2026): "69d6b19bd937c4166b253f15",   # elementary lunch, May 2026
    (6, 2026): "69fcc669c294c00ad12d64a2",   # elementary lunch, June 2026
}
# Menus that must NEVER be returned by elementary-lunch discovery.
WRONG_MENUS = {
    "69d6b2756737d03c7903bd18": "BREAKFAST (shared across all levels)",
    "69d6b278d8aea24b4b7ea43a": "SECONDARY 7-12 lunch",
}


def fetch(menu_id):
    """Goes through fetch_menu so we verify exactly what the pipeline sees,
    including the 0-indexed-month normalisation."""
    return fetch_menu(menu_id)


def check(month, year, expect=None):
    label = f"{calendar.month_name[month]} {year}"
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")

    menu_id = scrape_menu_id_from_website(month, year)
    if not menu_id:
        print(f"RESULT: no menu id — either not published yet, or discovery broke.")
        print("        Check the site by hand before assuming it's just timing.")
        return None

    if menu_id in WRONG_MENUS:
        print(f"RESULT: *** WRONG MENU *** resolved to {WRONG_MENUS[menu_id]}")
        return False

    if expect and menu_id != expect:
        print(f"RESULT: FAIL — expected {expect}, got {menu_id}")
        return False

    menu = fetch(menu_id)
    items = [i for i in menu["items"] if not i.get("hidden")]
    days = sorted({i["day"] for i in items if i.get("day")})
    api_month = menu["month"]

    print(f"  menu id     : {menu_id}")
    print(f"  month       : {api_month} ({calendar.month_name[api_month]}) after normalisation")
    print(f"  items / days: {len(items)} items across {len(days)} school days")
    print(f"  day numbers : {days}")

    if api_month != month:
        print(f"  WARNING: this menu is {calendar.month_name[api_month]}, "
              f"you asked for {calendar.month_name[month]}.")
        print("           Month normalisation may have broken, or the API changed")
        print("           convention. Do NOT trust the feed until this is explained.")
        return False

    print("\n  Spot-check these against the district's published PDF:")
    for d in days[:3]:
        names = [i["product"]["name"] for i in items
                 if i.get("day") == d and i.get("product") and i["product"].get("name")]
        print(f"    {calendar.month_name[month]} {d}: {' | '.join(names)}")

    print("\nRESULT: PASS (still confirm the spot-checks by eye)")
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if "--regress" in sys.argv:
        results = [check(m, y, expect=mid) for (m, y), mid in KNOWN_GOOD.items()]
        ok = all(r is True for r in results)
        print(f"\n{'=' * 60}\nREGRESSION: {'ALL PASS' if ok else 'FAILURES ABOVE'}")
        return 0 if ok else 1

    if len(args) == 2:
        check(int(args[0]), int(args[1]))
        return 0

    today = date.today()
    check(today.month, today.year)
    nxt_m, nxt_y = (1, today.year + 1) if today.month == 12 else (today.month + 1, today.year)
    check(nxt_m, nxt_y)
    return 0


if __name__ == "__main__":
    sys.exit(main())
