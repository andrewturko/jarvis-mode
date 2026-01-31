"""
Unified cache for external context.

Reads / writes ``data/external_context.json``.  The structure is:

    {
      "generated_at": "ISO timestamp of last write",
      "signals": [ ... merged from all providers ... ],
      "providers": {
        "<name>": {
          "refreshed_at": "ISO timestamp",
          "stale_after_minutes": 30,
          "data": { ... provider-specific ... },
          "signals": [ ... ],
          "narrative": "..."
        }
      },
      "narrative": "All provider narratives joined."
    }

``signals`` and ``narrative`` at the top level are convenience roll-ups.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Resolve paths relative to *this* file → scripts/external_context/
_SCRIPT_DIR = Path(__file__).resolve().parent.parent        # scripts/
_SKILL_DIR = _SCRIPT_DIR.parent                              # jarvis-mode/
DATA_DIR = _SKILL_DIR / "data"
CACHE_FILE = DATA_DIR / "external_context.json"

# Returned when there's nothing cached (or cache is totally missing)
EMPTY_CONTEXT: Dict = {
    "generated_at": None,
    "signals": [],
    "providers": {},
    "narrative": "No external context available.",
}


def read_cache() -> Dict:
    """Load the full cache dict.  Returns EMPTY_CONTEXT on any error."""
    if not CACHE_FILE.exists():
        return dict(EMPTY_CONTEXT)
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(EMPTY_CONTEXT)


def write_cache(data: Dict) -> None:
    """Atomically write the cache file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(CACHE_FILE)


def get_provider_entry(name: str) -> Optional[Dict]:
    """Return a single provider's cached entry, or None."""
    cache = read_cache()
    return cache.get("providers", {}).get(name)


def is_provider_stale(name: str, stale_after_minutes: int) -> bool:
    """Check whether a provider's cached data has expired."""
    entry = get_provider_entry(name)
    if entry is None:
        return True  # never refreshed
    refreshed_at = entry.get("refreshed_at")
    if not refreshed_at:
        return True
    try:
        dt = datetime.fromisoformat(refreshed_at)
        age = (datetime.now() - dt).total_seconds() / 60
        return age > stale_after_minutes
    except (ValueError, TypeError):
        return True


def get_context(max_age_minutes: int = 60) -> Optional[Dict]:
    """High-level read: return cache if *any* provider was updated within
    ``max_age_minutes``, else None (signals to callers that context is stale).

    This is the function life_context.py calls.
    """
    cache = read_cache()
    generated_at = cache.get("generated_at")
    if not generated_at:
        return None
    try:
        dt = datetime.fromisoformat(generated_at)
        age = (datetime.now() - dt).total_seconds() / 60
        if age > max_age_minutes:
            return None
    except (ValueError, TypeError):
        return None
    return cache
