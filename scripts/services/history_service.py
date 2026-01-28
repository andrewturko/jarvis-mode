#!/usr/bin/env python3
"""
History Service - Observation timeline and anomaly detection.

Provides temporal analysis of room activity:
- Activity timelines
- Occupancy duration tracking
- Previous context retrieval
- Anomaly detection
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_manager import StateManager
from services.pattern_service import PatternService


class HistoryService:
    """
    Analyze observation history for temporal insights and anomalies.
    """

    def __init__(self, state_manager: StateManager, pattern_service: Optional[PatternService] = None):
        """
        Initialize history service.

        Args:
            state_manager: StateManager instance
            pattern_service: Optional PatternService for anomaly detection
        """
        self.state_manager = state_manager
        self.pattern_service = pattern_service

    def get_activity_timeline(self, room: str, hours: int = 2) -> List[Dict[str, Any]]:
        """
        Get recent activity timeline for a room.

        Returns chronological list of activities with timestamps.

        Args:
            room: Room name
            hours: Number of hours to look back

        Returns:
            List of activity dicts with timestamp, activity, summary
        """
        observations = self.state_manager.get_recent_observations(room, hours)

        # Observations are already sorted newest first, reverse for timeline
        timeline = []

        for obs in reversed(observations):
            timeline.append({
                "timestamp": obs.get("timestamp"),
                "activity": obs.get("activity", "unknown"),
                "summary": obs.get("summary", ""),
                "context": obs.get("context")
            })

        return timeline

    def get_occupancy_duration(self, room: str) -> timedelta:
        """
        Get how long room has been in current occupancy state.

        Args:
            room: Room name

        Returns:
            timedelta object representing duration
        """
        duration_minutes = self.state_manager.get_occupancy_duration(room)

        if duration_minutes is None:
            return timedelta(0)

        return timedelta(minutes=duration_minutes)

    def get_previous_context(self, room: str) -> Optional[str]:
        """
        Get what context was happening before current one.

        Looks at decision log to find last different context for this room.

        Args:
            room: Room name

        Returns:
            Previous context name or None
        """
        decisions = self.state_manager.get_decision_log(limit=50)

        current_context = None
        for decision in decisions:
            if decision.get("room") != room:
                continue

            context = decision.get("context_inferred")
            if not context:
                continue

            if current_context is None:
                # First context we found is current
                current_context = context
            elif context != current_context:
                # Found different context - this is previous
                return context

        return None

    def detect_anomaly(self, room: str) -> Optional[str]:
        """
        Detect unusual patterns in room activity.

        Compares current activity against learned patterns.

        Examples:
        - "Up at 3am when usually asleep"
        - "Been working 3 hours (unusual, typically 45min)"
        - "Kitchen active at unusual time"

        Args:
            room: Room name

        Returns:
            Anomaly description string or None if no anomaly
        """
        if not self.pattern_service:
            return None

        now = datetime.now()
        current_hour = now.hour

        # Get current room state
        room_state = self.state_manager.get_room_state(room)
        if not room_state:
            return None

        occupancy = room_state.get("occupancy", {})
        is_occupied = occupancy.get("current", False)

        if not is_occupied:
            return None

        # Check 1: Unusual time for this room
        typical_hours = self.pattern_service.get_typical_room_time(room)
        if typical_hours and current_hour not in typical_hours:
            # Room is occupied at unusual time
            if current_hour >= 23 or current_hour < 6:
                return f"Active in {room} at unusual late night hour ({current_hour}:00)"
            else:
                return f"Active in {room} at unusual time (typically used at {typical_hours[0]}:00)"

        # Check 2: Unusual duration for current context
        last_context = room_state.get("last_context", {})
        context = last_context.get("inferred")

        if context:
            duration_minutes = self.state_manager.get_occupancy_duration(room) or 0
            typical_duration = self.pattern_service.get_typical_duration(context)

            if typical_duration and duration_minutes > typical_duration * 2:
                # Duration is 2x typical - unusual
                return (
                    f"Been {context} for {duration_minutes} minutes "
                    f"(unusual, typically {typical_duration} min)"
                )

        # Check 3: Late night activity
        if (current_hour >= 23 or current_hour < 6) and room != "primary_bedroom":
            return f"Active in {room} during typical sleeping hours"

        return None

    def get_context_duration(self, room: str) -> Optional[int]:
        """
        Get how long current context has been active.

        Args:
            room: Room name

        Returns:
            Duration in minutes or None
        """
        room_state = self.state_manager.get_room_state(room)
        if not room_state:
            return None

        last_context = room_state.get("last_context", {})
        if not last_context:
            return None

        timestamp = last_context.get("timestamp")
        if not timestamp:
            return None

        try:
            context_time = datetime.fromisoformat(timestamp)
            duration = datetime.now() - context_time
            return int(duration.total_seconds() / 60)
        except ValueError:
            return None

    def get_recent_context_changes(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get recent context changes across all rooms.

        Args:
            hours: Number of hours to look back

        Returns:
            List of context change events
        """
        decisions = self.state_manager.get_decision_log(limit=100)
        cutoff = datetime.now() - timedelta(hours=hours)

        changes = []
        last_contexts = {}  # room -> last context

        # Process decisions chronologically (oldest first)
        for decision in reversed(decisions):
            try:
                timestamp = datetime.fromisoformat(decision["timestamp"])
                if timestamp < cutoff:
                    continue

                room = decision.get("room")
                context = decision.get("context_inferred")

                if not room or not context:
                    continue

                # Check if context changed
                if room in last_contexts and last_contexts[room] != context:
                    changes.append({
                        "timestamp": decision["timestamp"],
                        "room": room,
                        "from_context": last_contexts[room],
                        "to_context": context,
                        "confidence": decision.get("confidence")
                    })

                last_contexts[room] = context

            except (KeyError, ValueError):
                continue

        # Return most recent first
        return list(reversed(changes))

    def get_daily_summary(self, date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get summary of activity for a day.

        Args:
            date: Date to summarize (defaults to today)

        Returns:
            Dict with daily activity summary
        """
        if date is None:
            date = datetime.now()

        # Get decisions for the day
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        decisions = self.state_manager.get_decision_log(limit=500)

        daily_decisions = []
        for decision in decisions:
            try:
                timestamp = datetime.fromisoformat(decision["timestamp"])
                if start_of_day <= timestamp < end_of_day:
                    daily_decisions.append(decision)
            except (KeyError, ValueError):
                continue

        # Analyze
        contexts_seen = set()
        rooms_used = set()
        total_spoke = 0

        for decision in daily_decisions:
            contexts_seen.add(decision.get("context_inferred"))
            rooms_used.add(decision.get("room"))
            if decision.get("decision") == "spoke":
                total_spoke += 1

        return {
            "date": date.date().isoformat(),
            "total_checks": len(daily_decisions),
            "contexts_observed": list(contexts_seen),
            "rooms_active": list(rooms_used),
            "times_spoke": total_spoke,
            "silence_rate": round(1 - (total_spoke / len(daily_decisions)), 2) if daily_decisions else 0
        }
