"""Observation and suggestion tracking for pattern learning.

Records observations, suggestion responses, sent suggestions, and user
feedback. Feeds the temporal learner and fatigue tracker.
"""

from datetime import datetime, timedelta
from typing import Optional

from intelligence._helpers import (
    load_json, save_json, get_life_model, get_patterns, save_patterns,
)
from core.paths import STATE_FILE

# Temporal learner — record observations for time-based learning
try:
    from services.temporal_learner import (
        record_observation as record_temporal_observation,
    )
    TEMPORAL_LEARNING_AVAILABLE = True
except ImportError:
    TEMPORAL_LEARNING_AVAILABLE = False

# Fatigue tracker — adaptive cooldown for suggestions
try:
    from services.fatigue_tracker import (
        get_cooldown_hours as _fatigue_cooldown,
    )
    FATIGUE_TRACKING_AVAILABLE = True
except ImportError:
    FATIGUE_TRACKING_AVAILABLE = False


def record_observation(context: str, room: str, observation: dict):
    """Record an observation for pattern learning.

    Also feeds the temporal learner when a context is observed
    with sufficient confidence.
    """
    patterns = get_patterns()

    history = patterns.get("context_observations", {}).get("history", [])
    history.append({
        "timestamp": datetime.now().isoformat(),
        "context": context,
        "room": room,
        "observation": observation
    })

    # Keep last 100 observations
    patterns["context_observations"]["history"] = history[-100:]
    save_patterns(patterns)

    # --- Temporal learning: record high-confidence context observations ---
    confidence = observation.get("confidence", 0)
    if confidence >= 0.5 and TEMPORAL_LEARNING_AVAILABLE:
        record_temporal_observation(context, positive=True)


def record_suggestion_response(suggestion: dict, accepted: bool):
    """Record whether a suggestion was accepted for learning.

    Also records a temporal observation so the temporal learner
    knows when contexts are confirmed or rejected.
    """
    patterns = get_patterns()

    # Record in history
    recent = patterns.get("suggestion_history", {}).get("recent", [])
    recent.append({
        "timestamp": datetime.now().isoformat(),
        "suggestion": suggestion,
        "accepted": accepted
    })
    patterns["suggestion_history"]["recent"] = recent[-50:]

    # Update learned patterns
    if "context" in suggestion and "type" in suggestion:
        pattern_key = f"{suggestion['context']}+{suggestion['type']}"
        learned = patterns.setdefault("learned_patterns", {}).setdefault("patterns", {})

        if pattern_key not in learned:
            learned[pattern_key] = {
                "total": 0,
                "accepted": 0,
                "preferred_action": None,
                "acceptance_rate": 0
            }

        learned[pattern_key]["total"] += 1
        if accepted:
            learned[pattern_key]["accepted"] += 1
            learned[pattern_key]["preferred_action"] = suggestion.get("action")

        total = learned[pattern_key]["total"]
        acc = learned[pattern_key]["accepted"]
        learned[pattern_key]["acceptance_rate"] = round(acc / total, 2) if total > 0 else 0

    save_patterns(patterns)

    # --- Temporal learning: record when contexts are confirmed/rejected ---
    context_name = suggestion.get("context")
    if context_name and TEMPORAL_LEARNING_AVAILABLE:
        record_temporal_observation(context_name, positive=accepted)


def record_sent_suggestion(room: str, suggestion: dict, message_sent: str = None, context: str = None):
    """
    Record that a suggestion was SENT to the user.

    This is different from suggestions being generated - this tracks
    what was actually delivered via message tool.

    Args:
        room: Room where the suggestion was made
        suggestion: The suggestion dict that was acted on
        message_sent: The actual message text sent to user
        context: The inferred context (e.g., "waking_up", "cooking") - important for learning
    """
    patterns = get_patterns()

    # Initialize sent_suggestions if needed
    if "sent_suggestions" not in patterns:
        patterns["sent_suggestions"] = {"recent": []}

    # Infer context from room if not provided
    inferred_context = context
    if not inferred_context:
        # Try to get from state.json
        state = load_json(STATE_FILE)
        room_data = state.get("rooms", {}).get(room, {})
        last_ctx = room_data.get("last_context", {})
        inferred_context = last_ctx.get("inferred") if last_ctx else None

    # Ensure suggestion has context for learning
    suggestion_with_context = dict(suggestion)
    if inferred_context:
        suggestion_with_context["context"] = inferred_context

    patterns["sent_suggestions"]["recent"].append({
        "timestamp": datetime.now().isoformat(),
        "room": room,
        "context": inferred_context,
        "suggestion": suggestion_with_context,
        "message": message_sent,
        "awaiting_feedback": True
    })

    # Keep last 100 sent suggestions
    patterns["sent_suggestions"]["recent"] = patterns["sent_suggestions"]["recent"][-100:]

    # Also track the most recent one for easy feedback matching
    patterns["sent_suggestions"]["last"] = {
        "timestamp": datetime.now().isoformat(),
        "room": room,
        "context": inferred_context,
        "suggestion": suggestion_with_context,
        "message": message_sent
    }

    save_patterns(patterns)


