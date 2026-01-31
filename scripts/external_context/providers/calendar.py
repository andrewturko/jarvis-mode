"""
Apple Calendar provider — pulls upcoming events via cal-events.sh.

Signals emitted:
    calendar_event_soon        — event within 60 min
    calendar_event_upcoming    — event within 60–180 min
    calendar_event_later       — event within 180–480 min
    calendar_dinner_reservation
    calendar_meeting
    calendar_social
    calendar_empty_evening     — after 5 PM with no events
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from external_context.base_provider import ExternalContextProvider

# Path to apple-calendar skill scripts
_SKILL_DIR = Path(os.environ.get(
    "APPLE_CALENDAR_SKILL_DIR",
    Path.home() / "clawd" / "skills" / "apple-calendar" / "scripts",
))

CAL_EVENTS_SCRIPT = _SKILL_DIR / "cal-events.sh"
CAL_TIMEOUT = 15

# Calendars to skip (read-only or noise)
SKIP_CALENDARS = {"Birthdays", "US Holidays", "Siri Suggestions"}

# ---------------------------------------------------------------------------
# Classification keywords
# ---------------------------------------------------------------------------

DINING_KEYWORDS = [
    "dinner", "restaurant", "brunch", "lunch", "reservation",
    "canlis", "sushi", "steakhouse", "bistro", "cafe", "dining",
    "happy hour", "bar", "tavern", "pub", "eat",
]
MEETING_KEYWORDS = [
    "meeting", "standup", "sync", "1:1", "one-on-one", "review",
    "sprint", "retro", "planning", "call", "zoom", "teams",
    "interview", "demo", "workshop", "all-hands", "huddle",
]
SOCIAL_KEYWORDS = [
    "party", "hangout", "game night", "birthday", "bbq",
    "get-together", "potluck", "drinks", "social", "concert",
    "show", "movie", "theater", "festival", "friend",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cal_events(days_ahead: int = 1) -> Optional[List[Dict]]:
    """Run cal-events.sh and parse the pipe-delimited output."""
    if not CAL_EVENTS_SCRIPT.exists():
        print(f"[calendar] script not found: {CAL_EVENTS_SCRIPT}", file=sys.stderr)
        return None

    try:
        result = subprocess.run(
            ["bash", str(CAL_EVENTS_SCRIPT), str(days_ahead)],
            capture_output=True, text=True, timeout=CAL_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"[calendar] cal-events error: {result.stderr.strip()}", file=sys.stderr)
            return None
    except subprocess.TimeoutExpired:
        print("[calendar] cal-events timed out", file=sys.stderr)
        return None

    events = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        uid, summary, start_str, end_str, all_day, location, calendar = parts[:7]
        if calendar in SKIP_CALENDARS:
            continue
        events.append({
            "uid": uid,
            "summary": summary,
            "start": start_str,
            "end": end_str,
            "all_day": all_day.lower() == "true",
            "location": location,
            "calendar": calendar,
        })
    return events


def _parse_apple_datetime(raw: str) -> Optional[datetime]:
    """Parse Apple Calendar datetime strings like 'Saturday, January 31, 2026 at 2:00:00 PM'."""
    if not raw:
        return None
    # AppleScript format: "DayOfWeek, Month Day, Year at H:MM:SS AM/PM"
    for fmt in (
        "%A, %B %d, %Y at %I:%M:%S %p",
        "%A, %B %d, %Y at %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _minutes_until(dt: datetime) -> int:
    """Minutes from now until *dt*. Negative = in the past."""
    diff = dt - datetime.now()
    return int(diff.total_seconds() / 60)


def _classify_event(summary: str) -> List[str]:
    text = summary.lower()
    tags = []
    if any(kw in text for kw in DINING_KEYWORDS):
        tags.append("calendar_dinner_reservation")
    if any(kw in text for kw in MEETING_KEYWORDS):
        tags.append("calendar_meeting")
    if any(kw in text for kw in SOCIAL_KEYWORDS):
        tags.append("calendar_social")
    return tags


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class CalendarProvider(ExternalContextProvider):
    name = "calendar"
    stale_after_minutes = 30

    def refresh(self) -> Dict:
        raw_events = _run_cal_events(days_ahead=1)

        data = {"next_event": None, "events_today": []}

        if not raw_events:
            return data

        for ev in raw_events:
            start_dt = _parse_apple_datetime(ev["start"])
            mins_until = _minutes_until(start_dt) if start_dt else None

            data["events_today"].append({
                "summary": ev["summary"],
                "start": ev["start"],
                "location": ev["location"],
                "calendar": ev["calendar"],
                "all_day": ev["all_day"],
                "minutes_until": mins_until,
            })

        # Determine next event (soonest future non-all-day event)
        future = [
            e for e in data["events_today"]
            if e["minutes_until"] is not None
            and e["minutes_until"] >= -15
            and not e["all_day"]
        ]
        if future:
            future.sort(key=lambda e: e["minutes_until"])
            data["next_event"] = future[0]

        return data

    def signals(self, data: Dict) -> List[str]:
        sigs = []
        now_hour = datetime.now().hour

        for ev in data.get("events_today", []):
            ev_sigs = _classify_event(ev.get("summary", ""))
            sigs.extend(ev_sigs)

            mins = ev.get("minutes_until")
            if mins is not None and not ev.get("all_day"):
                if 0 <= mins <= 60:
                    sigs.append("calendar_event_soon")
                elif 60 < mins <= 180:
                    sigs.append("calendar_event_upcoming")
                elif 180 < mins <= 480:
                    sigs.append("calendar_event_later")

        if not data.get("events_today") and now_hour >= 17:
            sigs.append("calendar_empty_evening")

        # Deduplicate preserving order
        seen = set()
        deduped = []
        for s in sigs:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    def narrative(self, data: Dict) -> str:
        ne = data.get("next_event")
        total = len(data.get("events_today", []))

        if not ne:
            if total > 0:
                return f"{total} all-day event(s) today."
            now_hour = datetime.now().hour
            if now_hour >= 17:
                return "No evening plans."
            return ""

        mins = ne.get("minutes_until")
        if mins is None:
            return ""
        if mins <= 60:
            time_desc = f"in {mins} minutes"
        elif mins <= 120:
            time_desc = "in about 1 hour"
        else:
            time_desc = f"in about {mins // 60} hours"

        loc = f" at {ne['location']}" if ne.get("location") else ""
        return f"{ne['summary']}{loc} {time_desc}."
