#!/usr/bin/env python3
"""
Generate suggestion catalog entries from capabilities + life-model using LLM.

Feeds the life-model's semantic structure, home capabilities, and existing
manual catalog to an LLM, which reasons about which suggestions actually
make sense. Output goes to data/generated-suggestions.json.

Run directly or called from refresh-inventory.py after capabilities update.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.paths import (
    LIFE_MODEL_FILE, CAPABILITIES_FILE, SUGGESTION_CATALOG_FILE,
    GENERATED_SUGGESTIONS_FILE,
)
from intelligence._helpers import load_json, save_json


# ---------------------------------------------------------------------------
# Gateway resolution (same pattern as snapshot_service.py)
# ---------------------------------------------------------------------------

def _resolve_gateway():
    """Resolve the openclaw gateway URL and auth credentials."""
    gateway_url = os.environ.get("OPENCLAW_GATEWAY_URL")
    gateway_password = os.environ.get("OPENCLAW_GATEWAY_PASSWORD")

    if not gateway_url:
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                gw = config.get("gateway", {})
                port = gw.get("port", 18789)
                gateway_url = f"http://localhost:{port}"
                if not gateway_password:
                    gateway_password = gw.get("auth", {}).get("password")
            except (json.JSONDecodeError, OSError):
                pass

    return gateway_url, gateway_password


# ---------------------------------------------------------------------------
# Coverage index (kept from original)
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


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(life_model, capabilities, human_catalog, covered):
    """Build the LLM prompt with full context about the home and model."""

    # Summarize contexts
    contexts_summary = {}
    for name, ctx in life_model.get("contexts", {}).items():
        contexts_summary[name] = {
            "description": ctx.get("description", ""),
            "typical_needs": ctx.get("typical_needs", []),
            "transitions_to": ctx.get("transitions_to", []),
        }

    # Summarize capabilities with room placement
    caps_summary = {}
    for cap_type, cap_data in capabilities.items():
        if cap_type.startswith("_"):
            continue
        rooms = (
            cap_data.get("rooms")
            or cap_data.get("speakers")
            or cap_data.get("indoor")
            or cap_data.get("devices")
        )
        if isinstance(rooms, dict):
            caps_summary[cap_type] = {"rooms": list(rooms.keys())}
        else:
            caps_summary[cap_type] = {"global": True}

    # Summarize what the manual catalog already covers
    covered_summary = {}
    for (ctx, cap), actions in covered.items():
        covered_summary.setdefault(ctx, [])
        covered_summary[ctx].append(f"{cap}: {', '.join(actions)}")

    # Needs definitions
    needs_summary = {
        name: {
            "description": n.get("description", ""),
            "capability_types": n.get("capability_types", []),
        }
        for name, n in life_model.get("needs", {}).items()
    }

    prompt = f"""You are generating a suggestion catalog for a smart home AI assistant called Jarvis.

## Home capabilities (devices and room placement)
{json.dumps(caps_summary, indent=2)}

## Life-model contexts (what the user might be doing)
{json.dumps(contexts_summary, indent=2)}

## Needs → capability types (semantic model)
{json.dumps(needs_summary, indent=2)}

## Already covered by manual catalog (DO NOT duplicate these)
{json.dumps(covered_summary, indent=2)}

## Available state requirements (use when appropriate)
- "music_not_playing" — only suggest music if not already playing
- "media_not_playing" — only suggest TV/media if nothing is playing
- "room_lights_off" — room lights must be off (suggest turning on)
- "room_lights_on" — room lights must be on (suggest dimming/adjusting)
- "room_lights_not_bright" — lights on but below 80% (suggest brightening)
- {{"condition": "any_in_state", "target": "closed", "scope": "_current", "capability": "shades"}} — shades in current room must be closed
- {{"condition": "any_in_state", "target": "open", "scope": "_current", "capability": "shades"}} — shades in current room must be open
- {{"condition": "any_in_state", "target": "open", "scope": "_any", "capability": "shades"}} — any shades in home must be open

## Instructions

Generate suggestions for context + capability gaps NOT covered by the manual catalog.

