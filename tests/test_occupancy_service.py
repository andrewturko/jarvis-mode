"""
Tests for OccupancyService - occupancy detection and transitions.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from services.occupancy_service import OccupancyService
from core.config import JarvisConfig
from core.state_manager import StateManager
from services.ha_service import HAService


class TestOccupancyService:
    """Test suite for OccupancyService."""

    @pytest.fixture
    def occupancy_service(self, mock_config_file, temp_dir):
        """Create OccupancyService instance."""
        config = JarvisConfig.load(mock_config_file)
        state_manager = StateManager(temp_dir / "state.json")
        ha_service = Mock(spec=HAService)

        return OccupancyService(config, state_manager, ha_service)

    def test_should_check_room_manual(self, occupancy_service):
        """Test manual check always proceeds."""
        result = occupancy_service.should_check_room("kitchen", trigger="manual")

        assert result["should_check"] is True
        assert result["reason"] == "manual override"

    def test_should_check_room_cooldown(self, occupancy_service, temp_dir):
        """Test cooldown prevents check."""
        # Set recent check time
        occupancy_service.state_manager.update_room("kitchen", {
            "last_check": datetime.now().isoformat()
        })

        result = occupancy_service.should_check_room("kitchen", trigger="scheduled")

        assert result["should_check"] is False
        assert "cooldown" in result["reason"]

    def test_should_check_room_no_motion(self, occupancy_service):
        """Test motion-aware prevents check when no motion."""
        # Mock no motion detected
        occupancy_service.ha_service.is_motion_detected.return_value = False

        result = occupancy_service.should_check_room("kitchen", trigger="scheduled")

        assert result["should_check"] is False
        assert result["reason"] == "no motion detected"

    def test_should_check_room_ready(self, occupancy_service):
        """Test room is ready for check."""
        # Mock motion detected
        occupancy_service.ha_service.is_motion_detected.return_value = True

        result = occupancy_service.should_check_room("kitchen", trigger="scheduled")

        assert result["should_check"] is True
        assert result["reason"] == "ready"

    def test_poll_occupancy_no_transitions(self, occupancy_service):
        """Test polling with no occupancy changes."""
        # Mock all rooms empty
        occupancy_service.ha_service.is_motion_detected.return_value = False

        result = occupancy_service.poll_occupancy()

        assert result["polled"] is True
        assert result["has_transitions"] is False
        assert len(result["transitions"]) == 0

    def test_poll_occupancy_room_becomes_occupied(self, occupancy_service):
        """Test detecting room becoming occupied."""
        # Set initial state: kitchen empty
        occupancy_service.state_manager.update_occupancy("kitchen", False)

        # Mock motion detected
        occupancy_service.ha_service.is_motion_detected.return_value = True

        result = occupancy_service.poll_occupancy()

        assert result["has_transitions"] is True
        assert len(result["transitions"]) == 1
        transition = result["transitions"][0]
        assert transition["room"] == "kitchen"
        assert transition["transition"] == "occupied"
        assert transition["previous"] == "empty"
        assert transition["current"] == "occupied"

    def test_poll_occupancy_room_becomes_empty(self, occupancy_service):
        """Test detecting room becoming empty."""
        # Set initial state: kitchen occupied
        occupancy_service.state_manager.update_occupancy("kitchen", True)

        # Mock no motion detected
        occupancy_service.ha_service.is_motion_detected.return_value = False

        result = occupancy_service.poll_occupancy()

        assert result["has_transitions"] is True
        transition = result["transitions"][0]
        assert transition["transition"] == "emptied"
        assert transition["previous"] == "occupied"
        assert transition["current"] == "empty"

    def test_poll_occupancy_multiple_rooms(self, occupancy_service):
        """Test polling multiple rooms."""
        # Set initial states
        occupancy_service.state_manager.update_occupancy("kitchen", False)
        occupancy_service.state_manager.update_occupancy("living_room", True)

        # Mock different states for each room
        def mock_motion(sensor):
            if "kitchen" in sensor:
                return True  # Kitchen becomes occupied
            else:
                return False  # Living room becomes empty

        occupancy_service.ha_service.is_motion_detected.side_effect = mock_motion

        result = occupancy_service.poll_occupancy()

        # Note: living_room is disabled in mock config, so only kitchen transitions
        assert result["has_transitions"] is True
        assert len(result["transitions"]) == 1  # Only kitchen (living_room disabled)

    def test_cooldown_tracking(self, occupancy_service):
        """Test cooldown is tracked correctly."""
        # First check should succeed
        result1 = occupancy_service.should_check_room("kitchen", trigger="scheduled")

        # Immediately after, should be in cooldown
        occupancy_service.state_manager.update_room("kitchen", {
            "last_check": datetime.now().isoformat()
        })

        result2 = occupancy_service.should_check_room("kitchen", trigger="scheduled")

        assert result2["should_check"] is False
        assert "cooldown" in result2["reason"]
