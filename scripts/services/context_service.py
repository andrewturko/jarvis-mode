#!/usr/bin/env python3
"""
Context Service - Bridge between observations and intelligence layer.

Combines snapshot analysis, recent history, and home state to produce
rich context with suggestions and silence logic.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_manager import StateManager
import life_context


@dataclass
class ContextAnalysis:
    """
    Result of context analysis for a room.

    Attributes:
        context: Inferred context name (e.g., "cooking", "winding_down")
        confidence: Confidence score (0-1)
        signals: List of signals that led to this context
        previous_context: What context was before (if changed)
        duration_minutes: How long in current state
        suggestions: List of actionable suggestions
        should_speak: Whether agent should speak now
        silence_reason: Why agent should stay silent (if should_speak is False)
        typical_needs: Expected needs for this context
        time_context: Time-based context dict
    """
    context: str
    confidence: float
    signals: List[str]
    previous_context: Optional[str] = None
    duration_minutes: int = 0
    suggestions: List[Dict[str, Any]] = field(default_factory=list)
    should_speak: bool = False
    silence_reason: Optional[str] = None
    typical_needs: List[str] = field(default_factory=list)
    time_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "context": self.context,
            "confidence": self.confidence,
            "signals": self.signals,
            "previous_context": self.previous_context,
            "duration_minutes": self.duration_minutes,
            "suggestions": self.suggestions,
            "should_speak": self.should_speak,
            "silence_reason": self.silence_reason,
            "typical_needs": self.typical_needs,
            "time_context": self.time_context
        }


class ContextService:
    """
    Bridge between observations and intelligence layer.

    Analyzes rooms by combining:
    - Current snapshot (if available)
    - Recent observation history (last 2 hours)
    - Current home state (all rooms, devices)
    - Learned patterns

    Produces:
    - Inferred context with confidence
    - Actionable suggestions
    - Decision on whether to speak
    """

    def __init__(self, state_manager: StateManager):
        """
        Initialize context service.

        Args:
            state_manager: StateManager instance for state access
        """
        self.state_manager = state_manager

    def analyze_room(
        self,
        room: str,
        snapshot_path: Optional[str] = None,
        vision_summary: Optional[Dict[str, Any]] = None
    ) -> ContextAnalysis:
        """
        Analyze a room and produce rich context with suggestions.

        Args:
            room: Room name
            snapshot_path: Optional path to snapshot image
            vision_summary: Optional pre-analyzed vision summary

        Returns:
            ContextAnalysis with context, suggestions, and speak decision
        """
        # 1. Get recent observations (last 2 hours)
        recent_obs = self.state_manager.get_recent_observations(room, hours=2)

        # 2. Get current room state
        room_state = self.state_manager.get_room_state(room)

        # 3. Get all room states for global context
        full_state = self.state_manager.read_state()
        all_rooms = full_state.get('rooms', {})

        # 4. Build room observations dict for life_context
        room_observations = self._build_room_observations(
            room, room_state, vision_summary, all_rooms
        )

        # 5. Build home state dict
        home_state = self._build_home_state(all_rooms)

        # 6. Infer context using life_context engine
        context_result = life_context.infer_context(room_observations, home_state)

        # 7. Get occupancy duration
        duration = self.state_manager.get_occupancy_duration(room) or 0

        # 8. Generate suggestions
        capabilities = life_context.get_capabilities()
        suggestions = life_context.get_suggestions(context_result, capabilities)

        # 9. Apply silence logic
        should_speak, silence_reason = self._should_speak(
            context_result,
            suggestions,
            recent_obs,
            room
        )

        # 10. Build and return ContextAnalysis
        return ContextAnalysis(
            context=context_result["context"],
            confidence=context_result["confidence"],
            signals=context_result["signals"],
            previous_context=context_result.get("previous_context"),
            duration_minutes=duration,
            suggestions=suggestions,
            should_speak=should_speak,
            silence_reason=silence_reason,
            typical_needs=context_result.get("typical_needs", []),
            time_context=context_result.get("time", {})
        )

    def _build_room_observations(
        self,
        room: str,
        room_state: Optional[Dict[str, Any]],
        vision_summary: Optional[Dict[str, Any]],
        all_rooms: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Build room observations dict for life_context.infer_context.

        Args:
            room: Current room name
            room_state: Current room state
            vision_summary: Vision analysis summary
            all_rooms: All room states

        Returns:
            Dict mapping room -> observation dict
        """
        observations = {}

        # Add current room with enriched data
        if room_state:
            occupancy = room_state.get('occupancy', {})
            is_occupied = occupancy.get('current', False)

            obs = {
                "person_detected": is_occupied,
                "activity_duration": self.state_manager.get_occupancy_duration(room) or 0
            }

            # Add vision summary if available
            if vision_summary:
                obs["activity"] = vision_summary.get("activity", "unknown")
                obs["summary"] = vision_summary.get("summary", "")

            observations[room] = obs

        # Add other rooms with basic occupancy
        for other_room, other_state in all_rooms.items():
            if other_room == room:
                continue

            occupancy = other_state.get('occupancy', {})
            observations[other_room] = {
                "person_detected": occupancy.get('current', False)
            }

        return observations

    def _build_home_state(self, all_rooms: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build home state dict for context inference.

        Args:
            all_rooms: All room states

        Returns:
            Dict with home-level state
        """
        # Count occupied rooms
        occupied_count = sum(
            1 for room_state in all_rooms.values()
            if room_state.get('occupancy', {}).get('current', False)
        )

        return {
            "occupied_rooms": occupied_count,
            "total_rooms": len(all_rooms),
            "all_empty": occupied_count == 0
        }

    def _should_speak(
        self,
        context_result: Dict[str, Any],
        suggestions: List[Dict[str, Any]],
        recent_history: List[Dict[str, Any]],
        room: str
    ) -> tuple[bool, Optional[str]]:
        """
        Determine if agent should speak based on silence logic.

        SPEAK if:
        - Context just changed (confidence > 0.7) AND suggestions available
        - Safety/security issue detected
        - New actionable suggestion (not offered recently)

        STAY SILENT if:
        - Same context, no new suggestions
        - Same suggestion offered < 2 hours ago
        - Low confidence (< 0.5)
        - Focus context (working, sleeping)
        - No actionable suggestions

        Args:
            context_result: Context inference result
            suggestions: Generated suggestions
            recent_history: Recent observations
            room: Room name

        Returns:
            Tuple of (should_speak, silence_reason)
        """
        context = context_result["context"]
        confidence = context_result["confidence"]
        previous_context = context_result.get("previous_context")

        # Rule 1: Low confidence -> stay silent
        if confidence < 0.5:
            return False, f"Low confidence ({confidence})"

        # Rule 2: No suggestions -> stay silent
        if not suggestions:
            return False, "No actionable suggestions"

        # Rule 3: Focus contexts -> stay silent
        focus_contexts = ["working", "sleeping", "concentrating"]
        if context in focus_contexts:
            return False, f"Focus context ({context})"

        # Rule 4: Context just changed with high confidence -> speak
        if previous_context and previous_context != context and confidence > 0.7:
            return True, None

        # Rule 5: Check if suggestions are new (not offered recently)
        recent_suggestions = self._get_recent_suggestions(room, hours=2)
        new_suggestions = self._filter_new_suggestions(suggestions, recent_suggestions)

        if not new_suggestions:
            return False, "All suggestions offered recently"

        # Rule 6: High-value suggestion with good acceptance rate -> speak
        high_value = [s for s in new_suggestions if s.get("acceptance_rate", 0) > 0.7]
        if high_value:
            return True, None

        # Rule 7: Context stable, medium suggestions -> conditional speak
        if len(new_suggestions) > 0 and confidence > 0.6:
            return True, None

        # Default: stay silent
        return False, "No compelling reason to speak"

    def _get_recent_suggestions(self, room: str, hours: int = 2) -> List[Dict[str, Any]]:
        """
        Get suggestions offered in recent decision log.

        Args:
            room: Room name
            hours: How many hours to look back

        Returns:
            List of recently offered suggestions
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []

        # Get decision log
        decisions = self.state_manager.get_decision_log(limit=50)

        for decision in decisions:
            # Check if for this room and within time window
            if decision.get("room") != room:
                continue

            try:
                timestamp = datetime.fromisoformat(decision["timestamp"])
                if timestamp < cutoff:
                    continue

                # Extract suggestions from decision
                if "suggestions_offered" in decision:
                    recent.extend(decision["suggestions_offered"])
            except (KeyError, ValueError):
                continue

        return recent

    def _filter_new_suggestions(
        self,
        suggestions: List[Dict[str, Any]],
        recent: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filter out suggestions that were offered recently.

        Args:
            suggestions: Current suggestions
            recent: Recently offered suggestions

        Returns:
            List of new suggestions
        """
        if not recent:
            return suggestions

        # Build set of recent suggestion types/actions
        recent_keys = set()
        for sugg in recent:
            key = f"{sugg.get('type', '')}:{sugg.get('action', '')}"
            recent_keys.add(key)

        # Filter new suggestions
        new = []
        for sugg in suggestions:
            key = f"{sugg.get('type', '')}:{sugg.get('action', '')}"
            if key not in recent_keys:
                new.append(sugg)

        return new

    def on_transition(self, room: str, from_state: bool, to_state: bool):
        """
        Handle occupancy state transition.

        Called when room occupancy changes (empty -> occupied or vice versa).

        Args:
            room: Room name
            from_state: Previous occupancy state
            to_state: New occupancy state
        """
        # Update context when transitioning to occupied
        if to_state and not from_state:
            # Room became occupied - analyze context
            analysis = self.analyze_room(room)

            # Update room state with inferred context
            self.state_manager.update_room(room, {
                "last_context": {
                    "inferred": analysis.context,
                    "confidence": analysis.confidence,
                    "timestamp": datetime.now().isoformat()
                }
            })
