"""Context inference engine — determines what the user is doing.

Combines room observations, time signals, home state, activity chains,
and temporal learning to score and select the most likely life context.
"""

from datetime import datetime, timedelta

from intelligence._helpers import load_json, save_json, get_life_model
from intelligence.activity_chains import get_activity_chain, chain_signals
from core.paths import STATE_FILE

# Temporal learner — adaptive time-based probabilities
try:
    from services.temporal_learner import get_temporal_score
    TEMPORAL_LEARNING_AVAILABLE = True
except ImportError:
    TEMPORAL_LEARNING_AVAILABLE = False


def get_time_context():
    """Get time-based context signals.

    Note: is_meal_time is now computed by the temporal learner from real
    observations instead of hardcoded hours. The field is kept for backward
    compatibility but derived from learned probabilities.
    """
    now = datetime.now()
    hour = now.hour

    # Use learned temporal probability for meal time instead of hardcoded hours.
    # Threshold: if "eating" is >2x more likely than average at this hour,
    # we consider it a plausible meal time.
    if TEMPORAL_LEARNING_AVAILABLE:
        eating_score = get_temporal_score("eating", hour)
        is_meal_time = eating_score > 2.0
    else:
        # Fallback to old hardcoded rule if temporal learner unavailable
        is_meal_time = hour in [7, 8, 12, 13, 18, 19, 20]

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
        "is_meal_time": is_meal_time,
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

    # Working-from-home signals
    if time_ctx["is_work_hours"] and not time_ctx["is_weekend"]:
        signals.append("daytime_hours")
        for room, obs in room_observations.items():
            if not obs.get("person_detected"):
                continue
            activity = (obs.get("activity") or "").lower()
            duration = obs.get("activity_duration", 0)
            # Desk/computer activity detected by vision
            if (duration > 30 and
                    any(kw in activity for kw in [
                        "sitting", "desk", "computer", "working",
                        "laptop", "typing", "at desk"])):
                signals.append("office_or_desk_presence")
                signals.append("minimal_movement")
                break
            # Long stationary presence in non-kitchen/dining room during work hours
            if (duration > 60 and
                    room not in ["kitchen", "dining"] and
                    "tv_or_couch" not in signals):
                signals.append("minimal_movement")

    if not occupied_rooms:
        signals.append("all_rooms_empty")
        signals.append("no_motion_all_rooms")

    # Check for context transitions (single-step, from state)
    last_context = state.get("current_context")
    last_room = state.get("last_occupied_room")

    if last_room == "kitchen" and "living_room" in occupied_rooms:
        signals.append("left_kitchen")
        if time_ctx["is_evening"]:
            signals.append("post_meal_time")

    if last_room == "living_room" and "primary_bedroom" in occupied_rooms:
        signals.append("bedroom_transition")

    # ---- Home Transition Signals ----
    # Detect home arrival, settling period, and pass-through from state.
    # TransitionDetector writes home_state to state.json; we read it here.
    home_transition = state.get("home_state", {})
    home_status = home_transition.get("status", "unknown")
    if home_status == "away" or (home_status == "unknown" and not occupied_rooms):
        signals.append("extended_no_motion")
    settling_until_str = home_transition.get("settling_until")
    if settling_until_str:
        try:
            settling_until = datetime.fromisoformat(settling_until_str)
            if datetime.now() < settling_until:
                signals.append("first_motion_after_away")
                signals.append("settling_period")
                if time_ctx["is_evening"]:
                    signals.append("typical_arrival_time")
        except (ValueError, TypeError):
            pass

    # ---- Activity Chain Signals (multi-step, from decision history) ----
    # Read recent activity chain and inject derived signals so that
    # sequential patterns BOOST confidence for matching contexts.
    activity_chain = get_activity_chain(hours=2)
    chain_sigs = chain_signals(activity_chain)
    signals.extend(chain_sigs)

    # Score each context
    context_scores = {}
    contexts = model.get("contexts", {})

    for ctx_name, ctx_def in contexts.items():
        ctx_signals = ctx_def.get("signals", [])
        matches = sum(1 for s in ctx_signals if s in signals)
        if matches > 0:
            raw_score = matches / len(ctx_signals) if ctx_signals else 0

            # Apply temporal learning: multiply by learned time-of-day score.
            # This replaces hardcoded time gates — contexts that rarely happen
            # at this hour get dampened, frequent ones get boosted.
            if TEMPORAL_LEARNING_AVAILABLE:
                temporal_mult = get_temporal_score(ctx_name, time_ctx["hour"])
                # Blend: weighted average of raw score and temporally-adjusted score.
                # 70% temporal, 30% raw — lets signals still matter but time dominates.
                adjusted_score = raw_score * (0.3 + 0.7 * (temporal_mult / max(temporal_mult, 1.0)))
                # If temporal strongly disagrees (< 0.3x), dampen more aggressively
                if temporal_mult < 0.3:
                    adjusted_score = raw_score * temporal_mult
                context_scores[ctx_name] = adjusted_score
            else:
                context_scores[ctx_name] = raw_score

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


def _count_recent_transitions(state: dict, minutes: int = 15) -> int:
    """Count distinct room transitions in decision log within recent minutes."""
    cutoff = datetime.now() - timedelta(minutes=minutes)
    log = state.get("decision_log", [])
    rooms_seen = set()
    transitions = 0
    for entry in reversed(log):
        try:
            ts = datetime.fromisoformat(entry.get("timestamp", ""))
            if ts < cutoff:
                break
            room = entry.get("room")
            if room and room not in rooms_seen:
                rooms_seen.add(room)
                transitions += 1
        except (ValueError, TypeError):
            continue
    return transitions


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

        # Rapid multi-room transitions suggest multiple people (guests)
        state = load_json(STATE_FILE)
        recent_transitions = _count_recent_transitions(state, minutes=15)
        if recent_transitions >= 4 or occupied_count >= 4:
            signals.append("high_activity")
            signals.append("social_gathering_indicators")
            global_context = "guests_over"
            confidence = 0.65

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
