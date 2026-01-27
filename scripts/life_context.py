#!/usr/bin/env python3
"""
Life Context Engine
Infers life context from observations, learns patterns, suggests actions.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

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


def get_suggestions(context_result: dict, capabilities: dict = None) -> list:
    """
    Generate suggestions based on inferred context and available capabilities.
    
    Returns list of suggestion dicts with action, reason, priority.
    """
    if capabilities is None:
        capabilities = get_capabilities()
    
    model = get_life_model()
    patterns = get_patterns()
    
    context = context_result["context"]
    needs = context_result["typical_needs"]
    time_ctx = context_result["time"]
    
    suggestions = []
    
    # Check learned patterns first
    learned = patterns.get("learned_patterns", {}).get("patterns", {})
    for need in needs:
        pattern_key = f"{context}+{need}"
        if pattern_key in learned:
            pattern = learned[pattern_key]
            if pattern.get("acceptance_rate", 0) > 0.5:
                suggestions.append({
                    "action": pattern.get("preferred_action"),
                    "reason": f"You usually want this during {context}",
                    "priority": "high",
                    "learned": True,
                    "acceptance_rate": pattern["acceptance_rate"]
                })
    
    # Context-specific suggestions
    if context == "post_meal":
        if "vacuum" in capabilities:
            vacuum_caps = capabilities["vacuum"].get("devices", {})
            if vacuum_caps:
                suggestions.append({
                    "type": "cleanliness",
                    "action": "vacuum_kitchen",
                    "capability": "vacuum.s8",
                    "button": "button.s8_after_meals",
                    "reason": "Kitchen could use a quick clean after the meal",
                    "priority": "medium"
                })
    
    elif context == "winding_down":
        # Suggest entertainment if not playing
        if "tv" in capabilities:
            suggestions.append({
                "type": "entertainment",
                "action": "suggest_show",
                "reason": "Settling in for the evening",
                "priority": "low"
            })
        
        # Suggest ambient lighting
        if "lighting" in capabilities:
            suggestions.append({
                "type": "comfort",
                "action": "dim_lights",
                "reason": "Evening ambiance",
                "priority": "low"
            })
    
    elif context == "going_to_bed":
        suggestions.append({
            "type": "transition",
            "action": "goodnight_routine",
            "reason": "Prepare the house for sleep",
            "priority": "medium"
        })
    
    elif context == "away":
        if "vacuum" in capabilities:
            suggestions.append({
                "type": "cleanliness",
                "action": "full_clean",
                "capability": "vacuum.s8",
                "button": "button.s8_full_cleaning",
                "reason": "Good time to clean while nobody's home",
                "priority": "medium"
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
