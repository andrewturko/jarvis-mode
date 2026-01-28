#!/usr/bin/env python3
"""
Camera snapshot service for Jarvis Mode.

Handles camera snapshot capture from Home Assistant with cooldown tracking.
Now includes vision analysis for person detection.
"""

import base64
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import urllib.request

from core.logger import get_logger
from core.state_manager import StateManager

logger = get_logger("jarvis.snapshot")


class SnapshotService:
    """
    Service for capturing camera snapshots from Home Assistant.

    Features:
    - Cooldown tracking to prevent API spam
    - Automatic snapshot directory management
    - Manual override for user-requested snapshots
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        snapshot_dir: Path,
        state_manager: StateManager
    ):
        """
        Initialize snapshot service.

        Args:
            ha_url: Home Assistant URL
            ha_token: HA long-lived access token
            snapshot_dir: Directory to store snapshots
            state_manager: StateManager instance for cooldown tracking
        """
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.snapshot_dir = Path(snapshot_dir)
        self.state_manager = state_manager

        # Ensure snapshot directory exists
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def get_snapshot(
        self,
        room_name: str,
        camera_entity_id: str,
        cooldown_minutes: int = 30,
        manual: bool = False
    ) -> Optional[str]:
        """
        Get camera snapshot from Home Assistant.

        Args:
            room_name: Room name (for cooldown tracking)
            camera_entity_id: Camera entity ID in HA
            cooldown_minutes: Cooldown period in minutes
            manual: If True, bypass cooldown (user-requested)

        Returns:
            Path to snapshot file as string, or None if failed/blocked
        """
        if not self.ha_token:
            logger.error("get_snapshot_no_token", room=room_name)
            return None

        if not camera_entity_id:
            logger.error("get_snapshot_no_entity", room=room_name)
            return None

        # Check cooldown for automated requests
        if not manual:
            cooldown_ok, remaining = self._check_cooldown(room_name, cooldown_minutes)

            if not cooldown_ok:
                logger.info("snapshot_blocked_cooldown",
                          room=room_name,
                          remaining_minutes=remaining)
                return None

        # Generate snapshot filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        snapshot_path = self.snapshot_dir / f"{room_name}_{timestamp}.jpg"

        # Capture snapshot via HA API
        try:
            start_time = datetime.now()

            result = subprocess.run([
                "curl", "-s", "-o", str(snapshot_path),
                f"{self.ha_url}/api/camera_proxy/{camera_entity_id}",
                "-H", f"Authorization: Bearer {self.ha_token}"
            ], capture_output=True, timeout=30)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Verify snapshot was captured successfully
            if not snapshot_path.exists():
                logger.error("snapshot_file_not_created",
                           room=room_name,
                           path=str(snapshot_path))
                return None

            file_size = snapshot_path.stat().st_size

            # Check minimum file size (corrupted/error responses are small)
            if file_size < 1000:
                logger.error("snapshot_file_too_small",
                           room=room_name,
                           size_bytes=file_size)
                snapshot_path.unlink(missing_ok=True)
                return None

            # Success - record snapshot time for cooldown
            self._record_snapshot_time(room_name)

            logger.info("snapshot_captured",
                       room=room_name,
                       path=str(snapshot_path),
                       size_bytes=file_size,
                       duration_ms=duration_ms,
                       manual=manual)

            return str(snapshot_path)

        except subprocess.TimeoutExpired:
            logger.error("snapshot_timeout",
                        room=room_name,
                        timeout=30)
            snapshot_path.unlink(missing_ok=True)
            return None
        except Exception as e:
            logger.error("snapshot_error",
                        room=room_name,
                        error=str(e),
                        exc_info=True)
            snapshot_path.unlink(missing_ok=True)
            return None

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

        last_snapshot = room_state.get("last_snapshot")

        if not last_snapshot:
            return True, 0

        try:
            last_time = datetime.fromisoformat(last_snapshot)
            elapsed = datetime.now() - last_time

            if elapsed >= timedelta(minutes=cooldown_minutes):
                return True, 0

            remaining = cooldown_minutes - int(elapsed.total_seconds() / 60)
            return False, remaining
        except ValueError:
            # Invalid timestamp format
            logger.warning("invalid_snapshot_timestamp",
                         room=room_name,
                         timestamp=last_snapshot)
            return True, 0

    def _record_snapshot_time(self, room_name: str):
        """
        Record snapshot time for cooldown tracking.

        Args:
            room_name: Room name
        """
        try:
            self.state_manager.update_room(room_name, {
                "last_snapshot": datetime.now().isoformat()
            })
        except Exception as e:
            logger.error("record_snapshot_time_failed",
                        room=room_name,
                        error=str(e))

    def cleanup_old_snapshots(self, days: int = 1):
        """
        Delete snapshots older than specified days.

        Args:
            days: Delete snapshots older than this many days
        """
        try:
            cutoff = datetime.now() - timedelta(days=days)
            deleted_count = 0

            for snapshot_file in self.snapshot_dir.glob("*.jpg"):
                # Check file modification time
                mtime = datetime.fromtimestamp(snapshot_file.stat().st_mtime)

                if mtime < cutoff:
                    snapshot_file.unlink()
                    deleted_count += 1

            if deleted_count > 0:
                logger.info("snapshots_cleaned_up",
                          deleted=deleted_count,
                          days=days)
        except Exception as e:
            logger.error("cleanup_snapshots_error", error=str(e), exc_info=True)

    def analyze_for_person(self, snapshot_path: str) -> Dict[str, Any]:
        """
        Analyze snapshot for person presence using Claude vision API.

        This is the source of truth for occupancy — motion sensors only detect
        movement, not presence. Someone sitting still is still there.

        Args:
            snapshot_path: Path to snapshot image

        Returns:
            Dict with:
                - person_detected (bool): True if person visible
                - confidence (str): high/medium/low
                - description (str): Brief description of what's seen
        """
        # Get API key from environment or clawdbot config
        api_key = os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            config_path = Path.home() / ".clawdbot" / "clawdbot.json"
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                    # Try to get from auth profiles
                    profiles = config.get("auth", {}).get("profiles", {})
                    for profile in profiles.values():
                        if profile.get("provider") == "anthropic" and profile.get("apiKey"):
                            api_key = profile["apiKey"]
                            break
                except Exception:
                    pass

        if not api_key:
            # Try .anthropic file
            anthropic_file = Path.home() / ".anthropic"
            if anthropic_file.exists():
                try:
                    api_key = anthropic_file.read_text().strip()
                except Exception:
                    pass

        if not api_key:
            logger.warning("vision_analysis_no_api_key")
            return {
                "person_detected": None,
                "confidence": "unknown",
                "description": "No API key available for vision analysis"
            }

        # Read and encode image
        try:
            with open(snapshot_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error("vision_read_image_failed", error=str(e))
            return {
                "person_detected": None,
                "confidence": "unknown",
                "description": f"Failed to read image: {e}"
            }

        # Call Claude API with vision
        try:
            request_body = json.dumps({
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": "Is there a person in this image? Reply with ONLY a JSON object: {\"person\": true/false, \"confidence\": \"high\"/\"medium\"/\"low\", \"brief\": \"2-3 word description\"}. Nothing else."
                        }
                    ]
                }]
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=request_body,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01"
                }
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())

            # Parse response
            text = result.get("content", [{}])[0].get("text", "")

            # Extract JSON from response
            try:
                # Handle potential markdown wrapping
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]

                parsed = json.loads(text.strip())
                person_detected = parsed.get("person", False)
                confidence = parsed.get("confidence", "medium")
                description = parsed.get("brief", "analyzed")

                logger.info("vision_analysis_complete",
                           person_detected=person_detected,
                           confidence=confidence)

                return {
                    "person_detected": person_detected,
                    "confidence": confidence,
                    "description": description
                }
            except json.JSONDecodeError:
                # Fallback: look for keywords
                text_lower = text.lower()
                person_detected = "yes" in text_lower or "person" in text_lower or "true" in text_lower

                return {
                    "person_detected": person_detected,
                    "confidence": "low",
                    "description": text[:50]
                }

        except Exception as e:
            logger.error("vision_api_failed", error=str(e))
            return {
                "person_detected": None,
                "confidence": "unknown",
                "description": f"Vision API error: {e}"
            }
