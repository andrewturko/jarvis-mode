"""
Tests for JarvisConfig - type-safe configuration management.
"""

import json
import pytest
import os

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.config import (
    JarvisConfig,
    CameraConfig,
    AutoActionsConfig,
    ActiveHoursConfig,
    SuggestionsConfig
)


class TestCameraConfig:
    """Test suite for CameraConfig."""

    def test_valid_camera_config(self):
        """Test valid camera configuration."""
        config = CameraConfig(
            entity_id="camera.kitchen",
            enabled=True,
            motion_sensor="binary_sensor.kitchen_motion"
        )

        assert config.entity_id == "camera.kitchen"
        assert config.enabled is True
        assert config.motion_sensor == "binary_sensor.kitchen_motion"

    def test_camera_config_defaults(self):
        """Test camera config defaults."""
        config = CameraConfig(entity_id="camera.kitchen")

        assert config.enabled is True
        assert config.motion_sensor is None

    def test_camera_config_validation(self):
        """Test camera config validation."""
        with pytest.raises(ValueError, match="entity_id is required"):
            CameraConfig(entity_id="")


class TestActiveHoursConfig:
    """Test suite for ActiveHoursConfig."""

    def test_valid_active_hours(self):
        """Test valid active hours."""
        config = ActiveHoursConfig(start=7, end=23)

        assert config.start == 7
        assert config.end == 23

    def test_active_hours_defaults(self):
        """Test active hours defaults."""
        config = ActiveHoursConfig()

        assert config.start == 7
        assert config.end == 23

    def test_active_hours_validation(self):
        """Test active hours validation."""
        with pytest.raises(ValueError, match="start hour must be 0-23"):
            ActiveHoursConfig(start=25, end=23)

        with pytest.raises(ValueError, match="end hour must be 0-23"):
            ActiveHoursConfig(start=7, end=24)

    def test_is_active(self):
        """Test is_active method."""
        config = ActiveHoursConfig(start=7, end=23)

        assert config.is_active(12) is True  # Midday
        assert config.is_active(7) is True   # Start hour
        assert config.is_active(22) is True  # Before end
        assert config.is_active(23) is False # At end
        assert config.is_active(2) is False  # Night

    def test_is_active_wraparound(self):
        """Test is_active with wraparound (e.g., 22:00 to 2:00)."""
        config = ActiveHoursConfig(start=22, end=2)

        assert config.is_active(23) is True  # Late night
        assert config.is_active(0) is True   # Midnight
        assert config.is_active(1) is True   # Early morning
        assert config.is_active(2) is False  # At end
        assert config.is_active(12) is False # Daytime


class TestJarvisConfig:
    """Test suite for JarvisConfig."""

    def test_load_valid_config(self, mock_config_file):
        """Test loading valid configuration."""
        config = JarvisConfig.load(mock_config_file)

        assert config.enabled is True
        assert config.check_interval_minutes == 5
        assert config.cooldown_minutes == 30
        assert len(config.cameras) == 2
        assert isinstance(config.cameras["kitchen"], CameraConfig)

    def test_config_validation(self):
        """Test configuration validation."""
        with pytest.raises(ValueError, match="check_interval_minutes must be >= 1"):
            JarvisConfig(
                enabled=True,
                check_interval_minutes=0,
                cameras={}
            )

    def test_get_enabled_cameras(self, mock_config_file):
        """Test getting enabled cameras."""
        config = JarvisConfig.load(mock_config_file)

        enabled = config.get_enabled_cameras()

        assert len(enabled) == 1
        assert "kitchen" in enabled
        assert "living_room" not in enabled  # Disabled

    def test_is_suggestion_enabled(self, mock_config_file):
        """Test checking suggestion types."""
        config = JarvisConfig.load(mock_config_file)

        assert config.is_suggestion_enabled("music") is True
        assert config.is_suggestion_enabled("lighting") is True
        assert config.is_suggestion_enabled("nonexistent") is False

    def test_config_save(self, temp_dir, mock_config_data):
        """Test saving configuration."""
        config_file = temp_dir / "config.json"

        # Create config
        config = JarvisConfig(**mock_config_data)

        # Save
        config.save(config_file)

        # Verify file exists and is valid
        assert config_file.exists()
        with open(config_file) as f:
            saved_data = json.load(f)
        assert saved_data["enabled"] is True

    def test_camelcase_conversion(self, temp_dir):
        """Test camelCase to snake_case conversion."""
        config_file = temp_dir / "config.json"

        # Write config with camelCase keys
        camel_case_config = {
            "enabled": True,
            "checkIntervalMinutes": 5,
            "cooldownMinutes": 30,
            "motionCooldownMinutes": 10,
            "motionAware": True,
            "instantAlerts": True,
            "quietMode": True,
            "autoActions": {
                "enabled": False,
                "announceActions": True
            },
            "activeHours": {
                "start": 7,
                "end": 23
            },
            "cameras": {},
            "suggestions": {},
            "notifyChannel": "telegram"
        }

        with open(config_file, 'w') as f:
            json.dump(camel_case_config, f)

        # Should load and convert
        config = JarvisConfig.load(config_file)

        assert config.check_interval_minutes == 5
        assert config.motion_cooldown_minutes == 10
        assert config.notify_channel == "telegram"

    def test_env_var_interpolation(self, temp_dir, monkeypatch):
        """Test environment variable interpolation."""
        config_file = temp_dir / "config.json"

        # Set env var
        monkeypatch.setenv("TEST_CHANNEL", "discord")

        # Write config with env var placeholder
        config_data = {
            "enabled": True,
            "check_interval_minutes": 5,
            "cooldown_minutes": 30,
            "motion_cooldown_minutes": 10,
            "cameras": {},
            "notify_channel": "${TEST_CHANNEL}"
        }

        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        # Load
        config = JarvisConfig.load(config_file)

        assert config.notify_channel == "discord"

    def test_env_var_with_default(self, temp_dir):
        """Test environment variable with default value."""
        config_file = temp_dir / "config.json"

        # Write config with env var + default
        config_data = {
            "enabled": True,
            "check_interval_minutes": 5,
            "cooldown_minutes": 30,
            "motion_cooldown_minutes": 10,
            "cameras": {},
            "notify_channel": "${NONEXISTENT_VAR:-telegram}"
        }

        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        # Load
        config = JarvisConfig.load(config_file)

        assert config.notify_channel == "telegram"

    def test_missing_config_file(self, temp_dir):
        """Test loading missing config file."""
        config_file = temp_dir / "nonexistent.json"

        with pytest.raises(FileNotFoundError):
            JarvisConfig.load(config_file)
