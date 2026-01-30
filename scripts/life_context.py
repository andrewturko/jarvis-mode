#!/usr/bin/env python3
"""
Life Context Engine — Facade module.

All intelligence functions have been reorganized into the intelligence/ package.
This module re-exports everything for backward compatibility.

External callers can continue to use:
    import life_context
    life_context.infer_context(...)
"""

import json
import sys
from datetime import datetime

# Re-export everything from intelligence package
from intelligence import *  # noqa: F401,F403

# Also make specific constants available for backward compatibility
from intelligence._helpers import load_json
from intelligence.context_inference import TEMPORAL_LEARNING_AVAILABLE
from intelligence.suggestion_engine import (
    PREFERENCE_STORE_AVAILABLE, FATIGUE_TRACKING_AVAILABLE, BASE_COOLDOWN_HOURS,
)


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
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Failed to parse stdin: {e}", file=sys.stderr)

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
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Failed to parse stdin: {e}", file=sys.stderr)

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