def get_last_awaiting_feedback() -> Optional[dict]:
    """
    Get the most recent suggestion that's awaiting user feedback.

    Returns:
        Dict with suggestion info or None if nothing awaiting
    """
    patterns = get_patterns()
    last = patterns.get("sent_suggestions", {}).get("last")

    if not last:
        return None

    # Check if it was sent within the feedback window (configurable via life-model settings)
    try:
        model = get_life_model()
        feedback_window = model.get("settings", {}).get("feedback_window_hours", 4)
        timestamp = datetime.fromisoformat(last.get("timestamp", ""))
        if datetime.now() - timestamp > timedelta(hours=feedback_window):
            return None
    except (ValueError, AttributeError):
        return None

    return last


def process_user_feedback(response: str) -> Optional[dict]:
    """
    Process a user's yes/no response to the last suggestion.

    Args:
        response: User's response text (e.g., "yes", "no", "sure", "nah")

    Returns:
        Dict with feedback result or None if no suggestion awaiting
    """
    last = get_last_awaiting_feedback()
    if not last:
        return None

    # Determine if accepted
    positive_responses = ["yes", "yeah", "sure", "ok", "okay", "do it", "please", "yep", "y"]
    negative_responses = ["no", "nope", "nah", "not now", "later", "skip", "n"]

    response_lower = response.lower().strip()

    accepted = None
    if any(pos in response_lower for pos in positive_responses):
        accepted = True
    elif any(neg in response_lower for neg in negative_responses):
        accepted = False
    else:
        return None  # Couldn't determine intent

    # Record the feedback
    suggestion = last.get("suggestion", {})
    # Use the stored context from when the suggestion was sent
    if "context" not in suggestion:
        suggestion["context"] = last.get("context") or last.get("room", "unknown")
    record_suggestion_response(suggestion, accepted)

    # Clear the awaiting feedback
    patterns = get_patterns()
    if "sent_suggestions" in patterns:
        patterns["sent_suggestions"]["last"] = None
        save_patterns(patterns)

    return {
        "suggestion": suggestion.get("action"),
        "accepted": accepted,
        "room": last.get("room")
    }


def get_recently_sent_suggestions(hours: int = 2) -> list:
    """
    Get suggestions that were SENT to user in recent hours.

    Args:
        hours: How many hours to look back

    Returns:
        List of sent suggestion records
    """
    patterns = get_patterns()
    sent = patterns.get("sent_suggestions", {}).get("recent", [])

    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []

    for entry in sent:
        try:
            timestamp = datetime.fromisoformat(entry.get("timestamp", ""))
            if timestamp >= cutoff:
                recent.append(entry)
        except (ValueError, AttributeError):
            continue

    return recent


def was_suggestion_sent_recently(suggestion_action: str, hours: int = None) -> bool:
    """
    Check if a specific suggestion action was sent recently.

    Uses adaptive cooldown from FatigueTracker when available.
    Ignored suggestions get longer cooldowns (exponential backoff).

    Args:
        suggestion_action: The action name (e.g., "play_morning_music")
        hours: Override cooldown hours (None = use adaptive/default 2h)

    Returns:
        True if this suggestion was sent within the cooldown window
    """
    if hours is None:
        if FATIGUE_TRACKING_AVAILABLE:
            hours = _fatigue_cooldown(suggestion_action)
        else:
            hours = 2

    recent = get_recently_sent_suggestions(hours=max(int(hours) + 1, 2))

    cutoff = datetime.now() - timedelta(hours=hours)
    for entry in recent:
        sent_action = entry.get("suggestion", {}).get("action")
        if sent_action == suggestion_action:
            try:
                ts = datetime.fromisoformat(entry.get("timestamp", ""))
                if ts >= cutoff:
                    return True
            except (ValueError, AttributeError):
                return True  # Can't parse timestamp, assume recent

    return False


def get_recent_observations(room: str, hours: int = 2) -> list:
    """
    Get recent observations for a room within time window.

    Temporal context - what's been happening in this room.

    Args:
        room: Room name
        hours: Number of hours to look back

    Returns:
        List of observation dicts with timestamps
    """
    state = load_json(STATE_FILE)
    rooms = state.get("rooms", {})
    room_state = rooms.get(room, {})
    observations = room_state.get("recent_observations", [])

    # Filter by time window
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = []

    for obs in observations:
        try:
            obs_time = datetime.fromisoformat(obs.get("timestamp", ""))
            if obs_time >= cutoff:
                recent.append(obs)
        except (ValueError, KeyError):
            continue

    return recent
