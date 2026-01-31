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
from core.paths import SUGGESTION_CATALOG_FILE, GENERATED_SUGGESTIONS_FILE

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


# ---------------------------------------------------------------------------
# Generic state requirement resolver
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# helpers for the lights_on format (list of dicts with entity_id + brightness)
# ---------------------------------------------------------------------------

def _light_eid(item):
    """Extract entity_id from a lights_on entry (dict or legacy string)."""
    return item["entity_id"] if isinstance(item, dict) else item


def _light_brightness_pct(item):
    """Extract brightness_pct from a lights_on entry (None if unavailable)."""
    return item.get("brightness_pct") if isinstance(item, dict) else None


def _avg_brightness_pct(lights_on: list) -> Optional[float]:
    """Average brightness % of all lights that report it. None if no data."""
    vals = [_light_brightness_pct(l) for l in lights_on]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# Legacy string tokens → simple lambdas for backward compatibility
_LEGACY_STATE_CHECKS = {
    "music_not_playing": lambda hs, **_: not hs.get("music_playing", False),
    "media_not_playing": lambda hs, **_: not any(hs.get("media_playing", [])),
    "kitchen_lights_off": lambda hs, **_: not any("kitchen" in _light_eid(l) for l in hs.get("lights_on", [])),
    "room_lights_off": lambda hs, **_: len(hs.get("lights_on", [])) == 0,
    "room_lights_on": lambda hs, **_: len(hs.get("lights_on", [])) > 0,
    "room_lights_not_bright": lambda hs, **_: (_avg_brightness_pct(hs.get("lights_on", [])) or 0) < 80,
}


def _get_capability_entities(capabilities: dict, cap_type: str, room: str = None) -> list:
    """Get entity IDs for a capability type, optionally scoped to a room."""
    cap_data = capabilities.get(cap_type, {})

    # shades/lighting: rooms/{room}/[entity_ids]
    rooms = cap_data.get("rooms", {})
    if rooms:
        if room:
            return list(rooms.get(room, []))
        return [eid for eids in rooms.values() for eid in eids]

    # music: speakers/{room}/{ha_entity}
    speakers = cap_data.get("speakers", {})
    if speakers:
        if room:
            spk = speakers.get(room, {})
            eid = spk.get("ha_entity") if isinstance(spk, dict) else spk
            return [eid] if eid else []
        return [
            (s.get("ha_entity") if isinstance(s, dict) else s)
            for s in speakers.values()
            if (isinstance(s, dict) and s.get("ha_entity")) or isinstance(s, str)
        ]

    # climate: thermostats/{name}/entity_id (not room-scoped)
    thermostats = cap_data.get("thermostats", {})
    if thermostats:
        return list(thermostats.values())

    # tv/vacuum/appliances: devices/{name}/{entity|c4_entity}
    devices = cap_data.get("devices", {})
    if devices:
        entities = []
        for dev in devices.values():
            if isinstance(dev, dict):
                entities.append(dev.get("entity") or dev.get("c4_entity") or "")
        return [e for e in entities if e]

    return []


def _build_entity_state_map(home_state: dict) -> dict:
    """Build {entity_id: state_string} from the flat home_state lists."""
    states = {}
    for item in home_state.get("lights_on", []):
        states[_light_eid(item)] = "on"
    for eid in home_state.get("lights_off", []):
        states[eid] = "off"
    for eid in home_state.get("media_playing", []):
        states[eid] = "playing"
    for eid in home_state.get("covers_open", []):
        states[eid] = "open"
    for eid in home_state.get("covers_closed", []):
        states[eid] = "closed"
    for eid, data in home_state.get("climate", {}).items():
        states[eid] = data.get("state", "unknown") if isinstance(data, dict) else str(data)
    return states


def _check_state_requirement(state_req, home_state: dict, current_room: str,
                             capabilities: dict) -> bool:
    """
    Check if a state requirement is satisfied.

    Returns True if the requirement IS met (suggestion should proceed).
    Returns False if NOT met (suggestion should be skipped).

    Handles two formats:
    - Legacy string tokens: "music_not_playing", "room_lights_on", etc.
    - Structured dicts: {"condition": "any_in_state", "target": "open", "scope": "_current"}
    """
    # Legacy string tokens
    if isinstance(state_req, str):
        check = _LEGACY_STATE_CHECKS.get(state_req)
        return check(home_state) if check else True

    # Structured requirement
    condition = state_req.get("condition")
    target = state_req.get("target")
    scope = state_req.get("scope", "_current")
    cap_type = state_req.get("capability")

    # Resolve scope to room name
    room = current_room if scope == "_current" else (None if scope == "_any" else scope)

    # Get entity IDs in scope
    entity_ids = _get_capability_entities(capabilities, cap_type, room) if cap_type else []
    if not entity_ids:
        return True  # No entities found — don't filter (capability check handles this)

    # Look up actual states
    state_map = _build_entity_state_map(home_state)
    entity_states = [state_map.get(eid) for eid in entity_ids if eid in state_map]

    if not entity_states:
        return True  # Entities not in home_state — HA may not have returned them

    # Evaluate condition
    if condition == "any_in_state":
        return any(s == target for s in entity_states)
    elif condition == "none_in_state":
        return not any(s == target for s in entity_states)
    elif condition == "all_in_state":
        return all(s == target for s in entity_states)

    return True  # Unknown condition — don't filter


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

    # Merge auto-generated suggestions (fills gaps not covered by human catalog)
    generated = load_json(GENERATED_SUGGESTIONS_FILE)
    generated_contexts = generated.get("contexts", {})
    human_actions = {
        ctx: {s["action"] for s in data.get("suggestions", [])}
        for ctx, data in catalog_contexts.items()
    }
    for ctx_name, ctx_data in generated_contexts.items():
        if ctx_name not in catalog_contexts:
            catalog_contexts[ctx_name] = ctx_data
        else:
            existing = human_actions.get(ctx_name, set())
            for entry in ctx_data.get("suggestions", []):
                if entry["action"] not in existing:
                    catalog_contexts[ctx_name]["suggestions"].append(entry)

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
    current_room = home_state.get("current_room", "")

    for entry in catalog_entries:
        reqs = entry.get("requires", {})
        cap_req = reqs.get("capability")
        state_req = reqs.get("state")

        # Check capability requirement
        if cap_req and cap_req not in capabilities:
            continue

        # Check state requirement (generic resolver — handles both legacy strings and structured dicts)
        if state_req and not _check_state_requirement(state_req, home_state, current_room, capabilities):
            continue

        # Brightness-aware filtering: skip "brighten/adjust lights" suggestions
        # when lights are already at ≥80% brightness.
        if cap_req == "lighting" and lights_on:
            avg_bright = _avg_brightness_pct(lights_on)
            if avg_bright is not None and avg_bright >= 80:
                action = entry.get("action", "")
                # Only skip "increase" suggestions — dimming/mood suggestions are fine
                if any(kw in action for kw in ("bright", "focus", "bump", "adjust", "welcoming")):
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
        # Carry through requires (for capability-level cooldown) and extra fields
        if "requires" in entry:
            suggestion["requires"] = entry["requires"]
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
