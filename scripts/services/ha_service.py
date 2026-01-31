#!/usr/bin/env python3
"""
Home Assistant service for Jarvis Mode.

Centralizes all Home Assistant API interactions with proper error handling,
retry logic, and caching.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import time

from core.logger import get_logger
from core.resilience import retry, get_circuit_breaker, graceful_degradation

logger = get_logger("jarvis.ha_service")


class HACache:
    """
    Simple time-based cache for HA API responses.

    Reduces API calls by caching responses with a TTL.
    """

    def __init__(self, ttl_seconds: int = 30):
        """
        Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cache entries in seconds
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[float, any]] = {}

    def get(self, key: str) -> Optional[any]:
        """
        Get cached value if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        if key not in self._cache:
            return None

        timestamp, value = self._cache[key]
        age = time.time() - timestamp

        if age > self.ttl_seconds:
            # Expired
            del self._cache[key]
            logger.debug("cache_miss", key=key, reason="expired", age_seconds=age)
            return None

        logger.debug("cache_hit", key=key, age_seconds=age)
        return value

    def set(self, key: str, value: any):
        """
        Store value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        self._cache[key] = (time.time(), value)
        logger.debug("cache_set", key=key)

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()
        logger.debug("cache_cleared")

    def clear_key(self, key: str):
        """Clear specific cache entry."""
        if key in self._cache:
            del self._cache[key]
            logger.debug("cache_key_cleared", key=key)


