"""
Event Planner provider — checks for planned events from the manifest file.

The agent writes planned events to a JSON manifest; this provider reads it
and surfaces relevant signals so Jarvis can adapt its context.

Manifest path: ~/clawd/skills/jarvis-mode/data/planned_events.json

Signals emitted:
    event_planned_today   — there's a planned event today
    event_planned_soon    — planned event within 3 hours
    event_date_night      — it's a date night
    event_social          — social outing planned
    event_dinner_out      — dinner reservation / plan
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from external_context.base_provider import ExternalContextProvider

MANIFEST_PATH = os.path.join(
    os.path.expanduser("~"),
    "clawd", "skills", "jarvis-mode", "data", "planned_events.json",
)

# ---------------------------------------------------------------------------
# Event type mapping
# ---------------------------------------------------------------------------

DATE_NIGHT_TYPES = ["date-night", "date_night", "date", "romantic"]
SOCIAL_TYPES = ["social", "party", "hangout", "gathering", "game-night"]
DINNER_TYPES = ["dinner", "restaurant", "dining", "date-night", "date_night"]

DATE_NIGHT_KEYWORDS = ["date night", "date-night", "romantic", "anniversary"]
SOCIAL_KEYWORDS = ["party", "hangout", "game night", "bbq", "potluck", "drinks"]
DINNER_KEYWORDS = [
    "dinner", "restaurant", "reservation", "dining", "brunch",
    "lunch", "supper", "canlis", "steakhouse", "sushi",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_manifest() -> List[Dict]:
    """Load the planned events manifest. Returns empty list on any error."""
    if not os.path.exists(MANIFEST_PATH):
        return []
    try:
        with open(MANIFEST_PATH, "r") as f:
            data = json.load(f)
        events = data.get("events", [])
        if not isinstance(events, list):
            return []
        return events
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        print(f"[event_planner] Error reading manifest: {exc}", file=sys.stderr)
        return []


def _parse_event_datetime(event: Dict) -> Optional[datetime]:
    """Parse the event date + start_time into a datetime."""
    date_str = event.get("date", "")
    time_str = event.get("start_time", "")
    if not date_str:
        return None
    try:
        if time_str:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


def _is_today(event: Dict) -> bool:
    """Check if the event is scheduled for today."""
    date_str = event.get("date", "")
    if not date_str:
        return False
    try:
        event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        return event_date == datetime.now().date()
    except ValueError:
        return False


def _minutes_until(dt: datetime) -> int:
    """Minutes from now until dt. Negative = in the past."""
    diff = dt - datetime.now()
    return int(diff.total_seconds() / 60)


def _classify_event(event: Dict) -> List[str]:
    """Classify an event by its type and name into signal tags."""
    tags: List[str] = []
    event_type = event.get("type", "").lower()
    event_name = event.get("name", "").lower()
    venue = event.get("venue", "").lower()
    text = f"{event_name} {event_type} {venue}"

    if event_type in DATE_NIGHT_TYPES or any(kw in text for kw in DATE_NIGHT_KEYWORDS):
        tags.append("event_date_night")
    if event_type in SOCIAL_TYPES or any(kw in text for kw in SOCIAL_KEYWORDS):
        tags.append("event_social")
    if event_type in DINNER_TYPES or any(kw in text for kw in DINNER_KEYWORDS):
        tags.append("event_dinner_out")

    return tags


def _format_time(time_str: str) -> str:
    """Format '19:00' as '7pm', '14:30' as '2:30pm'."""
    if not time_str:
        return ""
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        if dt.minute == 0:
            return dt.strftime("%-I%p").lower()
        return dt.strftime("%-I:%M%p").lower()
    except ValueError:
        return time_str


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class EventPlannerProvider(ExternalContextProvider):
    name = "event_planner"
    stale_after_minutes = 15

    def refresh(self) -> Dict:
        events = _load_manifest()
        if not events:
            return {"today_events": [], "upcoming_events": []}

        now = datetime.now()
        today_events: List[Dict] = []
        upcoming_events: List[Dict] = []

        for event in events:
            event_dt = _parse_event_datetime(event)
            is_today = _is_today(event)
            mins_until = _minutes_until(event_dt) if event_dt else None

            enriched = {
                "name": event.get("name", "Untitled"),
                "date": event.get("date", ""),
                "start_time": event.get("start_time", ""),
                "venue": event.get("venue", ""),
                "location": event.get("location", ""),
                "type": event.get("type", ""),
                "minutes_until": mins_until,
                "is_today": is_today,
                "tags": _classify_event(event),
            }

            if is_today:
                today_events.append(enriched)
            elif mins_until is not None and 0 < mins_until <= 4320:
                # Within 3 days
                upcoming_events.append(enriched)

        return {
            "today_events": today_events,
            "upcoming_events": upcoming_events,
        }

    def signals(self, data: Dict) -> List[str]:
        sigs: List[str] = []

        for ev in data.get("today_events", []):
            sigs.append("event_planned_today")
            mins = ev.get("minutes_until")
            if mins is not None and 0 <= mins <= 180:
                sigs.append("event_planned_soon")
            sigs.extend(ev.get("tags", []))

        # Deduplicate preserving order
        seen = set()
        deduped: List[str] = []
        for s in sigs:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    def narrative(self, data: Dict) -> str:
        today = data.get("today_events", [])
        if not today:
            return ""

        parts: List[str] = []
        for ev in today:
            name = ev.get("name", "Event")
            venue = ev.get("venue", "")
            time_str = _format_time(ev.get("start_time", ""))
            location = ev.get("location", "")

            desc = name
            if venue:
                desc += f" at {venue}"
            if time_str:
                desc += f" tonight at {time_str}" if ev.get("minutes_until", 0) > 0 else f" at {time_str}"
            parts.append(desc)

        return "; ".join(parts) + "."
