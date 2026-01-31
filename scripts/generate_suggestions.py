#!/usr/bin/env python3
"""
Generate suggestion catalog entries from capabilities + life-model.

Walks the life-model's semantic chain (context -> needs -> capability_types),
checks what the human-authored catalog already covers, and generates entries
for the gaps. Output goes to data/generated-suggestions.json.

Run directly or called from refresh-inventory.py after capabilities update.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.paths import (
    LIFE_MODEL_FILE, CAPABILITIES_FILE, SUGGESTION_CATALOG_FILE,
    GENERATED_SUGGESTIONS_FILE,
)
from intelligence._helpers import load_json, save_json


# ---------------------------------------------------------------------------
# Context-aware phrase fragments for natural example generation
# ---------------------------------------------------------------------------
# Phrases describe ACTIVITY only — never time-of-day.
# Time is a separate primitive provided at runtime via time_natural.
CONTEXT_PHRASES = {
    "waking_up":       {"while": "as you get up",        "setting": "to start your day"},
    "morning_routine": {"while": "while you get ready",  "setting": "while you get ready"},
    "cooking":         {"while": "while you cook",       "setting": "for cooking"},
    "eating":          {"while": "during your meal",     "setting": "for your meal"},
    "post_meal":       {"while": "after your meal",      "setting": "post-meal"},
    "working":         {"while": "while you work",       "setting": "for focus"},
    "break":           {"while": "during your break",    "setting": "while you recharge"},
    "winding_down":    {"while": "as you wind down",     "setting": "to wind down"},
    "going_to_bed":    {"while": "before bed",           "setting": "for bed"},
    "sleeping":        {"while": "while you sleep",      "setting": "while you sleep"},
    "arriving_home":   {"while": "now that you're home", "setting": "for your return"},
    "leaving_home":    {"while": "before you head out",  "setting": "while you're away"},
    "going_out":       {"while": "before you head out",  "setting": "while you're away"},
    "away":            {"while": "while you're out",     "setting": "while nobody's home"},
    "guests_over":     {"while": "while you have guests","setting": "for the group"},
    "reading":         {"while": "while you read",       "setting": "for reading"},
    "gaming":          {"while": "while you game",       "setting": "for gaming"},
    "exercising":      {"while": "during your workout",  "setting": "for the workout"},
}

# Need -> suggestion type mapping
NEED_TYPE_MAP = {
    "comfort": "comfort",
    "entertainment": "entertainment",
    "background_entertainment": "ambiance",
    "cleanliness": "cleanliness",
    "ambiance": "ambiance",
    "focus": "comfort",
    "transition": "transition",
    "security": "security",
    "efficiency": "efficiency",
    "quiet": "comfort",
    "hospitality": "ambiance",
    "information": "information",
    "energy": "comfort",
    "assistance": "comfort",
    "minimal_interruption": "comfort",
}


# ---------------------------------------------------------------------------
# Example generators per capability type
# ---------------------------------------------------------------------------

def _phrases(context_name):
    return CONTEXT_PHRASES.get(context_name, {"while": "", "setting": ""})


def _lighting_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    by_need = {
        "comfort": [
            f"Want me to adjust the lights {p['setting']}?",
            f"Lights {p['setting']}?",
        ],
        "ambiance": [
            f"Set the mood {p['setting']}?",
            f"Want some ambient lighting {p['while']}?",
        ],
        "focus": [
            f"Bright lights {p['setting']}?",
            f"Want me to set the lights for focus?",
        ],
        "transition": [
            f"Should I adjust the lights {p['while']}?",
            f"Lights {p['setting']}?",
        ],
        "efficiency": [
            f"Want me to turn off the lights {p['while']}?",
            f"Should I kill the lights {p['setting']}?",
        ],
        "hospitality": [
            f"Want me to set the lights {p['setting']}?",
            f"Some welcoming lights {p['setting']}?",
        ],
    }
    return by_need.get(need, by_need["comfort"])


def _music_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    by_need = {
        "entertainment": [
            f"Want some music {p['while']}?",
            f"Music {p['setting']}?",
        ],
        "background_entertainment": [
            f"Some background music {p['while']}?",
            f"Want something playing {p['while']}?",
        ],
        "ambiance": [
            f"Set the mood with some music {p['setting']}?",
            f"Want some ambient tunes {p['while']}?",
        ],
        "hospitality": [
            f"Music {p['setting']}?",
            f"Want me to put something on {p['setting']}?",
        ],
    }
    return by_need.get(need, by_need.get("entertainment", ["Want some music?"]))


def _tv_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    return [
        f"Want to watch something {p['while']}?",
        f"TV {p['setting']}?",
    ]


def _climate_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    by_need = {
        "comfort": [
            f"Want me to adjust the temperature {p['setting']}?",
            f"Temperature good, or should I tweak it?",
        ],
        "focus": [
            f"Want me to optimize the temperature {p['setting']}?",
            f"Should I tweak the thermostat for focus?",
        ],
        "efficiency": [
            f"Should I set eco mode {p['while']}?",
            f"I can dial back the thermostat {p['setting']}.",
        ],
    }
    return by_need.get(need, by_need["comfort"])


def _vacuum_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    has_routines = any(
        isinstance(d, dict) and d.get("routines")
        for d in cap_data.get("devices", {}).values()
    )
    if has_routines:
        return [
            f"Good time for a vacuum run {p['setting']}?",
            f"Want me to send the robot {p['while']}?",
        ]
    return [
        f"Run the vacuum {p['while']}?",
        f"Want a clean {p['setting']}?",
    ]


def _shades_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    by_need = {
        "comfort": [
            f"Want me to adjust the shades {p['setting']}?",
            f"Shades {p['setting']}?",
        ],
        "ambiance": [
            f"Should I set the shades {p['setting']}?",
            f"Shades for the mood {p['setting']}?",
        ],
        "efficiency": [
            f"Close the shades {p['while']}?",
            f"Want the shades down {p['setting']}?",
        ],
    }
    return by_need.get(need, by_need["comfort"])


def _appliance_examples(context_name, need, cap_data):
    p = _phrases(context_name)
    appliance_names = [n for n in cap_data if not n.startswith("_")] if isinstance(cap_data, dict) else []
    if appliance_names:
        name = appliance_names[0]
        return [
            f"Want me to start the {name} {p['while']}?",
            f"Good time to run the {name}?",
        ]
    return [
        f"Want me to run an appliance {p['while']}?",
        f"Anything to run {p['setting']}?",
    ]


# ---------------------------------------------------------------------------
# Capability templates — maps each type to its generation behavior
# ---------------------------------------------------------------------------
CAPABILITY_TEMPLATES = {
    "lighting": {
        "action_for_need": {
            "comfort": "adjust_lights", "ambiance": "set_mood_lights",
            "focus": "focus_lights", "transition": "transition_lights",
            "efficiency": "lights_off", "hospitality": "welcoming_lights",
        },
        "default_action": "adjust_lights",
        "state_requirement": {},
        "default_cooldown": 6,
        "example_fn": _lighting_examples,
    },
    "music": {
        "action_for_need": {
            "entertainment": "play_music", "background_entertainment": "play_bg_music",
            "ambiance": "play_ambient_music", "hospitality": "play_social_music",
        },
        "default_action": "play_music",
        "state_requirement": {"state": "music_not_playing"},
        "default_cooldown": 4,
        "example_fn": _music_examples,
    },
    "tv": {
        "action_for_need": {"entertainment": "suggest_tv", "comfort": "suggest_tv"},
        "default_action": "suggest_tv",
        "state_requirement": {"state": "media_not_playing"},
        "default_cooldown": 6,
        "example_fn": _tv_examples,
    },
    "climate": {
        "action_for_need": {
            "comfort": "adjust_temperature", "focus": "optimize_temperature",
            "efficiency": "eco_temperature", "transition": "adjust_climate",
        },
        "default_action": "adjust_climate",
        "state_requirement": {},
        "default_cooldown": 8,
        "example_fn": _climate_examples,
    },
    "vacuum": {
        "action_for_need": {"cleanliness": "run_vacuum", "efficiency": "schedule_vacuum"},
        "default_action": "run_vacuum",
        "state_requirement": {},
        "default_cooldown": 12,
        "example_fn": _vacuum_examples,
    },
    "shades": {
        "action_for_need": {
            "comfort": "adjust_shades", "ambiance": "set_shades",
            "efficiency": "close_shades", "transition": "adjust_shades",
        },
        "default_action": "adjust_shades",
        "state_requirement": {},
        "default_cooldown": 8,
        "example_fn": _shades_examples,
    },
    "appliances": {
        "action_for_need": {
            "cleanliness": "run_appliance", "efficiency": "run_appliance",
            "transition": "run_appliance",
        },
        "default_action": "run_appliance",
        "state_requirement": {},
        "default_cooldown": 12,
        "example_fn": _appliance_examples,
    },
}


# ---------------------------------------------------------------------------
# Core generation logic
# ---------------------------------------------------------------------------

def build_coverage_index(human_catalog):
    """Build {(context, capability_type): [action_names]} from human catalog."""
    covered = {}
    for ctx_name, ctx_data in human_catalog.get("contexts", {}).items():
        for suggestion in ctx_data.get("suggestions", []):
            cap_req = suggestion.get("requires", {}).get("capability")
            if cap_req:
                key = (ctx_name, cap_req)
                covered.setdefault(key, [])
                covered[key].append(suggestion["action"])
    return covered


def generate_entry(context_name, need, cap_type, cap_data, template):
    """Generate a single suggestion entry for a context/capability gap."""
    action_verb = template["action_for_need"].get(need, template["default_action"])
    action_key = f"gen_{action_verb}_{context_name}"
    sug_type = NEED_TYPE_MAP.get(need, "comfort")

    examples = template["example_fn"](context_name, need, cap_data)

    entry = {
        "action": action_key,
        "type": sug_type,
        "intent": "offer",
        "requires": {"capability": cap_type},
        "priority": "low",
        "base_weight": 0.6,
        "cooldown_hours": template["default_cooldown"],
        "examples": examples,
        "_generated": True,
        "_generation_source": f"{context_name}/{need}/{cap_type}",
    }

    state_req = template.get("state_requirement", {})
    if state_req:
        entry["requires"].update(state_req)

    return entry


def generate_favorite_entries(life_model, capabilities, covered):
    """Generate context-specific music suggestions from tagged favorites."""
    favorites = capabilities.get("music", {}).get("favorites", {})
    if not favorites:
        return {}

    results = {}
    for fav_name, fav_data in favorites.items():
        tagged_contexts = fav_data.get("context", [])
        position = fav_data.get("position")
        display_name = fav_name.replace("_", " ").title()

        for context_name in tagged_contexts:
            if (context_name, "music") in covered:
                continue

            p = _phrases(context_name)
            entry = {
                "action": f"gen_play_favorite_{fav_name}_{context_name}",
                "type": "ambiance",
                "intent": "offer",
                "requires": {"capability": "music", "state": "music_not_playing"},
                "priority": "low",
                "base_weight": 0.7,
                "cooldown_hours": 4,
                "examples": [
                    f"Want me to put on {display_name} {p['while']}?",
                    f"{display_name} {p['setting']}?",
                ],
                "favorite_position": position,
                "_generated": True,
                "_generation_source": f"favorite/{fav_name}/{context_name}",
            }
            results.setdefault(context_name, [])
            results[context_name].append(entry)

    return results


def generate():
    """Main generation: walk life-model chain, fill gaps, write output."""
    life_model = load_json(LIFE_MODEL_FILE)
    capabilities = load_json(CAPABILITIES_FILE)
    human_catalog = load_json(SUGGESTION_CATALOG_FILE)

    covered = build_coverage_index(human_catalog)
    needs_map = life_model.get("needs", {})
    contexts = life_model.get("contexts", {})

    generated_contexts = {}
    seen_actions = set()

    # Needs that mean "reduce/suppress" — don't generate activation suggestions
    suppression_needs = {"quiet", "minimal_interruption", "security"}

    for ctx_name, ctx_def in contexts.items():
        typical_needs = ctx_def.get("typical_needs", [])

        for need in typical_needs:
            if need in suppression_needs:
                continue
            need_def = needs_map.get(need, {})
            cap_types = need_def.get("capability_types", [])

            for cap_type in cap_types:
                if cap_type not in capabilities:
                    continue
                if (ctx_name, cap_type) in covered:
                    continue

                template = CAPABILITY_TEMPLATES.get(cap_type)
                if not template:
                    continue

                entry = generate_entry(
                    ctx_name, need, cap_type,
                    capabilities[cap_type], template,
                )

                # Deduplicate within generated output
                if entry["action"] in seen_actions:
                    continue
                seen_actions.add(entry["action"])

                generated_contexts.setdefault(ctx_name, {"suggestions": []})
                generated_contexts[ctx_name]["suggestions"].append(entry)

    # Music favorites
    fav_entries = generate_favorite_entries(life_model, capabilities, covered)
    for ctx_name, entries in fav_entries.items():
        generated_contexts.setdefault(ctx_name, {"suggestions": []})
        for entry in entries:
            if entry["action"] not in seen_actions:
                seen_actions.add(entry["action"])
                generated_contexts[ctx_name]["suggestions"].append(entry)

    output = {
        "_description": "Auto-generated suggestions. DO NOT EDIT -- regenerated by generate_suggestions.py",
        "_generated_at": datetime.now().isoformat(),
        "_source_versions": {
            "capabilities": capabilities.get("_last_updated", "unknown"),
            "life_model": "static",
        },
        "contexts": generated_contexts,
    }

    save_json(GENERATED_SUGGESTIONS_FILE, output)

    total = sum(len(c["suggestions"]) for c in generated_contexts.values())
    ctx_count = len(generated_contexts)
    print(f"Generated {total} suggestions across {ctx_count} contexts -> {GENERATED_SUGGESTIONS_FILE}")
    for ctx_name, ctx_data in sorted(generated_contexts.items()):
        actions = [s["action"] for s in ctx_data["suggestions"]]
        print(f"  {ctx_name}: {', '.join(actions)}")


if __name__ == "__main__":
    generate()
