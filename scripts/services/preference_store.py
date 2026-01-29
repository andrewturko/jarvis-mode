#!/usr/bin/env python3
"""
Preference Store — General-purpose preference learning for Jarvis Mode.

Learns and stores user preferences from four sources:
  1. Stated  — things the user explicitly says ("I don't like jazz")
  2. Observed — inferred from behavior (always rejects morning music)
  3. Routine  — recurring patterns ("I work out on Mondays")
  4. Correction — mistakes Jarvis was corrected on ("That was dishes, not cooking")

Each preference entry carries:
  - category (music, lighting, suggestions, routine, correction, …)
  - key (genre_dislike, nighttime_preference, suppress_context, …)
  - value (any JSON-serializable)
  - source (stated | observed | routine | correction)
  - confidence (0.0–1.0; stated = 1.0 always)
  - timestamps for creation and last update
  - decay metadata (observed preferences decay over time)

Storage: preferences.json in the skill directory.

Usage as library:
    from preference_store import PreferenceStore
    store = PreferenceStore()
    store.record("music", "genre_like", "jazz", source="stated")
    prefs = store.get(category="music")

Usage as CLI:
    python3 preference_store.py record music genre_like jazz --source stated
    python3 preference_store.py query --category music
    python3 preference_store.py correct "cooking" "dishes" --context late_night_dining
    python3 preference_store.py suppress entertainment cooking 22
    python3 preference_store.py modifiers winding_down
    python3 preference_store.py decay
    python3 preference_store.py dump
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

SKILL_DIR = Path(__file__).resolve().parent.parent.parent  # jarvis-mode/
PREFERENCES_FILE = SKILL_DIR / "preferences.json"

# --------------------------------------------------------------------------- #
# Preference entry helpers
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    return datetime.now().isoformat()


def _make_entry(
    category: str,
    key: str,
    value: Any,
    source: str = "stated",
    confidence: float = 1.0,
) -> Dict[str, Any]:
    """Create a new preference entry dict."""
    # Stated preferences are always confidence 1.0
    if source == "stated":
        confidence = 1.0
    # Corrections are permanent
    if source == "correction":
        confidence = 1.0

    return {
        "category": category,
        "key": key,
        "value": value,
        "source": source,
        "confidence": round(confidence, 3),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "access_count": 0,
    }


# --------------------------------------------------------------------------- #
# PreferenceStore class (importable API)
# --------------------------------------------------------------------------- #

class PreferenceStore:
    """
    Structured store for user preferences.

    Thread-safety: reads/writes are not locked — acceptable for single-writer
    CLI / agent usage.  If concurrent writes become common, add fcntl or
    filelock.
    """

    # Confidence below which a preference is considered decayed / inactive
    ACTIVE_THRESHOLD = 0.3

    # How much to decay observed preferences per day since last update
    DECAY_RATE_PER_DAY = 0.02  # lose ~2% confidence per day of inactivity

    # Sources that NEVER decay
    PERMANENT_SOURCES = {"stated", "correction"}

    def __init__(self, path: Optional[Path] = None):
        self._path = path or PREFERENCES_FILE
        self._preferences: List[Dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _load(self):
        """Load preferences from disk. Graceful on missing/corrupt file."""
        try:
            with open(self._path) as f:
                data = json.load(f)
            if isinstance(data, list):
                self._preferences = data
            elif isinstance(data, dict) and "preferences" in data:
                self._preferences = data["preferences"]
            else:
                self._preferences = []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._preferences = []

    def _save(self):
        """Persist preferences to disk."""
        # Ensure directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._preferences, f, indent=2, default=str)
        tmp.replace(self._path)

    # ------------------------------------------------------------------ #
    # Core API
    # ------------------------------------------------------------------ #

    def record(
        self,
        category: str,
        key: str,
        value: Any,
        source: str = "stated",
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Add or update a preference.

        If a matching (category, key, source) entry exists, its value and
        confidence are updated (and updated_at refreshed).  Otherwise a new
        entry is appended.

        Args:
            category: Preference domain (music, lighting, suggestions, routine, …)
            key: Specific preference key (genre_like, nighttime_preference, …)
            value: Any JSON-serializable value
            source: Where the preference came from (stated, observed, routine, correction)
            confidence: 0.0–1.0 (forced to 1.0 for stated/correction)

        Returns:
            The recorded entry dict.
        """
        existing = self._find(category, key, source)
        if existing is not None:
            existing["value"] = value
            existing["confidence"] = round(
                1.0 if source in self.PERMANENT_SOURCES else confidence, 3
            )
            existing["updated_at"] = _now_iso()
            existing["access_count"] = existing.get("access_count", 0) + 1
            self._save()
            return existing

        entry = _make_entry(category, key, value, source, confidence)
        self._preferences.append(entry)
        self._save()
        return entry

    def get(
        self,
        category: Optional[str] = None,
        key: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query preferences with optional filters.

        All filters are AND-ed.  Returns a (shallow) copy of matching entries.
        """
        results = []
        for p in self._preferences:
            if category and p["category"] != category:
                continue
            if key and p["key"] != key:
                continue
            if source and p["source"] != source:
                continue
            results.append(dict(p))
        return results

    def get_active(
        self,
        category: Optional[str] = None,
        key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return preferences that are still active (confidence >= ACTIVE_THRESHOLD).

        Stated & correction entries are always active.
        """
        results = []
        for p in self._preferences:
            if category and p["category"] != category:
                continue
            if key and p["key"] != key:
                continue
            # Permanent sources are always active
            if p["source"] in self.PERMANENT_SOURCES:
                results.append(dict(p))
                continue
            if p.get("confidence", 0) >= self.ACTIVE_THRESHOLD:
                results.append(dict(p))
        return results

    def should_suppress(
        self,
        suggestion_type: str,
        context: Optional[str] = None,
        hour: Optional[int] = None,
    ) -> bool:
        """
        Check whether a suggestion should be suppressed based on learned preferences.

        Looks at:
          - Explicit suppress_context entries (stated)
          - Time-based suppression (observed)
          - Low acceptance contexts (observed)

        Args:
            suggestion_type: Type of suggestion (e.g., "music", "entertainment", "lighting")
            context: Current life context (e.g., "cooking", "cleaning")
            hour: Current hour (0–23)

        Returns:
            True if the suggestion should be suppressed.
        """
        for p in self._preferences:
            # Check confidence threshold for non-permanent sources
            if p["source"] not in self.PERMANENT_SOURCES:
                if p.get("confidence", 0) < self.ACTIVE_THRESHOLD:
                    continue

            val = p.get("value", {})

            # --- Explicit suppress_context ---
            if p["key"] == "suppress_context":
                if isinstance(val, dict):
                    match_type = val.get("type", "").lower()
                    match_ctx = val.get("context", "").lower()
                    if (
                        match_type == suggestion_type.lower()
                        and (not context or match_ctx == context.lower())
                    ):
                        return True
                continue

            # --- Time-based suppression ---
            if p["key"] == "time_preference":
                if isinstance(val, dict):
                    match_type = val.get("type", "").lower()
                    suppress_hours = val.get("suppress_hours", [])
                    if match_type == suggestion_type.lower() and hour in suppress_hours:
                        return True
                continue

            # --- Late-night cooking/eating suppress ---
            if p["key"] == "suppress_late_night" and p["category"] == "suggestions":
                if isinstance(val, dict):
                    suppress_types = val.get("types", [])
                    after_hour = val.get("after_hour", 23)
                    if hour is not None and hour >= after_hour:
                        if suggestion_type.lower() in [t.lower() for t in suppress_types]:
                            return True
                continue

        return False

    def get_preference_modifiers(self, context: str) -> Dict[str, Any]:
        """
        Return preference-based adjustments for a given context.

        Scans active preferences and returns a dict of modifiers that the
        suggestion engine or agent can apply. Example return:

            {
                "music": {"preferred_genre": "jazz", "avoid_genres": ["pop"]},
                "lighting": {"prefer_warm": True, "level": "dim"},
                "volume": {"default": 12, "sleeping": 10},
                "tv": {"default_source": "Apple TV", "default_app": "YouTube"},
                "room_grouping": {"great_room": ["living_room", "kitchen", "dining"]},
            }

        Only active preferences with confidence >= ACTIVE_THRESHOLD are included.
        """
        modifiers: Dict[str, Any] = {}

        for p in self.get_active():
            cat = p["category"]
            key = p["key"]
            val = p["value"]
            conf = p.get("confidence", 1.0)

            # --- Music modifiers ---
            if cat == "music":
                music = modifiers.setdefault("music", {})
                if key == "genre_like":
                    music.setdefault("preferred_genres", [])
                    if val not in music["preferred_genres"]:
                        music["preferred_genres"].append(val)
                elif key == "genre_dislike":
                    music.setdefault("avoid_genres", [])
                    if val not in music["avoid_genres"]:
                        music["avoid_genres"].append(val)
                elif key.startswith("volume"):
                    music[key] = val

            # --- Lighting modifiers ---
            elif cat == "lighting":
                lighting = modifiers.setdefault("lighting", {})
                if key == "nighttime_preference":
                    lighting["prefer_warm"] = val == "warm"
                elif key == "bedtime_light":
                    lighting["bedtime_light"] = val
                elif key == "late_night_dim":
                    lighting["late_night_dim"] = val
                else:
                    lighting[key] = val

            # --- Volume modifiers ---
            elif cat == "volume":
                volume = modifiers.setdefault("volume", {})
                volume[key] = val

            # --- TV modifiers ---
            elif cat == "tv":
                tv = modifiers.setdefault("tv", {})
                tv[key] = val

            # --- Room grouping ---
            elif cat == "room_grouping":
                groups = modifiers.setdefault("room_grouping", {})
                groups[key] = val

            # --- Routine modifiers ---
            elif cat == "routine":
                routines = modifiers.setdefault("routines", {})
                routines[key] = val

            # --- Context-specific corrections (may refine future inferences) ---
            elif cat == "correction":
                corrections = modifiers.setdefault("corrections", {})
                corrections.setdefault("history", []).append({
                    "key": key,
                    "value": val,
                    "confidence": conf,
                })

        return modifiers

    def record_correction(
        self,
        wrong_inference: str,
        correct_inference: str,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a correction — Jarvis got something wrong.

        Corrections are permanent (confidence 1.0, never decay).

        Args:
            wrong_inference: What Jarvis inferred incorrectly
            correct_inference: What the user said was actually happening
            context: Optional context key (e.g., "late_night_dining")

        Returns:
            The recorded correction entry.
        """
        key = f"{wrong_inference}_to_{correct_inference}"
        if context:
            key = f"{context}_{key}"

        value = {
            "wrong": wrong_inference,
            "right": correct_inference,
            "context": context,
            "corrected_at": _now_iso(),
        }
        return self.record("correction", key, value, source="correction", confidence=1.0)

    def decay_preferences(self) -> int:
        """
        Decay observed preferences that haven't been reinforced recently.

        Stated and correction preferences never decay.

        Returns:
            Number of preferences whose confidence was reduced.
        """
        decayed_count = 0
        now = datetime.now()

        for p in self._preferences:
            if p["source"] in self.PERMANENT_SOURCES:
                continue

            # Calculate days since last update
            try:
                last_update = datetime.fromisoformat(p.get("updated_at", p.get("created_at", "")))
            except (ValueError, TypeError):
                continue

            days_since = (now - last_update).total_seconds() / 86400
            if days_since < 1:
                continue  # Don't decay within the first day

            old_conf = p.get("confidence", 0.5)
            new_conf = max(0.0, old_conf - self.DECAY_RATE_PER_DAY * days_since)
            if new_conf != old_conf:
                p["confidence"] = round(new_conf, 3)
                decayed_count += 1

        if decayed_count > 0:
            self._save()

        return decayed_count

    def remove(self, category: str, key: str, source: Optional[str] = None) -> bool:
        """
        Remove a preference entry.

        Args:
            category, key: Identify the preference
            source: If given, only remove that specific source variant

        Returns:
            True if anything was removed.
        """
        before = len(self._preferences)
        self._preferences = [
            p for p in self._preferences
            if not (
                p["category"] == category
                and p["key"] == key
                and (source is None or p["source"] == source)
            )
        ]
        removed = len(self._preferences) < before
        if removed:
            self._save()
        return removed

    def dump(self) -> List[Dict[str, Any]]:
        """Return all preferences (for debugging/inspection)."""
        return list(self._preferences)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _find(
        self, category: str, key: str, source: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find a preference entry by (category, key, source)."""
        for p in self._preferences:
            if p["category"] == category and p["key"] == key:
                if source is None or p["source"] == source:
                    return p
        return None


# --------------------------------------------------------------------------- #
# Seed defaults
# --------------------------------------------------------------------------- #

def seed_defaults(store: PreferenceStore):
    """
    Populate the store with reasonable defaults based on known preferences.

    Only seeds entries that don't already exist (safe to call multiple times).
    """
    seeds = [
        # Lighting
        ("lighting", "nighttime_preference", "warm", "stated", 1.0),
        ("lighting", "bedtime_light", "wall_wash", "stated", 1.0),
        ("lighting", "late_night_dim", True, "observed", 0.9),

        # Volume
        ("volume", "default", 12, "stated", 1.0),
        ("volume", "others_sleeping", 10, "stated", 1.0),

        # TV
        ("tv", "default_source", "Apple TV", "stated", 1.0),
        ("tv", "default_app", "YouTube", "stated", 1.0),

        # Room grouping
        ("room_grouping", "great_room", ["living_room", "kitchen", "dining"], "stated", 1.0),

        # Suggestion suppression — don't suggest cooking/eating late at night
        (
            "suggestions",
            "suppress_late_night",
            {"types": ["cooking", "eating"], "after_hour": 23},
            "stated",
            1.0,
        ),
    ]

    seeded = 0
    for category, key, value, source, confidence in seeds:
        existing = store._find(category, key, source)
        if existing is None:
            store.record(category, key, value, source=source, confidence=confidence)
            seeded += 1

    return seeded


# --------------------------------------------------------------------------- #
# CLI interface
# --------------------------------------------------------------------------- #

def _parse_value(raw: str) -> Any:
    """Try to parse a CLI value as JSON, fall back to string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def cli_main():
    parser = argparse.ArgumentParser(
        description="Jarvis Preference Store — learn and query user preferences",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Record a stated preference
  %(prog)s record music genre_like jazz --source stated

  # Record an observed preference with confidence
  %(prog)s record lighting late_night_dim true --source observed --confidence 0.8

  # Query all music preferences
  %(prog)s query --category music

  # Query only active (non-decayed) preferences
  %(prog)s active --category lighting

  # Check if a suggestion should be suppressed
  %(prog)s suppress entertainment cooking 22

  # Get preference modifiers for a context
  %(prog)s modifiers winding_down

  # Record a correction
  %(prog)s correct cooking dishes --context late_night_dining

  # Run decay on observed preferences
  %(prog)s decay

  # Seed default preferences
  %(prog)s seed

  # Remove a preference
  %(prog)s remove music genre_dislike --source stated

  # Dump all preferences
  %(prog)s dump
""",
    )

    sub = parser.add_subparsers(dest="command", help="Command to run")

    # --- record ---
    p_rec = sub.add_parser("record", help="Record a preference")
    p_rec.add_argument("category", help="Preference category (music, lighting, …)")
    p_rec.add_argument("key", help="Preference key (genre_like, nighttime_preference, …)")
    p_rec.add_argument("value", help="Value (string or JSON)")
    p_rec.add_argument("--source", default="stated", choices=["stated", "observed", "routine", "correction"])
    p_rec.add_argument("--confidence", type=float, default=1.0)

    # --- query ---
    p_query = sub.add_parser("query", help="Query preferences")
    p_query.add_argument("--category", help="Filter by category")
    p_query.add_argument("--key", help="Filter by key")
    p_query.add_argument("--source", help="Filter by source")

    # --- active ---
    p_active = sub.add_parser("active", help="Query active (non-decayed) preferences")
    p_active.add_argument("--category", help="Filter by category")
    p_active.add_argument("--key", help="Filter by key")

    # --- suppress ---
    p_suppress = sub.add_parser("suppress", help="Check if a suggestion should be suppressed")
    p_suppress.add_argument("suggestion_type", help="Suggestion type (music, entertainment, …)")
    p_suppress.add_argument("context", nargs="?", default=None, help="Current context")
    p_suppress.add_argument("hour", nargs="?", type=int, default=None, help="Current hour (0–23)")

    # --- modifiers ---
    p_mod = sub.add_parser("modifiers", help="Get preference modifiers for a context")
    p_mod.add_argument("context", help="Life context (cooking, winding_down, …)")

    # --- correct ---
    p_correct = sub.add_parser("correct", help="Record a correction")
    p_correct.add_argument("wrong", help="What Jarvis got wrong")
    p_correct.add_argument("right", help="What was actually happening")
    p_correct.add_argument("--context", default=None, help="Context key (e.g., late_night_dining)")

    # --- decay ---
    sub.add_parser("decay", help="Run decay on observed preferences")

    # --- seed ---
    sub.add_parser("seed", help="Seed default preferences (safe to re-run)")

    # --- remove ---
    p_remove = sub.add_parser("remove", help="Remove a preference")
    p_remove.add_argument("category")
    p_remove.add_argument("key")
    p_remove.add_argument("--source", default=None)

    # --- dump ---
    sub.add_parser("dump", help="Dump all preferences as JSON")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    store = PreferenceStore()

    if args.command == "record":
        entry = store.record(
            args.category,
            args.key,
            _parse_value(args.value),
            source=args.source,
            confidence=args.confidence,
        )
        print(json.dumps(entry, indent=2, default=str))

    elif args.command == "query":
        results = store.get(category=args.category, key=args.key, source=args.source)
        print(json.dumps(results, indent=2, default=str))

    elif args.command == "active":
        results = store.get_active(category=args.category, key=args.key)
        print(json.dumps(results, indent=2, default=str))

    elif args.command == "suppress":
        suppressed = store.should_suppress(
            args.suggestion_type,
            context=args.context,
            hour=args.hour,
        )
        print(json.dumps({"suppressed": suppressed}))

    elif args.command == "modifiers":
        mods = store.get_preference_modifiers(args.context)
        print(json.dumps(mods, indent=2, default=str))

    elif args.command == "correct":
        entry = store.record_correction(args.wrong, args.right, context=args.context)
        print(json.dumps(entry, indent=2, default=str))

    elif args.command == "decay":
        count = store.decay_preferences()
        print(json.dumps({"decayed_count": count}))

    elif args.command == "seed":
        count = seed_defaults(store)
        print(json.dumps({"seeded_count": count, "total": len(store.dump())}))

    elif args.command == "remove":
        removed = store.remove(args.category, args.key, source=args.source)
        print(json.dumps({"removed": removed}))

    elif args.command == "dump":
        print(json.dumps(store.dump(), indent=2, default=str))


if __name__ == "__main__":
    cli_main()
