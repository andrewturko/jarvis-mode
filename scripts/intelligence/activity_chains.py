"""Activity chain inference for detecting sequential behavior patterns.

Reads recent decision log entries and detects sequential patterns that tell
a story across observations. A single snapshot is limited, but a chain like
"kitchen (cooking, 20 min) -> dining (presence)" strongly implies eating.
"""

from datetime import datetime, timedelta

from intelligence._helpers import load_json
from core.paths import STATE_FILE


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
    #    Bedroom/waking_up -> Kitchen -> Living room (classic morning pattern)
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

        # Also: living room evening -> bedroom transition
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
    #    Kitchen cooking -> kitchen empty = post_meal / cleanup window
    for i in range(len(segments) - 1):
        if (segments[i]["room"] == "kitchen"
                and segments[i]["context"] == "cooking"
                and segments[i + 1]["room"] != "kitchen"):
            signals.append("chain_cooking_then_empty_kitchen")
            break

    # 8. chain_full_meal_flow
    #    Kitchen (cooking) -> Dining -> Living room = post-meal winding down
    for i in range(len(segments) - 2):
        if (segments[i]["room"] == "kitchen"
                and segments[i]["context"] == "cooking"):
            if segments[i + 1]["room"] == "dining":
                if segments[i + 2]["room"] == "living_room":
                    signals.append("chain_full_meal_flow")
                    break

    return list(set(signals))  # deduplicate