For each context, think like a thoughtful butler: what actions would genuinely help or enhance this moment? Generate a suggestion when the context provides a real reason for the action — the person's activity, transition, or situation should motivate it.

Ask yourself:
- Does the context CREATE a reason? (leaving home → eco mode: YES, nobody needs climate running)
- Does the context ENHANCE with this? (gaming + dim lights: YES, reduces glare and sets the mood)
- Or does it merely COINCIDE? (cooking + adjust thermostat: NO, cooking says nothing about temperature)

Be thorough — cover each context with the capabilities that genuinely serve it. Don't be overly conservative. If a reasonable butler would think of it, include it.

GOOD suggestions (generate these kinds):
- waking_up + lights: Brighten the room to help wake up
- cooking + music: Background music enhances cooking
- leaving_home + climate eco: Save energy while away
- away + lights off: No one needs lights on
- away + eco mode: No one needs full climate
- winding_down + dim lights: Softer lighting for evening relaxation
- going_to_bed + close shades: Privacy and darkness for sleep
- going_out + lights off: Turn off lights before leaving
- gaming + music: Background music for gaming
- gaming + dim lights: Reduce glare, set the mood
- exercising + music: Upbeat music for workouts
- guests_over + ambient music: Social atmosphere

BAD suggestions (do NOT generate these):
- cooking + climate adjust: Cooking doesn't mean the temperature is wrong
- gaming + shades: Gaming doesn't affect window coverings
- sleeping + anything: Person is asleep — don't prompt them
- eating + climate: Eating doesn't mean temp needs adjusting
- reading + shades: Reading doesn't inherently mean shades need changing
- gaming + TV: The gaming context is DETECTED by the TV being on — TV is already in use
- exercising + shades: Exercising doesn't affect shades
- exercising + climate: Exercising doesn't mean the thermostat is wrong

For state requirements: always use them when a suggestion only makes sense if a device is in a specific state. Music suggestions should always require music_not_playing. Shade suggestions should check whether shades are open or closed.

## Output format

Reply with ONLY a JSON object (no markdown fences, no explanation):

{{
  "contexts": {{
    "context_name": {{
      "suggestions": [
        {{
          "action": "gen_descriptive_action_name",
          "type": "comfort|entertainment|ambiance|cleanliness|transition|efficiency|observation",
          "intent": "offer|inform",
          "requires": {{"capability": "capability_type", "state": "optional_state_requirement"}},
          "priority": "low|medium",
          "base_weight": 0.6,
          "cooldown_hours": 6,
          "examples": ["Short casual message", "Another short message"]
        }}
      ]
    }}
  }}
}}

