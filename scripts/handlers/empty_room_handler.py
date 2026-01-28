#!/usr/bin/env python3
"""
Handler for empty room events.

Suggests or executes actions when a room becomes empty (lights off, etc.)
"""

from typing import Dict, List

from core.logger import get_logger
from core.config import JarvisConfig
from services.ha_service import HAService
from services.snapshot_service import SnapshotService

logger = get_logger("jarvis.empty_room_handler")

# Room-to-lights mapping (should eventually move to config)
ROOM_LIGHTS_MAP = {
    "kitchen": [
        "light.adaptive_phase_dimmer_counter",
    ],
    "living_room": [
        "light.lutron_leap_dimmer_floor_lamp",
        "light.philips_hue_light_os_3_3_0_floor_lamp",
        "light.lutron_leap_dimmer_table_lamp",
        "light.philips_hue_light_os_3_3_0_wall_wash",
    ],
    "dining": [
        "light.philips_hue_light_os_3_3_0_chandelier",
        "light.adaptive_phase_dimmer_entry",
    ],
}


class EmptyRoomHandler:
    """
    Handler for empty room events.

    Features:
    - Checks if lights are on in empty room
    - Suggests turning off lights (or turns off if auto-actions enabled)
    - Optional verification with vision snapshot
    """

    def __init__(
        self,
        config: JarvisConfig,
        ha_service: HAService,
        snapshot_service: SnapshotService
    ):
        """
        Initialize empty room handler.

        Args:
            config: Jarvis configuration
            ha_service: HAService instance
            snapshot_service: SnapshotService instance
        """
        self.config = config
        self.ha_service = ha_service
        self.snapshot_service = snapshot_service

    def handle(
        self,
        room_name: str,
        dry_run: bool = False,
        skip_verify: bool = False
    ) -> Dict:
        """
        Handle empty room event.

        Args:
            room_name: Room that became empty
            dry_run: If True, don't actually turn off lights
            skip_verify: If True, skip vision verification

        Returns:
            Dict with action, lights, and suggestion
        """
        logger.info("handle_empty_room", room=room_name, dry_run=dry_run)

        # Check which lights are on
        lights_on = self.ha_service.get_room_lights(room_name, ROOM_LIGHTS_MAP)

        if not lights_on:
            logger.debug("empty_room_no_lights", room=room_name)
            return {
                "room": room_name,
                "action": "none",
                "lights": [],
                "suggestion": None
            }

        # Optional: verify room is actually empty with vision
        if not skip_verify and self.config.cameras.get(room_name):
            camera_config = self.config.cameras[room_name]
            snapshot_path = self.snapshot_service.get_snapshot(
                room_name,
                camera_config.entity_id,
                manual=True  # Bypass cooldown for verification
            )

            if snapshot_path:
                logger.info("empty_room_verification_snapshot",
                          room=room_name,
                          path=snapshot_path)
                # Note: Actual vision analysis would happen in agent
                # This just captures the snapshot for verification

        # Check if auto-actions are enabled
        if self.config.auto_actions.enabled and not dry_run:
            # Turn off lights automatically
            results = self.ha_service.turn_off_lights(lights_on)
            success_count = sum(1 for success in results.values() if success)

            logger.info("auto_turn_off_lights",
                       room=room_name,
                       total=len(lights_on),
                       success=success_count)

            # Optionally announce action
            announce = self.config.auto_actions.announce_actions

            return {
                "room": room_name,
                "action": "turned_off",
                "lights": lights_on,
                "success_count": success_count,
                "announce": announce,
                "suggestion": f"Turned off {success_count} light(s) in empty {room_name.replace('_', ' ')}"
            }
        else:
            # Just suggest
            return {
                "room": room_name,
                "action": "suggest",
                "lights": lights_on,
                "suggestion": f"Lights are on in empty {room_name.replace('_', ' ')}: {', '.join(lights_on)}"
            }
