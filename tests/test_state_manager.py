"""
Tests for StateManager - thread-safe state operations.
"""

import json
import pytest
import threading
import time
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.state_manager import StateManager


class TestStateManager:
    """Test suite for StateManager."""

    def test_initialization(self, temp_dir):
        """Test StateManager initializes correctly."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        assert state_file.exists()
        state = manager.read_state()
        assert state["schema_version"] == 2
        assert "rooms" in state
        assert "decision_log" in state

    def test_read_write_state(self, temp_dir):
        """Test basic read/write operations."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Write state
        test_state = {
            "schema_version": 2,
            "rooms": {"kitchen": {"test": "data"}},
            "decision_log": []
        }
        manager.write_state(test_state)

        # Read back
        read_state = manager.read_state()
        assert read_state["rooms"]["kitchen"]["test"] == "data"

    def test_atomic_write(self, temp_dir):
        """Test writes are atomic (no corruption on interruption)."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Write initial state
        initial = {"schema_version": 2, "rooms": {}, "decision_log": [], "data": "initial"}
        manager.write_state(initial)

        # Simulate concurrent write (should not corrupt)
        for i in range(10):
            state = manager.read_state()
            state["data"] = f"iteration_{i}"
            manager.write_state(state)

        # Verify file is still valid JSON and not corrupted
        final = manager.read_state()
        assert "data" in final
        assert final["data"].startswith("iteration_")

    def test_concurrent_writes(self, temp_dir):
        """Test concurrent writes don't cause data loss."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Initialize state
        manager.write_state({
            "schema_version": 2,
            "rooms": {},
            "decision_log": [],
            "counter": 0
        })

        def increment_counter(thread_id):
            """Increment counter in state."""
            for _ in range(10):
                manager.atomic_update(lambda state: {
                    **state,
                    "counter": state.get("counter", 0) + 1
                })
                time.sleep(0.001)  # Small delay to increase race condition chance

        # Run 5 threads concurrently
        threads = []
        for i in range(5):
            t = threading.Thread(target=increment_counter, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify all increments were applied
        final_state = manager.read_state()
        # With file locking, all 50 increments should be preserved
        assert final_state["counter"] == 50

    def test_update_room(self, temp_dir):
        """Test partial room updates."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Update room
        manager.update_room("kitchen", {"test_field": "test_value"})

        # Verify
        state = manager.read_state()
        assert "kitchen" in state["rooms"]
        assert state["rooms"]["kitchen"]["test_field"] == "test_value"

    def test_get_room_state(self, temp_dir):
        """Test getting room state."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        manager.update_room("kitchen", {"field": "value"})

        room_state = manager.get_room_state("kitchen")
        assert room_state is not None
        assert room_state["field"] == "value"

        # Non-existent room
        assert manager.get_room_state("nonexistent") is None

    def test_record_observation(self, temp_dir):
        """Test recording observations."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Record observation
        obs = {
            "activity": "cooking",
            "summary": "Person at stove"
        }
        manager.record_observation("kitchen", obs)

        # Verify
        room_state = manager.get_room_state("kitchen")
        assert len(room_state["recent_observations"]) == 1
        assert room_state["recent_observations"][0]["activity"] == "cooking"
        assert "timestamp" in room_state["recent_observations"][0]

    def test_observation_limit(self, temp_dir):
        """Test observations are limited to max count."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Record 10 observations
        for i in range(10):
            manager.record_observation("kitchen", {"activity": f"test_{i}"})

        # Should only keep last 5
        room_state = manager.get_room_state("kitchen")
        assert len(room_state["recent_observations"]) == 5
        # Most recent should be test_9
        assert room_state["recent_observations"][0]["activity"] == "test_9"

    def test_log_decision(self, temp_dir):
        """Test decision logging."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Log decision
        decision = {
            "room": "kitchen",
            "context": "cooking",
            "decision": "spoke",
            "reason": "Context transition"
        }
        manager.log_decision(decision)

        # Verify
        log = manager.get_decision_log()
        assert len(log) == 1
        assert log[0]["room"] == "kitchen"
        assert "timestamp" in log[0]

    def test_decision_log_limit(self, temp_dir):
        """Test decision log is limited to max count."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Log 150 decisions
        for i in range(150):
            manager.log_decision({"decision_id": i})

        # Should only keep last 100
        log = manager.get_decision_log(limit=150)  # Request more than stored
        assert len(log) == 100
        # Most recent should be 149
        assert log[0]["decision_id"] == 149

    def test_get_recent_observations(self, temp_dir):
        """Test getting recent observations within time window."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Record observations with different timestamps
        now = datetime.now()
        old_obs = {
            "activity": "old",
            "timestamp": (now - timedelta(hours=3)).isoformat()
        }
        recent_obs = {
            "activity": "recent",
            "timestamp": now.isoformat()
        }

        manager.record_observation("kitchen", old_obs)
        manager.record_observation("kitchen", recent_obs)

        # Get last 2 hours
        recent = manager.get_recent_observations("kitchen", hours=2)
        assert len(recent) == 1
        assert recent[0]["activity"] == "recent"

    def test_update_occupancy(self, temp_dir):
        """Test occupancy updates."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Set occupancy
        manager.update_occupancy("kitchen", True)

        room_state = manager.get_room_state("kitchen")
        assert room_state["occupancy"]["current"] is True
        assert room_state["occupancy"]["changed_at"] is not None

        # Change occupancy
        time.sleep(0.1)  # Small delay to see duration change
        manager.update_occupancy("kitchen", False)

        room_state = manager.get_room_state("kitchen")
        assert room_state["occupancy"]["current"] is False
        assert room_state["occupancy"]["previous_state"] is True

    def test_get_occupancy_duration(self, temp_dir):
        """Test occupancy duration calculation."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Set occupancy
        manager.update_occupancy("kitchen", True)

        # Wait a bit
        time.sleep(0.1)

        # Get duration
        duration = manager.get_occupancy_duration("kitchen")
        assert duration is not None
        assert duration >= 0

    def test_backup_restore(self, temp_dir):
        """Test backup and restore."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Write some data
        manager.update_room("kitchen", {"data": "original"})

        # Backup
        backup_path = manager.backup_state()
        assert backup_path.exists()

        # Modify state
        manager.update_room("kitchen", {"data": "modified"})

        # Restore
        manager.restore_from_backup(backup_path)

        # Verify restored
        room_state = manager.get_room_state("kitchen")
        assert room_state["data"] == "original"

    def test_schema_validation(self, temp_dir):
        """Test schema version mismatch triggers auto-recovery."""
        state_file = temp_dir / "state.json"

        # Write invalid schema version
        with open(state_file, 'w') as f:
            json.dump({"schema_version": 1, "rooms": {}}, f)

        manager = StateManager(state_file)

        # Should auto-recover by reinitializing (no backups available)
        state = manager.read_state()
        assert state["schema_version"] == manager.SCHEMA_VERSION
        assert "rooms" in state


@pytest.mark.integration
class TestStateManagerIntegration:
    """Integration tests for StateManager."""

    def test_real_world_workflow(self, temp_dir):
        """Test realistic usage workflow."""
        state_file = temp_dir / "state.json"
        manager = StateManager(state_file)

        # Simulate Jarvis workflow
        # 1. Record observation
        manager.record_observation("kitchen", {
            "activity": "cooking",
            "summary": "Person at stove"
        })

        # 2. Update occupancy
        manager.update_occupancy("kitchen", True)

        # 3. Log decision
        manager.log_decision({
            "room": "kitchen",
            "context": "cooking",
            "decision": "spoke",
            "suggestions_offered": 1
        })

        # 4. Verify all data persisted
        state = manager.read_state()
        assert len(state["rooms"]["kitchen"]["recent_observations"]) == 1
        assert state["rooms"]["kitchen"]["occupancy"]["current"] is True
        assert len(state["decision_log"]) == 1
