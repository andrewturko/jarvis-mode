"""
Tests for HAService - Home Assistant integration.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
import subprocess

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from services.ha_service import HAService


class TestHAService:
    """Test suite for HAService."""

    @pytest.fixture
    def ha_service(self):
        """Create HAService instance with test credentials."""
        return HAService(
            ha_url="http://test.local:8123",
            ha_token="test_token_12345"
        )

    def test_initialization(self):
        """Test HAService initializes correctly."""
        service = HAService(
            ha_url="http://test.local:8123",
            ha_token="test_token"
        )

        assert service.ha_url == "http://test.local:8123"
        assert service.ha_token == "test_token"

    def test_initialization_from_env(self, monkeypatch):
        """Test initialization from environment variables."""
        monkeypatch.setenv("HA_URL", "http://env.local:8123")
        monkeypatch.setenv("HA_TOKEN", "env_token")

        service = HAService()

        assert service.ha_url == "http://env.local:8123"
        assert service.ha_token == "env_token"

    @patch('subprocess.run')
    def test_get_entity_state_success(self, mock_run, ha_service, mock_ha_entity):
        """Test getting entity state successfully."""
        # Mock successful response
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(mock_ha_entity("light.kitchen", "on", brightness=255))
        )

        result = ha_service.get_entity_state("light.kitchen")

        assert result is not None
        assert result["entity_id"] == "light.kitchen"
        assert result["state"] == "on"

    @patch('subprocess.run')
    def test_get_entity_state_not_found(self, mock_run, ha_service):
        """Test getting non-existent entity."""
        # Mock error response
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({"error": "Entity not found"})
        )

        result = ha_service.get_entity_state("light.nonexistent")

        assert result is None

    @patch('subprocess.run')
    def test_get_entity_state_timeout(self, mock_run, ha_service):
        """Test entity state query timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("curl", 10)

        result = ha_service.get_entity_state("light.kitchen")

        assert result is None

    @patch('subprocess.run')
    def test_get_all_states_success(self, mock_run, ha_service, mock_ha_states):
        """Test getting all states successfully."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(mock_ha_states)
        )

        states = ha_service.get_all_states()

        assert len(states) == 6
        assert any(s["entity_id"] == "light.kitchen" for s in states)

    @patch('subprocess.run')
    def test_get_all_states_failure(self, mock_run, ha_service):
        """Test get_all_states failure."""
        mock_run.return_value = Mock(
            returncode=1,
            stderr="Connection error"
        )

        states = ha_service.get_all_states()

        assert states == []

    @patch('subprocess.run')
    def test_is_motion_detected_on(self, mock_run, ha_service, mock_ha_entity):
        """Test motion detected (on state)."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(mock_ha_entity("binary_sensor.kitchen_motion", "on"))
        )

        result = ha_service.is_motion_detected("binary_sensor.kitchen_motion")

        assert result is True

    @patch('subprocess.run')
    def test_is_motion_detected_off(self, mock_run, ha_service, mock_ha_entity):
        """Test no motion detected (off state)."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(mock_ha_entity("binary_sensor.kitchen_motion", "off"))
        )

        result = ha_service.is_motion_detected("binary_sensor.kitchen_motion")

        assert result is False

    @patch('subprocess.run')
    def test_is_motion_detected_error(self, mock_run, ha_service):
        """Test motion detection error."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="{}"
        )

        result = ha_service.is_motion_detected("binary_sensor.kitchen_motion")

        assert result is None

    @patch('subprocess.run')
    def test_get_home_state(self, mock_run, ha_service, mock_ha_states):
        """Test getting aggregated home state."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(mock_ha_states)
        )

        home_state = ha_service.get_home_state()

        assert "lights_on" in home_state
        assert "lights_off" in home_state
        assert "media_playing" in home_state
        assert "climate" in home_state

        assert "light.kitchen" in home_state["lights_on"]
        assert "light.living_room" in home_state["lights_off"]
        assert "media_player.sonos" in home_state["media_playing"]

    @patch('subprocess.run')
    def test_get_room_lights(self, mock_run, ha_service, mock_ha_states):
        """Test getting lights on in a room."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(mock_ha_states)
        )

        room_lights_map = {
            "kitchen": ["light.kitchen"]
        }

        lights = ha_service.get_room_lights("kitchen", room_lights_map)

        assert "light.kitchen" in lights
        assert "light.living_room" not in lights

    @patch('subprocess.run')
    def test_turn_off_lights_success(self, mock_run, ha_service):
        """Test turning off lights successfully."""
        mock_run.return_value = Mock(returncode=0)

        results = ha_service.turn_off_lights(["light.kitchen", "light.living_room"])

        assert results["light.kitchen"] is True
        assert results["light.living_room"] is True
        assert mock_run.call_count == 2

    @patch('subprocess.run')
    def test_turn_off_lights_failure(self, mock_run, ha_service):
        """Test light turn off failure."""
        mock_run.return_value = Mock(returncode=1, stderr="Service call failed")

        results = ha_service.turn_off_lights(["light.kitchen"])

        assert results["light.kitchen"] is False

    def test_turn_off_lights_no_token(self):
        """Test turn off lights without token."""
        service = HAService(ha_url="http://test.local", ha_token="")

        results = service.turn_off_lights(["light.kitchen"])

        assert results["light.kitchen"] is False

    @patch('subprocess.run')
    def test_turn_off_lights_timeout(self, mock_run, ha_service):
        """Test light turn off timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("curl", 10)

        results = ha_service.turn_off_lights(["light.kitchen"])

        assert results["light.kitchen"] is False


@pytest.mark.integration
@pytest.mark.requires_ha
class TestHAServiceIntegration:
    """Integration tests for HAService (requires real HA)."""

    @pytest.mark.skip(reason="Requires real Home Assistant instance")
    def test_real_ha_connection(self):
        """Test connecting to real HA (skip by default)."""
        # This test would connect to a real HA instance
        # Only run when explicitly testing against HA
        service = HAService()
        states = service.get_all_states()
        assert isinstance(states, list)
