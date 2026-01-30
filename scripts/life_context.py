#!/usr/bin/env python3
"""
Life Context Engine
Infers life context from observations, learns patterns, suggests actions.

Integrates with:
- PatternAnalyzer for learned behavioral predictions
- TemporalLearner for adaptive time-based context probabilities
  (replaces hardcoded time rules like is_meal_time)
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add services to path for pattern analyzer and temporal learner
sys.path.insert(0, str(Path(__file__).parent / "services"))

# Temporal learner — adaptive time-based probabilities
try:
    from temporal_learner import (
        get_temporal_score,
        record_observation as record_temporal_observation,
    )
    TEMPORAL_LEARNING_AVAILABLE = True
except ImportError:
    TEMPORAL_LEARNING_AVAILABLE = False

# Preference store — general-purpose preference learning
try:
    from preference_store import PreferenceStore
    _pref_store = PreferenceStore()
    PREFERENCE_STORE_AVAILABLE = True
except ImportError:
    _pref_store = None
    PREFERENCE_STORE_AVAILABLE = False

# Fatigue tracker — adaptive silence based on engagement
try:
    from fatigue_tracker import (
        get_cooldown_hours as _fatigue_cooldown,
        get_dynamic_threshold as _fatigue_threshold,
        has_budget_remaining as _fatigue_has_budget,
        process_ignored_suggestions as _fatigue_process_ignored,
        record_suggestion_sent as _fatigue_record_sent,
    )
    FATIGUE_TRACKING_AVAILABLE = True
except ImportError:
    FATIGUE_TRACKING_AVAILABLE = False

SKILL_DIR = Path(__file__).parent.parent
LIFE_MODEL_FILE = SKILL_DIR / "life-model.json"
CAPABILITIES_FILE = SKILL_DIR / "capabilities.json"
PATTERNS_FILE = SKILL_DIR / "patterns.json"
STATE_FILE = SKILL_DIR / "state.json"
SUGGESTION_CATALOG_FILE = SKILL_DIR / "suggestion-catalog.json"


# ---------------------------------------------------------------------------
# Activity Chain Inference
# ---------------------------------------------------------------------------
# These functions read recent decision log entries and detect sequential
# patterns that tell a story across observations.  A single snapshot is
# limited — e.g. "person in dining room" — but a *chain* like
# "kitchen (cooking, 20 min) → dining (presence)" strongly implies eating.
# Chain signals are injected into the signal list before scoring so they
# BOOST confidence without replacing existing single-observation signals.
# ---------------------------------------------------------------------------

def get_activity_chain(hours: float = 2) -> list:
    """
    Read recent decision log entries and return an ordered activity chain.

    Returns a list of dicts (oldest-first) with keys:
        timestamp (datetime), room (str), context (str), confidence (float)

    Only includes entries where a context was actually inferred.
    Gracefully returns [] on any error (missing file, bad data, etc.).
    """
    try:
        state = load_json(STATE_FILE)
        decision_log = state.get("decision_log", [])
    except Exception:
        return []

    cutoff = datetime.now() - timedelta(hours=hours)
    chain = []

    for entry in decision_log:
        # Skip entries without required fields
        ctx = entry.get("context_inferred")
        room = entry.get("room")
        ts_str = entry.get("timestamp")
        if not ctx or not room or not ts_str:
            continue

        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue

        if ts < cutoff:
            continue  # outside window

        chain.append({
            "timestamp": ts,
            "room": room,
            "context": ctx,
            "confidence": entry.get("confidence", 0),
        })

    # Decision log is newest-first; reverse so chain is chronological
    chain.reverse()
    return chain


def chain_signals(chain: list) -> list:
    """
    Analyze a chronological activity chain and return derived chain signals.

    Each signal is a string like "chain_cooking_then_dining" that can appear
    in a context's signal list in life-model.json.

    The analysis is intentionally lightweight — simple sequential scans,
    no heavy computation.
    """
    if not chain:
        return []

    signals = []

    # --- Helper: collapse consecutive duplicate (room, context) entries ---
    # This turns 5 consecutive "kitchen/cooking" entries into one segment
    # with a duration, making pattern detection cleaner.
    segments = []
    for entry in chain:
        if segments and segments[-1]["room"] == entry["room"] and segments[-1]["context"] == entry["context"]:
            # Extend existing segment
            segments[-1]["end"] = entry["timestamp"]
            segments[-1]["count"] += 1
            # Track max confidence seen in this segment
            if entry["confidence"] > segments[-1]["confidence"]:
                segments[-1]["confidence"] = entry["confidence"]
        else:
            segments.append({
                "room": entry["room"],
                "context": entry["context"],
                "start": entry["timestamp"],
                "end": entry["timestamp"],
                "confidence": entry["confidence"],
                "count": 1,
            })

    # Compute durations for each segment
    for seg in segments:
        seg["duration_minutes"] = (seg["end"] - seg["start"]).total_seconds() / 60

    # --- Chain pattern detection ---

    # 1. chain_cooking_then_dining
    #    Kitchen cooking (any duration) followed by dining presence
    for i in range(len(segments) - 1):
        if segments[i]["room"] == "kitchen" and segments[i]["context"] == "cooking":
            # Look ahead for dining within next few segments
            for j in range(i + 1, min(i + 4, len(segments))):
                if segments[j]["room"] == "dining":
                    signals.append("chain_cooking_then_dining")
                    # Also implies post_cooking for the eating context
                    signals.append("post_cooking")
                    break

    # 2. chain_morning_flow
    #    Bedroom/waking_up → Kitchen → Living room (classic morning pattern)
    rooms_seen = [seg["room"] for seg in segments]
    contexts_seen = [seg["context"] for seg in segments]

    # Check for waking_up anywhere early in the chain + kitchen + living room after
    if "waking_up" in contexts_seen:
        wake_idx = contexts_seen.index("waking_up")
        remaining_rooms = rooms_seen[wake_idx:]
        if "kitchen" in remaining_rooms:
            kitchen_idx = remaining_rooms.index("kitchen") + wake_idx
            if "living_room" in rooms_seen[kitchen_idx:]:
                signals.append("chain_morning_flow")
        # Simpler variant: just waking_up then kitchen = morning routine flow
        if "kitchen" in remaining_rooms:
            signals.append("chain_morning_flow")

    # 3. chain_winding_down
    #    Activity decreasing, moving toward bedroom in evening hours
    if len(segments) >= 2:
        last_seg = segments[-1]
        if last_seg["room"] == "primary_bedroom" or last_seg["context"] == "going_to_bed":
            # Check if earlier segments were in living room / common areas
            earlier_rooms = {seg["room"] for seg in segments[:-1]}
            if "living_room" in earlier_rooms or "kitchen" in earlier_rooms:
                signals.append("chain_winding_down")

        # Also: living room evening → bedroom transition
        for i in range(len(segments) - 1):
            if (segments[i]["room"] == "living_room"
                    and segments[i]["context"] in ("winding_down", "post_meal")
                    and segments[i + 1]["room"] == "primary_bedroom"):
                signals.append("chain_winding_down")
                signals.append("chain_bedroom_after_evening")
                break

    # 4. chain_left_home
    #    Was active in rooms, now all rooms empty (no recent occupied segment)
    if len(segments) >= 2:
        # Check if the last segment(s) show no occupancy / away
        last_contexts = [seg["context"] for seg in segments[-2:]]
        earlier_has_activity = any(
            seg["context"] not in ("away", "leaving_home", "left_home")
            for seg in segments[:-2]
        ) if len(segments) > 2 else False

        if earlier_has_activity and all(
            c in ("away", "leaving_home", "left_home") for c in last_contexts
        ):
            signals.append("chain_left_home")

    # 5. chain_post_meal
    #    Was cooking/eating, now in a different activity
    for i in range(len(segments) - 1):
        if segments[i]["context"] in ("cooking", "eating"):
            later = segments[i + 1]
            if later["context"] not in ("cooking", "eating"):
                signals.append("chain_post_meal")
                break

    # 6. chain_extended_activity
    #    Same context sustained for 30+ minutes (indicates commitment, not passing through)
    for seg in segments:
        if seg["duration_minutes"] >= 30:
            signals.append("chain_extended_activity")
            signals.append(f"chain_extended_{seg['context']}")
            break  # Only need one to signal this

    # 7. chain_cooking_then_empty_kitchen
    #    Kitchen cooking → kitchen empty = post_meal / cleanup window
    for i in range(len(segments) - 1):
        if (segments[i]["room"] == "kitchen"
                and segments[i]["context"] == "cooking"
                and segments[i + 1]["room"] != "kitchen"):
            signals.append("chain_cooking_then_empty_kitchen")
            break

    # 8. chain_full_meal_flow
    #    Kitchen (cooking) → Dining → Living room = post-meal winding down
    for i in range(len(segments) - 2):
        if (segments[i]["room"] == "kitchen"
                and segments[i]["context"] == "cooking"):
            if segments[i + 1]["room"] == "dining":
                if segments[i + 2]["room"] == "living_room":
                    signals.append("chain_full_meal_flow")
                    break

    return list(set(signals))  # deduplicate


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


def get_suggestions(context_result: dict, capabilities: dict = None,
                    recent_actions: list = None, home_state: dict = None) -> list:
    """
    Generate state-aware suggestions based on context and home state.

    Checks current home state (music playing, lights on/off) to avoid
    suggesting things that are already active or irrelevant.

    Also consults the preference store to:
    - Suppress suggestions the user doesn't want
    - Apply preference modifiers (e.g., preferred genres, lighting levels)

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

    # Load preference modifiers for this context
    pref_modifiers = {}
    if PREFERENCE_STORE_AVAILABLE and _pref_store:
        pref_modifiers = _pref_store.get_preference_modifiers(context)

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

    # ------------------------------------------------------------------ #
    # Catalog-driven suggestion generation with diversity weighting
    # ------------------------------------------------------------------ #
    import random
    catalog = load_json(SUGGESTION_CATALOG_FILE)
    catalog_contexts = catalog.get("contexts", {})

    # Look up context in catalog (also check waking_up -> morning_routine alias)
    context_key = context
    if context_key not in catalog_contexts and context in ["waking_up"]:
        context_key = "morning_routine" if "morning_routine" in catalog_contexts else context_key
    # For morning_routine, also include waking_up catalog entries
    catalog_keys = [context_key]
    if context in ["waking_up", "morning_routine"]:
        for k in ["waking_up", "morning_routine"]:
            if k in catalog_contexts and k not in catalog_keys:
                catalog_keys.append(k)

    catalog_entries = []
    for ck in catalog_keys:
        catalog_entries.extend(catalog_contexts.get(ck, {}).get("suggestions", []))

    # Filter by requirements (capabilities, home state)
    kitchen_lights_on = any("kitchen" in l for l in lights_on)
    room_lights_on = len(lights_on) > 0

    for entry in catalog_entries:
        reqs = entry.get("requires", {})
        cap_req = reqs.get("capability")
        state_req = reqs.get("state")

        # Check capability requirement
        if cap_req and cap_req not in capabilities:
            continue

        # Check state requirements
        if state_req == "music_not_playing" and music_playing:
            continue
        if state_req == "media_not_playing" and media_playing:
            continue
        if state_req == "kitchen_lights_off" and kitchen_lights_on:
            continue
        if state_req == "room_lights_off" and room_lights_on:
            continue
        if state_req == "room_lights_on" and not room_lights_on:
            continue

        # Check weekday_only
        if entry.get("weekday_only") and time_ctx.get("is_weekend", False):
            continue

        # Pick a random example as fallback message
        examples = entry.get("examples", [])
        fallback_message = random.choice(examples) if examples else entry.get("action", "")

        # Determine tone based on context and time
        tone_map = {
            "waking_up": "warm", "morning_routine": "warm",
            "cooking": "casual", "eating": "casual",
            "winding_down": "warm", "going_to_bed": "brief",
            "arriving_home": "warm", "away": "brief",
            "post_meal": "casual",
        }
        tone = tone_map.get(context, "casual")

        suggestion = {
            "type": entry.get("type", "comfort"),
            "action": entry["action"],
            "reason": examples[0] if examples else entry["action"],
            "priority": entry.get("priority", "low"),
            "message": fallback_message,
            "intent": entry.get("intent", "offer"),
            "base_weight": entry.get("base_weight", 1.0),
            "cooldown_hours": entry.get("cooldown_hours", 4),
            "examples": examples,
            "message_template": {
                "intent": entry.get("intent", "offer"),
                "action": entry["action"],
                "context": context,
                "tone": tone,
                "examples": examples,
                "environmental_cues": ["time_of_day", "duration_in_room"],
            },
        }
        # Carry through any extra fields (capability, button)
        for extra_key in ["capability", "button"]:
            if extra_key in entry:
                suggestion[extra_key] = entry[extra_key]

        suggestions.append(suggestion)

    # Apply diversity weighting — penalize recently-used, bonus for novel
    recently_sent = get_recently_sent_suggestions(hours=24)
    recent_actions = [e.get("suggestion", {}).get("action") for e in recently_sent]
    action_counts = {}
    for a in recent_actions:
        action_counts[a] = action_counts.get(a, 0) + 1

    for s in suggestions:
        base_w = s.get("base_weight", 1.0)
        action = s["action"]
        repeats = action_counts.get(action, 0)
        if repeats > 0:
            # Each repeat halves the weight
            s["_effective_weight"] = base_w * (0.5 ** repeats)
        else:
            # Novelty bonus for never-seen suggestions
            s["_effective_weight"] = base_w * 1.2

    # Sort by effective weight (descending) then priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(
        key=lambda s: (-s.get("_effective_weight", 1.0),
                       priority_order.get(s.get("priority", "low"), 2))
    )

    # Select top suggestions (max 3) to avoid overwhelming
    suggestions = suggestions[:3]

    # ------------------------------------------------------------------ #
    # Preference-based filtering and modification
    # ------------------------------------------------------------------ #
    if PREFERENCE_STORE_AVAILABLE and _pref_store:
        hour = time_ctx.get("hour")
        filtered = []
        for s in suggestions:
            stype = s.get("type", "")
            # Check if this suggestion type should be suppressed
            if _pref_store.should_suppress(stype, context=context, hour=hour):
                continue
            # Also check by action name (e.g., suppress "play_cooking_music")
            action = s.get("action", "")
            if _pref_store.should_suppress(action, context=context, hour=hour):
                continue
            filtered.append(s)
        suggestions = filtered

        # Apply preference modifiers to enrich suggestion metadata
        # (downstream consumers can use these to customize execution)
        if pref_modifiers:
            for s in suggestions:
                s["_pref_modifiers"] = pref_modifiers

    return suggestions


