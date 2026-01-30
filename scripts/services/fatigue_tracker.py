#!/usr/bin/env python3
"""
Fatigue Tracker - Adaptive silence based on user engagement.

Tracks suggestion fatigue and adjusts system behavior:
- Treats no-response as soft negative signal (after 30 min timeout)
- Exponential backoff per suggestion type when ignored
- Dynamic confidence threshold based on acceptance rate
- Daily suggestion budget that decreases with low engagement
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

from core.paths import PATTERNS_FILE

# Defaults
DEFAULT_DAILY_BUDGET = 8
IGNORE_TIMEOUT_MINUTES = 30
BACKOFF_MULTIPLIER = 1.5
MAX_BACKOFF_HOURS = 24.0
BASE_COOLDOWN_HOURS = 2.0


def _load_patterns() -> dict:
    try:
        return json.loads(PATTERNS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_patterns(patterns: dict):
    PATTERNS_FILE.write_text(json.dumps(patterns, indent=2))


def _get_fatigue_state(patterns: Optional[dict] = None) -> dict:
    """Get or initialize fatigue state from patterns.json."""
    if patterns is None:
        patterns = _load_patterns()

    fatigue = patterns.get("fatigue_state", {})
    today = datetime.now().strftime("%Y-%m-%d")

    # Reset daily counters if new day
    if fatigue.get("last_reset") != today:
        fatigue = {
            "daily_budget": DEFAULT_DAILY_BUDGET,
            "suggestions_today": 0,
            "accepted_today": 0,
            "ignored_today": 0,
            "dynamic_threshold": 0.3,
            "backoff_multipliers": fatigue.get("backoff_multipliers", {}),
            "last_reset": today
        }
        patterns["fatigue_state"] = fatigue
        _save_patterns(patterns)

    return fatigue


def _save_fatigue_state(fatigue: dict):
    """Save fatigue state back to patterns.json."""
    patterns = _load_patterns()
    patterns["fatigue_state"] = fatigue
    _save_patterns(patterns)


def process_ignored_suggestions():
    """
    Called periodically (e.g., on each poll).
    Marks suggestions awaiting feedback for 30+ min as ignored.
    Records soft negative signal.
    """
    patterns = _load_patterns()
    sent = patterns.get("sent_suggestions", {}).get("recent", [])
    cutoff = datetime.now() - timedelta(minutes=IGNORE_TIMEOUT_MINUTES)
    fatigue = _get_fatigue_state(patterns)

    changed = False
    for entry in sent:
        if entry.get("awaiting_feedback", False):
            ts_str = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts < cutoff:
                    entry["awaiting_feedback"] = False
                    entry["outcome"] = "ignored"
                    changed = True

                    # Update backoff multiplier for this action
                    action = entry.get("suggestion", {}).get("action", "")
                    if action:
                        multipliers = fatigue.get("backoff_multipliers", {})
                        current = multipliers.get(action, 1.0)
                        multipliers[action] = min(current * BACKOFF_MULTIPLIER, MAX_BACKOFF_HOURS / BASE_COOLDOWN_HOURS)
                        fatigue["backoff_multipliers"] = multipliers

                    fatigue["ignored_today"] = fatigue.get("ignored_today", 0) + 1
            except (ValueError, TypeError):
                continue

    if changed:
        patterns["fatigue_state"] = fatigue
        _save_patterns(patterns)


def get_cooldown_hours(action: str) -> float:
    """
    Get adaptive cooldown for a suggestion action.

    Base cooldown (2h) * backoff multiplier for this action.
    Actions that are repeatedly ignored get longer cooldowns.
    """
    fatigue = _get_fatigue_state()
    multiplier = fatigue.get("backoff_multipliers", {}).get(action, 1.0)
    return BASE_COOLDOWN_HOURS * multiplier


def get_dynamic_threshold() -> float:
    """
    Get raised confidence threshold based on today's acceptance rate.

    Low engagement -> higher bar for speaking.
    """
    fatigue = _get_fatigue_state()
    sent = fatigue.get("suggestions_today", 0)
    accepted = fatigue.get("accepted_today", 0)

    if sent < 3:
        return 0.3  # Not enough data, use default

    rate = accepted / sent
    if rate < 0.1:
        return 0.7   # Very low engagement - high bar
    elif rate < 0.3:
        return 0.5   # Low engagement - moderate bar
    else:
        return 0.3   # Normal engagement


def has_budget_remaining() -> bool:
    """
    Check if daily suggestion budget allows more suggestions.

    Budget halves when 3+ suggestions sent with 0 accepted.
    """
    fatigue = _get_fatigue_state()
    budget = fatigue.get("daily_budget", DEFAULT_DAILY_BUDGET)
    sent = fatigue.get("suggestions_today", 0)
    accepted = fatigue.get("accepted_today", 0)

    # Cut budget if engagement is very low
    if sent >= 3 and accepted == 0:
        budget = max(3, budget // 2)

    return sent < budget


def record_suggestion_sent():
    """Record that a suggestion was sent (increments daily counter)."""
    fatigue = _get_fatigue_state()
    fatigue["suggestions_today"] = fatigue.get("suggestions_today", 0) + 1
    _save_fatigue_state(fatigue)


def record_acceptance(action: str = ""):
    """
    Record that a suggestion was accepted.
    Resets backoff multiplier for that action.
    """
    fatigue = _get_fatigue_state()
    fatigue["accepted_today"] = fatigue.get("accepted_today", 0) + 1

    # Reset backoff for accepted action
    if action:
        multipliers = fatigue.get("backoff_multipliers", {})
        if action in multipliers:
            multipliers[action] = 1.0
        fatigue["backoff_multipliers"] = multipliers

    _save_fatigue_state(fatigue)
