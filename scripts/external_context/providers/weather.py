"""
Weather provider — fetches current conditions and forecast via wttr.in.

Signals emitted:
    weather_rainy          — currently raining or rain in forecast
    weather_sunny          — clear / sunny conditions
    weather_cold           — below 40°F
    weather_hot            — above 85°F
    weather_nice_evening   — pleasant evening weather (good for going out)
    weather_storm          — severe weather alert
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, List

from external_context.base_provider import ExternalContextProvider

WEATHER_LOCATION = os.environ.get("WEATHER_LOCATION", "Bellevue,WA")
CURL_TIMEOUT = 15

# ---------------------------------------------------------------------------
# Weather condition keywords
# ---------------------------------------------------------------------------

RAIN_KEYWORDS = [
    "rain", "drizzle", "shower", "thunderstorm", "sleet",
    "light rain", "heavy rain", "moderate rain",
]
SUNNY_KEYWORDS = [
    "sunny", "clear", "fine",
]
STORM_KEYWORDS = [
    "thunderstorm", "thunder", "tornado", "hurricane",
    "blizzard", "hail", "severe",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_weather(location: str) -> Dict:
    """Fetch weather JSON from wttr.in via curl."""
    url = f"wttr.in/{location}?format=j1"
    try:
        result = subprocess.run(
            ["curl", "-s", url],
            capture_output=True, text=True, timeout=CURL_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"[weather] curl error: {result.stderr.strip()}", file=sys.stderr)
            return {}
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("[weather] curl not found in PATH", file=sys.stderr)
        return {}
    except subprocess.TimeoutExpired:
        print("[weather] curl timed out", file=sys.stderr)
        return {}
    except json.JSONDecodeError as exc:
        print(f"[weather] wttr.in response not valid JSON: {exc}", file=sys.stderr)
        return {}


def _safe_int(val: str, default: int = 0) -> int:
    """Safely parse a string to int."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _condition_text(current: Dict) -> str:
    """Extract a human-readable condition description."""
    descs = current.get("weatherDesc", [])
    if descs and isinstance(descs, list) and descs[0].get("value"):
        return descs[0]["value"]
    return ""


def _is_rainy(condition: str) -> bool:
    return any(kw in condition.lower() for kw in RAIN_KEYWORDS)


def _is_sunny(condition: str) -> bool:
    return any(kw in condition.lower() for kw in SUNNY_KEYWORDS)


def _is_stormy(condition: str) -> bool:
    return any(kw in condition.lower() for kw in STORM_KEYWORDS)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class WeatherProvider(ExternalContextProvider):
    name = "weather"
    stale_after_minutes = 60

    def refresh(self) -> Dict:
        raw = _fetch_weather(WEATHER_LOCATION)
        if not raw:
            return {}

        current_list = raw.get("current_condition", [])
        current = current_list[0] if current_list else {}

        temp_f = _safe_int(current.get("temp_F", "0"))
        condition = _condition_text(current)
        humidity = _safe_int(current.get("humidity", "0"))

        # Today's forecast (hourly breakdown)
        forecast_days = raw.get("weather", [])
        today_forecast = forecast_days[0] if forecast_days else {}
        today_max_f = _safe_int(today_forecast.get("maxtempF", "0"))
        today_min_f = _safe_int(today_forecast.get("mintempF", "0"))

        # Check evening hours (18:00, 21:00) for rain or nice weather
        hourly = today_forecast.get("hourly", [])
        evening_rain = False
        evening_nice = False
        for hour_data in hourly:
            hour_time = _safe_int(hour_data.get("time", "0"))
            # wttr.in uses 0, 300, 600, ..., 2100 format
            if hour_time >= 1800:
                hour_condition = _condition_text(hour_data)
                hour_temp_f = _safe_int(hour_data.get("tempF", "0"))
                if _is_rainy(hour_condition):
                    evening_rain = True
                if 55 <= hour_temp_f <= 80 and not _is_rainy(hour_condition):
                    evening_nice = True

        # Check all forecast hours for rain
        forecast_rain = False
        for hour_data in hourly:
            hour_condition = _condition_text(hour_data)
            if _is_rainy(hour_condition):
                forecast_rain = True
                break

        return {
            "temp_f": temp_f,
            "condition": condition,
            "humidity": humidity,
            "today_max_f": today_max_f,
            "today_min_f": today_min_f,
            "currently_rainy": _is_rainy(condition),
            "currently_sunny": _is_sunny(condition),
            "currently_stormy": _is_stormy(condition),
            "forecast_rain": forecast_rain,
            "evening_rain": evening_rain,
            "evening_nice": evening_nice,
            "location": WEATHER_LOCATION,
        }

    def signals(self, data: Dict) -> List[str]:
        if not data:
            return []

        sigs: List[str] = []
        temp_f = data.get("temp_f", 65)

        if data.get("currently_rainy") or data.get("forecast_rain"):
            sigs.append("weather_rainy")
        if data.get("currently_sunny"):
            sigs.append("weather_sunny")
        if temp_f < 40:
            sigs.append("weather_cold")
        if temp_f > 85:
            sigs.append("weather_hot")
        if data.get("evening_nice") and not data.get("evening_rain"):
            sigs.append("weather_nice_evening")
        if data.get("currently_stormy"):
            sigs.append("weather_storm")

        # Deduplicate preserving order
        seen = set()
        deduped: List[str] = []
        for s in sigs:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    def narrative(self, data: Dict) -> str:
        if not data:
            return ""

        temp_f = data.get("temp_f", 0)
        condition = data.get("condition", "unknown")
        parts = [f"{temp_f}°F and {condition.lower()}"]

        if data.get("evening_rain"):
            parts.append("rain expected this evening")
        elif data.get("evening_nice"):
            parts.append("nice evening ahead")
        elif data.get("forecast_rain"):
            parts.append("rain in the forecast")

        return ", ".join(parts) + "."
