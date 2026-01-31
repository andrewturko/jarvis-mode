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

    def analyze_snapshot(self, snapshot_path: str) -> Dict[str, Any]:
        """
        Analyze snapshot using Claude vision API.

        Returns scene description, people count, identified needs (from the
        life-model needs taxonomy), and notable observations. The needs chain
        directly to capability types → devices.

        Args:
            snapshot_path: Path to snapshot image

        Returns:
            Dict with:
                - people_count (int|None): Number of people visible
                - person_detected (bool|None): True if people_count > 0
                - activity (str|None): Brief scene description
                - needs (list[str]): Identified needs from taxonomy
                - notable (str|None): Anything unusual
                - confidence (str): "high" if clean parse, "low" if fallback
        """
        # Resolve openclaw gateway URL and auth for API calls
        gateway_url = os.environ.get("OPENCLAW_GATEWAY_URL")
        gateway_password = os.environ.get("OPENCLAW_GATEWAY_PASSWORD")
        if not gateway_url:
            config_path = Path.home() / ".openclaw" / "openclaw.json"
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                    gw = config.get("gateway", {})
                    port = gw.get("port", 18789)
                    gateway_url = f"http://localhost:{port}"
                    if not gateway_password:
                        gateway_password = gw.get("auth", {}).get("password")
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("vision_openclaw_config_read_failed", error=str(e))

        if not gateway_url:
            logger.warning("vision_no_gateway", message="No openclaw gateway URL found")
            return {
                "people_count": None,
                "person_detected": None,
                "activity": None,
                "needs": [],
                "notable": None,
                "confidence": "unknown",
            }

        # Read and encode image
        try:
            with open(snapshot_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error("vision_read_image_failed", error=str(e))
            return {
                "people_count": None,
                "person_detected": None,
                "activity": None,
                "needs": [],
                "notable": None,
                "confidence": "unknown",
            }

        # Call Claude API with vision
        try:
            prompt_text = (
                'Analyze this home camera snapshot. Reply with ONLY a JSON object:\n'
                '{\n'
                '  "people_count": 0,\n'
                '  "activity": "brief description of what is happening",\n'
                '  "needs": ["comfort"],\n'
                '  "notable": "anything unusual, or null"\n'
                '}\n\n'
                'For "needs", pick relevant items from: comfort, entertainment, '
                'background_entertainment, cleanliness, focus, transition, security, '
                'efficiency, ambiance, quiet, hospitality.\n'
                'Keep activity under 10 words. Nothing outside the JSON.'
            )

            request_body = json.dumps({
                "model": "anthropic/claude-sonnet-4-20250514",
                "max_tokens": 250,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt_text
                        }
                    ]
                }]
            }).encode("utf-8")

            headers = {"Content-Type": "application/json"}
            if gateway_password:
                headers["Authorization"] = f"Bearer {gateway_password}"

            req = urllib.request.Request(
                f"{gateway_url}/v1/chat/completions",
                data=request_body,
                headers=headers,
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())

            # Parse response (OpenAI chat completions format)
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")

            try:
                # Handle potential markdown wrapping
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]

                parsed = json.loads(text.strip())
                people_count = parsed.get("people_count", 0)
                activity = parsed.get("activity", "unknown")
                needs = parsed.get("needs", [])
                notable = parsed.get("notable")

                logger.info("vision_analysis_complete",
                           people_count=people_count,
                           activity=activity,
                           needs=needs)

                return {
                    "people_count": people_count,
                    "person_detected": people_count > 0,
                    "activity": activity,
                    "needs": needs if isinstance(needs, list) else [],
                    "notable": notable,
                    "confidence": "high",
                }
            except json.JSONDecodeError:
                # Fallback: extract what we can from raw text
                text_lower = text.lower()
                person_detected = "person" in text_lower or "people" in text_lower or "someone" in text_lower

                return {
                    "people_count": 1 if person_detected else 0,
                    "person_detected": person_detected,
                    "activity": text[:80],
                    "needs": [],
                    "notable": None,
                    "confidence": "low",
                }

        except Exception as e:
            logger.error("vision_api_failed", error=str(e))
            return {
                "people_count": None,
                "person_detected": None,
                "activity": None,
                "needs": [],
                "notable": None,
                "confidence": "unknown",
            }
