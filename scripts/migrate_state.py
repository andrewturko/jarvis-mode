#!/usr/bin/env python3
"""
State migration script for Jarvis Mode.

Migrates from schema v1 (flat structure) to v2 (structured with occupancy tracking).
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.paths import SKILL_DIR, STATE_FILE, BACKUP_DIR


def backup_state(state_file: Path) -> Path:
    """Create backup of existing state."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = state_file.with_name(f'state.v1.backup.{timestamp}.json')

    with open(state_file, 'r') as f:
        state = json.load(f)

    with open(backup_path, 'w') as f:
        json.dump(state, f, indent=2, default=str)

    print(f"✓ Backed up state to: {backup_path}")
    return backup_path


def migrate_v1_to_v2(old_state: dict) -> dict:
    """
    Migrate schema v1 to v2.

    V1 structure:
    {
      "lastGlobalCheck": "...",
      "rooms": {
        "kitchen": {
          "lastCheck": "...",
          "lastActivity": "...",
          "recentObservations": [...],
          "lastOccupancy": false,
          "occupancyChangedAt": "..."
        }
      }
    }

    V2 structure:
    {
      "schema_version": 2,
      "created_at": "...",
      "rooms": {
        "kitchen": {
          "occupancy": {
            "current": false,
            "changed_at": "...",
            "duration_minutes": 0
          },
          "recent_observations": [...],  // Keep last 5 only
          "last_context": null,
          "last_snapshot": "..."
        }
      },
      "decision_log": [],
      "last_poll": "..."
    }
    """
    new_state = {
        "schema_version": 2,
        "created_at": datetime.now().isoformat(),
        "rooms": {},
        "decision_log": [],
        "last_poll": old_state.get("lastPoll") or old_state.get("lastGlobalCheck")
    }

    # Migrate rooms
    old_rooms = old_state.get("rooms", {})
    for room_name, room_data in old_rooms.items():
        # Convert occupancy to new structure
        last_occupancy = room_data.get("lastOccupancy")
        occupancy_changed = room_data.get("occupancyChangedAt")

        new_room = {
            "occupancy": {
                "current": last_occupancy,
                "changed_at": occupancy_changed,
                "duration_minutes": 0  # Can't calculate from v1
            },
            "recent_observations": room_data.get("recentObservations", [])[:5],  # Keep last 5
            "last_context": None,  # Not tracked in v1
            "last_snapshot": room_data.get("lastSnapshot")
        }

        new_state["rooms"][room_name] = new_room

    return new_state


def cleanup_legacy_fields(state: dict) -> dict:
    """
    Clean up legacy v1 fields from a v2 state.

    This handles the case where both v1 and v2 fields exist.
    """
    # Room-level legacy fields to remove
    legacy_room_fields = [
        'lastOccupancy', 'occupancyChangedAt', 'lastCheck', 'lastSnapshot',
        'lastActivity', 'recentObservations'
    ]

    # Global legacy fields to remove
    legacy_global_fields = ['lastPoll', 'lastGlobalCheck']

    rooms = state.get("rooms", {})
    cleaned_count = 0

    for room_name, room_data in rooms.items():
        # Merge legacy observations into canonical if needed
        legacy_obs = room_data.get('recentObservations', [])
        canonical_obs = room_data.get('recent_observations', [])

        if legacy_obs and canonical_obs:
            # Merge, preferring non-pending observations
            existing_timestamps = {obs.get('timestamp') for obs in canonical_obs}
            for obs in legacy_obs:
                if obs.get('timestamp') not in existing_timestamps:
                    if not obs.get('pending'):  # Skip stale pending observations
                        canonical_obs.append(obs)

            # Sort and trim
            canonical_obs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            room_data['recent_observations'] = canonical_obs[:5]

        # Clean up legacy fields
        for field in legacy_room_fields:
            if field in room_data:
                del room_data[field]
                cleaned_count += 1

    # Clean up global legacy fields
    for field in legacy_global_fields:
        if field in state:
            del state[field]
            cleaned_count += 1

    return state, cleaned_count


def migrate(state_file: Path, backup: bool = True, dry_run: bool = False, clean: bool = False):
    """
    Run migration.

    Args:
        state_file: Path to state.json
        backup: Whether to create backup before migration
        dry_run: If True, don't actually write new state
        clean: If True, clean up legacy fields from v2 state
    """
    if not state_file.exists():
        print(f"✗ State file not found: {state_file}")
        sys.exit(1)

    # Load old state
    print(f"Reading state from: {state_file}")
    with open(state_file, 'r') as f:
        old_state = json.load(f)

    # Check if already migrated
    if old_state.get('schema_version') == 2:
        if clean:
            print("Schema is v2, cleaning up legacy fields...")
            if backup:
                backup_path = backup_state(state_file)
            else:
                backup_path = None
                print("! Skipping backup (--no-backup specified)")

            new_state, cleaned_count = cleanup_legacy_fields(old_state)

            if cleaned_count == 0:
                print("✓ No legacy fields found. State is clean.")
                sys.exit(0)

            print(f"  Cleaned up {cleaned_count} legacy field(s)")

            if dry_run:
                print("\n! Dry run mode - not writing changes")
                print("\nNew state would be:")
                print(json.dumps(new_state, indent=2, default=str))
                return

            # Write cleaned state
            print(f"\nWriting cleaned state to: {state_file}")
            new_state['last_updated'] = datetime.now().isoformat()
            with open(state_file, 'w') as f:
                json.dump(new_state, f, indent=2, default=str)

            print("✓ Cleanup complete!")
            if backup_path:
                print(f"\nBackup location: {backup_path}")
            return

        print("✓ State is already at schema v2. Use --clean to remove legacy fields.")
        sys.exit(0)

    # Create backup
    if backup:
        backup_path = backup_state(state_file)
    else:
        print("! Skipping backup (--no-backup specified)")

    # Migrate
    print("\nMigrating schema v1 → v2...")
    new_state = migrate_v1_to_v2(old_state)

    # Show summary
    old_room_count = len(old_state.get("rooms", {}))
    new_room_count = len(new_state["rooms"])

    print(f"\nMigration summary:")
    print(f"  Rooms: {old_room_count} → {new_room_count}")
    print(f"  Schema version: {old_state.get('schema_version', 1)} → {new_state['schema_version']}")

    # Count observations
    total_obs = sum(len(r.get("recent_observations", [])) for r in new_state["rooms"].values())
    print(f"  Observations preserved: {total_obs}")

    if dry_run:
        print("\n! Dry run mode - not writing changes")
        print("\nNew state would be:")
        print(json.dumps(new_state, indent=2, default=str))
        return

    # Write new state
    print(f"\nWriting new state to: {state_file}")
    with open(state_file, 'w') as f:
        json.dump(new_state, f, indent=2, default=str)

    print("✓ Migration complete!")
    print(f"\nBackup location: {backup_path if backup else 'N/A'}")
    print("\nNext steps:")
    print("  1. Review the new state.json")
    print("  2. Test with: python3 scripts/jarvis.py status")
    print("  3. If issues, restore with: cp state.v1.backup.*.json state.json")


def main():
    parser = argparse.ArgumentParser(description="Migrate Jarvis state to schema v2")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=STATE_FILE,
        help="Path to state.json (default: %(default)s)"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating backup before migration"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean up legacy v1 fields from v2 state"
    )

    args = parser.parse_args()

    print("=== Jarvis State Migration ===\n")
    migrate(
        state_file=args.state_file,
        backup=not args.no_backup,
        dry_run=args.dry_run,
        clean=args.clean
    )


if __name__ == "__main__":
    main()
