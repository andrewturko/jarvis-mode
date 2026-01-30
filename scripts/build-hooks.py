#!/usr/bin/env python3
"""
Build hooks.json from template files.

Reads templates/*.md and assembles hooks.json with messageTemplate fields.
This keeps hook logic in readable markdown files instead of escaped JSON strings.

Usage: python3 scripts/build-hooks.py
"""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from core.paths import TEMPLATES_DIR, HOOKS_FILE

# Hook definitions: id -> (match path, template file, extra fields)
HOOK_DEFS = [
    {
        "id": "jarvis-motion",
        "match": {"path": "/jarvis/motion"},
        "action": "agent",
        "deliver": False,
        "template_file": "motion-hook.md"
    },
    {
        "id": "jarvis-check",
        "match": {"path": "/jarvis/check"},
        "action": "agent",
        "deliver": False,
        "template_file": "check-hook.md"
    },
    {
        "id": "jarvis-voice",
        "match": {"path": "/jarvis/voice"},
        "action": "agent",
        "deliver": False,
        "template_file": "voice-hook.md"
    },
    {
        "id": "jarvis-poll",
        "match": {"path": "/jarvis/poll"},
        "action": "agent",
        "deliver": False,
        "template_file": "poll-hook.md"
    },
    {
        "id": "jarvis-feedback",
        "match": {"path": "/jarvis/feedback"},
        "action": "agent",
        "deliver": False,
        "template_file": "feedback-hook.md"
    },
]


def build():
    mappings = []
    for hook_def in HOOK_DEFS:
        template_path = TEMPLATES_DIR / hook_def["template_file"]
        if not template_path.exists():
            print(f"WARNING: Template not found: {template_path}")
            continue

        template_content = template_path.read_text().strip()

        mapping = {
            "id": hook_def["id"],
            "match": hook_def["match"],
            "action": hook_def["action"],
            "deliver": hook_def["deliver"],
            "messageTemplate": template_content,
        }
        mappings.append(mapping)

    hooks = {
        "description": "Jarvis Mode hooks for Clawdbot. Run 'jarvis.py setup' to register. To see what Jarvis said today: jarvis.py activity",
        "hooks": {
            "mappings": mappings
        }
    }

    HOOKS_FILE.write_text(json.dumps(hooks, indent=2) + "\n")
    print(f"Built {HOOKS_FILE} with {len(mappings)} hooks from templates/")


if __name__ == "__main__":
    build()
