"""
Provider registry — auto-discovers and orchestrates all providers.

Public API:
    refresh_all(force=False)   — refresh stale (or all) providers, write cache
    refresh_stale()            — alias for refresh_all(force=False)
    get_context()              — read merged context from cache
    list_providers()           — print discovered providers to stdout
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Type

from external_context.base_provider import ExternalContextProvider
from external_context.cache import (
    EMPTY_CONTEXT,
    is_provider_stale,
    read_cache,
    write_cache,
    get_context,
)

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _discover_providers() -> List[Type[ExternalContextProvider]]:
    """Scan the ``providers/`` package and return all provider classes."""
    providers_pkg = Path(__file__).resolve().parent / "providers"
    found: List[Type[ExternalContextProvider]] = []

    for importer, modname, _ispkg in pkgutil.iter_modules([str(providers_pkg)]):
        try:
            mod = importlib.import_module(f"external_context.providers.{modname}")
        except Exception as exc:
            print(f"[external_context] failed to import provider {modname}: {exc}", file=sys.stderr)
            continue

        for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, ExternalContextProvider)
                and obj is not ExternalContextProvider
                and getattr(obj, "name", "")
            ):
                found.append(obj)

    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh_all(force: bool = False) -> Dict:
    """Refresh providers whose cache is stale (or all if *force*).

    Returns the full merged context dict (also written to cache).
    """
    provider_classes = _discover_providers()
    cache = read_cache()
    providers_section = cache.get("providers", {})

    for cls in provider_classes:
        provider = cls()
        name = provider.name

        if not force and not is_provider_stale(name, provider.stale_after_minutes):
            continue  # still fresh — skip

        try:
            data = provider.refresh()
        except Exception as exc:
            print(f"[external_context] provider '{name}' refresh failed: {exc}", file=sys.stderr)
            continue

        try:
            sigs = provider.signals(data)
        except Exception as exc:
            print(f"[external_context] provider '{name}' signals() failed: {exc}", file=sys.stderr)
            sigs = []

        try:
            narr = provider.narrative(data)
        except Exception as exc:
            print(f"[external_context] provider '{name}' narrative() failed: {exc}", file=sys.stderr)
            narr = ""

        providers_section[name] = {
            "refreshed_at": datetime.now().isoformat(),
            "stale_after_minutes": provider.stale_after_minutes,
            "data": data,
            "signals": sigs,
            "narrative": narr,
        }

    # Merge top-level signals + narrative from all providers
    all_signals: List[str] = []
    narrative_parts: List[str] = []
    for _name, entry in providers_section.items():
        all_signals.extend(entry.get("signals", []))
        narr = entry.get("narrative", "")
        if narr:
            narrative_parts.append(narr)

    # Deduplicate signals preserving order
    seen = set()
    deduped: List[str] = []
    for s in all_signals:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    result = {
        "generated_at": datetime.now().isoformat(),
        "signals": deduped,
        "providers": providers_section,
        "narrative": " ".join(narrative_parts),
    }

    write_cache(result)
    return result


def refresh_stale() -> Dict:
    """Convenience: refresh only stale providers."""
    return refresh_all(force=False)


def list_providers() -> None:
    """Print discovered providers to stdout."""
    provider_classes = _discover_providers()
    if not provider_classes:
        print("No providers discovered.")
        return
    for cls in provider_classes:
        p = cls()
        print(f"  {p.name:20s}  stale_after={p.stale_after_minutes}m")
