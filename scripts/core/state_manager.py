#!/usr/bin/env python3
"""
Thread-safe state management for Jarvis Mode.

Provides atomic writes, file locking, and schema versioning for reliable
concurrent access to state.json.
"""

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import contextmanager


class StateManager:
    """
    Thread-safe state operations with atomic writes and file locking.

    Features:
    - File locking via fcntl for concurrent access protection
    - Atomic writes (temp file + rename) to prevent corruption
    - Schema versioning for migrations
    - Decision log management with automatic cleanup
    - Observation history per room
    """

    SCHEMA_VERSION = 2
    MAX_OBSERVATIONS_PER_ROOM = 5
    MAX_DECISION_LOG_ENTRIES = 100

    def __init__(self, state_file_path: Path):
        """
        Initialize state manager.

        Args:
            state_file_path: Path to state.json file
        """
        self.state_file = Path(state_file_path)
        self.lock_file = self.state_file.with_suffix('.lock')

        # Ensure state file exists
        if not self.state_file.exists():
            self._initialize_state()

    def _initialize_state(self):
        """Initialize a fresh state file with schema v2."""
        initial_state = {
            "schema_version": self.SCHEMA_VERSION,
            "created_at": datetime.now().isoformat(),
            "rooms": {},
            "decision_log": [],
            "last_poll": None
        }
        self._write_state_atomic(initial_state)

    @contextmanager
    def _file_lock(self, mode='r'):
        """
        Context manager for file locking.

        Args:
            mode: File open mode ('r' or 'r+')

        Yields:
            Open file handle with exclusive lock
        """
        # Ensure lock file exists
        self.lock_file.touch(exist_ok=True)

        lock_fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT)
        try:
            # Acquire exclusive lock
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            # Ensure state file exists
            if not self.state_file.exists():
                self._initialize_state()

            with open(self.state_file, mode) as f:
                yield f
        finally:
            # Release lock
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def read_state(self) -> Dict[str, Any]:
        """
        Read state with file lock.

        Returns:
            State dictionary
        """
        with self._file_lock('r') as f:
            try:
                state = json.load(f)

                # Validate schema version
                if state.get('schema_version') != self.SCHEMA_VERSION:
                    raise ValueError(
                        f"State schema version {state.get('schema_version')} "
                        f"does not match expected {self.SCHEMA_VERSION}. "
                        f"Run migrate_state.py to upgrade."
                    )

                return state
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Corrupted state file: {e}")

    def _write_state_atomic(self, state: Dict[str, Any]):
        """
        Write state atomically (temp file + rename).

        This prevents corruption if the process crashes mid-write.

        Args:
            state: State dictionary to write
        """
        # Write to temp file
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self.state_file.parent,
            prefix='.state.tmp.',
            suffix='.json'
        )

        try:
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(state, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            os.replace(temp_path, self.state_file)
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise RuntimeError(f"Failed to write state: {e}")

    def write_state(self, state: Dict[str, Any]):
        """
        Write state with file lock and atomic write.

        Args:
            state: State dictionary to write
        """
        with self._file_lock('r+'):
            # Update timestamp
            state['last_updated'] = datetime.now().isoformat()

            # Validate schema version
            if 'schema_version' not in state:
                state['schema_version'] = self.SCHEMA_VERSION

            self._write_state_atomic(state)

    def atomic_update(self, update_fn):
        """
        Perform atomic read-modify-write operation.

        Holds the file lock for the entire operation to prevent race conditions.

        Args:
            update_fn: Function that takes current state and returns modified state

        Returns:
            Updated state
        """
        with self._file_lock('r+') as f:
            # Read current state directly (don't call read_state to avoid nested lock)
            try:
                f.seek(0)
                state = json.load(f)
            except json.JSONDecodeError:
                state = self._empty_state()

            # Apply update function
            state = update_fn(state)

            # Update timestamp
            state['last_updated'] = datetime.now().isoformat()

            # Validate schema version
            if 'schema_version' not in state:
                state['schema_version'] = self.SCHEMA_VERSION

            # Write back atomically
            self._write_state_atomic(state)

            return state

    def update_room(self, room: str, updates: Dict[str, Any]):
        """
        Partial update to a single room's state.

        Args:
            room: Room name
            updates: Dictionary of fields to update
        """
        state = self.read_state()

        if 'rooms' not in state:
            state['rooms'] = {}

        if room not in state['rooms']:
            state['rooms'][room] = self._empty_room_state()

        # Merge updates
        state['rooms'][room].update(updates)

        self.write_state(state)

    def _empty_room_state(self) -> Dict[str, Any]:
        """Create empty room state structure."""
        return {
            "occupancy": {
                "current": False,  # Default to empty, not None/unknown
                "changed_at": None,
                "duration_minutes": 0
            },
            "recent_observations": [],
            "last_context": None,
            "last_snapshot": None,
            "last_motion_at": None,  # When motion was last detected
            "last_check": None       # When room was last checked/analyzed
        }

    def get_room_state(self, room: str) -> Optional[Dict[str, Any]]:
        """
        Get state for a single room.

        Args:
            room: Room name

        Returns:
            Room state dict or None if room doesn't exist
        """
        state = self.read_state()
        return state.get('rooms', {}).get(room)

    def record_observation(self, room: str, observation: Dict[str, Any]):
        """
        Record an observation for a room.

        Maintains last N observations (FIFO queue).
        If recording a non-pending observation, clears any existing pending ones.

        Args:
            room: Room name
            observation: Observation dict with timestamp, activity, summary, etc.
        """
        state = self.read_state()

        if 'rooms' not in state:
            state['rooms'] = {}

        if room not in state['rooms']:
            state['rooms'][room] = self._empty_room_state()

        # Add timestamp if not present
        if 'timestamp' not in observation:
            observation['timestamp'] = datetime.now().isoformat()

        # Get observations from canonical field
        observations = state['rooms'][room].get('recent_observations', [])

        # Also check legacy field and merge if it has newer data
        legacy_observations = state['rooms'][room].get('recentObservations', [])
        if legacy_observations:
            # Merge legacy into canonical, avoiding duplicates by timestamp
            existing_timestamps = {obs.get('timestamp') for obs in observations}
            for legacy_obs in legacy_observations:
                if legacy_obs.get('timestamp') not in existing_timestamps:
                    observations.append(legacy_obs)
            # Sort by timestamp descending
            observations.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        # If this is a real observation (not pending), remove any pending observations
        if not observation.get('pending'):
            observations = [obs for obs in observations if not obs.get('pending')]

        # Prepend new observation
        observations.insert(0, observation)

        # Keep only last N observations
        state['rooms'][room]['recent_observations'] = observations[:self.MAX_OBSERVATIONS_PER_ROOM]

        # Clean up legacy field - migrate to canonical
        if 'recentObservations' in state['rooms'][room]:
            del state['rooms'][room]['recentObservations']
        if 'lastActivity' in state['rooms'][room]:
            del state['rooms'][room]['lastActivity']

        self.write_state(state)

    def log_decision(self, decision: Dict[str, Any]):
        """
        Log a decision to the decision log.

        Decision should include:
        - timestamp
        - room
        - trigger (motion, poll, manual)
        - context_inferred
        - confidence
        - decision (spoke, silent)
        - reason
        - suggestions_offered
        - agent_response (if spoke)
        - cost (tokens, etc.)

        Args:
            decision: Decision dictionary
        """
        state = self.read_state()

        if 'decision_log' not in state:
            state['decision_log'] = []

        # Add timestamp if not present
        if 'timestamp' not in decision:
            decision['timestamp'] = datetime.now().isoformat()

        # Prepend to log
        state['decision_log'].insert(0, decision)

        # Keep only last N entries
        state['decision_log'] = state['decision_log'][:self.MAX_DECISION_LOG_ENTRIES]

        self.write_state(state)

    def get_decision_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent decision log entries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of decision dicts (most recent first)
        """
        state = self.read_state()
        log = state.get('decision_log', [])
        return log[:limit]

    def get_recent_observations(self, room: str, hours: int = 2) -> List[Dict[str, Any]]:
        """
        Get recent observations for a room within the last N hours.

        Args:
            room: Room name
            hours: Number of hours to look back

        Returns:
            List of observation dicts
        """
        room_state = self.get_room_state(room)
        if not room_state:
            return []

        observations = room_state.get('recent_observations', [])

        # Filter by time window
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []

        for obs in observations:
            try:
                obs_time = datetime.fromisoformat(obs['timestamp'])
                if obs_time >= cutoff:
                    recent.append(obs)
            except (KeyError, ValueError):
                # Skip observations without valid timestamps
                continue

        return recent

    def get_occupancy_duration(self, room: str) -> Optional[int]:
        """
        Get how long room has been in current occupancy state (minutes).

        Args:
            room: Room name

        Returns:
            Duration in minutes, or None if unknown
        """
        room_state = self.get_room_state(room)
        if not room_state:
            return None

        occupancy = room_state.get('occupancy', {})
        changed_at = occupancy.get('changed_at')

        if not changed_at:
            return None

        try:
            changed_time = datetime.fromisoformat(changed_at)
            duration = datetime.now() - changed_time
            return int(duration.total_seconds() / 60)
        except ValueError:
            return None

    def update_occupancy(self, room: str, is_occupied: bool):
        """
        Update occupancy state for a room.

        Tracks when occupancy changed and calculates duration.
        Also cleans up legacy occupancy fields.

        Args:
            room: Room name
            is_occupied: True if occupied, False if empty
        """
        room_state = self.get_room_state(room)

        if not room_state:
            room_state = self._empty_room_state()

        current_state = room_state.get('occupancy', {}).get('current')

        # Check if state changed
        if current_state != is_occupied:
            # State transition
            now = datetime.now().isoformat()
            duration = self.get_occupancy_duration(room) or 0

            room_state['occupancy'] = {
                "current": is_occupied,
                "changed_at": now,
                "duration_minutes": duration,
                "previous_state": current_state
            }

        # Clean up legacy occupancy fields (migrate to canonical schema)
        legacy_fields = ['lastOccupancy', 'occupancyChangedAt', 'lastCheck', 'lastSnapshot', 'lastActivity']
        for field in legacy_fields:
            if field in room_state:
                del room_state[field]

        # Update room state
        state = self.read_state()
        if 'rooms' not in state:
            state['rooms'] = {}
        state['rooms'][room] = room_state
        self.write_state(state)

    def backup_state(self, backup_path: Optional[Path] = None) -> Path:
        """
        Create a backup of the current state file.

        Args:
            backup_path: Optional custom backup path

        Returns:
            Path to backup file
        """
        if backup_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.state_file.with_suffix(f'.backup.{timestamp}.json')

        state = self.read_state()

        with open(backup_path, 'w') as f:
            json.dump(state, f, indent=2, default=str)

        return backup_path

    def restore_from_backup(self, backup_path: Path):
        """
        Restore state from a backup file.

        Args:
            backup_path: Path to backup file
        """
        with open(backup_path, 'r') as f:
            state = json.load(f)

        self.write_state(state)