class HAService:
    """
    Service for interacting with Home Assistant API.

    Features:
    - Environment variable and config-based HA credentials
    - Proper error handling with logging
    - Entity state queries with caching (30s TTL)
    - Light control
    - Motion sensor reading
    - Circuit breaker for resilience
    - Retry logic with exponential backoff
    """

    def __init__(
        self,
        ha_url: Optional[str] = None,
        ha_token: Optional[str] = None,
        cache_ttl_seconds: int = 30
    ):
        """
        Initialize HA service.

        Args:
            ha_url: Home Assistant URL (defaults to env/config)
            ha_token: Home Assistant long-lived token (defaults to env/config)
            cache_ttl_seconds: Cache TTL in seconds (default: 30)
        """
        self.ha_url, self.ha_token = self._get_ha_config(ha_url, ha_token)
        self.cache = HACache(ttl_seconds=cache_ttl_seconds)
        self.circuit_breaker = get_circuit_breaker("ha_service", failure_threshold=5, timeout_seconds=60)
        self._last_known_status = "unknown"
        self._down_since = None
        self._notification_sent = False

        if not self.ha_token:
            logger.warning("ha_service_init", message="No HA token found - HA operations will fail")

    def _get_ha_config(self, url: Optional[str] = None, token: Optional[str] = None) -> Tuple[str, str]:
        """
        Get HA configuration from environment or openclaw config.

        Priority:
        1. Explicitly provided parameters
        2. Environment variables (HA_URL, HA_TOKEN)
        3. OpenClaw config file (~/.openclaw/openclaw.json)
        4. Defaults

        Returns:
            Tuple of (url, token)
        """
        # Use provided or environment
        url = url or os.environ.get("HA_URL")
        token = token or os.environ.get("HA_TOKEN")

        # Try openclaw config as fallback
        if not url or not token:
            try:
                config_path = Path.home() / ".openclaw" / "openclaw.json"
                with open(config_path) as f:
                    config = json.load(f)
                    env_vars = config.get("env", {}).get("vars", {})
                    url = url or env_vars.get("HA_URL")
                    token = token or env_vars.get("HA_TOKEN")
            except Exception as e:
                logger.debug("openclaw_config_read_failed", error=str(e))

        # Defaults
        url = url or "http://homeassistant.local:8123"
        token = token or ""

        return url, token

    def get_entity_state(self, entity_id: str, timeout: int = 10) -> Optional[Dict]:
        """
        Get state of a single entity from HA.

        Args:
            entity_id: Entity ID to query (e.g., "light.kitchen")
            timeout: Request timeout in seconds

        Returns:
            Entity state dict or None if error
        """
        if not self.ha_token:
            logger.error("get_entity_state_no_token", entity_id=entity_id)
            return None

        try:
            result = subprocess.run([
                "curl", "-s",
                f"{self.ha_url}/api/states/{entity_id}",
                "-H", f"Authorization: Bearer {self.ha_token}"
            ], capture_output=True, text=True, timeout=timeout)

            if result.returncode != 0:
                logger.error("get_entity_state_curl_failed",
                           entity_id=entity_id,
                           returncode=result.returncode,
                           stderr=result.stderr[:200])
                return None

            data = json.loads(result.stdout)

            # Check if entity exists
            if "error" in data or not data.get("entity_id"):
                logger.warning("get_entity_state_not_found", entity_id=entity_id)
                return None

            return data
        except json.JSONDecodeError as e:
            logger.error("get_entity_state_json_error",
                       entity_id=entity_id,
                       error=str(e),
                       response=result.stdout[:200])
            return None
        except subprocess.TimeoutExpired:
            logger.error("get_entity_state_timeout", entity_id=entity_id, timeout=timeout)
            return None
        except Exception as e:
            logger.error("get_entity_state_error", entity_id=entity_id, error=str(e), exc_info=True)
            return None

    def get_all_states(self, timeout: int = 30, use_cache: bool = True) -> List[Dict]:
        """
        Get all entity states from HA with caching.

        Args:
            timeout: Request timeout in seconds
            use_cache: Whether to use cached results (default: True)

        Returns:
            List of entity state dicts (empty list on error)
        """
        cache_key = "all_states"

        # Check cache first
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        if not self.ha_token:
            logger.error("get_all_states_no_token")
            return []

        @retry(max_attempts=3, delay_seconds=1.0, exceptions=(subprocess.TimeoutExpired,))
        def _fetch_states():
            result = subprocess.run([
                "curl", "-s",
                f"{self.ha_url}/api/states",
                "-H", f"Authorization: Bearer {self.ha_token}"
            ], capture_output=True, text=True, timeout=timeout)

            if result.returncode != 0:
                raise Exception(f"curl failed: {result.stderr[:200]}")

            return result.stdout

        try:
            stdout = _fetch_states()
            states = json.loads(stdout)

            if not isinstance(states, list):
                logger.error("get_all_states_unexpected_format",
                           response_type=type(states).__name__)
                return []

            # Cache successful response
            self.cache.set(cache_key, states)

            return states
        except json.JSONDecodeError as e:
            logger.error("get_all_states_json_error", error=str(e))
            return []
        except Exception as e:
            logger.error("get_all_states_error", error=str(e), exc_info=True)
            return []

    def is_motion_detected(self, motion_sensor: str) -> Optional[bool]:
        """
        Check if motion is detected via binary sensor.

        Args:
            motion_sensor: Motion sensor entity ID (e.g., "binary_sensor.kitchen_motion")

        Returns:
            True if motion detected, False if no motion, None if error/unknown
        """
        state_data = self.get_entity_state(motion_sensor)

        if not state_data:
            return None

        state = state_data.get("state", "").lower()

        if state == "on":
            logger.debug("motion_detected", sensor=motion_sensor)
            return True
        elif state == "off":
            return False
        else:
            logger.warning("motion_state_unknown", sensor=motion_sensor, state=state)
            return None

    @graceful_degradation(fallback_value={
        "lights_on": [],
        "lights_off": [],
        "media_playing": [],
        "climate": {},
        "covers_open": [],
        "covers_closed": [],
    })
    def get_home_state(self, use_cache: bool = True) -> Dict:
        """
        Get aggregated home state (lights, media, climate, covers) with caching.

        Args:
            use_cache: Whether to use cached state (default: True)

        Returns:
            Dict with lights_on, lights_off, media_playing, climate, covers_open, covers_closed
        """
        states = self.get_all_states(use_cache=use_cache)

        home_state = {
            "lights_on": [],
            "lights_off": [],
            "media_playing": [],
            "climate": {},
            "covers_open": [],
            "covers_closed": [],
        }

        for entity in states:
            eid = entity.get("entity_id", "")
            state = entity.get("state", "")

            if eid.startswith("light."):
                if state == "on":
                    attrs = entity.get("attributes", {})
                    home_state["lights_on"].append({
                        "entity_id": eid,
                        "brightness": attrs.get("brightness"),       # 0-255
                        "brightness_pct": round(attrs["brightness"] / 255 * 100) if attrs.get("brightness") is not None else None,
                        "color_temp": attrs.get("color_temp"),
                    })
                else:
                    home_state["lights_off"].append(eid)
            elif eid.startswith("media_player."):
                # Exclude Cast/TV entities — they hold stale "playing"
                # state from old sessions.
                _EXCLUDE_MEDIA = {
                    "media_player.master_bedroom_tv",
                    "media_player.living",
                    "media_player.global",
                    "media_player.primary_bedroom",
                    "media_player.guest_bedroom",
                }
                if state == "playing" and eid not in _EXCLUDE_MEDIA:
                    attrs = entity.get("attributes", {})
                    # Require actual media metadata — stale entities
                    # report "playing" but have no title or content id.
                    if attrs.get("media_title") or attrs.get("media_content_id"):
                        home_state["media_playing"].append(eid)
            elif eid.startswith("climate."):
                home_state["climate"][eid] = {
                    "state": state,
                    **entity.get("attributes", {})
                }
            elif eid.startswith("cover."):
                if state in ("open", "opening"):
                    home_state["covers_open"].append(eid)
                else:
                    home_state["covers_closed"].append(eid)

        logger.debug("get_home_state",
                    lights_on=len(home_state["lights_on"]),
                    lights_off=len(home_state["lights_off"]),
                    media_playing=len(home_state["media_playing"]),
                    covers_open=len(home_state["covers_open"]))

        return home_state

    @graceful_degradation(fallback_value=[])
    def get_room_lights(
        self,
        room_name: str,
        room_lights_map: Dict[str, List[str]],
        use_cache: bool = True
    ) -> List[str]:
        """
        Get lights that are currently on in a specific room with caching.

        Args:
            room_name: Room name (e.g., "kitchen")
            room_lights_map: Mapping of room names to light entity IDs
            use_cache: Whether to use cached state (default: True)

        Returns:
            List of light entity IDs that are currently on
        """
        explicit_lights = room_lights_map.get(room_name, [])

        if explicit_lights:
            # Use explicit light list for this room
            patterns = None
        else:
            # Fall back to pattern matching by room name
            patterns = [room_name.replace("_", "")]

        states = self.get_all_states(use_cache=use_cache)
        lights_on = []

        for entity in states:
            eid = entity.get("entity_id", "")
            state = entity.get("state", "")

            if not eid.startswith("light."):
                continue

            if state != "on":
                continue

            # Check if light matches this room
            if explicit_lights:
                if eid in explicit_lights:
                    lights_on.append(eid)
            elif patterns:
                # Pattern matching: check if room name appears in entity ID
                if any(pattern in eid for pattern in patterns):
                    lights_on.append(eid)

        logger.debug("get_room_lights", room=room_name, count=len(lights_on))
        return lights_on

    def turn_off_lights(self, entity_ids: List[str]) -> Dict[str, bool]:
        """
        Turn off specified lights via HA service call.

        Args:
            entity_ids: List of light entity IDs to turn off

        Returns:
            Dict mapping entity_id to success (True/False)
        """
        if not self.ha_token:
            logger.error("turn_off_lights_no_token")
            return {eid: False for eid in entity_ids}

        if not entity_ids:
            logger.debug("turn_off_lights_no_entities")
            return {}

        results = {}

        for entity_id in entity_ids:
            try:
                payload = json.dumps({"entity_id": entity_id})

                result = subprocess.run([
                    "curl", "-s", "-X", "POST",
                    f"{self.ha_url}/api/services/light/turn_off",
                    "-H", f"Authorization: Bearer {self.ha_token}",
                    "-H", "Content-Type: application/json",
                    "-d", payload
                ], capture_output=True, text=True, timeout=10)

                success = result.returncode == 0
                results[entity_id] = success

                if success:
                    logger.info("light_turned_off", entity_id=entity_id)
                    # Invalidate cache after state change
                    self.cache.clear_key("all_states")
                else:
                    logger.error("light_turn_off_failed",
                               entity_id=entity_id,
                               returncode=result.returncode,
                               stderr=result.stderr[:200])
            except subprocess.TimeoutExpired:
                logger.error("light_turn_off_timeout", entity_id=entity_id)
                results[entity_id] = False
            except Exception as e:
                logger.error("light_turn_off_error", entity_id=entity_id, error=str(e), exc_info=True)
                results[entity_id] = False

        return results

    def check_health(self) -> Dict[str, any]:
        """
        Check if Home Assistant is reachable and responding.

        Returns:
            Dict with status, latency_ms, and error (if any)
        """
        if not self.ha_token:
            return {
                "status": "down",
                "error": "No HA token configured"
            }

        try:
            start_time = time.time()

            result = subprocess.run([
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                f"{self.ha_url}/api/",
                "-H", f"Authorization: Bearer {self.ha_token}"
            ], capture_output=True, text=True, timeout=5)

            latency_ms = int((time.time() - start_time) * 1000)

            if result.returncode == 0 and result.stdout.strip() in ["200", "201"]:
                logger.debug("ha_health_check", status="up", latency_ms=latency_ms)
                return {
                    "status": "up",
                    "latency_ms": latency_ms
                }
            else:
                logger.warning("ha_health_check", status="down", http_code=result.stdout.strip())
                return {
                    "status": "down",
                    "error": f"HTTP {result.stdout.strip()}"
                }
        except subprocess.TimeoutExpired:
            logger.error("ha_health_check", status="down", error="timeout")
            return {
                "status": "down",
                "error": "Timeout (>5s)"
            }
        except Exception as e:
            logger.error("ha_health_check", status="down", error=str(e), exc_info=True)
            return {
                "status": "down",
                "error": str(e)
            }

    def check_health_with_tracking(self) -> Dict[str, any]:
        """Check health and track status transitions for notifications."""
        health = self.check_health()
        new_status = health.get("status", "unknown")

        if new_status == "down" and self._last_known_status != "down":
            self._down_since = datetime.now()
            self._notification_sent = False

        if new_status == "up" and self._last_known_status == "down":
            downtime = datetime.now() - self._down_since if self._down_since else None
            self._down_since = None
            self._notification_sent = False
            health["recovered"] = True
            health["downtime_minutes"] = int(downtime.total_seconds() / 60) if downtime else None

        self._last_known_status = new_status
        return health

    def should_notify_ha_down(self) -> Optional[str]:
        """
        Returns a notification message if HA has been down long enough
        to warrant alerting the user. Returns None if no notification needed.
        """
        if self._last_known_status != "down" or self._notification_sent:
            return None
        if self._down_since is None:
            return None

        down_minutes = (datetime.now() - self._down_since).total_seconds() / 60
        if down_minutes >= 5:
            self._notification_sent = True
            return (f"Home Assistant has been unreachable for "
                    f"{int(down_minutes)} minutes. I'm running blind on "
                    f"home state until it comes back.")
