#!/usr/bin/env python3
"""
Handler for occupied room events.

Generates contextual suggestions when a room becomes occupied.
"""

from datetime import datetime
from typing import Dict, List, Optional

from core.logger import get_logger
from core.config import JarvisConfig
from services.ha_service import HAService
from services.context_service import ContextService

logger = get_logger("jarvis.occupied_room_handler")


class OccupiedRoomHandler:
    """
    Handler for occupied room events.

    Features:
    - Context-aware suggestions based on inferred context
    - Uses context_service for intelligent analysis (Phase 2)
    - Silence logic to prevent excessive notifications
    """

    def __init__(
        self,
        config: JarvisConfig,
        ha_service: HAService,
        context_service: Optional[ContextService] = None
    ):
        """
        Initialize occupied room handler.

        Args:
            config: Jarvis configuration
            ha_service: HAService instance
            context_service: Optional ContextService for intelligent analysis
        """
        self.config = config
        self.ha_service = ha_service
        self.context_service = context_service

    def handle(self, room_name: str) -> Dict:
        """
        Handle occupied room event.

        Uses context_service for intelligent analysis if available,
        otherwise falls back to basic time-based logic.

        Args:
            room_name: Room that became occupied

        Returns:
            Dict with room, context, suggestions, should_speak, etc.
        """
        logger.info("handle_occupied_room", room=room_name)

        # Phase 2: Use context_service if available
        if self.context_service:
            return self._handle_with_context_service(room_name)
        else:
            return self._handle_basic(room_name)

    def _handle_with_context_service(self, room_name: str) -> Dict:
        """
        Handle with context_service (Phase 2 intelligence).

        Args:
            room_name: Room name

        Returns:
            Dict with enriched context and suggestions
        """
        # Analyze room with context service
        analysis = self.context_service.analyze_room(room_name)

        hour = datetime.now().hour

        # Get current state
        from ..handlers.empty_room_handler import ROOM_LIGHTS_MAP
        lights_on = self.ha_service.get_room_lights(room_name, ROOM_LIGHTS_MAP)
        home_state = self.ha_service.get_home_state()

        result = {
            "room": room_name,
            "hour": hour,
            "timeOfDay": analysis.time_context.get("time_of_day", "unknown"),
            "lightsOn": lights_on,
            "musicPlaying": len(home_state.get("media_playing", [])) > 0,
            "context": {
                "inferred": analysis.context,
                "confidence": analysis.confidence,
                "signals": analysis.signals,
                "previous_context": analysis.previous_context,
                "duration_minutes": analysis.duration_minutes
            },
            "suggestions": analysis.suggestions,
            "should_speak": analysis.should_speak,
            "silence_reason": analysis.silence_reason,
            "hasSuggestions": len(analysis.suggestions) > 0
        }

        logger.info(
            "occupied_room_context_analyzed",
            room=room_name,
            context=analysis.context,
            confidence=analysis.confidence,
            should_speak=analysis.should_speak,
            suggestions_count=len(analysis.suggestions)
        )

        return result

    def _handle_basic(self, room_name: str) -> Dict:
        """
        Basic handler without context_service (fallback).

        Args:
            room_name: Room name

        Returns:
            Dict with basic time-based suggestions
        """
        hour = datetime.now().hour

        # Get current state
        from ..handlers.empty_room_handler import ROOM_LIGHTS_MAP
        lights_on = self.ha_service.get_room_lights(room_name, ROOM_LIGHTS_MAP)
        home_state = self.ha_service.get_home_state()

        suggestions = []

        # Check if lights should be on
        is_dark_time = hour < 7 or hour >= 18
        if is_dark_time and not lights_on and self.config.is_suggestion_enabled('lighting'):
            suggestions.append({
                "type": "lighting",
                "message": f"It's dark - want me to turn on the {room_name.replace('_', ' ')} lights?"
            })

        # Check if music is playing anywhere
        music_playing = len(home_state.get("media_playing", [])) > 0

        # Morning routine suggestions
        if 6 <= hour <= 9 and not music_playing and self.config.is_suggestion_enabled('music'):
            suggestions.append({
                "type": "music",
                "message": "Good morning! Want some background music?"
            })

        # Evening suggestions
        if 17 <= hour <= 20 and not music_playing and self.config.is_suggestion_enabled('music'):
            suggestions.append({
                "type": "music",
                "message": "Evening wind-down. Some chill music?"
            })

        # Determine time of day category
        if 6 <= hour < 10:
            time_of_day = "morning"
        elif 10 <= hour < 17:
            time_of_day = "daytime"
        elif 17 <= hour < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        result = {
            "room": room_name,
            "hour": hour,
            "timeOfDay": time_of_day,
            "lightsOn": lights_on,
            "musicPlaying": music_playing,
            "suggestions": suggestions,
            "hasSuggestions": len(suggestions) > 0,
            "should_speak": len(suggestions) > 0  # Basic: speak if have suggestions
        }

        logger.debug("occupied_room_suggestions_basic",
                    room=room_name,
                    suggestions_count=len(suggestions),
                    time_of_day=time_of_day)

        return result
