#!/usr/bin/env python3
"""
External Context Engine — Calendar & Email integration for Jarvis.

Pulls upcoming calendar events and important emails via the `gog` CLI,
derives contextual signals, and caches the result as JSON for fast reads
by Jarvis hooks and life_context inference.

Usage:
    python3 external_context.py refresh   # Pull fresh data and write cache
    python3 external_context.py read      # Print cached context (or empty)

Environment:
    GOG_ACCOUNT  — Google account email (default: andrewpturko@gmail.com)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DATA_DIR = SKILL_DIR / "data"
CACHE_FILE = DATA_DIR / "external_context.json"

GOG_ACCOUNT = os.environ.get("GOG_ACCOUNT", "andrewpturko@gmail.com")
GOG_TIMEOUT = 15  # seconds — don't let gog hang the heartbeat

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_gog(args: list[str]) -> dict | list | None:
    """Run a gog CLI command with timeout, return parsed JSON or None."""
    cmd = ["gog"] + args + ["--json", "--account", GOG_ACCOUNT]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GOG_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"[external_context] gog error: {result.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("[external_context] gog CLI not found in PATH", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[external_context] gog timed out", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[external_context] gog output not valid JSON: {e}", file=sys.stderr)
        return None


def _parse_event_time(raw: str) -> datetime | None:
    """Parse an event start/end time from Google Calendar JSON.
    
    Handles both dateTime (specific time) and date (all-day) formats.
    """
    if not raw:
        return None
    # Try ISO datetime first (most common)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            # Ensure timezone-aware for comparison
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    # Fallback: dateutil-style parse
    try:
        from dateutil.parser import parse as du_parse
        return du_parse(raw)
    except Exception:
        return None


def _minutes_until(dt: datetime) -> int:
    """Minutes from now until a datetime. Negative = in the past."""
    now = datetime.now()
    # Strip timezone if the target is naive (all-day events)
    if dt.tzinfo is not None:
        now = datetime.now(timezone.utc)
        # Convert dt to UTC-aware for comparison
        diff = dt - now
    else:
        diff = dt - now
    return int(diff.total_seconds() / 60)


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

# Keywords for classifying events
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

# Email subject/from keywords
DELIVERY_KEYWORDS = [
    "delivered", "delivery", "shipped", "tracking", "package",
    "ups", "fedex", "usps", "amazon", "out for delivery",
    "arriving today", "informed delivery",
]
TRAVEL_KEYWORDS = [
    "flight", "boarding pass", "itinerary", "check-in",
    "reservation confirm", "hotel", "airline", "booking",
    "trip", "travel",
]


def _classify_event(summary: str, description: str = "") -> list[str]:
    """Return signal tags for a calendar event."""
    text = f"{summary} {description}".lower()
    tags = []
    if any(kw in text for kw in DINING_KEYWORDS):
        tags.append("calendar_dinner_reservation")
    if any(kw in text for kw in MEETING_KEYWORDS):
        tags.append("calendar_meeting")
    if any(kw in text for kw in SOCIAL_KEYWORDS):
        tags.append("calendar_social")
    return tags


def _classify_email(subject: str, from_addr: str, snippet: str = "") -> list[str]:
    """Return signal tags for an email."""
    text = f"{subject} {from_addr} {snippet}".lower()
    tags = []
    if any(kw in text for kw in DELIVERY_KEYWORDS):
        tags.append("email_delivery")
    if any(kw in text for kw in TRAVEL_KEYWORDS):
        tags.append("email_travel")
    return tags


# ---------------------------------------------------------------------------
# Core: Refresh
# ---------------------------------------------------------------------------

def refresh() -> dict:
    """Pull calendar events and emails, compute signals, write cache."""
    now = datetime.now()
    now_utc = datetime.now(timezone.utc)

    signals = []
    calendar_data = {"next_event": None, "events_today": []}
    email_data = {"important_unread": [], "flags": []}
    narrative_parts = []

    # ── Calendar ──────────────────────────────────────────────────────────
    from_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_time = (now_utc + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")

    raw_cal = _run_gog([
        "calendar", "events", "primary",
        "--from", from_time,
        "--to", to_time,
    ])

    events = []
    if raw_cal:
        # gog returns {"events": [...]} or a list directly
        if isinstance(raw_cal, dict):
            events = raw_cal.get("events", raw_cal.get("items", []))
        elif isinstance(raw_cal, list):
            events = raw_cal

    if events:
        for ev in events:
            summary = ev.get("summary", "Untitled")
            start_raw = ev.get("start", {})
            # gog may return {dateTime: ...} or {date: ...} or flat string
            if isinstance(start_raw, dict):
                start_str = start_raw.get("dateTime") or start_raw.get("date", "")
            else:
                start_str = str(start_raw)

            location = ev.get("location", "")
            description = ev.get("description", "")
            start_dt = _parse_event_time(start_str)
            mins_until = _minutes_until(start_dt) if start_dt else None

            event_entry = {
                "summary": summary,
                "start": start_str,
                "location": location,
                "minutes_until": mins_until,
            }
            calendar_data["events_today"].append(event_entry)

            # Classify this event
            ev_signals = _classify_event(summary, description)
            signals.extend(ev_signals)

            # Proximity signals
            if mins_until is not None:
                if 0 <= mins_until <= 60:
                    signals.append("calendar_event_soon")
                elif 60 < mins_until <= 180:
                    signals.append("calendar_event_upcoming")
                elif 180 < mins_until <= 480:
                    signals.append("calendar_event_later")

        # Set next event (soonest future event)
        future_events = [
            e for e in calendar_data["events_today"]
            if e["minutes_until"] is not None and e["minutes_until"] >= -15
        ]
        if future_events:
            future_events.sort(key=lambda e: e["minutes_until"])
            calendar_data["next_event"] = future_events[0]

        # Build narrative for calendar
        if calendar_data["next_event"]:
            ne = calendar_data["next_event"]
            mins = ne["minutes_until"]
            if mins <= 60:
                time_desc = f"in {mins} minutes"
            elif mins <= 120:
                time_desc = f"in about {mins // 60} hour{'s' if mins >= 120 else ''}"
            else:
                time_desc = f"in about {mins // 60} hours"
            loc_part = f" at {ne['location']}" if ne.get("location") else ""
            narrative_parts.append(f"{ne['summary']}{loc_part} {time_desc}.")
    else:
        # Check if evening is empty (after 5 PM with no events)
        if now.hour >= 17:
            signals.append("calendar_empty_evening")
            narrative_parts.append("No evening plans.")

    # Deduplicate signals so far
    signals = list(dict.fromkeys(signals))

    # ── Email ─────────────────────────────────────────────────────────────
    raw_mail = _run_gog([
        "gmail", "messages", "search",
        "in:inbox is:unread -category:promotions -category:social",
        "--max", "5",
    ])

    messages = []
    if raw_mail:
        if isinstance(raw_mail, dict):
            messages = raw_mail.get("messages", [])
        elif isinstance(raw_mail, list):
            messages = raw_mail

    email_flags = set()
    if messages:
        signals.append("email_important_unread")
        for msg in messages:
            subject = msg.get("subject", "")
            from_addr = msg.get("from", "")
            snippet = msg.get("snippet", "")

            email_data["important_unread"].append({
                "from": from_addr,
                "subject": subject,
                "snippet": snippet,
            })

            msg_flags = _classify_email(subject, from_addr, snippet)
            email_flags.update(msg_flags)
            signals.extend(msg_flags)

        email_data["flags"] = sorted(email_flags)

        count = len(messages)
        narrative_parts.append(f"{count} unread email{'s' if count != 1 else ''}.")
    else:
        narrative_parts.append("No important unread emails.")

    # ── Deduplicate signals ───────────────────────────────────────────────
    signals = list(dict.fromkeys(signals))

    # ── Build output ──────────────────────────────────────────────────────
    result = {
        "generated_at": now.isoformat(),
        "signals": signals,
        "calendar": calendar_data,
        "email": email_data,
        "narrative": " ".join(narrative_parts),
    }

    # Write cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(result, f, indent=2)

    return result


# ---------------------------------------------------------------------------
# Core: Read
# ---------------------------------------------------------------------------

def read_cache(max_age_minutes: int = 60) -> Optional[dict]:
    """Read cached external context. Returns None if missing or stale."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Check staleness
    generated_at = data.get("generated_at")
    if generated_at:
        try:
            gen_time = datetime.fromisoformat(generated_at)
            age_minutes = (datetime.now() - gen_time).total_seconds() / 60
            if age_minutes > max_age_minutes:
                return None
        except (ValueError, TypeError):
            pass

    return data


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: external_context.py <refresh|read>")
        print("  refresh  — Pull calendar + email, update cache")
        print("  read     — Print cached context (or empty if stale)")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "refresh":
        result = refresh()
        print(json.dumps(result, indent=2))
    elif cmd == "read":
        cached = read_cache()
        if cached:
            print(json.dumps(cached, indent=2))
        else:
            # Return empty context structure
            print(json.dumps({
                "generated_at": None,
                "signals": [],
                "calendar": {"next_event": None, "events_today": []},
                "email": {"important_unread": [], "flags": []},
                "narrative": "No external context available.",
            }, indent=2))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
