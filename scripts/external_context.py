#!/usr/bin/env python3
"""
External context CLI for Jarvis.

Thin wrapper that delegates to the modular provider system in
``external_context/``.  Preserves the original CLI interface so that
HEARTBEAT.md and other callers keep working:

    python3 scripts/external_context.py refresh [--force]
    python3 scripts/external_context.py read
    python3 scripts/external_context.py providers
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the scripts/ directory is on sys.path so ``external_context``
# package can be imported regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from external_context.registry import refresh_all, list_providers
from external_context.cache import get_context, EMPTY_CONTEXT


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"

    if cmd in ("-h", "--help"):
        print("Usage: external_context.py <refresh|read|providers>")
        print("  refresh [--force]  — Pull fresh data from all providers")
        print("  read               — Print cached context as JSON")
        print("  providers          — List discovered providers")
        sys.exit(0)

    if cmd == "refresh":
        force = "--force" in sys.argv
        result = refresh_all(force=force)
        print(json.dumps(result, indent=2))

    elif cmd == "read":
        cached = get_context()
        if cached:
            print(json.dumps(cached, indent=2))
        else:
            print(json.dumps(EMPTY_CONTEXT, indent=2))

    elif cmd == "providers":
        list_providers()

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
