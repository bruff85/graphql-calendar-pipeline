#!/usr/bin/env python3
"""
LCUSD Elementary School Lunch Calendar Generator

Schedule:
- Starts running on the 27th of each month at 8pm PT
- Then runs daily at 10am and 6pm PT until next month's menu is found
- Once found, stops updating until the 27th of the following month
- Manual triggers always run regardless of date
"""

import hashlib
import uuid
import re
import requests
from datetime import datetime, date, timedelta
import os
from notify import notify_success, notify_found_failure, notify_not_found

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
GRAPHQL_URL = "https://api.schoolnutritionandfitness.com/graphql"
LCUSD_MENU_URL = "https://nutrition.lcusd.net/index.php?sid=2506080150154913&page=menus&sm={month}&sy={year}"
SEED_MENU_ID = "698b7e94cc6f3104111f19e7"
MENU_ID_FILE = "current_menu_id.txt"
NEXT_MONTH_FOUND_FILE = "next_month_found.txt"
EXCLUDE_CATEGORIES = {"Milk", "Condiment", "Extra"}

# ─────────────────────────────────────────────
# FEEDS — prod and dev are separate files
# ─────────────────────────────────────────────
# Prod and dev previously shared ONE ics file, so anything written while testing
# was immediately live to paying parents — there was no staging step to "promote"
# from, because there was only ever one artifact. That is how 25 hand-authored
# "TEST:" events ended up being what a prod subscriber's calendar would show.
#
# Dev is a MIRROR, not a sandbox: same fetch, same parse, same generation, so a
# bug in the real path actually reproduces there. The only difference is a
# "[DEV] " prefix on every summary and on the calendar name, so when you're
# subscribed to both you can tell at a glance which one you're looking at —
# including after the real menu publishes, when the two would otherwise be
# identical exactly when you're mid-troubleshoot.
CALNAME = "LCE AI Lunch Calendar"
DEV_PREFIX = "[DEV] "

FEEDS = [
    {"env": "prod", "path": "docs/lunch.ics",     "prefix": "",         "calname": CALNAME},
    {"env": "dev",  "path": "docs/lunch-dev.ics", "prefix": DEV_PREFIX, "calname": DEV_PREFIX + CALNAME},
]

# DEV_ONLY=true writes just the dev feed, leaving prod untouched — the way to
# watch a fix land in your own calendar before it reaches a parent's. The next
# scheduled run writes both again and restores the mirror.
def active_feeds():
    if os.environ.get("DEV_ONLY", "false").lower() == "true":
        print("DEV_ONLY=true — writing the dev feed only; prod will not be touched.")
        return [f for f in FEEDS if f["env"] == "dev"]
    return FEEDS

# ─────────────────────────────────────────────
# PLACEHOLDER EVENTS
# ─────────────────────────────────────────────
# When a month's menu hasn't been published yet, an empty calendar is
# indistinguishable from a broken one. A parent who just paid and sees nothing
# assumes the product is broken and contacts support. These events say "we're
# working, the district hasn't posted yet" instead of saying nothing.
#
# They replace themselves. Event UIDs are derived from the date, so when the
# real menu arrives the same UID is reissued with real food and calendar apps
# update the entry in place — no duplicates, nothing for the parent to do.
#
# Source: school_calendars row la-canada-unified/2026-27 (status=verified,
# manually validated 2026-07-18 from the district PDF). UPDATE EACH YEAR.
SCHOOL_YEAR_LABEL = "2026-27"
FIRST_DAY = date(2026, 8, 13)
LAST_DAY = date(2027, 6, 3)
FALL_SEMESTER_END = date(2026, 12, 18)
SPRING_SEMESTER_START = date(2027, 1, 5)

# Known no-school weekdays. The verified calendar row only records semester
# boundaries, so this list is incomplete — holidays inside a month (Thanksgiving,
# MLK, spring break) are NOT all here yet. That is tolerable because a
# placeholder is cleared the moment the real menu publishes, and it only ever
# claims "not posted yet" rather than asserting lunch is served. Fill in from the
# district calendar when convenient.
NO_SCHOOL_DATES = {
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 11),  # Veterans Day
}

