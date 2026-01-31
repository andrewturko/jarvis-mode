"""
Abstract base class for external-context providers.

To add a new source (weather, Slack, iMessage, …):
  1. Create ``providers/my_source.py``
  2. Subclass ``ExternalContextProvider``
  3. Set ``name`` and ``stale_after_minutes``
  4. Implement ``refresh()``, ``signals()``, ``narrative()``
  5. Done — the registry auto-discovers it on next run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List


class ExternalContextProvider(ABC):
    """Base class every external-context provider must implement."""

    # ---- Override these in subclasses --------------------------------
    name: str = ""                  # unique key, e.g. "calendar"
    stale_after_minutes: int = 30   # how often this provider should refresh

    # ---- Required methods --------------------------------------------

    @abstractmethod
    def refresh(self) -> Dict:
        """Pull fresh data from the external source.

        Returns a provider-specific dict that will be stored verbatim in the
        cache under ``providers.<name>.data``.  Must be JSON-serialisable.

        Should handle its own errors gracefully (log + return empty dict).
        """

    @abstractmethod
    def signals(self, data: Dict) -> List[str]:
        """Extract signal strings from previously-refreshed data.

        These get merged into the top-level ``signals`` list in the cache
        and flow into the Jarvis context-inference pipeline.
        """

    @abstractmethod
    def narrative(self, data: Dict) -> str:
        """Return a short, human-readable summary of this provider's state.

        Examples:
          "Dinner at Canlis in 90 minutes."
          "No important unread emails."
          "Currently 54°F and cloudy."
        """