Rules:
- action names must start with "gen_" and be unique snake_case
- examples: 2-3 short messages (under 12 words each), casual tone, varied phrasing
- base_weight: 0.3-1.0 (higher = more eager to suggest)
- cooldown_hours: 4-12 typical
- Do NOT generate anything for the "sleeping" context
- Do NOT duplicate actions already in the manual catalog
- Include state requirements wherever they make sense"""

    return prompt


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

VALID_TYPES = {
    "comfort", "entertainment", "ambiance", "cleanliness", "transition",
    "efficiency", "security", "information", "observation",
}
VALID_INTENTS = {"offer", "inform", "observe"}
VALID_PRIORITIES = {"low", "medium", "high"}
LEGACY_STATE_TOKENS = {
    "music_not_playing", "media_not_playing", "kitchen_lights_off",
    "room_lights_off", "room_lights_on", "room_lights_not_bright",
}
VALID_CONDITIONS = {"any_in_state", "none_in_state", "all_in_state"}


def _validate_state_req(state_req):
    """Validate a state requirement. Returns True if valid."""
    if isinstance(state_req, str):
        return state_req in LEGACY_STATE_TOKENS
    if isinstance(state_req, dict):
        condition = state_req.get("condition")
        return condition in VALID_CONDITIONS and "target" in state_req
    return False


def validate_and_clean(raw_contexts, capabilities, covered_actions):
    """Validate LLM output and return cleaned contexts dict.

    Returns (cleaned_contexts, warnings) where warnings is a list of strings.
    Invalid individual entries are dropped; valid ones are kept.
    """
    valid_caps = set(k for k in capabilities if not k.startswith("_"))
    cleaned = {}
    seen_actions = set()
    warnings = []

    for ctx_name, ctx_data in raw_contexts.items():
        suggestions = ctx_data.get("suggestions", [])
        valid_suggestions = []

        for entry in suggestions:
            action = entry.get("action", "")

            # Required: action name
            if not action or not isinstance(action, str):
                warnings.append(f"Skipped entry in {ctx_name}: missing/invalid action")
                continue

            # Required: valid capability
            requires = entry.get("requires", {})
            cap_req = requires.get("capability") if isinstance(requires, dict) else None
            if not cap_req or cap_req not in valid_caps:
                warnings.append(f"Skipped {action}: invalid capability '{cap_req}'")
                continue

            # Required: examples
            examples = entry.get("examples", [])
            if not examples or not isinstance(examples, list):
                warnings.append(f"Skipped {action}: missing examples")
                continue

            # Unique action name (no collisions with manual catalog or within batch)
            if action in seen_actions or action in covered_actions:
                warnings.append(f"Skipped {action}: duplicate action name")
                continue
            seen_actions.add(action)

            # Validate state requirement if present; strip if invalid
            state_req = requires.get("state")
            if state_req and not _validate_state_req(state_req):
                warnings.append(f"Warning {action}: removed invalid state requirement")
                entry["requires"] = {"capability": cap_req}

            # Validate and apply defaults
            entry.setdefault("type", "comfort")
            if entry["type"] not in VALID_TYPES:
                entry["type"] = "comfort"

            entry.setdefault("intent", "offer")
            if entry["intent"] not in VALID_INTENTS:
                entry["intent"] = "offer"

            entry.setdefault("priority", "low")
            if entry["priority"] not in VALID_PRIORITIES:
                entry["priority"] = "low"

            entry.setdefault("base_weight", 0.6)
            try:
                entry["base_weight"] = float(entry["base_weight"])
                entry["base_weight"] = max(0.1, min(1.0, entry["base_weight"]))
            except (ValueError, TypeError):
                entry["base_weight"] = 0.6

            entry.setdefault("cooldown_hours", 6)
            try:
                entry["cooldown_hours"] = int(entry["cooldown_hours"])
                entry["cooldown_hours"] = max(1, min(24, entry["cooldown_hours"]))
            except (ValueError, TypeError):
                entry["cooldown_hours"] = 6

            # Clean examples to strings
            entry["examples"] = [str(e) for e in examples if e]

            # Stamp as generated
            entry["_generated"] = True
            entry["_generation_source"] = f"llm/{ctx_name}/{cap_req}"

            valid_suggestions.append(entry)

        if valid_suggestions:
            cleaned[ctx_name] = {"suggestions": valid_suggestions}

    return cleaned, warnings


# ---------------------------------------------------------------------------
# Music favorites (structured — doesn't need LLM reasoning)
# ---------------------------------------------------------------------------

_CONTEXT_PHRASES = {
    "waking_up":       {"while": "as you get up",        "setting": "to start your day"},
    "morning_routine": {"while": "while you get ready",  "setting": "while you get ready"},
    "cooking":         {"while": "while you cook",       "setting": "for cooking"},
    "eating":          {"while": "during your meal",     "setting": "for your meal"},
    "post_meal":       {"while": "after your meal",      "setting": "post-meal"},
    "working":         {"while": "while you work",       "setting": "for focus"},
    "break":           {"while": "during your break",    "setting": "while you recharge"},
    "winding_down":    {"while": "as you wind down",     "setting": "to wind down"},
    "going_to_bed":    {"while": "before bed",           "setting": "for bed"},
    "arriving_home":   {"while": "now that you're home", "setting": "for your return"},
    "leaving_home":    {"while": "before you head out",  "setting": "while you're away"},
    "going_out":       {"while": "before you head out",  "setting": "while you're away"},
    "away":            {"while": "while you're out",     "setting": "while nobody's home"},
    "guests_over":     {"while": "while you have guests","setting": "for the group"},
}


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

            p = _CONTEXT_PHRASES.get(context_name, {"while": "", "setting": ""})
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


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def generate():
    """Main generation: call LLM to produce contextually appropriate suggestions."""
    life_model = load_json(LIFE_MODEL_FILE)
    capabilities = load_json(CAPABILITIES_FILE)
    human_catalog = load_json(SUGGESTION_CATALOG_FILE)

    covered = build_coverage_index(human_catalog)

    # All manual catalog action names for dedup
    covered_actions = set()
    for ctx_data in human_catalog.get("contexts", {}).values():
        for s in ctx_data.get("suggestions", []):
            covered_actions.add(s.get("action", ""))

    # Resolve gateway
    gateway_url, gateway_password = _resolve_gateway()

    generated_contexts = {}
    llm_used = False

    if gateway_url:
        prompt = _build_prompt(life_model, capabilities, human_catalog, covered)

        try:
            request_body = json.dumps({
                "model": "anthropic/claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                }],
            }).encode("utf-8")

            headers = {"Content-Type": "application/json"}
            if gateway_password:
                headers["Authorization"] = f"Bearer {gateway_password}"

            req = urllib.request.Request(
                f"{gateway_url}/v1/chat/completions",
                data=request_body,
                headers=headers,
            )

            print("Calling LLM for suggestion generation...")
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode())

            text = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            # Handle markdown wrapping
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            parsed = json.loads(text.strip())
            raw_contexts = parsed.get("contexts", {})

            # Validate and clean
            generated_contexts, warnings = validate_and_clean(
                raw_contexts, capabilities, covered_actions,
            )

            for w in warnings:
                print(f"  \u26a0 {w}")

            llm_used = True

        except json.JSONDecodeError as e:
            print(f"  \u2717 LLM returned invalid JSON: {e}")
            print("  Keeping existing generated-suggestions.json")
        except Exception as e:
            print(f"  \u2717 LLM call failed: {e}")
            print("  Keeping existing generated-suggestions.json")
    else:
        print("  No gateway available \u2014 keeping existing generated-suggestions.json")

    if not llm_used:
        # Fallback: preserve existing file, still update favorites below
        existing = load_json(GENERATED_SUGGESTIONS_FILE)
        generated_contexts = existing.get("contexts", {})

    # Music favorites (structured, doesn't need LLM)
    seen_actions = set()
    for ctx_data in generated_contexts.values():
        for s in ctx_data.get("suggestions", []):
            seen_actions.add(s.get("action", ""))

    fav_entries = generate_favorite_entries(life_model, capabilities, covered)
    for ctx_name, entries in fav_entries.items():
        generated_contexts.setdefault(ctx_name, {"suggestions": []})
        for entry in entries:
            if entry["action"] not in seen_actions and entry["action"] not in covered_actions:
                seen_actions.add(entry["action"])
                generated_contexts[ctx_name]["suggestions"].append(entry)

    output = {
        "_description": "Auto-generated suggestions. DO NOT EDIT -- regenerated by generate_suggestions.py",
        "_generated_at": datetime.now().isoformat(),
        "_source_versions": {
            "capabilities": capabilities.get("_last_updated", "unknown"),
            "life_model": "static",
        },
        "_generation_method": "llm" if llm_used else "cached",
        "contexts": generated_contexts,
    }

    save_json(GENERATED_SUGGESTIONS_FILE, output)

    total = sum(len(c["suggestions"]) for c in generated_contexts.values())
    ctx_count = len(generated_contexts)
    method = "LLM" if llm_used else "cached"
    print(f"Generated {total} suggestions across {ctx_count} contexts ({method}) -> {GENERATED_SUGGESTIONS_FILE}")
    for ctx_name, ctx_data in sorted(generated_contexts.items()):
        actions = [s["action"] for s in ctx_data["suggestions"]]
        print(f"  {ctx_name}: {', '.join(actions)}")


if __name__ == "__main__":
    generate()