PLACEHOLDER_SUMMARY = "Lunch menu not posted yet"
PLACEHOLDER_DESCRIPTION = (
    "The district hasn't published this month's lunch menu yet. "
    "This will fill in automatically once they do - nothing for you to do. - LunchLook"
)
# Marks an event as ours to remove later. Without a marker the cleanup pass would
# have to match on summary text, which breaks the moment the wording changes.
PLACEHOLDER_MARKER = "X-LUNCHLOOK-PLACEHOLDER"

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


def get_next_month_found():
    """Returns the month/year string we already found, e.g. '5/2026', or None."""
    if os.path.exists(NEXT_MONTH_FOUND_FILE):
        with open(NEXT_MONTH_FOUND_FILE, "r") as f:
            val = f.read().strip()
            if val:
                return val
    return None


def save_next_month_found(month, year):
    with open(NEXT_MONTH_FOUND_FILE, "w") as f:
        f.write(f"{month}/{year}")


def clear_next_month_found():
    if os.path.exists(NEXT_MONTH_FOUND_FILE):
        os.remove(NEXT_MONTH_FOUND_FILE)


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
    menu = data["data"]["menu"]
    # The SNF API reports months 0-INDEXED: May comes back as 4. Python's date
    # and datetime are 1-indexed. Normalise once, here at the boundary, so every
    # comparison and every date() call downstream speaks one convention --
    # converting at each use site is exactly how this off-by-one survived.
    if menu and menu.get("month") is not None:
        menu["month"] = int(menu["month"]) + 1
    return menu


# Which menu we want, and how to recognise it on the page.
# LCUSD groups menus under an <h3> per level, each holding a "Breakfast Menu"
# and a "Lunch Menu" link. Both carry the SAME siteCode, so the site code alone
# cannot tell breakfast from lunch — the heading and link text are what
# disambiguate. Elementary is siteCode 24701; secondary 7-12 is 25235/24702 and
# shares one menu across both (see docs/ in lunchlook-backend: MENU_PIPELINE.md).
TARGET_SECTION = "Elementary School"
TARGET_LINK_TEXT = "Lunch Menu"
TARGET_SITE_CODE = "24701"

DOWNLOAD_LINK_RE = re.compile(
    r'<a\s+href="(/downloadMenu\.php/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>', re.IGNORECASE
)
SECTION_RE = re.compile(r"<h3[^>]*>\s*([^<]+?)\s*</h3>", re.IGNORECASE)


def _resolve_download_link(path):
    """Follow a /downloadMenu.php/... link one hop and pull the menu id out of it.

    The redesigned site no longer prints the 24-hex menu id in the page HTML.
    It emits a numeric download link that 302s to the familiar viewer URL:
        .../webmenus2/#/view?id=<24hex>&siteCode=<code>
    Returns (menu_id, site_code), either of which may be None.
    """
    try:
        resp = requests.get(
            f"https://nutrition.lcusd.net{path}",
            timeout=30,
            allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LunchCalendarBot/1.0)"},
        )
    except requests.RequestException as e:
        print(f"    Could not resolve {path}: {e}")
        return None, None

    location = resp.headers.get("Location", "")
    menu_id = re.search(r"id=([a-f0-9]{24})", location, re.IGNORECASE)
    site_code = re.search(r"siteCode=(\d+)", location, re.IGNORECASE)
    return (menu_id.group(1) if menu_id else None,
            site_code.group(1) if site_code else None)


def scrape_via_download_links(html):
    """Find the elementary LUNCH menu id on the redesigned nutrition site.

    Walks the page in order, tracking the most recent <h3> heading, so a link is
    only considered once we're inside the Elementary section. Guards on the site
    code as a final check: if the district ever reorders the page, we'd rather
    return nothing (and alert) than silently publish the wrong menu.
    """
    markers = [(m.start(), "section", m.group(1)) for m in SECTION_RE.finditer(html)]
    markers += [(m.start(), "link", (m.group(1), m.group(2))) for m in DOWNLOAD_LINK_RE.finditer(html)]
    markers.sort()

    section = None
    for _, kind, value in markers:
        if kind == "section":
            section = value
            continue
        path, text = value
        if section != TARGET_SECTION or TARGET_LINK_TEXT.lower() not in text.lower():
            continue

        print(f"  Found '{section}' / '{text}' -> {path}")
        menu_id, site_code = _resolve_download_link(path)
        if not menu_id:
            print("    Link did not redirect to a menu id.")
            return None
        if site_code != TARGET_SITE_CODE:
            print(f"    REFUSING: expected siteCode {TARGET_SITE_CODE}, got {site_code}. "
                  f"Page layout may have changed — not guessing.")
            return None
        print(f"  Resolved menu ID: {menu_id} (siteCode {site_code})")
        return menu_id

    print(f"  No '{TARGET_SECTION}' / '{TARGET_LINK_TEXT}' download link on the page.")
    return None


