# Handoff: Event "Notes" with a Link Back to the Source

How this repo puts a **Notes / Description** blurb on each calendar event — including a
clickable link to the exact source menu — and how to reproduce it in a **swim
calendar** repo where the link should point at the swim calendar instead.

All code referenced here lives in `fetch_menu.py`.

---

## 1. What "Notes" actually is

In an `.ics` (iCalendar / RFC 5545) file, each event is a `VEVENT` block. The
text a calendar app shows as **"Notes"** (Apple Calendar) or **"Description"**
(Google Calendar, Outlook) is the **`DESCRIPTION`** property of that event.

A minimal event with notes looks like this:

```
BEGIN:VEVENT
UID:...
DTSTAMP:20260804T120000Z
DTSTART;TZID=America/Los_Angeles:20260825T113000
DTEND;TZID=America/Los_Angeles:20260825T123000
SUMMARY:Cheese Quesadilla | Beef Nachos | Corn
DESCRIPTION:Cheese Quesadilla · Beef Nachos · Corn\n\nFull published menu for August 2026 (milk\, fruit & anything not shown above):\nhttps://www.schoolnutritionandfitness.com/webmenus2/#/view?id=...&siteCode=24701\n\n...
TRANSP:TRANSPARENT
END:VEVENT
```

Two things are doing the work:

1. The whole notes body is a single `DESCRIPTION:` line.
2. Line breaks inside the notes are the literal two-character sequence `\n`, and
   commas are escaped as `\,`. That escaping is mandatory — see §4.

---

## 2. The three functions involved

| Function | Location | Role |
|---|---|---|
| `build_menu_description()` | `fetch_menu.py:434` | Builds the notes **text**, including the source link |
| `ics_escape()` | `fetch_menu.py:411` | Escapes that text so it's a legal ICS value |
| `build_event()` | `fetch_menu.py:469` | Drops the escaped text into the `DESCRIPTION:` line of the `VEVENT` |

The flow is: **build the text → escape it → put it on the `DESCRIPTION` line.**

### 2a. Building the notes text — `build_menu_description()`

```python
# fetch_menu.py:430
SNF_MENU_VIEW_URL = ("https://www.schoolnutritionandfitness.com/webmenus2/"
                     "#/view?id={menu_id}&siteCode={site_code}")

# fetch_menu.py:434
def build_menu_description(items, day_date, menu_id=None):
    month_label = day_date.strftime("%B %Y")
    if menu_id:
        url = SNF_MENU_VIEW_URL.format(menu_id=menu_id, site_code=TARGET_SITE_CODE)
    else:
        url = LCUSD_MENU_URL.format(month=day_date.month, year=day_date.year)
    parts = []
    if items:
        parts.append(" · ".join(items))   # human-readable list of the day's items
        parts.append("")                  # blank line
    parts.append(f"Full published menu for {month_label} "
                 f"(milk, fruit & anything not shown above):")
    parts.append(url)                     # <-- the link back to the source
    parts.append("")
    parts.append("Menus follow the school's published calendar; "
                 "actual meals may vary. Confirm with your school.")
    parts.append("")
    parts.append(ATTRIBUTION)             # brand + donation nudge, every event
    return ics_escape("\n".join(parts))   # join with real newlines, THEN escape
```

The pattern to notice:

- The notes are assembled as a **list of lines** (`parts`), then joined with `"\n"`.
- **The link is just one of those lines.** Nothing special — the calendar app
  auto-linkifies a bare URL in the notes.
- There are **two ways to build the link**, and the code prefers the specific one:
  - **Deep link** — `SNF_MENU_VIEW_URL` with a `menu_id`, which opens the *exact*
    source that generated the events (one tap, no navigation).
  - **Fallback link** — `LCUSD_MENU_URL` (a month-picker page), used when there's
    no `menu_id`. Still correct, just one extra click.
- `ATTRIBUTION` (`fetch_menu.py:97`) is appended to **every** event's notes so any
  re-shared copy still carries branding.

### 2b. Escaping — `ics_escape()`

```python
# fetch_menu.py:411
def ics_escape(text):
    return (text.replace("\\", "\\\\")   # backslash FIRST (or it double-escapes)
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\n", "\\n"))   # real newline -> literal \n
```

### 2c. Placing it on the event — `build_event()`

```python
# fetch_menu.py:469
def build_event(date_str, uid, now, summary, description, placeholder=False):
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART;TZID=America/Los_Angeles:{date_str}T113000",
        f"DTEND;TZID=America/Los_Angeles:{date_str}T123000",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",   # <-- escaped notes go here
        "TRANSP:TRANSPARENT",
    ]
    ...
    return "\r\n".join(lines)
```

Call site (`fetch_menu.py:557`):

```python
new_events[date_str] = build_event(
    date_str, event_uid(date_str), now,
    title,
    build_menu_description(items, day_date, menu_id),   # notes built here
)
```

---

## 3. Why the source link is built the way it is

