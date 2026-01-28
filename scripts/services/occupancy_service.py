#!/usr/bin/env python3
"""
Occupancy detection service for Jarvis Mode.

Handles occupancy polling, transition detection, and cooldown logic.

IMPORTANT: Motion sensors only detect MOVEMENT, not PRESENCE.
- Motion "on" = definitely occupied (someone is moving)
- Motion "off" = unknown - could be empty OR someone sitting still

To determine if a room is truly empty, we use camera verification
before marking a room as empty.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

from core.logger import get_logger
from core.state_manager import StateManager
from core.config import JarvisConfig
from .ha_service import HAService

if TYPE_CHECKING:
    from .context_service import ContextService
    from .snapshot_service import SnapshotService

logger = get_logger("jarvis.occupancy")

# Default vacancy timeout in minutes - how long to wait after motion stops
# before attempting to verify vacancy via camera
DEFAULT_VACANCY_TIMEOUT_MINUTES = 5


class OccupancyService:
    """
    Service for detecting room occupancy and transitions.

    Features:
    - Polls motion sensors to detect occupancy changes
    - Tracks occupancy transitions (empty → occupied, occupied → empty)
    - Uses camera verification before marking rooms as empty
    - Cooldown logic to prevent excessive checking
    - Motion-aware scheduling
    - Context service integration for intelligent analysis (Phase 2)

    IMPORTANT: Motion sensors detect MOVEMENT, not PRESENCE.
    - Motion "on" = definitely occupied
    - Motion "off" = requires camera verification to confirm empty
    """

    def __init__(
        self,
        config: JarvisConfig,
        state_manager: StateManager,
        ha_service: HAService,
        context_service: Optional['ContextService'] = None,
        snapshot_service: Optional['SnapshotService'] = None,
        vacancy_timeout_minutes: int = DEFAULT_VACANCY_TIMEOUT_MINUTES
    ):
        """
        Initialize occupancy service.

        Args:
            config: Jarvis configuration
            state_manager: StateManager instance
            ha_service: HAService instance
            context_service: Optional ContextService for transition analysis
            snapshot_service: Optional SnapshotService for vacancy verification
            vacancy_timeout_minutes: Minutes after motion stops before verifying vacancy
        """
        self.config = config
        self.state_manager = state_manager
        self.ha_service = ha_service
        self.context_service = context_service
        self.snapshot_service = snapshot_service
        self.vacancy_timeout_minutes = vacancy_timeout_minutes

    def should_check_room(
        self,
        room_name: str,
        trigger: str = "scheduled"
    ) -> Dict[str, any]:
        """
        Determine if a room should be checked now.

        Considers:
        - Cooldown periods (different for motion vs scheduled)
        - Motion detection (if motion-aware enabled)
        - Manual overrides

        Args:
            room_name: Room to check
            trigger: "scheduled" (interval), "motion" (motion-triggered), or "manual"

        Returns:
            Dict with keys:
            - should_check (bool): Whether to check this room
            - reason (str): Why or why not
            - motion_state (bool|None): Current motion detection state
        """
        # Manual checks always proceed
        if trigger == "manual":
            motion_state = self._get_motion_state(room_name)
            return {
                "should_check": True,
                "reason": "manual override",
                "motion_state": motion_state
            }

        # Get cooldown based on trigger type
        if trigger == "motion":
            cooldown_minutes = self.config.motion_cooldown_minutes
        else:
            cooldown_minutes = self.config.cooldown_minutes

        # Check cooldown
        cooldown_ok, minutes_remaining = self._check_cooldown(room_name, cooldown_minutes)

        if not cooldown_ok:
            return {
                "should_check": False,
                "reason": f"cooldown ({minutes_remaining}m remaining)",
                "motion_state": None
            }

        # Check motion if motion-aware is enabled (for scheduled checks)
        motion_state = self._get_motion_state(room_name)

        if trigger == "scheduled" and self.config.motion_aware:
            if motion_state is False:  # Explicitly no motion (not None/unknown)
                return {
                    "should_check": False,
                    "reason": "no motion detected",
                    "motion_state": motion_state
                }

        return {
            "should_check": True,
            "reason": "ready",
            "motion_state": motion_state
        }

    def _check_cooldown(self, room_name: str, cooldown_minutes: int) -> tuple[bool, int]:
        """
        Check if room is in cooldown period.

        Args:
            room_name: Room to check
            cooldown_minutes: Cooldown period in minutes

        Returns:
            Tuple of (cooldown_ok, minutes_remaining)
        """
        room_state = self.state_manager.get_room_state(room_name)

        if not room_state:
            return True, 0

        # Check last check time (for vision API cooldown)
        last_check = room_state.get("last_check")  # Old schema compatibility

        if not last_check:
            return True, 0

        try:
            last_time = datetime.fromisoformat(last_check)
            elapsed = datetime.now() - last_time

            if elapsed >= timedelta(minutes=cooldown_minutes):
                return True, 0

            remaining = cooldown_minutes - int(elapsed.total_seconds() / 60)
            return False, remaining
        except ValueError:
            return True, 0

    def _get_motion_state(self, room_name: str) -> Optional[bool]:
        """
        Get current motion detection state for a room.

        Args:
            room_name: Room name

        Returns:
            True if motion detected, False if no motion, None if unknown/error
        """
        camera_config = self.config.cameras.get(room_name)

        if not camera_config or not camera_config.motion_sensor:
            return None

        return self.ha_service.is_motion_detected(camera_config.motion_sensor)

    def _update_last_motion(self, room_name: str):
        """
        Update the last_motion_at timestamp for a room.

        Called when motion is detected to track when activity was last seen.

        Args:
            room_name: Room name
        """
        self.state_manager.update_room(room_name, {
            "last_motion_at": datetime.now().isoformat()
        })

    def _get_last_motion(self, room_name: str) -> Optional[datetime]:
        """
        Get the last motion timestamp for a room.

        Args:
            room_name: Room name

        Returns:
            datetime of last motion, or None if never recorded
        """
        room_state = self.state_manager.get_room_state(room_name)
        if not room_state:
            return None

        last_motion = room_state.get("last_motion_at")
        if not last_motion:
            return None

        try:
            return datetime.fromisoformat(last_motion)
        except ValueError:
            return None

    def _minutes_since_motion(self, room_name: str) -> Optional[int]:
        """
        Get minutes since last motion was detected.

        Args:
            room_name: Room name

        Returns:
            Minutes since last motion, or None if unknown
        """
        last_motion = self._get_last_motion(room_name)
        if not last_motion:
            return None

        elapsed = datetime.now() - last_motion
        return int(elapsed.total_seconds() / 60)

    def should_verify_vacancy(self, room_name: str) -> Dict[str, any]:
        """
        Check if a room should have its vacancy verified via camera.

        Vacancy verification is needed when:
        - Room was previously occupied
        - Motion sensor is currently off
        - Enough time has passed since last motion (vacancy_timeout_minutes)

        Args:
            room_name: Room to check

        Returns:
            Dict with:
            - needs_verification (bool): Whether to verify vacancy
            - reason (str): Why or why not
            - minutes_since_motion (int|None): Minutes since last motion
        """
        room_state = self.state_manager.get_room_state(room_name)
        if not room_state:
            return {
                "needs_verification": False,
                "reason": "no room state",
                "minutes_since_motion": None
            }

        # Check current occupancy state
        occupancy = room_state.get("occupancy", {})
        is_occupied = occupancy.get("current")

        # If not currently marked as occupied, no need to verify
        if not is_occupied:
            return {
                "needs_verification": False,
                "reason": "already marked empty",
                "minutes_since_motion": self._minutes_since_motion(room_name)
            }

        # Check current motion state
        motion_detected = self._get_motion_state(room_name)

        # If motion is currently on, definitely still occupied
        if motion_detected:
            return {
                "needs_verification": False,
                "reason": "motion currently detected",
                "minutes_since_motion": 0
            }

        # Motion is off - check how long since last motion
        minutes_since = self._minutes_since_motion(room_name)

        if minutes_since is None:
            # No motion history - assume we need to verify
            return {
                "needs_verification": True,
                "reason": "no motion history, need to verify",
                "minutes_since_motion": None
            }

        if minutes_since < self.vacancy_timeout_minutes:
            return {
                "needs_verification": False,
                "reason": f"motion too recent ({minutes_since}m < {self.vacancy_timeout_minutes}m timeout)",
                "minutes_since_motion": minutes_since
            }

        # Motion has been off long enough - should verify vacancy
        return {
            "needs_verification": True,
            "reason": f"no motion for {minutes_since}m, verify if empty",
            "minutes_since_motion": minutes_since
        }

    def poll_occupancy(self) -> Dict[str, any]:
        """
        Poll all rooms for occupancy changes.

        Logic:
        - Motion ON → Room is definitely occupied (update immediately)
        - Motion OFF → Does NOT mean empty! Could be someone sitting still.
          - If room was occupied and motion is now off, keep as occupied
          - Mark for vacancy verification (handled separately to respect cooldowns)

        Returns:
            Dict with keys:
            - polled (bool): Whether poll succeeded
            - timestamp (str): ISO timestamp
            - current_occupancy (dict): Room → bool occupancy state
            - transitions (list): List of transition dicts
            - has_transitions (bool): Whether any transitions detected
            - needs_verification (list): Rooms that need vacancy verification
        """
        logger.debug("poll_occupancy_start")

        transitions = []
        current_occupancy = {}
        needs_verification = []

        enabled_cameras = self.config.get_enabled_cameras()

        for room_name, camera_config in enabled_cameras.items():
            # Get current motion sensor state
            motion_detected = self._get_motion_state(room_name)

            # Get last known occupancy state
            room_state = self.state_manager.get_room_state(room_name)
            last_occupancy = None

            if room_state:
                occupancy_data = room_state.get("occupancy", {})
                last_occupancy = occupancy_data.get("current")

            # LOGIC: Motion sensors detect MOVEMENT, not PRESENCE
            #
            # - Motion ON → definitely occupied (someone is moving)
            # - Motion OFF → unknown (could be empty OR someone sitting still)
            #
            # To mark a room as EMPTY, we need camera verification

            if motion_detected:
                # Motion detected - room is definitely occupied
                self._update_last_motion(room_name)
                current_occupancy[room_name] = True

                if not last_occupancy:
                    # Transition: empty → occupied
                    transition = {
                        "room": room_name,
                        "transition": "occupied",
                        "previous": "empty",
                        "current": "occupied",
                        "timestamp": datetime.now().isoformat()
                    }
                    transitions.append(transition)
                    logger.info("room_occupied", room=room_name)

                # Update state to occupied
                self.state_manager.update_occupancy(room_name, True)

                # Notify context service
                if self.context_service and not last_occupancy:
                    self.context_service.on_transition(room_name, False, True)

            else:
                # Motion not detected - but this doesn't mean empty!
                # Keep current occupancy state, flag for verification if needed

                if last_occupancy:
                    # Room was occupied, motion is now off
                    # DON'T mark as empty - flag for verification instead
                    verification_check = self.should_verify_vacancy(room_name)
                    if verification_check["needs_verification"]:
                        needs_verification.append({
                            "room": room_name,
                            "reason": verification_check["reason"],
                            "minutes_since_motion": verification_check["minutes_since_motion"]
                        })

                    # Keep as occupied until verified empty
                    current_occupancy[room_name] = True

                elif last_occupancy is None:
                    # No previous state - default to unoccupied but flag for verification
                    current_occupancy[room_name] = False
                    self.state_manager.update_occupancy(room_name, False)

                else:
                    # Was empty, still empty
                    current_occupancy[room_name] = False

        # Update last poll time
        state = self.state_manager.read_state()
        state["last_poll"] = datetime.now().isoformat()
        self.state_manager.write_state(state)

        result = {
            "polled": True,
            "timestamp": datetime.now().isoformat(),
            "current_occupancy": current_occupancy,
            "transitions": transitions,
            "has_transitions": len(transitions) > 0,
            "needs_verification": needs_verification
        }

        logger.debug("poll_occupancy_complete",
                    transitions=len(transitions),
                    rooms_checked=len(current_occupancy),
                    needs_verification=len(needs_verification))

        return result

    def verify_vacancy(
        self,
        room_name: str,
        vision_result: Dict[str, any],
        manual: bool = False
    ) -> Dict[str, any]:
        """
        Process vacancy verification result from vision analysis.

        Called after camera snapshot has been analyzed to determine if room is empty.
        Cooldown is respected at the snapshot capture level, not here.

        Args:
            room_name: Room that was checked
            vision_result: Result from vision analysis, should contain:
                - person_detected (bool): Whether a person was seen
                - confidence (float): Confidence of detection
                - summary (str): Description of what was seen
            manual: If True, this was a user-requested check (cooldown bypassed at snapshot level)

        Returns:
            Dict with:
            - verified (bool): Whether verification was successful
            - is_empty (bool): Whether room is confirmed empty
            - transition (dict|None): Transition if occupancy changed
        """
        person_detected = vision_result.get("person_detected", False)
        confidence = vision_result.get("confidence", 0.0)

        room_state = self.state_manager.get_room_state(room_name)
        last_occupancy = room_state.get("occupancy", {}).get("current") if room_state else None

        result = {
            "verified": True,
            "is_empty": not person_detected,
            "transition": None
        }

        if person_detected:
            # Person seen in camera - room is occupied
            # Update last_motion_at since we have visual confirmation
            self._update_last_motion(room_name)

            if not last_occupancy:
                # Was marked empty but camera shows occupied
                self.state_manager.update_occupancy(room_name, True)
                result["transition"] = {
                    "room": room_name,
                    "transition": "occupied",
                    "previous": "empty",
                    "current": "occupied",
                    "source": "camera_verification",
                    "timestamp": datetime.now().isoformat()
                }
                logger.info("vacancy_verification_found_person", room=room_name)
        else:
            # No person seen - room is confirmed empty
            if last_occupancy:
                # Transition: occupied → empty (confirmed by camera)
                self.state_manager.update_occupancy(room_name, False)
                result["transition"] = {
                    "room": room_name,
                    "transition": "emptied",
                    "previous": "occupied",
                    "current": "empty",
                    "source": "camera_verification",
                    "timestamp": datetime.now().isoformat()
                }
                logger.info("vacancy_verified_empty", room=room_name)

                # Notify context service
                if self.context_service:
                    self.context_service.on_transition(room_name, True, False)

        return result
