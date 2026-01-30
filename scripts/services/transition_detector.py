#!/usr/bin/env python3
"""
Transition Detector - Detects home arrival, room transitions, and pass-throughs.

Distinguishes between:
- Home arrival: no motion in ANY room for 30+ min, then motion (user was away)
- Settling period: first 5 min after home arrival (suppress activity-specific suggestions)
- Pass-through: room occupied < 3 min (don't infer activity context)
- Room arrival: entered a new room (normal confidence rules apply)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_manager import StateManager


@dataclass
class TransitionState:
    """Result of transition detection."""
    is_home_arrival: bool = False       # away -> home transition
    is_room_arrival: bool = False       # entered a new room
    is_pass_through: bool = False       # brief room visit (< 3 min), not activity
    settling_period_active: bool = False # first 5 min after home arrival
    minutes_since_any_motion: float = 0 # across ALL rooms
    previous_home_status: str = "unknown"  # "home", "away", "settling", "unknown"
    arrival_room: Optional[str] = None


class TransitionDetector:
    """
    Detects home-level and room-level transitions from motion state.

    Uses state.json home_state to track home-level status and compares
    all rooms' last_motion_at timestamps.
    """

    HOME_AWAY_THRESHOLD_MIN = 30   # no motion anywhere = away
    SETTLING_PERIOD_MIN = 5        # grace period after home arrival
    PASS_THROUGH_MIN = 3           # brief visit, not activity

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager

    def detect(self, room: str, motion_detected: bool) -> TransitionState:
        """
        Analyze current motion event against history to detect transitions.

        Args:
            room: Room where motion was detected (or checked)
            motion_detected: Whether motion sensor is currently active

        Returns:
            TransitionState with all transition flags
        """
        now = datetime.now()
        state = self.state_manager.read_state()
        all_rooms = state.get('rooms', {})
        home_state = state.get('home_state', {})

        result = TransitionState()
        result.previous_home_status = home_state.get('status', 'unknown')

        # Calculate time since most recent motion in ANY room
        most_recent_motion = self._get_most_recent_motion(all_rooms)
        if most_recent_motion:
            result.minutes_since_any_motion = (now - most_recent_motion).total_seconds() / 60
        else:
            result.minutes_since_any_motion = float('inf')

        if not motion_detected:
            return result  # No motion = no transitions to detect

        # Check if settling period is still active from a previous home arrival
        settling_until_str = home_state.get('settling_until')
        if settling_until_str:
            try:
                settling_until = datetime.fromisoformat(settling_until_str)
                if now < settling_until:
                    result.settling_period_active = True
            except (ValueError, TypeError):
                pass

        # Detect home arrival: was anyone away for 30+ min?
        if result.minutes_since_any_motion >= self.HOME_AWAY_THRESHOLD_MIN:
            result.is_home_arrival = True
            result.arrival_room = room
            result.settling_period_active = True
            # Write settling state
            self._update_home_state(state, {
                'status': 'settling',
                'last_arrival_at': now.isoformat(),
                'settling_until': (now + timedelta(minutes=self.SETTLING_PERIOD_MIN)).isoformat(),
                'last_any_motion_at': now.isoformat()
            })
        elif result.previous_home_status == 'unknown':
            # First run or unknown state, assume home
            self._update_home_state(state, {
                'status': 'home',
                'last_any_motion_at': now.isoformat()
            })
        else:
            # Normal motion, update last motion timestamp
            self._update_home_state(state, {
                'status': 'home',
                'last_any_motion_at': now.isoformat()
            })
            # Clear settling if period expired
            if result.previous_home_status == 'settling' and not result.settling_period_active:
                self._update_home_state(state, {'status': 'home', 'settling_until': None})

        # Detect room arrival (this room specifically)
        room_state = all_rooms.get(room, {})
        last_room_motion = room_state.get('last_motion_at')
        if last_room_motion:
            try:
                minutes_in_room = (now - datetime.fromisoformat(last_room_motion)).total_seconds() / 60
                result.is_room_arrival = minutes_in_room >= 10
            except (ValueError, TypeError):
                result.is_room_arrival = True
        else:
            result.is_room_arrival = True

        if not room_state.get('occupancy', {}).get('current', False):
            result.is_room_arrival = True

        # Detect pass-through: room occupied for < 3 min
        # (only meaningful if room was recently entered)
        occupancy_changed = room_state.get('occupancy', {}).get('changed_at')
        if occupancy_changed and result.is_room_arrival:
            try:
                changed_dt = datetime.fromisoformat(occupancy_changed)
                minutes_occupied = (now - changed_dt).total_seconds() / 60
                if minutes_occupied < self.PASS_THROUGH_MIN:
                    result.is_pass_through = True
            except (ValueError, TypeError):
                pass

        return result

    def get_home_status(self) -> str:
        """Get current home status: 'home', 'away', 'settling', or 'unknown'."""
        state = self.state_manager.read_state()
        home_state = state.get('home_state', {})

        # Check if settling period expired
        status = home_state.get('status', 'unknown')
        if status == 'settling':
            settling_until = home_state.get('settling_until')
            if settling_until:
                try:
                    if datetime.now() >= datetime.fromisoformat(settling_until):
                        return 'home'  # Settling expired
                except (ValueError, TypeError):
                    pass
        return status

    def _get_most_recent_motion(self, all_rooms: Dict[str, Any]) -> Optional[datetime]:
        """Find the most recent motion timestamp across all rooms."""
        most_recent = None
        for room_state in all_rooms.values():
            motion_str = room_state.get('last_motion_at')
            if motion_str:
                try:
                    dt = datetime.fromisoformat(motion_str)
                    if most_recent is None or dt > most_recent:
                        most_recent = dt
                except (ValueError, TypeError):
                    pass
        return most_recent

    def _update_home_state(self, state: Dict[str, Any], updates: Dict[str, Any]):
        """Update home_state in state.json."""
        if 'home_state' not in state:
            state['home_state'] = {}
        state['home_state'].update(updates)
        self.state_manager.write_state(state)