- **Deep link vs. fallback:** always emit a link, but make it a one-tap deep link
  when the exact source id is known, and a broader picker URL otherwise. This is
  the key idea to carry over.
- **The link points at the precise source used**, not a generic homepage — for the
  menu it's the exact `menu_id` + `siteCode` the events were built from. For swim,
  the analog is linking to the specific meet/practice (or at least the right team's
  schedule), not just the club's front page.
- **The URL is a plain line in the notes.** No `<a>` tags, no HTML — `.ics`
  `DESCRIPTION` is plain text; calendar clients make bare URLs tappable.

---

## 4. Gotchas (these will bite you if you skip them)

1. **You MUST escape the text.** Commas and semicolons are field separators in ICS.
   A menu item like "Rice, Beans" or a note with a semicolon silently corrupts the
   event unless you run it through `ics_escape()`. **Escape backslash first.**
2. **URLs generally don't need escaping**, *but* a URL containing a comma or
   semicolon in its query string would — running the whole notes string through
   `ics_escape()` handles that for free, so build the URL into the text and escape
   the whole thing once (as `build_menu_description` does). Do **not** escape twice.
3. **Newlines become literal `\n`.** Build the notes as separate lines and join with
   `"\n"` *before* escaping; `ics_escape` converts them to the ICS `\n` sequence.
4. **Line folding is NOT implemented here.** RFC 5545 says lines longer than 75
   octets *should* be folded (continued on a new line starting with a space). This
   repo emits long unfolded `DESCRIPTION` lines and relies on client tolerance —
   Apple/Google/Outlook accept it. If your swim feed will be consumed by a stricter
   parser, add folding. (Not required to match this repo's behavior.)
5. **`TRANSP:TRANSPARENT`** marks the event as "free" so it doesn't block the user's
   availability — keep it for informational calendars.

---

## 5. Adapting this for the swim calendar

Goal: same notes pattern, but the link points at the **swim calendar** (e.g. the
team's schedule page, or a deep link to the specific meet/practice).

### Minimal port

```python
def ics_escape(text):
    return (text.replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\n", "\\n"))

# One static link to the swim calendar, OR a deep-link template for a specific event.
SWIM_CALENDAR_URL = "https://your-team.example.com/calendar"
SWIM_EVENT_URL    = "https://your-team.example.com/calendar/event/{event_id}"

SWIM_ATTRIBUTION = "Brought to you by <Your Team> — see the full schedule online."

def build_swim_description(details, day_date, event_id=None):
    """Notes for one swim event, with a link back to the swim calendar."""
    # Prefer a deep link to the exact event; fall back to the calendar home.
    if event_id:
        url = SWIM_EVENT_URL.format(event_id=event_id)
    else:
        url = SWIM_CALENDAR_URL

    parts = []
    if details:                       # e.g. ["Warm-up 5:30pm", "Meet vs. Rival 6:00pm"]
        parts.append(" · ".join(details))
        parts.append("")
    parts.append("Full swim schedule (times, location & any changes):")
    parts.append(url)                 # <-- link to the swim calendar
    parts.append("")
    parts.append("Times may change; confirm with your coach.")
    parts.append("")
    parts.append(SWIM_ATTRIBUTION)
    return ics_escape("\n".join(parts))   # build text, then escape ONCE
```

Then use it exactly like the menu version — build the escaped notes and pass them
as the `description` argument to your `build_event()`:

```python
description = build_swim_description(details, day_date, event_id=meet_id)
event = build_event(date_str, uid, now, summary, description)
```

### What to change vs. keep

| Keep as-is | Swap for swim |
|---|---|
| `ics_escape()` (verbatim) | `SNF_MENU_VIEW_URL` → `SWIM_EVENT_URL` / `SWIM_CALENDAR_URL` |
| "build list of lines → join with `\n` → escape once" shape | The item list (`items`) → swim details (times/opponent/location) |
| Deep-link-when-known, fallback-otherwise logic | `ATTRIBUTION` text → your team's text |
| Putting the URL on its own line as plain text | The disclaimer line wording |

### Decision to make

- **Static link** (every event points at the same swim-calendar page) is simplest.
- **Deep link per event** (each event links to its own meet/practice) is nicer if
  your swim source has stable per-event URLs/ids — same trade-off as the menu's
  `menu_id` deep link vs. the month-picker fallback.

---

## 6. TL;DR

1. Notes = the `DESCRIPTION` property of a `VEVENT`.
2. Build the notes as a list of lines (one of them a bare URL to the source), join
   with `"\n"`, then run the whole thing through `ics_escape()` **once**.
3. Prefer a **deep link to the exact source**; fall back to a general page when you
   don't have the specific id.
4. Escape backslash first; commas/semicolons/newlines are mandatory to escape.
5. For swim: reuse `ics_escape()` and the "lines → join → escape" shape unchanged;
   only swap the URL (to the swim calendar) and the descriptive text.
