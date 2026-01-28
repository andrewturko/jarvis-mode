# State Accuracy Fix

**Issue**: UI monitoring status was inaccurate - showing stale "Last check: Never" and outdated occupancy status

**Root Cause**: The `context` command (used by Clawdbot) was checking motion sensors and analyzing rooms but **never updating state.json** with current occupancy or check timestamps.

## The Problem

### What Was Happening

1. **`poll` command** (scheduled checks) ✅ correctly updated state via [occupancy_service.py:251](scripts/services/occupancy_service.py#L251)
2. **`context` command** (Clawdbot/manual checks) ❌ got motion state but never wrote it to state.json
3. **UI read from state.json** → showed stale data
4. **Result**: UI displayed "Last check: Never" even after recent checks

### Flow Before Fix

```
User/Clawdbot → context kitchen --manual
    ↓
Get motion sensor state (person_detected = false)
    ↓
Build context payload
    ↓
Log decision
    ↓
[state.json NEVER updated!]
    ↓
UI reads state.json → sees old data
```

## The Fix

### Changes Made

#### 1. Update State in `context` Command

[scripts/jarvis.py:295-306](scripts/jarvis.py#L295-L306)

```python
# Get motion state
person_detected = self.ha_service.is_motion_detected(camera_config.motion_sensor) if camera_config.motion_sensor else None

# Update state with current occupancy (for accurate UI monitoring)
if person_detected is not None:
    self.state_manager.update_occupancy(room, person_detected)

# Update last check timestamp for this room
self.state_manager.update_room(room, {
    "last_check": now.isoformat()
})
```

**Result**: Every context check now updates:
- `rooms.{room}.occupancy.current` - Current occupancy state
- `rooms.{room}.occupancy.changed_at` - When occupancy last changed (if transition)
- `rooms.{room}.last_check` - Timestamp of this check

#### 2. Update State in `verify-empty` Command

[scripts/jarvis.py:488-499](scripts/jarvis.py#L488-L499)

```python
# Update state with current occupancy
if person_detected is not None:
    self.state_manager.update_occupancy(room, person_detected)

# Update last check timestamp
self.state_manager.update_room(room, {
    "last_check": datetime.now().isoformat()
})
```

#### 3. Fixed Module-Level Functions for jarvis_server.py

[scripts/jarvis.py:718-835](scripts/jarvis.py#L718-L835)

Added backward-compatible module-level functions:
- `get_status()` - Properly reads state with schema v2
- `get_config()` - Legacy config access
- `should_check_room()` - Legacy room check logic
- `record_observation()` - Legacy observation recording

**Why**: jarvis_server.py (UI backend) imports these functions. After refactoring jarvis.py into a class-based CLI, these functions were missing, causing import errors.

## Flow After Fix

```
User/Clawdbot → context kitchen --manual
    ↓
Get motion sensor state (person_detected = false)
    ↓
✅ Update state.json with occupancy
✅ Update last_check timestamp
    ↓
Build context payload
    ↓
Log decision
    ↓
UI reads state.json → sees current data ✅
```

## Verification

### Test State Updates

```bash
# Check a room
python3 scripts/jarvis.py context kitchen --manual

# Verify state was updated
cat state.json | jq '.rooms.kitchen | {last_check, occupancy}'

# Expected output:
{
  "last_check": "2026-01-27T22:42:56.975835",  # Current timestamp ✅
  "occupancy": {
    "current": false,                           # Current motion state ✅
    "changed_at": "2026-01-27T21:20:53.237074",
    "duration_minutes": 0
  }
}
```

### Test UI Status

```bash
# Get status via API
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from jarvis import get_status
import json
status = get_status()
print(json.dumps(status['roomStates']['kitchen'], indent=2))
"

# Expected output shows current data:
{
  "lastCheck": "2026-01-27T22:42:56.975835",      # Recent ✅
  "lastOccupancy": false,                          # Accurate ✅
  "occupancyChangedAt": "2026-01-27T21:20:53..."  # Tracked ✅
}
```

## UI Impact

### Before Fix
```
📷 Kitchen
Last check: Never
Status: Unknown
```

### After Fix
```
📷 Kitchen 🔴 Empty
Last check: 2m ago
Status: Changed 1h ago
```

## Benefits

1. **Accurate monitoring**: UI always shows current room status
2. **Real-time updates**: Every check (poll, context, manual) updates state
3. **Better UX**: Users can see when rooms were last checked
4. **Pattern learning**: Accurate occupancy history for pattern detection
5. **Decision quality**: State reflects reality for better context inference

## Technical Notes

### State Schema v2

All state updates use the v2 schema structure:

```json
{
  "rooms": {
    "kitchen": {
      "occupancy": {
        "current": false,           // Current state
        "changed_at": "ISO-8601",   // When it changed
        "duration_minutes": 0       // Time in current state
      },
      "last_check": "ISO-8601",     // Last check timestamp
      "recent_observations": [...],
      "last_context": {...}
    }
  }
}
```

### Thread Safety

All state updates use [StateManager.update_occupancy()](scripts/core/state_manager.py#L304) which:
- Acquires file lock (fcntl)
- Reads current state
- Updates occupancy
- Calculates duration
- Writes atomically (temp file + rename)
- Releases lock

## Files Modified

- [scripts/jarvis.py](scripts/jarvis.py) - Added state updates in `cmd_context()` and `cmd_verify_empty()`
- [scripts/jarvis.py](scripts/jarvis.py) - Added module-level functions for jarvis_server.py compatibility

## Related

- [scripts/services/occupancy_service.py](scripts/services/occupancy_service.py) - Already updates state correctly in `poll_occupancy()`
- [scripts/core/state_manager.py](scripts/core/state_manager.py) - Provides thread-safe state operations
- [ui/index.html](ui/index.html) - Displays state from API
- [scripts/jarvis_server.py](scripts/jarvis_server.py) - Serves UI and API

---

**Result**: UI monitoring status is now **accurate and real-time** for best UX! ✅
