#!/usr/bin/env python3
"""
Jarvis Mode - Observation Engine
Handles camera snapshots and state management for proactive home suggestions.
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
CONFIG_FILE = SKILL_DIR / "config.json"
STATE_FILE = SKILL_DIR / "state.json"
PATTERNS_FILE = SKILL_DIR / "patterns.json"
SNAPSHOT_DIR = SKILL_DIR / "snapshots"
HOOKS_FILE = SKILL_DIR / "hooks.json"
GATEWAY_URL = os.environ.get("CLAWDBOT_GATEWAY", "http://127.0.0.1:18789")

# Environment - try env vars first, then clawdbot config
def _get_ha_config():
    """Get HA config from environment or clawdbot config."""
    url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")
    
    if not url or not token:
        # Try reading from clawdbot config
        try:
            config_path = Path.home() / ".clawdbot" / "clawdbot.json"
            with open(config_path) as f:
                config = json.load(f)
                env_vars = config.get("env", {}).get("vars", {})
                url = url or env_vars.get("HA_URL", "http://homeassistant.local:8123")
                token = token or env_vars.get("HA_TOKEN", "")
        except:
            pass
    
    return url or "http://homeassistant.local:8123", token or ""

HA_URL, HA_TOKEN = _get_ha_config()


def load_json(path):
    """Load JSON file."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {path}: {e}", file=sys.stderr)
        return {}


