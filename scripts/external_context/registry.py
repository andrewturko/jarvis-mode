"""
Provider registry — auto-discovers and runs all providers.

Scans ``providers/`` for subclasses of ``ExternalContextProvider``,
refreshes whichever are stale, and writes the merged cache.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Type

from external_context.base_provider import ExternalContextProvider
from external_context.cache import (
    read_cache, write_cache, is_provider_stale,
)

# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

_PROVIDERS_PKG = "external_context.providers"


def _discover_providers() -> List[Type[ExternalContextProvider]]:
    """Import every module in ``providers/`` and collect provider classes."""
    providers_dir = Path(__file__).resolve().parent / "providers"
    found: List[Type[ExternalContextProvider]] = []

    for _importer, mod_name, _is_pkg in pkgutil.iter_modules([str(providers_dir)]):
        if mod_name.startswith("_"):
            continue
        fqn = f"{_PROVIDERS_PKG}.{mod_name}"
        try:
            mod = importlib.import_module(fqn)
        except Exception as exc:
            print(f"[external_context] Failed to import {fqn}: {exc}",
                  file=sys.stderr)
            continue

        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (isinstance(obj, type)
                    and issubclass(obj, ExternalContextProvider)
                    and obj is not ExternalContextProvider
                    and getattr(obj, "name", "")):
                found.append(obj)

    return found


def _instantiate_providers() -> List[ExternalContextProvider]:
    """Return one instance of each discovered provider."""
    return [cls() for cls in _discover_providers()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh_stale() -> Dict:
    """Refresh only providers whose cache has expired, then write merged cache.

    Returns the full cache dict (same shape as external_context.json).
    """
    return _do_refresh(force=False)


def refresh_all() -> Dict:
    """Force-refresh every provider regardless of staleness."""
    return _do_refresh(force=True)


def _do_refresh(force: bool) -> Dict:
    """Core refresh loop."""
    cache = read_cache()
    providers_cache = cache.get("providers", {})
    any_updated = False

    for provider in _instantiate_providers():
        name = provider.name
        stale = is_provider_stale(name, provider.stale_after_minutes)

        if not force and not stale:
            continue  # this provider's data is still fresh

        # --- refresh this provider ---
        try:
            data = provider.refresh()
        except Exception:
            print(f"[external_context] {name}.refresh() failed:",
                  file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            data = {}

        try:
            signals = provider.signals(data)
        except Exception:
            print(f"[external_context] {name}.signals() failed:",
                  file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            signals = []

        try:
            narrative = provider.narrative(data)
        except Exception:
            print(f"[external_context] {name}.narrative() failed:",
                  file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            narrative = ""

        providers_cache[name] = {
            "refreshed_at": datetime.now().isoformat(),
            "stale_after_minutes": provider.stale_after_minutes,
            "data": data,
            "signals": signals,
            "narrative": narrative,
        }
        any_updated = True

    # --- merge all providers into top-level fields ---
    all_signals: List[str] = []
    all_narratives: List[str] = []

    for entry in providers_cache.values():
        all_signals.extend(entry.get("signals", []))
        narr = entry.get("narrative", "")
        if narr:
            all_narratives.append(narr)

    # Deduplicate signals while preserving order
    seen = set()
    deduped: List[str] = []
    for s in all_signals:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    merged = {
        "generated_at": datetime.now().isoformat(),
        "signals": deduped,
        "providers": providers_cache,
        "narrative": " ".join(all_narratives),
    }

    if any_updated or not cache.get("generated_at"):
        write_cache(merged)

    return merged


def list_providers() -> List[Dict]:
    """Return metadata about each discovered provider + cache status."""
    cache = read_cache()
    providers_cache = cache.get("providers", {})
    result = []

    for provider in _instantiate_providers():
        entry = providers_cache.get(provider.name, {})
        refreshed_at = entry.get("refreshed_at")
        stale = is_provider_stale(provider.name, provider.stale_after_minutes)

        result.append({
            "name": provider.name,
            "stale_after_minutes": provider.stale_after_minutes,
            "refreshed_at": refreshed_at,
            "is_stale": stale,
            "signal_count": len(entry.get("signals", [])),
            "narrative": entry.get("narrative", ""),
        })

    return result
