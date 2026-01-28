#!/usr/bin/env python3
"""
Pattern Service - Implicit pattern learning from observations.

Analyzes observation history to learn:
- Typical room usage times
- Context transitions
- Activity durations
- Preference acceptance rates
"""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_manager import StateManager


class PatternService:
    """
    Learn patterns from observations and decisions.

    Runs periodically (e.g., hourly) to analyze recent history and update
    learned patterns in patterns.json.
    """

    def __init__(self, state_manager: StateManager, patterns_file: Path):
        """
        Initialize pattern service.

        Args:
            state_manager: StateManager instance
            patterns_file: Path to patterns.json
        """
        self.state_manager = state_manager
        self.patterns_file = patterns_file

    def _load_patterns(self) -> Dict[str, Any]:
        """Load patterns from patterns.json."""
        try:
            with open(self.patterns_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "learned_patterns": {"patterns": {}},
                "context_observations": {"history": []},
                "suggestion_history": {"recent": []},
                "room_usage_patterns": {},
                "context_transitions": {},
                "activity_durations": {}
            }

    def _save_patterns(self, patterns: Dict[str, Any]):
        """Save patterns to patterns.json."""
        with open(self.patterns_file, 'w') as f:
            json.dump(patterns, f, indent=2)

    def analyze_and_update_patterns(self, days_back: int = 7):
        """
        Analyze observation history and update learned patterns.

        Args:
            days_back: How many days of history to analyze
        """
        patterns = self._load_patterns()

        # Get decision log
        decisions = self.state_manager.get_decision_log(limit=500)

        # Filter to time window
        cutoff = datetime.now() - timedelta(days=days_back)
        recent_decisions = []

        for decision in decisions:
            try:
                timestamp = datetime.fromisoformat(decision["timestamp"])
                if timestamp >= cutoff:
                    recent_decisions.append(decision)
            except (KeyError, ValueError):
                continue

        # Update various pattern types
        self._learn_room_usage_patterns(recent_decisions, patterns)
        self._learn_context_transitions(recent_decisions, patterns)
        self._learn_activity_durations(recent_decisions, patterns)
        self._learn_suggestion_acceptance(recent_decisions, patterns)

        # Save updated patterns
        patterns["last_analysis"] = datetime.now().isoformat()
        patterns["decisions_analyzed"] = len(recent_decisions)
        self._save_patterns(patterns)

    def _learn_room_usage_patterns(self, decisions: List[Dict], patterns: Dict):
        """
        Learn typical room usage times.

        Example: kitchen typically used at 7:15am ± 15min, 12:30pm, 6:45pm.

        Args:
            decisions: Recent decisions
            patterns: Patterns dict to update
        """
        # Track room usage by hour
        room_hours = defaultdict(list)

        for decision in decisions:
            room = decision.get("room")
            if not room:
                continue

            try:
                timestamp = datetime.fromisoformat(decision["timestamp"])
                # Record hour + day of week
                room_hours[room].append({
                    "hour": timestamp.hour,
                    "minute": timestamp.minute,
                    "day_of_week": timestamp.strftime("%A")
                })
            except (KeyError, ValueError):
                continue

        # Analyze patterns
        room_patterns = {}

        for room, times in room_hours.items():
            if len(times) < 3:  # Need minimum observations
                continue

            # Group by hour
            hour_counts = defaultdict(int)
            for t in times:
                hour_counts[t["hour"]] += 1

            # Find typical hours (appeared at least 3 times)
            typical_hours = [hour for hour, count in hour_counts.items() if count >= 3]
            typical_hours.sort()

            if typical_hours:
                room_patterns[room] = {
                    "typical_hours": typical_hours,
                    "total_observations": len(times),
                    "most_common_hour": max(hour_counts, key=hour_counts.get)
                }

        patterns["room_usage_patterns"] = room_patterns

    def _learn_context_transitions(self, decisions: List[Dict], patterns: Dict):
        """
        Learn common context transitions.

        Example: cooking → post_meal (70% of time).

        Args:
            decisions: Recent decisions
            patterns: Patterns dict to update
        """
        transitions = defaultdict(int)
        last_context = None

        # Sort by timestamp (oldest first)
        sorted_decisions = sorted(
            decisions,
            key=lambda d: d.get("timestamp", ""),
            reverse=False
        )

        for decision in sorted_decisions:
            context = decision.get("context_inferred")
            if not context:
                continue

            if last_context and last_context != context:
                transition_key = f"{last_context} → {context}"
                transitions[transition_key] += 1

            last_context = context

        # Convert to patterns
        transition_patterns = {}
        for transition, count in transitions.items():
            if count >= 2:  # Minimum occurrences
                transition_patterns[transition] = {
                    "count": count,
                    "confidence": min(count / 10.0, 1.0)  # Scale to 0-1
                }

        patterns["context_transitions"] = transition_patterns

    def _learn_activity_durations(self, decisions: List[Dict], patterns: Dict):
        """
        Learn typical activity durations.

        Example: cooking usually lasts 30 minutes.

        Args:
            decisions: Recent decisions
            patterns: Patterns dict to update
        """
        # Track duration for each context
        context_durations = defaultdict(list)

        for decision in decisions:
            context = decision.get("context_inferred")
            duration = decision.get("duration_minutes", 0)

            if context and duration > 0:
                context_durations[context].append(duration)

        # Calculate statistics
        duration_patterns = {}

        for context, durations in context_durations.items():
            if len(durations) < 3:
                continue

            avg_duration = sum(durations) / len(durations)
            max_duration = max(durations)
            min_duration = min(durations)

            duration_patterns[context] = {
                "typical_minutes": round(avg_duration),
                "min_minutes": min_duration,
                "max_minutes": max_duration,
                "sample_size": len(durations)
            }

        patterns["activity_durations"] = duration_patterns

    def _learn_suggestion_acceptance(self, decisions: List[Dict], patterns: Dict):
        """
        Learn which suggestions are accepted/rejected.

        Updates acceptance rates in learned_patterns.

        Args:
            decisions: Recent decisions
            patterns: Patterns dict to update
        """
        # This is handled by record_suggestion_response in life_context.py
        # But we can aggregate stats here
        learned = patterns.get("learned_patterns", {}).get("patterns", {})

        # Calculate aggregate stats
        total_patterns = len(learned)
        high_acceptance = sum(1 for p in learned.values() if p.get("acceptance_rate", 0) > 0.7)
        low_acceptance = sum(1 for p in learned.values() if p.get("acceptance_rate", 0) < 0.3)

        patterns["suggestion_stats"] = {
            "total_learned_patterns": total_patterns,
            "high_acceptance_count": high_acceptance,
            "low_acceptance_count": low_acceptance,
            "last_updated": datetime.now().isoformat()
        }

    def get_learned_routine(self, context: str, time_of_day: str) -> Optional[Dict[str, Any]]:
        """
        Get learned preferences for a context + time combination.

        Args:
            context: Context name (e.g., "cooking")
            time_of_day: Time of day (e.g., "evening")

        Returns:
            Dict with learned routine preferences or None
        """
        patterns = self._load_patterns()
        learned = patterns.get("learned_patterns", {}).get("patterns", {})

        # Look for pattern key
        for pattern_key, pattern_data in learned.items():
            # Pattern keys are like "cooking+music" or "winding_down+lighting"
            if context in pattern_key and pattern_data.get("acceptance_rate", 0) > 0.5:
                return pattern_data

        return None

    def get_typical_room_time(self, room: str) -> Optional[List[int]]:
        """
        Get typical usage hours for a room.

        Args:
            room: Room name

        Returns:
            List of typical hours (0-23) or None
        """
        patterns = self._load_patterns()
        room_patterns = patterns.get("room_usage_patterns", {})
        room_data = room_patterns.get(room, {})

        return room_data.get("typical_hours")

    def get_context_transition_likelihood(self, from_context: str, to_context: str) -> float:
        """
        Get likelihood of a context transition.

        Args:
            from_context: Starting context
            to_context: Ending context

        Returns:
            Confidence score (0-1) or 0 if no data
        """
        patterns = self._load_patterns()
        transitions = patterns.get("context_transitions", {})

        transition_key = f"{from_context} → {to_context}"
        transition_data = transitions.get(transition_key, {})

        return transition_data.get("confidence", 0)

    def get_typical_duration(self, context: str) -> Optional[int]:
        """
        Get typical duration for a context in minutes.

        Args:
            context: Context name

        Returns:
            Typical duration in minutes or None
        """
        patterns = self._load_patterns()
        durations = patterns.get("activity_durations", {})
        context_data = durations.get(context, {})

        return context_data.get("typical_minutes")