def scrape_menu_id_from_website(month, year):
    url = LCUSD_MENU_URL.format(month=month, year=year)
    print(f"  Scraping LCUSD site for {month}/{year}: {url}")
    try:
        response = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; LunchCalendarBot/1.0)"
        })
        response.raise_for_status()
        html = response.text
        if "No menus published for this month" in html:
            print(f"  Site confirms: no menus published for {month}/{year} yet.")
            return None

        # Current site structure (July 2026 redesign) — try this first.
        menu_id = scrape_via_download_links(html)
        if menu_id:
            return menu_id

        # Legacy inline patterns, kept in case the district reverts or a cached
        # page is served. NOTE: these match breakfast as readily as lunch, since
        # both share siteCode 24701 — they are a last resort, not a peer.
        print("  Falling back to legacy inline patterns...")
        patterns = [
            r'webmenus2[^"]*id=([a-f0-9]{24})[^"]*siteCode=24701',
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
    if month == 12:
        return 1, year + 1
    return month + 1, year


def should_run_today(today):
    """
    Run on the 27th or later (initial search for next month),
    or on the 1st through 15th (daily retries if not found yet).
    """
    return today.day >= 27 or today.day <= 15


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


def parse_existing_events(ics_path):
    # Parse existing ICS and return dict of {date_str: event_block}
    if not os.path.exists(ics_path):
        return {}
    with open(ics_path, "r", encoding="utf-8") as f:
        content = f.read()
    events = {}
    raw_events = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", content, re.DOTALL)
    for event in raw_events:
        match = re.search(r"DTSTART[^:]*:(\d{8})", event)
        if match:
            date_str = match.group(1)
            events[date_str] = event.strip()
    return events


def is_placeholder(event_block):
    return PLACEHOLDER_MARKER in event_block


def is_school_day(day_date):
    """Weekday, inside a semester, and not a known holiday."""
    if day_date.weekday() >= 5:
        return False
    if not (FIRST_DAY <= day_date <= LAST_DAY):
        return False
    # Winter break sits between the two semesters.
    if FALL_SEMESTER_END < day_date < SPRING_SEMESTER_START:
        return False
    return day_date not in NO_SCHOOL_DATES


def school_days_in_month(month, year):
    days, day_date = [], date(year, month, 1)
    while day_date.month == month:
        if is_school_day(day_date):
            days.append(day_date)
        day_date += timedelta(days=1)
    return days


def build_event(date_str, uid, now, summary, description, placeholder=False):
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART;TZID=America/Los_Angeles:{date_str}T113000",
        f"DTEND;TZID=America/Los_Angeles:{date_str}T123000",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        "TRANSP:TRANSPARENT",
    ]
    if placeholder:
        lines.append(f"{PLACEHOLDER_MARKER}:1")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def event_uid(date_str):
    """Deterministic per date — this is what lets a placeholder be replaced by the
    real menu in place rather than appearing alongside it."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"lcusd-lunch-{date_str}"))


def build_placeholder_events(month, year, now, prefix=""):
    events = {}
    for day_date in school_days_in_month(month, year):
        date_str = day_date.strftime("%Y%m%d")
        events[date_str] = build_event(
            date_str, event_uid(date_str), now,
            prefix + PLACEHOLDER_SUMMARY, PLACEHOLDER_DESCRIPTION, placeholder=True,
        )
    return events


def get_window_months(new_month, new_year):
    """
    Returns a set of (month, year) tuples for the rolling 2-month window:
    current month and the new month being added.
    """
    window = set()
    m, y = new_month, new_year
    for _ in range(2):
        window.add((m, y))
        if m == 1:
            m, y = 12, y - 1
        else:
            m -= 1
    return window


def generate_ics(daily_menu, month, year, existing_ics_path=None, prefix="", calname=CALNAME):
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # Determine the 4-month rolling window to keep
    window = get_window_months(month, year)

    # Load existing events and filter to only keep events within the window
    existing_events = {}
    if existing_ics_path:
        all_existing = parse_existing_events(existing_ics_path)
        dropped_placeholders = 0
        for date_str, event_block in all_existing.items():
            try:
                event_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
            except ValueError:
                continue
            if (event_date.month, event_date.year) not in window:
                continue
            # Drop every placeholder in the month we're publishing. Merging alone
            # is not enough: a placeholder on a date the real menu does NOT cover
            # (minimum day, staff day, an unlisted holiday) has nothing to
            # overwrite it, and would keep telling parents the menu isn't posted
            # after it was posted. Once we have the real menu, its silence about a
            # date IS the answer — no lunch that day.
            if is_placeholder(event_block) and (event_date.month, event_date.year) == (month, year):
                dropped_placeholders += 1
                continue
            existing_events[date_str] = event_block
        print(f"  Retaining {len(existing_events)} events within the 2-month window.")
        if dropped_placeholders:
            print(f"  Cleared {dropped_placeholders} placeholder(s) now that the real menu is in.")

    # Build new events for this month
    new_events = {}
    for day_date in sorted(daily_menu.keys()):
        items = daily_menu[day_date]
        title = prefix + (" | ".join(items) if items else "Lunch Menu")
        date_str = day_date.strftime("%Y%m%d")
        new_events[date_str] = build_event(
            date_str, event_uid(date_str), now,
            title, "LCUSD Elementary School Lunch Menu",
        )

    # Merge: new month overrides any existing events for same dates
    all_events = {**existing_events, **new_events}

    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LCUSD Elementary Lunch//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{calname}",
        "X-WR-TIMEZONE:America/Los_Angeles",
        "X-PUBLISHED-TTL:PT4H",
    ]
    event_lines = [all_events[d] for d in sorted(all_events.keys())]
    footer = ["END:VCALENDAR"]
    return "\r\n".join(header + event_lines + footer)

def write_placeholders(month, year, path, prefix="", calname=CALNAME):
    """Publish 'menu not posted yet' events for a month with no menu.

    Only ever ADDS to dates that have no event at all — never overwrites real
    food, and never touches other months. Returns True if the file changed.
    """
    school_days = school_days_in_month(month, year)
    label = datetime(year, month, 1).strftime("%B %Y")
    if not school_days:
        print(f"  {label} has no school days — no placeholders needed.")
        return False

    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    existing = parse_existing_events(path)
    placeholders = build_placeholder_events(month, year, now, prefix)

    # Skip any date that already has an event — real food, or a placeholder we
    # published earlier. Rebuilding an existing placeholder would only change its
    # DTSTAMP, which rewrites the file and creates a commit on every scheduled
    # run (twice a day, forever) while changing nothing a parent would see.
    to_add = {d: ev for d, ev in placeholders.items() if d not in existing}
    if not to_add:
        print(f"  {label}: every school day already has an event — nothing to hold.")
        return False

    merged = {**existing, **to_add}
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LCUSD Elementary Lunch//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{calname}",
        "X-WR-TIMEZONE:America/Los_Angeles",
        "X-PUBLISHED-TTL:PT4H",
    ]
    content = "\r\n".join(
        header + [merged[d] for d in sorted(merged)] + ["END:VCALENDAR"]
    )

    os.makedirs("docs", exist_ok=True)
    if file_hash(path) == hashlib.md5(content.encode()).hexdigest():
        print(f"  {label}: placeholders already published — no change.")
        return False

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Published {len(to_add)} placeholder(s) for {label} "
          f"so the calendar reads as pending rather than broken.")
    return True


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

    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"

    if not force_run and not should_run_today(today):
        print(f"Today (day {today.day}) is not a scheduled run day. Skipping.")
        return

    if force_run:
        print("Manual trigger detected — forcing run regardless of date.")

    # Determine target month
    # On the 27th or later we're looking for NEXT month
    # On the 1st-15th we're still looking for the current month
    if today.day >= 27:
        target_month, target_year = get_next_month(today.month, today.year)
    else:
        target_month, target_year = today.month, today.year

    target_label = datetime(target_year, target_month, 1).strftime("%B %Y")
    print(f"Target month: {target_label}")

    # Reset the found flag FIRST when we roll into a new search cycle (27th)
    if today.day == 27:
        print("Starting new monthly search cycle — resetting found flag.")
        clear_next_month_found()

    # Check if we already found and loaded this month successfully
    already_found = get_next_month_found()
    if already_found == f"{target_month}/{target_year}" and not force_run:
        print(f"Already successfully loaded {target_label} — nothing to do.")
        print("Will retry on the 27th for the following month.")
        return

    # ── Step 1: Traverse API chain ─────────────────────────
    current_id = get_current_menu_id()
    print(f"\nStep 1: Checking API chain from menu ID: {current_id}")
    current_menu = fetch_menu(current_id)
    target_menu = None
    visited = set()
    menu = current_menu

    for _ in range(6):
        m_id = menu["id"]
        if m_id in visited:
            print("  Traversal loop detected, stopping.")
            break
        visited.add(m_id)

        m_month = menu["month"]
        m_year = menu["year"]
        print(f"  Checking menu {m_id}: {m_month}/{m_year}")

        if m_month == target_month and m_year == target_year:
            print(f"  Found target month via API chain!")
            target_menu = menu
            save_menu_id(menu["id"])
            break

        next_info = menu.get("nextMonthPublished")
        if not next_info:
            # Do NOT relabel here. This menu carries a month, that month is not
            # the one we want, and having items proves nothing -- every menu has
            # items. Claiming it as the target published June's food on August
            # dates AND marked August "found", so the real August menu would
            # never have been fetched. Fall through to the website scrape, and
            # then to placeholders, which is the honest answer: not out yet.
            print(f"  Chain ends at {m_month}/{m_year} — {target_label} not published in the API.")
            break

        print(f"  Following nextMonthPublished to {next_info['id']}...")
        menu = fetch_menu(next_info["id"])

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
                    save_menu_id(scraped_id)
                else:
                    print(f"  Scraped ID returned wrong month: {candidate['month']}/{candidate['year']}")
                    # Trust today's date if items exist
                    item_days = set(item.get("day") for item in candidate["items"] if item.get("day"))
                    if item_days:
                        print(f"  Items exist — treating as {target_label}")
                        candidate["month"] = target_month
                        candidate["year"] = target_year
                        target_menu = candidate
                        save_menu_id(scraped_id)
            except Exception as e:
                print(f"  Failed to fetch scraped menu ID: {e}")

    # ── Step 3: No menu yet — publish placeholders instead ─
    if not target_menu:
        print(f"\n{target_label} menu not available yet.")
        for feed in active_feeds():
            print(f"  [{feed['env']}] {feed['path']}")
            if not write_placeholders(target_month, target_year,
                                      feed["path"], feed["prefix"], feed["calname"]):
                print("    No change.")
        print("Will retry at next scheduled run (10am or 6pm today, or tomorrow).")
        notify_not_found(CALNAME, target_label)
        return

    # ── Generate and save ICS ──────────────────────────────
    # One fetch, one parse, written to every active feed. Dev is generated from
    # the same data as prod rather than from a separate source, so a bug in the
    # real path shows up in dev instead of being masked by stand-in data.
    month = target_menu["month"]
    year = target_menu["year"]
    month_label = datetime(year, month, 1).strftime("%B %Y")
    print(f"\nGenerating ICS for: {month_label}")

    daily_menu = build_daily_menu(target_menu)
    print(f"Menu items found for {len(daily_menu)} school days")

    os.makedirs("docs", exist_ok=True)
    for feed in active_feeds():
        ics_content = generate_ics(daily_menu, month, year,
                                   existing_ics_path=feed["path"],
                                   prefix=feed["prefix"], calname=feed["calname"])
        if file_hash(feed["path"]) == hashlib.md5(ics_content.encode()).hexdigest():
            print(f"  [{feed['env']}] {feed['path']} already up to date.")
            continue
        with open(feed["path"], "w", encoding="utf-8") as f:
            f.write(ics_content)
        print(f"  [{feed['env']}] {feed['path']} updated with {len(daily_menu)} events.")
        # Only alert on the feed parents actually see; a dev-feed write is not news.
        if feed["env"] == "prod":
            notify_success(CALNAME, month_label, len(daily_menu))

    # Mark this month as successfully found so we stop retrying
    save_next_month_found(month, year)
    print(f"Marked {month}/{year} as found — retries will stop until next cycle.")
    print("\nDone! ✅")


if __name__ == "__main__":
    main()
