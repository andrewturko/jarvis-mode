"""
Google Calendar provider — pulls upcoming events via ``gog calendar events``.

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

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from external_context.base_provider import ExternalContextProvider

GOG_ACCOUNT = os.environ.get("GOG_ACCOUNT", "andrewpturko@gmail.com")
GOG_TIMEOUT = 15

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

def _run_gog(args: List[str]) -> Optional[object]:
    """Run a gog CLI command with timeout, return parsed JSON or None."""
    cmd = ["gog"] + args + ["--json", "--account", GOG_ACCOUNT]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GOG_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"[calendar] gog error: {result.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("[calendar] gog CLI not found in PATH", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[calendar] gog timed out", file=sys.stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"[calendar] gog output not valid JSON: {exc}", file=sys.stderr)
        return None


def _parse_event_time(raw: str) -> Optional[datetime]:
    """Parse an event start/end time from Google Calendar JSON."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    try:
        from dateutil.parser import parse as du_parse
        return du_parse(raw)
    except Exception:
        return None


def _minutes_until(dt: datetime) -> int:
    """Minutes from now until *dt*. Negative = in the past."""
    now = datetime.now()
    if dt.tzinfo is not None:
        now = datetime.now(timezone.utc)
    diff = dt - now
    return int(diff.total_seconds() / 60)


def _classify_event(summary: str, description: str = "") -> List[str]:
    text = f"{summary} {description}".lower()
    tags: List[str] = []
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
        now_utc = datetime.now(timezone.utc)
        from_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_time = (now_utc + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")

        raw = _run_gog([
            "calendar", "events", "primary",
            "--from", from_time,
            "--to", to_time,
        ])

        events: List[Dict] = []
        if raw:
            if isinstance(raw, dict):
                events = raw.get("events", raw.get("items", []))
            elif isinstance(raw, list):
                events = raw

        data: Dict = {"next_event": None, "events_today": []}

        for ev in events:
            summary = ev.get("summary", "Untitled")
            start_raw = ev.get("start", {})
            if isinstance(start_raw, dict):
                start_str = start_raw.get("dateTime") or start_raw.get("date", "")
            else:
                start_str = str(start_raw)

            location = ev.get("location", "")
            start_dt = _parse_event_time(start_str)
            mins_until = _minutes_until(start_dt) if start_dt else None

            data["events_today"].append({
                "summary": summary,
                "start": start_str,
                "location": location,
                "minutes_until": mins_until,
                "description": ev.get("description", ""),
            })

        # Determine next event (soonest future event)
        future = [
            e for e in data["events_today"]
            if e["minutes_until"] is not None and e["minutes_until"] >= -15
        ]
        if future:
            future.sort(key=lambda e: e["minutes_until"])
            data["next_event"] = future[0]

        return data

    def signals(self, data: Dict) -> List[str]:
        sigs: List[str] = []
        now_hour = datetime.now().hour

        for ev in data.get("events_today", []):
            ev_sigs = _classify_event(ev.get("summary", ""), ev.get("description", ""))
            sigs.extend(ev_sigs)

            mins = ev.get("minutes_until")
            if mins is not None:
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
        deduped: List[str] = []
        for s in sigs:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    def narrative(self, data: Dict) -> str:
        ne = data.get("next_event")
        if not ne:
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
