"""
Pytest configuration and shared fixtures for Jarvis Mode tests.
"""

import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime


@pytest.fixture
def temp_dir():
    """Temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config_data():
    """Mock configuration data."""
    return {
        "enabled": True,
        "check_interval_minutes": 5,
        "cooldown_minutes": 30,
        "motion_cooldown_minutes": 10,
        "motion_aware": True,
        "instant_alerts": True,
        "quiet_mode": True,
        "auto_actions": {
            "enabled": False,
            "announce_actions": True
        },
        "active_hours": {
            "start": 7,
            "end": 23
        },
        "cameras": {
            "kitchen": {
                "entity_id": "camera.kitchen",
                "enabled": True,
                "motion_sensor": "binary_sensor.kitchen_motion"
            },
            "living_room": {
                "entity_id": "camera.living_room",
                "enabled": False,
                "motion_sensor": "binary_sensor.living_room_motion"
            }
        },
        "suggestions": {
            "music": True,
            "lighting": True,
            "tv": True,
            "climate": True
        },
        "notify_channel": "telegram"
    }


@pytest.fixture
def mock_config_file(temp_dir, mock_config_data):
    """Mock config.json file."""
    config_file = temp_dir / "config.json"
    with open(config_file, 'w') as f:
        json.dump(mock_config_data, f)
    return config_file


@pytest.fixture
def mock_state_data():
    """Mock state data."""
    return {
        "schema_version": 2,
        "created_at": datetime.now().isoformat(),
        "rooms": {
            "kitchen": {
                "occupancy": {
                    "current": False,
                    "changed_at": datetime.now().isoformat(),
                    "duration_minutes": 0
                },
                "recent_observations": [
                    {
                        "timestamp": datetime.now().isoformat(),
                        "activity": "test activity",
                        "summary": "test summary"
                    }
                ],
                "last_context": None,
                "last_snapshot": None
            }
        },
        "decision_log": [],
        "last_poll": datetime.now().isoformat()
    }


@pytest.fixture
def mock_state_file(temp_dir, mock_state_data):
    """Mock state.json file."""
    state_file = temp_dir / "state.json"
    with open(state_file, 'w') as f:
        json.dump(mock_state_data, f)
    return state_file


@pytest.fixture
def mock_ha_states():
    """Mock Home Assistant states response."""
    return [
        {
            "entity_id": "light.kitchen",
            "state": "on",
            "attributes": {"brightness": 255}
        },
        {
            "entity_id": "light.living_room",
            "state": "off",
            "attributes": {}
        },
        {
            "entity_id": "media_player.sonos",
            "state": "playing",
            "attributes": {"media_title": "Test Song"}
        },
        {
            "entity_id": "binary_sensor.kitchen_motion",
            "state": "on",
            "attributes": {}
        },
        {
            "entity_id": "binary_sensor.living_room_motion",
            "state": "off",
            "attributes": {}
        },
        {
            "entity_id": "climate.thermostat",
            "state": "heat",
            "attributes": {"temperature": 72}
        }
    ]


@pytest.fixture
def mock_ha_entity():
    """Mock single HA entity response."""
    def _mock_entity(entity_id: str, state: str = "on", **attributes):
        return {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes
        }
    return _mock_entity
