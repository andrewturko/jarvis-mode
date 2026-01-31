#!/usr/bin/env python3
"""
External Context CLI — thin wrapper around the external_context package.

Usage:
    python3 external_context.py refresh [--force]   # refresh stale (or all) providers
    python3 external_context.py read                # print cached context
    python3 external_context.py providers           # list providers & status
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure scripts/ is on sys.path so the external_context package resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from external_context.registry import refresh_stale, refresh_all, list_providers
from external_context.cache import get_context, EMPTY_CONTEXT


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: external_context.py <refresh|read|providers> [--force]")
        print("  refresh [--force]  Refresh stale providers (--force = all)")
        print("  read               Print cached context")
        print("  providers          List providers and status")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "refresh":
        force = "--force" in sys.argv
        result = refresh_all() if force else refresh_stale()
        print(json.dumps(result, indent=2))

    elif cmd == "read":
        cached = get_context(max_age_minutes=60)
        if cached:
            print(json.dumps(cached, indent=2))
        else:
            print(json.dumps(EMPTY_CONTEXT, indent=2))

    elif cmd == "providers":
        info = list_providers()
        if not info:
            print("  (no providers discovered)")
        else:
            for p in info:
                stale_tag = "STALE" if p["is_stale"] else "ok"
                print(f"  {p['name']:12s}  every {p['stale_after_minutes']:>3d}m"
                      f"  [{stale_tag:5s}]  signals={p['signal_count']}"
                      f"  {p['narrative']}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
