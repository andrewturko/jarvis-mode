"""Suggestion engine — generates actionable suggestions based on context.

Checks current home state, consults learned patterns and preference store,
applies diversity weighting and fatigue-aware cooldowns.
"""

import random
from typing import Optional

from intelligence._helpers import (
    load_json, get_life_model, get_capabilities, get_patterns,
)
from intelligence.observation_tracker import get_recently_sent_suggestions
from core.paths import SUGGESTION_CATALOG_FILE

# Preference store — general-purpose preference learning
try:
    from services.preference_store import PreferenceStore
    _pref_store = PreferenceStore()
    PREFERENCE_STORE_AVAILABLE = True
except ImportError:
    _pref_store = None
    PREFERENCE_STORE_AVAILABLE = False

# Fatigue tracker — adaptive silence based on engagement
try:
    from services.fatigue_tracker import (
        get_cooldown_hours as _fatigue_cooldown,
        BASE_COOLDOWN_HOURS,
    )
    FATIGUE_TRACKING_AVAILABLE = True
except ImportError:
    FATIGUE_TRACKING_AVAILABLE = False
    BASE_COOLDOWN_HOURS = 2.0


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
    recent_actions_list = [e.get("suggestion", {}).get("action") for e in recently_sent]
    action_counts = {}
    for a in recent_actions_list:
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

    # Fatigue-aware weighting: penalize ignored actions, boost accepted ones
    if FATIGUE_TRACKING_AVAILABLE:
        for s in suggestions:
            action = s["action"]
            # Per-action backoff (2h base -> 1.0 normalized; higher = more ignored)
            cooldown_mult = _fatigue_cooldown(action) / BASE_COOLDOWN_HOURS
            if cooldown_mult > 1.0:
                s["_effective_weight"] = s.get("_effective_weight", 1.0) / cooldown_mult
            # Boost suggestions with good historical acceptance
            acceptance_rate = s.get("acceptance_rate", 0)
            if acceptance_rate > 0.5:
                s["_effective_weight"] = s.get("_effective_weight", 1.0) * (1.0 + acceptance_rate * 0.5)

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
        category: Preference domain (music, lighting, suggestions, ...)
        key: Preference key
        value: Any JSON-serializable value
        source: Where it came from (stated, observed, routine, correction)
        confidence: 0.0-1.0

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
