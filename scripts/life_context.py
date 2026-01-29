#!/usr/bin/env python3
"""
Life Context Engine
Infers life context from observations, learns patterns, suggests actions.

Now integrates with PatternAnalyzer for learned behavioral predictions.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add services to path for pattern analyzer
sys.path.insert(0, str(Path(__file__).parent / "services"))

SKILL_DIR = Path(__file__).parent.parent
LIFE_MODEL_FILE = SKILL_DIR / "life-model.json"
CAPABILITIES_FILE = SKILL_DIR / "capabilities.json"
PATTERNS_FILE = SKILL_DIR / "patterns.json"
STATE_FILE = SKILL_DIR / "state.json"


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_life_model():
    return load_json(LIFE_MODEL_FILE)


def get_capabilities():
    return load_json(CAPABILITIES_FILE)


def get_patterns():
    return load_json(PATTERNS_FILE)


def save_patterns(patterns):
    save_json(PATTERNS_FILE, patterns)


def get_time_context():
    """Get time-based context signals."""
    now = datetime.now()
    hour = now.hour
    
    return {
        "hour": hour,
        "time_of_day": (
            "night" if hour < 6 else
            "early_morning" if hour < 8 else
            "morning" if hour < 11 else
            "midday" if hour < 14 else
            "afternoon" if hour < 17 else
            "evening" if hour < 21 else
            "late_evening" if hour < 23 else
            "night"
        ),
        "is_morning": 6 <= hour < 11,
        "is_meal_time": hour in [7, 8, 12, 13, 18, 19, 20],
        "is_evening": 17 <= hour < 23,
        "is_late_night": hour >= 23 or hour < 6,
        "is_work_hours": 9 <= hour < 17,
        "day_of_week": now.strftime("%A"),
        "is_weekend": now.weekday() >= 5
    }


def infer_context(room_observations: dict, home_state: dict) -> dict:
    """
    Infer the current life context from observations.
    
    Args:
        room_observations: Dict with room -> {person_detected, activity, ...}
        home_state: Dict with lights_on, music_playing, etc.
    
    Returns:
        Dict with inferred context and confidence
    """
    time_ctx = get_time_context()
    model = get_life_model()
    state = load_json(STATE_FILE)
    
    # Gather signals
    signals = []
    
    # Time signals
    if time_ctx["is_morning"]:
        signals.append("morning_hours")
    if time_ctx["is_evening"]:
        signals.append("evening_hours")
    if time_ctx["is_late_night"]:
        signals.append("late_evening")
    if time_ctx["is_meal_time"]:
        signals.append("meal_time_hours")
    
    # Room presence signals
    occupied_rooms = []
    for room, obs in room_observations.items():
        if obs.get("person_detected"):
            occupied_rooms.append(room)
            if room == "kitchen":
                signals.append("kitchen_presence")
                if obs.get("activity_duration", 0) > 10:
                    signals.append("extended_kitchen_activity")
            elif room == "living_room":
                signals.append("living_room_presence")
                if obs.get("activity") in ["watching tv", "on couch", "relaxing"]:
                    signals.append("tv_or_couch")
                    signals.append("stationary")
            elif room == "dining":
                signals.append("dining_area_presence")
    
    if not occupied_rooms:
        signals.append("all_rooms_empty")
        signals.append("no_motion_all_rooms")
    
    # Check for context transitions
    last_context = state.get("current_context")
    last_room = state.get("last_occupied_room")
    
    if last_room == "kitchen" and "living_room" in occupied_rooms:
        signals.append("left_kitchen")
        if time_ctx["is_evening"]:
            signals.append("post_meal_time")
    
    if last_room == "living_room" and "primary_bedroom" in occupied_rooms:
        signals.append("bedroom_transition")
    
    # Score each context
    context_scores = {}
    contexts = model.get("contexts", {})
    
    for ctx_name, ctx_def in contexts.items():
        ctx_signals = ctx_def.get("signals", [])
        matches = sum(1 for s in ctx_signals if s in signals)
        if matches > 0:
            context_scores[ctx_name] = matches / len(ctx_signals) if ctx_signals else 0
    
    # Find best match
    if context_scores:
        best_context = max(context_scores, key=context_scores.get)
        confidence = context_scores[best_context]
    else:
        best_context = "unknown"
        confidence = 0
    
    # Get needs for this context
    ctx_def = contexts.get(best_context, {})
    typical_needs = ctx_def.get("typical_needs", [])
    
    return {
        "context": best_context,
        "confidence": round(confidence, 2),
        "signals": signals,
        "typical_needs": typical_needs,
        "time": time_ctx,
        "occupied_rooms": occupied_rooms,
        "previous_context": last_context
    }


def get_suggestions(context_result: dict, capabilities: dict = None,
                    recent_actions: list = None, home_state: dict = None) -> list:
    """
    Generate state-aware suggestions based on context and home state.

    Checks current home state (music playing, lights on/off) to avoid
    suggesting things that are already active or irrelevant.

    Returns list of suggestion dicts with action, reason, priority, message.
    Each suggestion includes a pre-composed 'message' the agent can send directly.
    """
    if capabilities is None:
        capabilities = get_capabilities()
    if home_state is None:
        home_state = {}

    model = get_life_model()
    patterns = get_patterns()

    context = context_result["context"]
    needs = context_result["typical_needs"]
    time_ctx = context_result["time"]

    # Current home state for filtering
    music_playing = home_state.get("music_playing", False)
    lights_on = home_state.get("lights_on", [])
    media_playing = home_state.get("media_playing", [])

    suggestions = []

    # Check file-based learned patterns (legacy)
    learned = patterns.get("learned_patterns", {}).get("patterns", {})
    for need in needs:
        pattern_key = f"{context}+{need}"
        if pattern_key in learned:
            pattern = learned[pattern_key]
            if pattern.get("acceptance_rate", 0) > 0.5:
                # Skip music suggestions if music already playing
                action = pattern.get("preferred_action", "")
                if "music" in action and music_playing:
                    continue
                suggestions.append({
                    "action": action,
                    "reason": f"You usually want this during {context}",
                    "priority": "high",
                    "learned": True,
                    "source": "file_patterns",
                    "acceptance_rate": pattern["acceptance_rate"]
                })

    # Context-specific suggestions (state-aware)
    if context == "cooking":
        if "music" in capabilities and not music_playing:
            suggestions.append({
                "type": "entertainment",
                "action": "play_cooking_music",
                "reason": "Some background music while you cook",
                "priority": "low",
                "message": "Want some background music while you cook?"
            })

        if "lighting" in capabilities:
            # Check if kitchen lights are already on
            kitchen_lights_on = any("kitchen" in l or "counter" in l or "downlight" in l
                                    for l in lights_on if "kitchen" in l or "counter" in l)
            if not kitchen_lights_on:
                suggestions.append({
                    "type": "comfort",
                    "action": "kitchen_bright_lights",
                    "reason": "Bright lighting for food prep",
                    "priority": "medium",
                    "message": "Want me to bring up the kitchen lights?"
                })

    elif context == "eating":
        if "music" in capabilities and not music_playing:
            suggestions.append({
                "type": "ambiance",
                "action": "play_dining_music",
                "reason": "Pleasant music for your meal",
                "priority": "low",
                "message": "Some music for dinner?"
            })

        if "lighting" in capabilities:
            suggestions.append({
                "type": "ambiance",
                "action": "dim_dining_lights",
                "reason": "Softer lighting for dining",
                "priority": "low",
                "message": "Want me to set the dining lights to something warmer?"
            })

    elif context == "post_meal":
        if "vacuum" in capabilities:
            vacuum_caps = capabilities["vacuum"].get("devices", {})
            if vacuum_caps:
                suggestions.append({
                    "type": "cleanliness",
                    "action": "vacuum_kitchen",
                    "capability": "vacuum.s8",
                    "button": "button.s8_after_meals",
                    "reason": "Kitchen could use a quick clean after the meal",
                    "priority": "medium",
                    "message": "Want me to run the vacuum in the kitchen?"
                })

    elif context in ["waking_up", "morning_routine"]:
        if "music" in capabilities and not music_playing:
            suggestions.append({
                "type": "ambiance",
                "action": "play_morning_music",
                "reason": "Some light music to start the day",
                "priority": "low",
                "message": "Morning! Want some light music to start the day?"
            })

        if "shades" in capabilities:
            suggestions.append({
                "type": "comfort",
                "action": "open_shades",
                "reason": "Let some natural light in",
                "priority": "low",
                "message": "Want me to open the shades?"
            })

        if "vacuum" in capabilities and not time_ctx.get("is_weekend", False):
            suggestions.append({
                "type": "planning",
                "action": "schedule_vacuum_departure",
                "reason": "I can run the vacuum when you leave for work",
                "priority": "low",
                "message": "I can run the vacuum when you head out. Want me to set that up?"
            })

    elif context == "winding_down":
        if "tv" in capabilities and not media_playing:
            suggestions.append({
                "type": "entertainment",
                "action": "suggest_show",
                "reason": "Settling in for the evening",
                "priority": "low",
                "message": "Settling in for the evening? Want a show recommendation?"
            })

        if "lighting" in capabilities:
            # Suggest dim lights if no lights on, or ambient if lights already on
            room_lights_on = len(lights_on) > 0
            if not room_lights_on:
                suggestions.append({
                    "type": "comfort",
                    "action": "evening_lights",
                    "reason": "It's dark - some ambient lighting",
                    "priority": "medium",
                    "message": "It's getting dark. Want me to set some ambient lighting?"
                })
            else:
                suggestions.append({
                    "type": "comfort",
                    "action": "dim_lights",
                    "reason": "Evening ambiance",
                    "priority": "low",
                    "message": "Want me to dim the lights for the evening?"
                })

        if "music" in capabilities and not music_playing:
            suggestions.append({
                "type": "ambiance",
                "action": "play_evening_music",
                "reason": "Relaxing music for the evening",
                "priority": "low",
                "message": "Want some relaxing music for the evening?"
            })

    elif context == "going_to_bed":
        suggestions.append({
            "type": "transition",
            "action": "goodnight_routine",
            "reason": "Prepare the house for sleep",
            "priority": "medium",
            "message": "Ready for bed? I can turn everything off and set the house to night mode."
        })

    elif context == "away":
        if "vacuum" in capabilities:
            suggestions.append({
                "type": "cleanliness",
                "action": "full_clean",
                "capability": "vacuum.s8",
                "button": "button.s8_full_cleaning",
                "reason": "Good time to clean while nobody's home",
                "priority": "medium",
                "message": "Nobody's home - want me to run a full clean?"
            })

    return suggestions


def record_observation(context: str, room: str, observation: dict):
    """Record an observation for pattern learning."""
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


def record_suggestion_response(suggestion: dict, accepted: bool):
    """Record whether a suggestion was accepted for learning."""
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

    # Check if it was sent within the last hour (reasonable response window)
    try:
        timestamp = datetime.fromisoformat(last.get("timestamp", ""))
        if datetime.now() - timestamp > timedelta(hours=1):
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


def was_suggestion_sent_recently(suggestion_action: str, hours: int = 2) -> bool:
    """
    Check if a specific suggestion action was sent recently.

    Args:
        suggestion_action: The action name (e.g., "play_morning_music")
        hours: How many hours to look back

    Returns:
        True if this suggestion was sent within the time window
    """
    recent = get_recently_sent_suggestions(hours)

    for entry in recent:
        sent_action = entry.get("suggestion", {}).get("action")
        if sent_action == suggestion_action:
            return True

    return False


def should_stay_silent(
    context: dict,
    suggestions: list,
    recent_history: list,
    confidence_threshold: float = 0.5
) -> tuple[bool, str]:
    """
    Determine if Jarvis should stay silent or speak.

    Returns (should_be_silent, reason)

    SPEAK if:
    - Context just changed (confidence > 0.7) AND suggestions available
    - Safety/security issue detected
    - User explicitly requested check
    - New actionable suggestion (not offered recently)

    STAY SILENT if:
    - Same context, no new suggestions
    - Same suggestion offered < 2 hours ago
    - Low confidence (< 0.5)
    - Focus context (working, sleeping)
    - No actionable suggestions

    Args:
        context: Context inference result dict
        suggestions: List of generated suggestions
        recent_history: Recent decision/observation history

    Returns:
        Tuple of (should_be_silent, reason_string)
    """
    ctx_name = context.get("context", "unknown")
    confidence = context.get("confidence", 0)
    previous_context = context.get("previous_context")

    # Rule 1: Low confidence -> stay silent
    # Use configurable threshold (defaults to 0.5, can be adjusted via UI)
    if confidence < confidence_threshold:
        return True, f"Low confidence ({confidence:.2f} < {confidence_threshold})"

    # Rule 2: No suggestions -> stay silent
    if not suggestions:
        return True, "No actionable suggestions"

    # Rule 2.5: Filter out suggestions already SENT to user recently
    # This prevents duplicate messages even if the decision_log shows should_speak
    recently_sent = get_recently_sent_suggestions(hours=2)
    sent_actions = {entry.get("suggestion", {}).get("action") for entry in recently_sent}

    not_yet_sent = [s for s in suggestions if s.get("action") not in sent_actions]
    if not not_yet_sent:
        return True, "All suggestions already sent to user recently"

    # Use filtered suggestions for remaining rules
    suggestions = not_yet_sent

    # Rule 3: Focus contexts -> stay silent
    focus_contexts = ["working", "sleeping", "concentrating", "on_call", "meeting"]
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
    # Threshold lowered from 0.6 to 0.3 - if we passed the basic confidence check
    # and have new suggestions, we should offer them
    if len(new_suggestions) > 0 and confidence > 0.3:
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


def infer_context_from_state(state: dict) -> dict:
    """
    Infer context from full state (all rooms).

    Useful for getting global context without needing to pass room observations.

    Args:
        state: Full state dict from state.json

    Returns:
        Context inference result
    """
    # Build room observations from state
    rooms = state.get("rooms", {})
    room_observations = {}

    for room, room_state in rooms.items():
        occupancy = room_state.get("occupancy", {})
        is_occupied = occupancy.get("current", False)

        room_observations[room] = {
            "person_detected": is_occupied,
            "activity_duration": 0  # Could calculate from changed_at
        }

    # Build home state
    home_state = {
        "occupied_rooms": sum(1 for obs in room_observations.values() if obs["person_detected"]),
        "total_rooms": len(room_observations)
    }

    # Infer context
    return infer_context(room_observations, home_state)


def get_context_transitions() -> list:
    """
    Detect recent context changes.

    Analyzes decision log to find context transitions.

    Returns:
        List of context transition dicts with from_context, to_context, timestamp
    """
    state = load_json(STATE_FILE)
    decision_log = state.get("decision_log", [])

    transitions = []
    last_context = None

    for decision in reversed(decision_log):  # Oldest to newest
        context = decision.get("context_inferred")
        timestamp = decision.get("timestamp")

        if context and last_context and context != last_context:
            transitions.append({
                "from_context": last_context,
                "to_context": context,
                "timestamp": timestamp,
                "room": decision.get("room")
            })

        last_context = context

    # Return most recent first
    return list(reversed(transitions))


def infer_global_context(all_rooms: dict) -> dict:
    """
    Infer household-level context from all rooms.

    Determines overall state: away, guests_over, settled, active, etc.

    Args:
        all_rooms: Dict of room_name -> room_state

    Returns:
        Dict with global_context, confidence, signals
    """
    time_ctx = get_time_context()

    # Count occupied rooms
    occupied_rooms = []
    for room, room_state in all_rooms.items():
        occupancy = room_state.get("occupancy", {})
        if occupancy.get("current", False):
            occupied_rooms.append(room)

    occupied_count = len(occupied_rooms)
    total_count = len(all_rooms)

    signals = []
    global_context = "unknown"
    confidence = 0.5

    # Determine global context
    if occupied_count == 0:
        global_context = "away"
        signals.append("no_occupancy")
        confidence = 0.9
    elif occupied_count == 1:
        global_context = "normal_activity"
        signals.append("single_room_occupied")
        confidence = 0.7

        # Refine based on which room
        if "primary_bedroom" in occupied_rooms:
            if time_ctx["is_late_night"] or time_ctx["is_morning"]:
                global_context = "sleeping" if time_ctx["is_late_night"] else "waking_up"
                signals.append("bedroom_hours")
                confidence = 0.85
    elif occupied_count >= 3:
        global_context = "active_household"
        signals.append("multiple_rooms_occupied")
        confidence = 0.75

        # Could be guests or just active day
        if occupied_count >= 4:
            signals.append("high_activity")
            global_context = "guests_over"
            confidence = 0.6  # Lower confidence, might just be moving around

    # Evening settled pattern
    if time_ctx["is_evening"] and occupied_count == 1 and "living_room" in occupied_rooms:
        global_context = "settled_evening"
        signals.append("evening_single_room")
        confidence = 0.8

    return {
        "global_context": global_context,
        "confidence": confidence,
        "signals": signals,
        "occupied_rooms": occupied_rooms,
        "occupied_count": occupied_count,
        "time": time_ctx
    }


def update_current_context(context: str, room: str):
    """Update state with current context."""
    state = load_json(STATE_FILE)
    state["current_context"] = context
    state["last_occupied_room"] = room
    state["context_updated_at"] = datetime.now().isoformat()
    save_json(STATE_FILE, state)


def main():
    if len(sys.argv) < 2:
        print("Usage: life_context.py <command> [args]")
        print("Commands: infer, suggest, record-response <accepted|rejected>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "infer":
        # Read room observations from stdin or use empty
        room_obs = {}
        home_state = {}
        
        if not sys.stdin.isatty():
            try:
                data = json.load(sys.stdin)
                room_obs = data.get("rooms", {})
                home_state = data.get("home_state", {})
            except:
                pass
        
        result = infer_context(room_obs, home_state)
        print(json.dumps(result, indent=2))
    
    elif cmd == "suggest":
        room_obs = {}
        home_state = {}
        
        if not sys.stdin.isatty():
            try:
                data = json.load(sys.stdin)
                room_obs = data.get("rooms", {})
                home_state = data.get("home_state", {})
            except:
                pass
        
        context_result = infer_context(room_obs, home_state)
        suggestions = get_suggestions(context_result)
        
        print(json.dumps({
            "context": context_result,
            "suggestions": suggestions
        }, indent=2))
    
    elif cmd == "record-response":
        if len(sys.argv) < 3:
            print("Usage: life_context.py record-response <accepted|rejected>", file=sys.stderr)
            sys.exit(1)
        
        accepted = sys.argv[2].lower() in ["accepted", "yes", "true", "1"]
        
        # Read suggestion from stdin
        if not sys.stdin.isatty():
            suggestion = json.load(sys.stdin)
            record_suggestion_response(suggestion, accepted)
            print(json.dumps({"recorded": True, "accepted": accepted}))
        else:
            print("Provide suggestion JSON on stdin", file=sys.stderr)
            sys.exit(1)
    
    elif cmd == "time":
        print(json.dumps(get_time_context(), indent=2))
    
    elif cmd == "capabilities":
        print(json.dumps(get_capabilities(), indent=2))
    
    elif cmd == "patterns":
        print(json.dumps(get_patterns(), indent=2))
    
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
