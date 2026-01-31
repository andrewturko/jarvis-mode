#!/usr/bin/env python3
"""
External Context Service - Lightweight context enrichment.

Provides basic time/weather awareness. Claude can naturally query other
OpenClaw skills during reasoning if needed — no complex pre-fetching required.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import urllib.request


class ExternalContext:
    """
    Lightweight context for Jarvis suggestions.

    Just provides quick basics — Claude can query other skills naturally.
    """

    def __init__(self):
        """Initialize with HA credentials if available."""
        self.ha_url = os.environ.get("HA_URL", "")
        self.ha_token = os.environ.get("HA_TOKEN", "")

        if not self.ha_url or not self.ha_token:
            self._load_ha_credentials()

    def _load_ha_credentials(self):
        """Load HA credentials from openclaw.json."""
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                env_vars = config.get("env", {}).get("vars", {})
                self.ha_url = env_vars.get("HA_URL", "")
                self.ha_token = env_vars.get("HA_TOKEN", "")
            except Exception:
                pass

    def _ha_get(self, endpoint: str) -> Optional[Dict]:
        """Quick HA API request."""
        if not self.ha_url or not self.ha_token:
            return None

        try:
            url = f"{self.ha_url.rstrip('/')}{endpoint}"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.ha_token}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def get_all_context(self) -> Dict[str, Any]:
        """
        Get basic context — time awareness + weather if available.

        Keep it simple. Claude can ask other skills for more if needed.
        """
        return {
            "time": self._get_time_context(),
            "weather": self._get_weather(),
        }

    def _get_time_context(self) -> Dict[str, Any]:
        """Time-based context — always available."""
        now = datetime.now()
        hour = now.hour

        if 5 <= hour < 9:
            period = "early_morning"
        elif 9 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 14:
            period = "midday"
        elif 14 <= hour < 17:
            period = "afternoon"
        elif 17 <= hour < 20:
            period = "evening"
        elif 20 <= hour < 23:
            period = "late_evening"
        else:
            period = "night"

        return {
            "period": period,
            "is_weekend": now.weekday() >= 5,
            "day": now.strftime("%A"),
        }

    def _get_weather(self) -> Optional[str]:
        """Get weather summary if HA has a weather entity."""
        # Find first weather entity
        states = self._ha_get("/api/states")
        if not states:
            return None

        for state in states:
            if state.get("entity_id", "").startswith("weather."):
                condition = state.get("state", "")
                temp = state.get("attributes", {}).get("temperature")
                if temp:
                    return f"{condition}, {temp}°"
                return condition

        return None

    def get_insights(self, home_context: Dict[str, Any]) -> List[str]:
        """
        Quick compound insights from time + home state.

        Nothing fancy — just obvious connections.
        """
        insights = []
        ctx = self.get_all_context()
        time = ctx.get("time", {})

        # Late evening + living room occupied
        if time.get("period") == "late_evening":
            if "living_room" in home_context.get("occupied_rooms", []):
                insights.append("Late evening — winding down?")

        # Early workday morning
        if time.get("period") == "early_morning" and not time.get("is_weekend"):
            insights.append("Workday morning")

        return insights


def get_external_context() -> Dict[str, Any]:
    """Get basic external context."""
    return ExternalContext().get_all_context()


def get_insights(home_context: Dict[str, Any]) -> List[str]:
    """Get compound insights."""
    return ExternalContext().get_insights(home_context)


if __name__ == "__main__":
    ctx = ExternalContext()
    print(json.dumps(ctx.get_all_context(), indent=2, default=str))