def record_preference(category: str, key: str, value, source: str = "stated",
                      confidence: float = 1.0) -> Optional[dict]:
    """
    Record a user preference (convenience wrapper for the preference store).

    Args:
        category: Preference domain (music, lighting, suggestions, …)
        key: Preference key
        value: Any JSON-serializable value
        source: Where it came from (stated, observed, routine, correction)
        confidence: 0.0–1.0

    Returns:
        The recorded entry, or None if preference store unavailable.
    """
    if not PREFERENCE_STORE_AVAILABLE or not _pref_store:
        return None
    return _pref_store.record(category, key, value, source=source, confidence=confidence)


def record_correction_from_feedback(wrong: str, right: str, context: str = None) -> Optional[dict]:
    """
    Record a correction when negative feedback includes what was wrong.

    Args:
        wrong: What Jarvis inferred incorrectly
        right: What the user said was actually happening
        context: Optional context key

    Returns:
        The recorded correction entry, or None if unavailable.
    """
    if not PREFERENCE_STORE_AVAILABLE or not _pref_store:
        return None
    return _pref_store.record_correction(wrong, right, context=context)


def get_preference_modifiers(context: str) -> dict:
    """Get preference-based modifiers for the given context."""
    if not PREFERENCE_STORE_AVAILABLE or not _pref_store:
        return {}
    return _pref_store.get_preference_modifiers(context)


def should_suppress_suggestion(suggestion_type: str, context: str = None, hour: int = None) -> bool:
    """Check if a suggestion type should be suppressed."""
    if not PREFERENCE_STORE_AVAILABLE or not _pref_store:
        return False
    return _pref_store.should_suppress(suggestion_type, context=context, hour=hour)


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