def save_json(path, data):
    """Save JSON file."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_config():
    """Get current config."""
    return load_json(CONFIG_FILE)


def get_state():
    """Get current state."""
    return load_json(STATE_FILE)


def save_state(state):
    """Save state."""
    save_json(STATE_FILE, state)


def is_enabled():
    """Check if Jarvis mode is enabled."""
    config = get_config()
    return config.get("enabled", False)


def is_active_hours():
    """Check if current time is within active hours."""
    config = get_config()
    hours = config.get("activeHours", {"start": 7, "end": 23})
    start = hours.get("start", 7)
    end = hours.get("end", 23)
    
    # 0-0 or 0-24 means 24/7
    if (start == 0 and end == 0) or (start == 0 and end == 24):
        return True
    
    current_hour = datetime.now().hour
    return start <= current_hour < end


def should_check_room(room_name, trigger="scheduled"):
    """
    Determine if a room should be checked.
    
    Args:
        room_name: Room to check
        trigger: "scheduled" (interval) or "motion" (motion-triggered)
    
    Returns:
        dict with 'should_check', 'reason', and 'motion_state'
    """
    config = get_config()
    state = get_state()
    
    # Get cooldown based on trigger type
    if trigger == "motion":
        cooldown_minutes = config.get("motionCooldownMinutes", 10)
    else:
        cooldown_minutes = config.get("cooldownMinutes", 30)
    
    room_state = state.get("rooms", {}).get(room_name, {})
    last_check = room_state.get("lastCheck")
    
    # Check cooldown
    cooldown_ok = True
    minutes_remaining = 0
    if last_check:
        last_check_time = datetime.fromisoformat(last_check)
        elapsed = datetime.now() - last_check_time
        cooldown_ok = elapsed > timedelta(minutes=cooldown_minutes)
        if not cooldown_ok:
            minutes_remaining = cooldown_minutes - int(elapsed.total_seconds() / 60)
    
    if not cooldown_ok:
        return {
            "should_check": False,
            "reason": f"cooldown ({minutes_remaining}m remaining)",
            "motion_state": None
        }
    
    # Check motion if motion-aware is enabled (for scheduled checks)
    motion_aware = config.get("motionAware", True)
    motion_state = get_motion_state(room_name)
    
    if trigger == "scheduled" and motion_aware:
        if motion_state is False:  # Explicitly no motion (not None/unknown)
            return {
                "should_check": False,
                "reason": "no motion detected",
                "motion_state": motion_state
            }
    
    return {
        "should_check": True,
        "reason": "ready",
        "motion_state": motion_state
    }


def should_check_room_simple(room_name):
    """Simple boolean check for backward compatibility."""
    result = should_check_room(room_name)
    return result["should_check"]


def get_camera_snapshot(room_name, manual=False):
    """
    Get camera snapshot from Home Assistant.
    
    Args:
        room_name: Room to snapshot
        manual: If True, bypass cooldown (user-requested)
    
    Returns:
        Path to snapshot file, or None if failed/blocked by cooldown
    """
    config = get_config()
    state = get_state()
    camera_config = config.get("cameras", {}).get(room_name, {})
    entity_id = camera_config.get("entity_id")
    
    if not entity_id or not HA_TOKEN:
        return None
    
    # Check cooldown for automated requests
    if not manual:
        cooldown_minutes = config.get("cooldownMinutes", 30)
        room_state = state.get("rooms", {}).get(room_name, {})
        last_snapshot = room_state.get("lastSnapshot")
        
        if last_snapshot:
            last_time = datetime.fromisoformat(last_snapshot)
            elapsed = datetime.now() - last_time
            if elapsed < timedelta(minutes=cooldown_minutes):
                remaining = cooldown_minutes - int(elapsed.total_seconds() / 60)
                print(f"Snapshot blocked: {room_name} in cooldown ({remaining}m remaining)", file=sys.stderr)
                return None
    
    # Ensure snapshot directory exists
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    
    # Get snapshot from HA
    snapshot_path = SNAPSHOT_DIR / f"{room_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    
    try:
        result = subprocess.run([
            "curl", "-s", "-o", str(snapshot_path),
            f"{HA_URL}/api/camera_proxy/{entity_id}",
            "-H", f"Authorization: Bearer {HA_TOKEN}"
        ], capture_output=True, timeout=30)
        
        if snapshot_path.exists() and snapshot_path.stat().st_size > 1000:
            # Record snapshot time for cooldown tracking
            if "rooms" not in state:
                state["rooms"] = {}
            if room_name not in state["rooms"]:
                state["rooms"][room_name] = {}
            state["rooms"][room_name]["lastSnapshot"] = datetime.now().isoformat()
            save_state(state)
            return str(snapshot_path)
        else:
            snapshot_path.unlink(missing_ok=True)
            return None
    except Exception as e:
        print(f"Error getting snapshot for {room_name}: {e}", file=sys.stderr)
        return None


def get_motion_state(room_name):
    """Check if motion is detected in a room via HA sensor."""
    config = get_config()
    camera_config = config.get("cameras", {}).get(room_name, {})
    motion_sensor = camera_config.get("motionSensor")
    
    if not motion_sensor or not HA_TOKEN:
        return None
    
    try:
        result = subprocess.run([
            "curl", "-s",
            f"{HA_URL}/api/states/{motion_sensor}",
            "-H", f"Authorization: Bearer {HA_TOKEN}"
        ], capture_output=True, text=True, timeout=10)
        
        data = json.loads(result.stdout)
        return data.get("state") == "on"
    except Exception as e:
        print(f"Error checking motion for {room_name}: {e}", file=sys.stderr)
        return None


def get_home_state():
    """Get current home state (lights, music, etc.) from HA."""
    if not HA_TOKEN:
        return {}
    
    try:
        result = subprocess.run([
            "curl", "-s",
            f"{HA_URL}/api/states",
            "-H", f"Authorization: Bearer {HA_TOKEN}"
        ], capture_output=True, text=True, timeout=30)
        
        states = json.loads(result.stdout)
        
        # Extract relevant state
        home_state = {
            "lights_on": [],
            "lights_off": [],
            "media_playing": [],
            "climate": {}
        }
        
        for entity in states:
            eid = entity.get("entity_id", "")
            state = entity.get("state", "")
            
            if eid.startswith("light."):
                if state == "on":
                    home_state["lights_on"].append(eid)
                else:
                    home_state["lights_off"].append(eid)
            elif eid.startswith("media_player."):
                if state == "playing":
                    home_state["media_playing"].append(eid)
            elif eid.startswith("climate."):
                home_state["climate"][eid] = state
        
        return home_state
    except Exception as e:
        print(f"Error getting home state: {e}", file=sys.stderr)
        return {}


def record_observation(room_name, observation):
    """Record an observation for a room."""
    state = get_state()
    
    if "rooms" not in state:
        state["rooms"] = {}
    if room_name not in state["rooms"]:
        state["rooms"][room_name] = {"recentObservations": []}
    
    state["rooms"][room_name]["lastCheck"] = datetime.now().isoformat()
    state["rooms"][room_name]["lastActivity"] = observation.get("activity")
    
    # Get existing observations
    observations = state["rooms"][room_name].get("recentObservations", [])
    
    # If this is NOT a pending observation, remove any pending ones for this room first
    if not observation.get("pending"):
        observations = [o for o in observations if not o.get("pending")]
    
    # Add new observation
    observations.insert(0, {
        "timestamp": datetime.now().isoformat(),
        **observation
    })
    
    # Keep last 5 non-pending observations per room
    state["rooms"][room_name]["recentObservations"] = observations[:5]
    
    state["lastGlobalCheck"] = datetime.now().isoformat()
    save_state(state)


def set_enabled(enabled):
    """Enable or disable Jarvis mode."""
    config = get_config()
    config["enabled"] = enabled
    save_json(CONFIG_FILE, config)
    return config


def get_status():
    """Get full Jarvis status."""
    config = get_config()
    state = get_state()
    
    # Build room states with motion info
    room_states = {}
    all_observations = []
    for room in config.get("cameras", {}):
        room_data = state.get("rooms", {}).get(room, {})
        check_result = should_check_room(room)
        room_states[room] = {
            "lastCheck": room_data.get("lastCheck"),
            "lastActivity": room_data.get("lastActivity"),
            "shouldCheck": check_result["should_check"],
            "reason": check_result["reason"],
            "motionDetected": check_result["motion_state"],
            "lastOccupancy": room_data.get("lastOccupancy"),
            "occupancyChangedAt": room_data.get("occupancyChangedAt")
        }
        # Collect observations with room name
        for obs in room_data.get("recentObservations", []):
            all_observations.append({"room": room, **obs})
    
    # Sort all observations by timestamp descending
    all_observations.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return {
        "enabled": config.get("enabled", False),
        "activeHours": is_active_hours(),
        "activeHoursConfig": config.get("activeHours", {"start": 7, "end": 23}),
        "checkInterval": config.get("checkIntervalMinutes", 5),
        "cooldown": config.get("cooldownMinutes", 30),
        "motionCooldown": config.get("motionCooldownMinutes", 10),
        "motionAware": config.get("motionAware", True),
        "instantAlerts": config.get("instantAlerts", False),
        "quietMode": config.get("quietMode", True),
        "autoActions": config.get("autoActions", {"enabled": False}),
        "lastCheck": state.get("lastGlobalCheck"),
        "lastPoll": state.get("lastPoll"),
        "cameras": list(config.get("cameras", {}).keys()),
        "roomStates": room_states,
        "recentObservations": all_observations[:5]
    }


def get_room_lights(room_name):
    """Get lights that are on in a specific room."""
    if not HA_TOKEN:
        return []
    
    # Map room names to specific light entity IDs or patterns
    room_lights_map = {
        "kitchen": [
            "light.adaptive_phase_dimmer_counter",
        ],
        "living_room": [
            "light.lutron_leap_dimmer_floor_lamp",
            "light.philips_hue_light_os_3_3_0_floor_lamp",
            "light.lutron_leap_dimmer_table_lamp",
            "light.philips_hue_light_os_3_3_0_wall_wash",
        ],
        "dining": [
            "light.philips_hue_light_os_3_3_0_chandelier",
            "light.adaptive_phase_dimmer_entry",
        ],
    }
    
    # Get explicit list for this room, or fall back to pattern matching
    explicit_lights = room_lights_map.get(room_name, [])
    if explicit_lights:
        patterns = None  # Use explicit list
    else:
        patterns = [room_name.replace("_", "")]
    
    try:
        result = subprocess.run([
            "curl", "-s",
            f"{HA_URL}/api/states",
            "-H", f"Authorization: Bearer {HA_TOKEN}"
        ], capture_output=True, text=True, timeout=30)
        
        states = json.loads(result.stdout)
        lights_on = []
        
        for entity in states:
            eid = entity.get("entity_id", "")
            state = entity.get("state", "")
            
            if eid.startswith("light.") and state == "on":
                # Check explicit list first
                if explicit_lights:
                    if eid in explicit_lights:
                        lights_on.append(eid)
                # Fall back to pattern matching
                elif patterns:
                    eid_lower = eid.lower()
                    for pattern in patterns:
                        if pattern.lower() in eid_lower:
                            lights_on.append(eid)
                            break
        
        return lights_on
    except Exception as e:
        print(f"Error getting room lights: {e}", file=sys.stderr)
        return []


def turn_off_lights(entity_ids):
    """Turn off specified lights via HA."""
    if not HA_TOKEN or not entity_ids:
        return {"success": False, "reason": "no token or no lights"}
    
    results = []
    for eid in entity_ids:
        try:
            result = subprocess.run([
                "curl", "-s", "-X", "POST",
                f"{HA_URL}/api/services/light/turn_off",
                "-H", f"Authorization: Bearer {HA_TOKEN}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"entity_id": eid})
            ], capture_output=True, text=True, timeout=10)
            results.append({"entity_id": eid, "success": True})
        except Exception as e:
            results.append({"entity_id": eid, "success": False, "error": str(e)})
    
    return {"success": all(r["success"] for r in results), "results": results}


def verify_room_empty(room_name):
    """
    Take a snapshot of a room for visual verification of emptiness.
    Used to double-check motion sensor before suggesting lights-off.
    
    Returns:
        dict with snapshot_path (for vision analysis) and sensor_state
    """
    # Get current sensor state
    person_detected = get_motion_state(room_name)
    
    # Take a snapshot (respects cooldown - limits vision API calls)
    snapshot_path = get_camera_snapshot(room_name, manual=False)
    
    # Get lights that would be affected
    lights_on = get_room_lights(room_name)
    
    return {
        "room": room_name,
        "snapshot_path": snapshot_path,
        "sensor_says_empty": person_detected == False,
        "lights_on": lights_on,
        "verification_prompt": f"Is this room actually empty? Check if there are any people visible. The motion sensor says {'no one is there' if person_detected == False else 'someone might be there'}."
    }


def handle_empty_room(room_name, dry_run=False, skip_verify=False):
    """
    Handle a room that just became empty.
    If autoActions enabled, turn off lights.
    Returns action taken or suggested.
    
    Args:
        skip_verify: If True, skip verification (already verified by agent)
    """
    config = get_config()
    auto_actions = config.get("autoActions", {})
    
    # Get lights that are on in this room
    lights_on = get_room_lights(room_name)
    
    if not lights_on:
        return {
            "room": room_name,
            "action": "none",
            "reason": "no lights on"
        }
    
    if auto_actions.get("enabled") and not dry_run:
        # Actually turn off the lights
        result = turn_off_lights(lights_on)
        return {
            "room": room_name,
            "action": "turned_off_lights",
            "lights": lights_on,
            "success": result["success"],
            "announce": auto_actions.get("announceActions", True)
        }
    else:
        # Just suggest
        return {
            "room": room_name,
            "action": "suggest",
            "suggestion": f"Lights are on in empty {room_name.replace('_', ' ')}: {', '.join(lights_on)}",
            "lights": lights_on
        }


def handle_occupied_room(room_name):
    """
    Handle a room that just became occupied.
    Check context and return potential suggestions.
    """
    config = get_config()
    hour = datetime.now().hour
    
    # Get current state
    lights_on = get_room_lights(room_name)
    home_state = get_home_state()
    
    suggestions = []
    
    # Check if lights should be on
    is_dark_time = hour < 7 or hour >= 18  # Before 7am or after 6pm
    if is_dark_time and not lights_on:
        suggestions.append({
            "type": "lighting",
            "message": f"It's dark - want me to turn on the {room_name.replace('_', ' ')} lights?"
        })
    
    # Check if music is playing anywhere
    music_playing = len(home_state.get("media_playing", [])) > 0
    
    # Morning routine suggestions
    if 6 <= hour <= 9 and not music_playing:
        suggestions.append({
            "type": "music",
            "message": "Good morning! Want some background music?"
        })
    
    # Evening suggestions
    if 17 <= hour <= 20 and not music_playing:
        suggestions.append({
            "type": "music", 
            "message": "Evening wind-down. Some chill music?"
        })
    
    return {
        "room": room_name,
        "hour": hour,
        "timeOfDay": "morning" if 6 <= hour < 10 else "daytime" if 10 <= hour < 17 else "evening" if 17 <= hour < 22 else "night",
        "lightsOn": lights_on,
        "musicPlaying": music_playing,
        "suggestions": suggestions,
        "hasSuggestions": len(suggestions) > 0
    }


def setup_jarvis():
    """
    Self-register Jarvis hooks and cron job with Clawdbot.
    - Hooks: patches ~/.clawdbot/clawdbot.json directly
    - Cron: uses clawdbot cron CLI
    """
    results = {
        "success": True,
        "hooks": {"registered": False, "error": None},
        "cron": {"registered": False, "error": None}
    }
    
    # Load hooks definition
    try:
        with open(HOOKS_FILE) as f:
            hooks_def = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to load hooks.json: {e}"}
    
    # Get user config for channel/destination
    config = get_config()
    notify_channel = config.get("notifyChannel", "telegram")
    
    # Update hooks with channel from config
    for mapping in hooks_def.get("hooks", {}).get("mappings", []):
        mapping["channel"] = notify_channel
    
    # Patch hooks directly into clawdbot.json
    clawdbot_config_path = Path.home() / ".clawdbot" / "clawdbot.json"
    try:
        with open(clawdbot_config_path) as f:
            clawdbot_config = json.load(f)
        
        # Ensure hooks section exists
        if "hooks" not in clawdbot_config:
            clawdbot_config["hooks"] = {"enabled": True, "mappings": []}
        
        clawdbot_config["hooks"]["enabled"] = True
        
        # Get existing mappings
        existing_mappings = clawdbot_config["hooks"].get("mappings", [])
        existing_ids = {m.get("id") for m in existing_mappings}
        
        # Add/update our hooks
        for new_mapping in hooks_def["hooks"]["mappings"]:
            if new_mapping["id"] in existing_ids:
                # Update existing
                for i, m in enumerate(existing_mappings):
                    if m.get("id") == new_mapping["id"]:
                        existing_mappings[i] = new_mapping
                        break
            else:
                # Add new
                existing_mappings.append(new_mapping)
        
        clawdbot_config["hooks"]["mappings"] = existing_mappings
        
        # Write back
        with open(clawdbot_config_path, 'w') as f:
            json.dump(clawdbot_config, f, indent=2)
        
        results["hooks"]["registered"] = True
        results["hooks"]["note"] = "Restart gateway to apply hooks"
        
    except FileNotFoundError:
        results["hooks"]["error"] = f"Clawdbot config not found at {clawdbot_config_path}"
        results["success"] = False
    except Exception as e:
        results["hooks"]["error"] = str(e)
        results["success"] = False
    
    # Register cron job via clawdbot cron CLI
    cron_def = hooks_def.get("cron")
    if cron_def:
        try:
            # Check if cron already exists
            result = subprocess.run(
                ["clawdbot", "cron", "list", "--json"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            existing_job = None
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    for job in data.get("jobs", []):
                        if job.get("name") == cron_def["name"]:
                            existing_job = job
                            break
                except:
                    pass
            
            # Build CLI args
            cron_expr = cron_def["schedule"].get("expr", "*/5 * * * *")
            payload_kind = cron_def["payload"].get("kind", "systemEvent")

            if existing_job:
                # Remove and re-add (simpler than edit)
                subprocess.run(
                    ["clawdbot", "cron", "rm", existing_job["id"]],
                    capture_output=True,
                    timeout=10
                )

            # Build command based on payload type
            cmd = [
                "clawdbot", "cron", "add",
                "--name", cron_def["name"],
                "--cron", cron_expr,
            ]

            # Add agent if specified
            if "agentId" in cron_def:
                cmd.extend(["--agent", cron_def["agentId"]])

            # Add delivery options if specified
            if cron_def.get("deliver"):
                cmd.append("--deliver")
            if "channel" in cron_def:
                cmd.extend(["--channel", cron_def["channel"]])

            # Add payload
            if payload_kind == "agentTurn":
                cmd.extend(["--message", cron_def["payload"].get("message", "")])
            else:  # systemEvent
                wake_mode = cron_def.get("wakeMode", "now")
                cmd.extend(["--wake", wake_mode])
                cmd.extend(["--system-event", cron_def["payload"].get("text", "")])

            cmd.append("--json")

            # Add job
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                results["cron"]["registered"] = True
                results["cron"]["action"] = "updated" if existing_job else "created"
            else:
                results["cron"]["error"] = result.stderr or result.stdout or "Failed"
                results["success"] = False
                
        except FileNotFoundError:
            results["cron"]["error"] = "clawdbot CLI not found - is Clawdbot installed?"
            results["success"] = False
        except Exception as e:
            results["cron"]["error"] = str(e)
            results["success"] = False
    
    return results


def poll_occupancy():
    """
    Poll all rooms for occupancy changes.
    Returns transitions (room became empty, room became occupied).
    """
    config = get_config()
    state = get_state()
    
    if "rooms" not in state:
        state["rooms"] = {}
    
    transitions = []
    current_occupancy = {}
    
    for room in config.get("cameras", {}):
        if room not in state["rooms"]:
            state["rooms"][room] = {}
        
        # Get current person detection state
        person_detected = get_motion_state(room)  # Uses person detection sensor
        current_occupancy[room] = person_detected
        
        # Get last known state
        last_occupancy = state["rooms"][room].get("lastOccupancy")
        
        # Detect transitions
        if last_occupancy is not None and person_detected is not None:
            if last_occupancy and not person_detected:
                # Room became empty
                transitions.append({
                    "room": room,
                    "transition": "emptied",
                    "previous": "occupied",
                    "current": "empty",
                    "timestamp": datetime.now().isoformat()
                })
                state["rooms"][room]["occupancyChangedAt"] = datetime.now().isoformat()
            elif not last_occupancy and person_detected:
                # Person arrived
                transitions.append({
                    "room": room,
                    "transition": "occupied",
                    "previous": "empty",
                    "current": "occupied",
                    "timestamp": datetime.now().isoformat()
                })
                state["rooms"][room]["occupancyChangedAt"] = datetime.now().isoformat()
        
        # Update last known state
        if person_detected is not None:
            state["rooms"][room]["lastOccupancy"] = person_detected
    
    state["lastPoll"] = datetime.now().isoformat()
    save_state(state)
    
    return {
        "polled": True,
        "timestamp": datetime.now().isoformat(),
        "currentOccupancy": current_occupancy,
        "transitions": transitions,
        "hasTransitions": len(transitions) > 0
    }


def main():
    """CLI interface."""
    if len(sys.argv) < 2:
        print("Usage: jarvis.py <command> [args]")
        print("Commands: status, enable, disable, snapshot <room>, check, motion <room>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "status":
        print(json.dumps(get_status(), indent=2, default=str))
    
    elif cmd == "enable":
        config = set_enabled(True)
        print(json.dumps({"enabled": True, "message": "Jarvis mode enabled"}))
    
    elif cmd == "disable":
        config = set_enabled(False)
        print(json.dumps({"enabled": False, "message": "Jarvis mode disabled"}))
    
    elif cmd == "set":
        # Set a config value: jarvis.py set <key> <value>
        if len(sys.argv) < 4:
            print("Usage: jarvis.py set <key> <value>", file=sys.stderr)
            print("Keys: motionAware, instantAlerts, checkIntervalMinutes, cooldownMinutes, motionCooldownMinutes")
            sys.exit(1)
        key = sys.argv[2]
        value = sys.argv[3]
        
        config = get_config()
        # Parse value type
        if value.lower() in ("true", "false"):
            value = value.lower() == "true"
        elif value.isdigit():
            value = int(value)
        
        config[key] = value
        save_json(CONFIG_FILE, config)
        print(json.dumps({"set": key, "value": value}))
    
    elif cmd == "snapshot":
        if len(sys.argv) < 3:
            print("Usage: jarvis.py snapshot <room> [--manual]", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        manual = "--manual" in sys.argv
        path = get_camera_snapshot(room, manual=manual)
        if path:
            print(json.dumps({"room": room, "snapshot": path, "manual": manual}))
        else:
            print(json.dumps({"room": room, "error": "Failed or blocked by cooldown", "manual": manual}))
            sys.exit(1)
    
    elif cmd == "motion":
        if len(sys.argv) < 3:
            print("Usage: jarvis.py motion <room>", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        motion = get_motion_state(room)
        print(json.dumps({"room": room, "motion": motion}))
    
    elif cmd == "home-state":
        print(json.dumps(get_home_state(), indent=2))
    
    elif cmd == "context":
        # Get full context for a room: snapshot + home state + config + person detection
        if len(sys.argv) < 3:
            print("Usage: jarvis.py context <room> [--manual]", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        manual = "--manual" in sys.argv
        snapshot = get_camera_snapshot(room, manual=manual)
        home_state = get_home_state()
        config = get_config()
        hour = datetime.now().hour
        
        # Check person detection for this room
        person_sensor = f"binary_sensor.{room}_person_detected"
        person_detected = None
        try:
            result = subprocess.run([
                "curl", "-s",
                f"{HA_URL}/api/states/{person_sensor}",
                "-H", f"Authorization: Bearer {HA_TOKEN}"
            ], capture_output=True, text=True, timeout=10)
            data = json.loads(result.stdout)
            person_detected = data.get("state") == "on"
        except:
            pass
        
        # Get room-specific lights
        room_lights = [l for l in home_state.get("lights_on", []) if room.replace("_", "") in l.replace("_", "").lower() or room.split("_")[0] in l.lower()]
        
        print(json.dumps({
            "room": room,
            "snapshot": snapshot,
            "time": datetime.now().strftime("%I:%M %p"),
            "hour": hour,
            "timeOfDay": "morning" if 6 <= hour < 10 else "daytime" if 10 <= hour < 17 else "evening" if 17 <= hour < 22 else "night",
            "personDetected": person_detected,
            "roomEmpty": person_detected == False,
            "quietMode": config.get("quietMode", True),
            "autoActions": config.get("autoActions", {}),
            "homeState": {
                "lightsOn": len(home_state.get("lights_on", [])),
                "lightsOff": len(home_state.get("lights_off", [])),
                "roomLightsOn": room_lights,
                "musicPlaying": len(home_state.get("media_playing", [])) > 0,
                "mediaPlayers": home_state.get("media_playing", [])
            }
        }, indent=2))
    
    elif cmd == "check":
        # Output rooms that should be checked
        config = get_config()
        trigger = sys.argv[2] if len(sys.argv) > 2 else "scheduled"
        rooms_info = []
        rooms_to_check = []
        for room in config.get("cameras", {}):
            result = should_check_room(room, trigger)
            rooms_info.append({
                "room": room,
                **result
            })
            if result["should_check"]:
                rooms_to_check.append(room)
        print(json.dumps({
            "enabled": is_enabled(),
            "activeHours": is_active_hours(),
            "trigger": trigger,
            "roomsToCheck": rooms_to_check,
            "roomDetails": rooms_info
        }, indent=2))
    
    elif cmd == "triggers":
        # Get and clear pending triggers
        triggers_file = SKILL_DIR / "triggers.json"
        try:
            with open(triggers_file, "r") as f:
                data = json.load(f)
        except:
            data = {"pending": []}
        
        pending = data.get("pending", [])
        
        # Clear triggers
        if len(sys.argv) > 2 and sys.argv[2] == "--clear":
            data["pending"] = []
            with open(triggers_file, "w") as f:
                json.dump(data, f, indent=2)
        
        print(json.dumps({"pending": pending}, indent=2))
    
    elif cmd == "motion-trigger":
        # Called by HA automation when motion detected
        if len(sys.argv) < 3:
            print("Usage: jarvis.py motion-trigger <room>", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        config = get_config()
        
        if not is_enabled():
            print(json.dumps({"triggered": False, "reason": "jarvis disabled"}))
            sys.exit(0)
        
        if not config.get("instantAlerts", False):
            print(json.dumps({"triggered": False, "reason": "instant alerts disabled"}))
            sys.exit(0)
        
        if not is_active_hours():
            print(json.dumps({"triggered": False, "reason": "outside active hours"}))
            sys.exit(0)
        
        result = should_check_room(room, trigger="motion")
        print(json.dumps({
            "triggered": result["should_check"],
            "room": room,
            "reason": result["reason"]
        }))
    
    elif cmd == "record":
        if len(sys.argv) < 4:
            print("Usage: jarvis.py record <room> <observation_json>", file=sys.stderr)
            print("   or: jarvis.py record <room> <activity> <summary>", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        # Try JSON first, fall back to activity/summary args
        try:
            observation = json.loads(sys.argv[3])
        except (json.JSONDecodeError, ValueError):
            # Treat as activity string, optional summary
            activity = sys.argv[3]
            summary = sys.argv[4] if len(sys.argv) > 4 else activity
            observation = {"activity": activity, "summary": summary}
        record_observation(room, observation)
        print(json.dumps({"recorded": True, "room": room}))
    
    elif cmd == "cleanup":
        # Delete all snapshots (call after analysis)
        deleted = 0
        for f in SNAPSHOT_DIR.glob("*.jpg"):
            try:
                f.unlink()
                deleted += 1
            except:
                pass
        print(json.dumps({"deleted": deleted}))
    
    elif cmd == "poll":
        # Poll occupancy states and detect transitions
        if not is_enabled():
            print(json.dumps({"polled": False, "reason": "jarvis disabled"}))
            sys.exit(0)
        
        if not is_active_hours():
            print(json.dumps({"polled": False, "reason": "outside active hours"}))
            sys.exit(0)
        
        result = poll_occupancy()
        print(json.dumps(result, indent=2))
    
    elif cmd == "occupancy":
        # Get current occupancy for all rooms (no state change)
        config = get_config()
        occupancy = {}
        for room in config.get("cameras", {}):
            occupancy[room] = get_motion_state(room)
        print(json.dumps({"occupancy": occupancy}))
    
    elif cmd == "setup":
        # Self-register hooks and cron with Clawdbot
        result = setup_jarvis()
        print(json.dumps(result, indent=2))
        if not result.get("success"):
            sys.exit(1)
    
    elif cmd == "verify-empty":
        # Take snapshot for visual verification before suggesting lights-off
        if len(sys.argv) < 3:
            print("Usage: jarvis.py verify-empty <room>", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        result = verify_room_empty(room)
        print(json.dumps(result, indent=2))
    
    elif cmd == "handle-empty":
        # Handle a room that became empty (call after verify-empty confirms)
        if len(sys.argv) < 3:
            print("Usage: jarvis.py handle-empty <room> [--dry-run] [--verified]", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        dry_run = "--dry-run" in sys.argv
        skip_verify = "--verified" in sys.argv
        result = handle_empty_room(room, dry_run=dry_run, skip_verify=skip_verify)
        print(json.dumps(result, indent=2))
    
    elif cmd == "handle-occupied":
        # Handle a room that became occupied
        if len(sys.argv) < 3:
            print("Usage: jarvis.py handle-occupied <room>", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        result = handle_occupied_room(room)
        print(json.dumps(result, indent=2))
    
    elif cmd == "room-lights":
        # Get lights that are on in a room
        if len(sys.argv) < 3:
            print("Usage: jarvis.py room-lights <room>", file=sys.stderr)
            sys.exit(1)
        room = sys.argv[2]
        lights = get_room_lights(room)
        print(json.dumps({"room": room, "lights_on": lights}))
    
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
