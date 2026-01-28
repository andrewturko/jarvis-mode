#!/usr/bin/env python3
"""
Proactive Suggestion Engine for Jarvis Mode.

Generates ranked, context-aware suggestions based on:
- Inferred life context and needs
- Learned patterns and acceptance rates
- Time-based rules
- Anomaly detection
- Available capabilities
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class Suggestion:
    """A single suggestion with metadata."""
    type: str
    action: str
    reason: str
    priority: str  # "urgent", "high", "medium", "low"
    acceptance_rate: Optional[float] = None
    capability: Optional[str] = None
    learned: bool = False
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "type": self.type,
            "action": self.action,
            "reason": self.reason,
            "priority": self.priority
        }
        if self.acceptance_rate is not None:
            result["acceptance_rate"] = self.acceptance_rate
        if self.capability:
            result["capability"] = self.capability
        if self.learned:
            result["learned"] = self.learned
        if self.metadata:
            result["metadata"] = self.metadata
        return result


class SuggestionEngine:
    """
    Generates and ranks proactive suggestions based on context and patterns.

    Features:
    - Context-based suggestion generation
    - Pattern-based learning (uses acceptance rates)
    - Time-based rules
    - Anomaly detection
    - Deduplication against recent history
    - Priority ranking
    """

    # Suggestion type priorities
    PRIORITY_ORDER = ["urgent", "high", "medium", "low"]

    def __init__(self, capabilities: Dict[str, Any] = None):
        """
        Initialize suggestion engine.

        Args:
            capabilities: Available home capabilities (vacuum, TV, lights, etc.)
        """
        self.capabilities = capabilities or {}

    def generate_suggestions(
        self,
        context: Dict[str, Any],
        learned_patterns: Dict[str, Any],
        home_state: Dict[str, Any],
        recent_observations: List[Dict[str, Any]] = None
    ) -> List[Suggestion]:
        """
        Generate ranked suggestions based on context and patterns.

        Args:
            context: Inferred context with confidence, signals, etc.
            learned_patterns: Dictionary of learned preference patterns
            home_state: Current home state (lights, media, etc.)
            recent_observations: Recent activity observations

        Returns:
            List of Suggestion objects, ranked by priority and acceptance rate
        """
        suggestions = []
        recent_observations = recent_observations or []

        ctx_name = context.get("context", "unknown")
        confidence = context.get("confidence", 0)
        time_ctx = context.get("time", {})

        # Skip if confidence too low
        if confidence < 0.4:
            return []

        # 1. Learned pattern-based suggestions (highest priority for high acceptance)
        pattern_suggestions = self._generate_from_patterns(
            ctx_name, learned_patterns, time_ctx
        )
        suggestions.extend(pattern_suggestions)

        # 2. Context-specific suggestions
        context_suggestions = self._generate_from_context(
            ctx_name, context, home_state, time_ctx
        )
        suggestions.extend(context_suggestions)

        # 3. Efficiency-based suggestions (lights in empty rooms, etc.)
        efficiency_suggestions = self._generate_efficiency_suggestions(
            home_state, context
        )
        suggestions.extend(efficiency_suggestions)

        # 4. Anomaly-based suggestions
        anomaly_suggestions = self._generate_anomaly_suggestions(
            context, recent_observations, time_ctx
        )
        suggestions.extend(anomaly_suggestions)

        # 5. Comfort-based suggestions (temperature, lighting)
        comfort_suggestions = self._generate_comfort_suggestions(
            ctx_name, home_state, time_ctx
        )
        suggestions.extend(comfort_suggestions)

        # Rank and return
        return self.rank_suggestions(suggestions)

    def _generate_from_patterns(
        self,
        context: str,
        learned_patterns: Dict[str, Any],
        time_ctx: Dict[str, Any]
    ) -> List[Suggestion]:
        """Generate suggestions from learned patterns."""
        suggestions = []

        for pattern_key, pattern_data in learned_patterns.items():
            # Check if pattern matches current context
            if not pattern_key.startswith(f"{context}+"):
                continue

            acceptance_rate = pattern_data.get("acceptance_rate", 0)

            # Only suggest if acceptance rate is decent
            if acceptance_rate < 0.3:
                continue

            # Extract pattern type
            parts = pattern_key.split("+")
            pattern_type = parts[1] if len(parts) > 1 else "unknown"

            # Check if typical time matches
            typical_times = pattern_data.get("typical_times", [])
            current_hour = time_ctx.get("hour", 0)

            time_match = False
            if typical_times:
                for time_str in typical_times:
                    try:
                        time_hour = int(time_str.split(":")[0])
                        if abs(time_hour - current_hour) <= 1:
                            time_match = True
                            break
                    except (ValueError, IndexError):
                        continue

            # If we have typical times and it's not a match, skip
            if typical_times and not time_match:
                continue

            preferred_action = pattern_data.get("action") or pattern_data.get("preferred_action")

            if preferred_action:
                priority = "high" if acceptance_rate > 0.7 else "medium"

                suggestions.append(Suggestion(
                    type=pattern_type,
                    action=preferred_action,
                    reason=f"You usually want this during {context}",
                    priority=priority,
                    acceptance_rate=acceptance_rate,
                    learned=True,
                    metadata={"pattern_key": pattern_key}
                ))

        return suggestions

    def _generate_from_context(
        self,
        context: str,
        context_data: Dict[str, Any],
        home_state: Dict[str, Any],
        time_ctx: Dict[str, Any]
    ) -> List[Suggestion]:
        """Generate context-specific suggestions."""
        suggestions = []

        # Cooking context
        if context == "cooking":
            # Check if music is not playing
            if not home_state.get("music_playing", False):
                suggestions.append(Suggestion(
                    type="entertainment",
                    action="play_background_music",
                    reason="Background music while cooking",
                    priority="medium"
                ))

        # Post-meal context
        elif context == "post_meal":
            if "vacuum" in self.capabilities:
                suggestions.append(Suggestion(
                    type="cleanliness",
                    action="vacuum_kitchen",
                    reason="Kitchen could use a quick clean after the meal",
                    priority="medium",
                    capability="vacuum"
                ))

        # Winding down context
        elif context == "winding_down":
            # Suggest dimming lights if bright
            if "lighting" in self.capabilities:
                suggestions.append(Suggestion(
                    type="comfort",
                    action="dim_lights",
                    reason="Evening ambiance",
                    priority="low"
                ))

            # Suggest entertainment
            if "tv" in self.capabilities:
                suggestions.append(Suggestion(
                    type="entertainment",
                    action="suggest_show",
                    reason="Settling in for the evening",
                    priority="low"
                ))

        # Going to bed context
        elif context == "going_to_bed":
            suggestions.append(Suggestion(
                type="transition",
                action="goodnight_routine",
                reason="Prepare the house for sleep",
                priority="medium"
            ))

        # Away context
        elif context == "away":
            if "vacuum" in self.capabilities:
                suggestions.append(Suggestion(
                    type="cleanliness",
                    action="full_clean",
                    reason="Good time to clean while nobody's home",
                    priority="medium",
                    capability="vacuum"
                ))

        # Working context - minimal suggestions
        elif context == "working":
            # Only suggest break if extended duration
            duration = context_data.get("duration_minutes", 0)
            if duration > 120:  # 2+ hours
                suggestions.append(Suggestion(
                    type="wellness",
                    action="suggest_break",
                    reason=f"You've been working for {duration} minutes",
                    priority="low"
                ))

        return suggestions

    def _generate_efficiency_suggestions(
        self,
        home_state: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[Suggestion]:
        """Generate efficiency-based suggestions (energy saving, etc.)."""
        suggestions = []

        # Check for lights on in presumably empty rooms
        # This would need room occupancy data
        # For now, just a placeholder

        return suggestions

    def _generate_anomaly_suggestions(
        self,
        context: Dict[str, Any],
        recent_observations: List[Dict[str, Any]],
        time_ctx: Dict[str, Any]
    ) -> List[Suggestion]:
        """Generate suggestions based on unusual patterns."""
        suggestions = []

        # Check for unusual late-night activity
        hour = time_ctx.get("hour", 0)
        if hour >= 2 and hour < 5:
            ctx_name = context.get("context", "unknown")
            if ctx_name not in ["sleeping", "going_to_bed"]:
                suggestions.append(Suggestion(
                    type="wellness",
                    action="check_in",
                    reason="You're up late - everything okay?",
                    priority="low"
                ))

        # Check for unusually long activity duration
        duration = context.get("duration_minutes", 0)
        typical_duration = context.get("typical_duration_minutes")

        if typical_duration and duration > typical_duration * 2:
            suggestions.append(Suggestion(
                type="wellness",
                action="suggest_break",
                reason=f"This is taking longer than usual ({duration} min vs typical {typical_duration} min)",
                priority="low"
            ))

        return suggestions

    def _generate_comfort_suggestions(
        self,
        context: str,
        home_state: Dict[str, Any],
        time_ctx: Dict[str, Any]
    ) -> List[Suggestion]:
        """Generate comfort-related suggestions (temperature, lighting, etc.)."""
        suggestions = []

        # Placeholder for temperature/climate suggestions
        # Would need climate sensor data from home_state

        return suggestions

    def filter_recent_suggestions(
        self,
        suggestions: List[Suggestion],
        recent_history: List[Dict[str, Any]],
        hours: int = 2
    ) -> List[Suggestion]:
        """
        Filter out suggestions that were offered recently.

        Args:
            suggestions: Current suggestions to filter
            recent_history: Recent decision log entries
            hours: How many hours to look back

        Returns:
            Filtered list of suggestions
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        recent_suggestions = []

        # Extract suggestions from history
        for entry in recent_history:
            try:
                timestamp = datetime.fromisoformat(entry.get("timestamp", ""))
                if timestamp >= cutoff:
                    if "suggestions_offered" in entry:
                        recent_suggestions.extend(entry.get("suggestions_offered", []))
            except (ValueError, AttributeError):
                continue

        # Build set of recent suggestion keys
        recent_keys = set()
        for sugg in recent_suggestions:
            if isinstance(sugg, dict):
                key = f"{sugg.get('type', '')}:{sugg.get('action', '')}"
                recent_keys.add(key)

        # Filter new suggestions
        filtered = []
        for sugg in suggestions:
            key = f"{sugg.type}:{sugg.action}"
            if key not in recent_keys:
                filtered.append(sugg)

        return filtered

    def rank_suggestions(self, suggestions: List[Suggestion]) -> List[Suggestion]:
        """
        Rank suggestions by priority, acceptance rate, and other factors.

        Args:
            suggestions: Unranked list of suggestions

        Returns:
            Ranked list of suggestions (most important first)
        """
        def suggestion_score(sugg: Suggestion) -> tuple:
            # Priority level (lower index = higher priority)
            try:
                priority_score = self.PRIORITY_ORDER.index(sugg.priority)
            except ValueError:
                priority_score = 99

            # Acceptance rate (higher is better, None = neutral)
            acceptance_score = -(sugg.acceptance_rate or 0.5)

            # Learned suggestions get slight boost
            learned_boost = 0 if sugg.learned else 1

            return (priority_score, acceptance_score, learned_boost)

        return sorted(suggestions, key=suggestion_score)
