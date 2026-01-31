"""
External Context — modular provider system for Jarvis.

Pulls signals from calendar, email, and any future sources (weather,
Slack, location, etc.) via a plugin architecture.  Each provider lives
in ``providers/`` and implements ``ExternalContextProvider``.

Public API used by the rest of Jarvis:

    from external_context import get_context, refresh_stale, refresh_all

Or via CLI:

    python3 scripts/external_context.py refresh [--force]
    python3 scripts/external_context.py read
    python3 scripts/external_context.py providers
"""

from __future__ import annotations

from external_context.registry import refresh_stale, refresh_all, list_providers
from external_context.cache import read_cache, get_context, EMPTY_CONTEXT

__all__ = [
    "refresh_stale",
    "refresh_all",
    "list_providers",
    "read_cache",
    "get_context",
    "EMPTY_CONTEXT",
]
