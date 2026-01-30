"""Silence logic — determines whether Jarvis should speak or stay quiet.

Applies a rule chain considering confidence, fatigue, context focus,
arrival/settling state, and suggestion freshness.
"""

from datetime import datetime, timedelta

from intelligence._helpers import get_life_model
from intelligence.observation_tracker import get_recently_sent_suggestions

# Fatigue tracker — adaptive silence based on engagement
try:
    from services.fatigue_tracker import (
        get_dynamic_threshold as _fatigue_threshold,
        has_budget_remaining as _fatigue_has_budget,
    )
    FATIGUE_TRACKING_AVAILABLE = True
except ImportError:
    FATIGUE_TRACKING_AVAILABLE = False


def should_stay_silent(
    context: dict,
    suggestions: list,
    recent_history: list,
    confidence_threshold: float = 0.5,
    is_arrival: bool = False,
    is_settling: bool = False
) -> tuple[bool, str]:
    """
    Determine if Jarvis should stay silent or speak.

    Returns (should_be_silent, reason)

    SPEAK if:
    - Person just arrived home AND suggestions available AND confidence reasonable
    - Context just changed (confidence > 0.7) AND suggestions available
    - Safety/security issue detected
    - User explicitly requested check
    - New actionable suggestion (not offered recently)

    STAY SILENT if:
    - Same context, no new suggestions
    - Same suggestion offered recently (adaptive cooldown)
    - Low confidence (< threshold)
    - Focus context (working, sleeping)
    - No actionable suggestions
    - Settling period active with only activity-specific suggestions

    Args:
        context: Context inference result dict
        suggestions: List of generated suggestions
        recent_history: Recent decision/observation history
        confidence_threshold: Minimum confidence to speak
        is_arrival: True if person just arrived HOME (30+ min away)
        is_settling: True if within 5 min of home arrival

    Returns:
        Tuple of (should_be_silent, reason_string)
    """
    ctx_name = context.get("context", "unknown")
    confidence = context.get("confidence", 0)
    previous_context = context.get("previous_context")

    # Rule 0: Arrival bypass - suggest when someone arrives home AND confidence is reasonable
    # A 0.03-confidence "arrival" is noise, not a real arrival
    ARRIVAL_MIN_CONFIDENCE = 0.2
    if is_arrival and suggestions and confidence >= ARRIVAL_MIN_CONFIDENCE:
        return False, f"Arrival detected - welcoming with suggestion"

    # Rule 0.5: Settling period - suppress activity-specific suggestions
    # During first 5 min after home arrival, only allow arrival/comfort/info suggestions
    if is_settling and suggestions:
        SETTLING_ALLOWED_TYPES = {"transition", "comfort", "information"}
        settling_suggestions = [s for s in suggestions
                               if s.get("type") in SETTLING_ALLOWED_TYPES]
        if settling_suggestions:
            return False, "Settling period - arrival suggestions only"
        return True, "Settling period - waiting for activity to stabilize"

    # Fatigue system: adjust threshold and check budget before other rules
    effective_threshold = confidence_threshold
    if FATIGUE_TRACKING_AVAILABLE:
        # Budget check — if we've exhausted the daily budget, stay silent
        if not _fatigue_has_budget():
            return True, "Daily suggestion budget exhausted"
        # Dynamic threshold — raise bar when engagement is low
        dynamic = _fatigue_threshold()
        effective_threshold = max(confidence_threshold, dynamic)

    # Rule 1: Low confidence -> stay silent
    if confidence < effective_threshold:
        return True, f"Low confidence ({confidence:.2f} < {effective_threshold})"

    # Rule 2: No suggestions -> stay silent
    if not suggestions:
        return True, "No actionable suggestions"

    # Rule 2.5: Filter out suggestions already SENT to user recently
    # Uses adaptive cooldown per action (longer for ignored suggestions)
    recently_sent = get_recently_sent_suggestions(hours=2)
    sent_actions = {entry.get("suggestion", {}).get("action") for entry in recently_sent}

    not_yet_sent = [s for s in suggestions if s.get("action") not in sent_actions]
    if not not_yet_sent:
        return True, "All suggestions already sent to user recently"

    # Use filtered suggestions for remaining rules
    suggestions = not_yet_sent

    # Rule 3: Focus contexts -> stay silent
    model = get_life_model()
    focus_contexts = model.get("focus_contexts", ["working", "sleeping", "concentrating", "on_call", "meeting"])
    if ctx_name in focus_contexts:
        return True, f"Focus context ({ctx_name})"

    # Rule 4: Context just changed with high confidence -> speak
    if previous_context and previous_context != ctx_name and confidence > 0.7:
        return False, f"Context transition: {previous_context} → {ctx_name}"

    # Rule 5: Check if suggestions are new (not in recent history)
    recent_suggestions = _extract_recent_suggestions(recent_history, hours=2)
    new_suggestions = _filter_new_suggestions(suggestions, recent_suggestions)

    if not new_suggestions:
        return True, "All suggestions offered recently"

    # Rule 6: High-value suggestion with good acceptance rate -> speak
    high_value = [s for s in new_suggestions if s.get("acceptance_rate", 0) > 0.7]
    if high_value:
        return False, f"High-value suggestion available (acceptance rate > 70%)"

    # Rule 7: Safety or urgent issue -> speak
    urgent_types = ["safety", "security", "emergency", "alert"]
    urgent_suggestions = [s for s in suggestions if s.get("priority") == "urgent" or s.get("type") in urgent_types]
    if urgent_suggestions:
        return False, "Safety or urgent issue detected"

    # Rule 8: Context stable with new suggestions -> speak
    # Uses the same confidence_threshold passed to this function (not hardcoded)
    if len(new_suggestions) > 0 and confidence >= confidence_threshold:
        return False, f"New suggestions available ({confidence:.0%} confidence)"

    # Default: stay silent
    return True, "No compelling reason to speak"


def _extract_recent_suggestions(history: list, hours: int = 2) -> list:
    """
    Extract suggestions from recent history.

    Args:
        history: List of recent observations/decisions
        hours: How many hours to look back

    Returns:
        List of recent suggestions
    """
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []

    for entry in history:
        try:
            timestamp = datetime.fromisoformat(entry.get("timestamp", ""))
            if timestamp >= cutoff:
                # Extract suggestions if present
                if "suggestions" in entry:
                    recent.extend(entry["suggestions"])
                if "suggestion" in entry:
                    recent.append(entry["suggestion"])
        except (ValueError, AttributeError):
            continue

    return recent


def _filter_new_suggestions(suggestions: list, recent: list) -> list:
    """
    Filter out suggestions that were offered recently.

    Args:
        suggestions: Current suggestions
        recent: Recently offered suggestions

    Returns:
        List of new (not recently offered) suggestions
    """
    if not recent:
        return suggestions

    # Build set of recent suggestion keys
    recent_keys = set()
    for sugg in recent:
        # Use type+action as key, or just action if type not present
        if "type" in sugg and "action" in sugg:
            key = f"{sugg['type']}:{sugg['action']}"
        elif "action" in sugg:
            key = sugg["action"]
        else:
            continue
        recent_keys.add(key)

    # Filter new suggestions
    new = []
    for sugg in suggestions:
        if "type" in sugg and "action" in sugg:
            key = f"{sugg['type']}:{sugg['action']}"
        elif "action" in sugg:
            key = sugg["action"]
        else:
            new.append(sugg)  # Include suggestions without action
            continue

        if key not in recent_keys:
            new.append(sugg)

    return new
