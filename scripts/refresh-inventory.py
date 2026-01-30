#!/usr/bin/env python3
"""
Refresh home-inventory.json with current HA entities.
Run periodically or when devices change.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from core.paths import SKILL_DIR, INVENTORY_FILE, CAPABILITIES_FILE

# Get HA config from environment or clawdbot config
def _get_ha_config():
    url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")
    
    if not url or not token:
        try:
            config_path = Path.home() / ".clawdbot" / "clawdbot.json"
            with open(config_path) as f:
                config = json.load(f)
                env_vars = config.get("env", {}).get("vars", {})
                url = url or env_vars.get("HA_URL", "http://homeassistant.local:8123")
                token = token or env_vars.get("HA_TOKEN", "")
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            print(f"Warning: Could not read clawdbot config: {e}", file=sys.stderr)
    
    return url or "http://homeassistant.local:8123", token or ""

HA_URL, HA_TOKEN = _get_ha_config()


def get_entities():
    """Fetch all entities from HA."""
    result = subprocess.run([
        "curl", "-s",
        f"{HA_URL}/api/states",
        "-H", f"Authorization: Bearer {HA_TOKEN}"
    ], capture_output=True, text=True)
    return json.loads(result.stdout)


def categorize_entities(entities):
    """Categorize entities by domain and area."""
    categories = {
        "lights": [],
        "media_players": [],
        "climate": [],
        "covers": [],
        "vacuum": [],
        "cameras": [],
        "sensors": {
            "motion": [],
            "person": [],
            "animal": [],
            "dark": []
        },
        "switches": [],
        "buttons": [],
        "other": {}  # domain -> [entity_ids] for any domain not above
    }

    for entity in entities:
        eid = entity.get("entity_id", "")
        if not eid:
            continue

        if eid.startswith("light."):
            categories["lights"].append(eid)
        elif eid.startswith("media_player."):
            categories["media_players"].append(eid)
        elif eid.startswith("climate."):
            categories["climate"].append(eid)
        elif eid.startswith("cover."):
            categories["covers"].append(eid)
        elif eid.startswith("vacuum."):
            categories["vacuum"].append(eid)
        elif eid.startswith("camera."):
            categories["cameras"].append(eid)
        elif eid.startswith("binary_sensor."):
            if "motion" in eid:
                categories["sensors"]["motion"].append(eid)
            elif "person" in eid:
                categories["sensors"]["person"].append(eid)
            elif "animal" in eid:
                categories["sensors"]["animal"].append(eid)
            elif "dark" in eid or "is_dark" in eid:
                categories["sensors"]["dark"].append(eid)
        elif eid.startswith("switch."):
            categories["switches"].append(eid)
        elif eid.startswith("button."):
            categories["buttons"].append(eid)
        else:
            domain = eid.split('.')[0]
            categories["other"].setdefault(domain, []).append(eid)

    return categories


def infer_room(entity_id, known_rooms):
    """Infer room from entity_id by matching known rooms or extracting from naming patterns."""
    name = entity_id.split('.', 1)[1] if '.' in entity_id else entity_id

    # Check against known rooms (longest first to avoid partial matches)
    for room in sorted(known_rooms, key=len, reverse=True):
        if room in name:
            return room

    # Pattern-based extraction for entity types with reliable naming
    if entity_id.startswith('camera.'):
        for suffix in ['_low_resolution_channel', '_high_resolution_channel', '_channel']:
            if name.endswith(suffix):
                return name[:-len(suffix)]

    if entity_id.startswith('cover.'):
        parts = name.split('_shade_')
        if len(parts) == 2:
            return parts[0]

    if entity_id.startswith('binary_sensor.'):
        for suffix in ['_motion', '_person_detected', '_animal_detected',
                        '_speaking_detected', '_is_dark']:
            if name.endswith(suffix):
                return name[:-len(suffix)]

    return None


def _collect_ids(obj, out):
    """Recursively collect all HA entity IDs (strings with a dot) from a nested structure."""
    if isinstance(obj, str) and '.' in obj:
        out.add(obj)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ids(item, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_ids(v, out)


def update_capabilities(categories):
    """Merge new HA entities into capabilities.json without overwriting existing data."""
    try:
        with open(CAPABILITIES_FILE) as f:
            caps = json.load(f)
    except FileNotFoundError:
        print("No capabilities.json found, skipping capabilities update")
        return
    except Exception as e:
        print(f"Error reading capabilities.json: {e}")
        return

    # Gather known room names from all sections
    known_rooms = set()
    for section_key, room_key in [
        ('lighting', 'rooms'), ('music', 'speakers'), ('shades', 'rooms'),
        ('cameras', 'indoor'), ('tv', 'devices')
    ]:
        rooms_dict = caps.get(section_key, {}).get(room_key, {})
        if isinstance(rooms_dict, dict):
            known_rooms.update(rooms_dict.keys())

    updates = []

    # --- Lighting ---
    lighting_rooms = caps.setdefault('lighting', {}).setdefault('rooms', {})
    existing_lights = {lid for lids in lighting_rooms.values() for lid in lids}

    for light_id in categories['lights']:
        if light_id in existing_lights:
            continue
        room = infer_room(light_id, known_rooms)
        if room:
            lighting_rooms.setdefault(room, [])
            if light_id not in lighting_rooms[room]:
                lighting_rooms[room].append(light_id)
                updates.append(f"  lighting/{room}: +{light_id}")

    # --- Cameras ---
    cameras_indoor = caps.setdefault('cameras', {}).setdefault('indoor', {})
    existing_cams = set(cameras_indoor.values())

    for cam_id in categories['cameras']:
        if cam_id in existing_cams:
            continue
        room = infer_room(cam_id, known_rooms)
        if room and room not in cameras_indoor:
            cameras_indoor[room] = cam_id
            updates.append(f"  cameras/{room}: +{cam_id}")
            known_rooms.add(room)

    # --- Shades ---
    shades_rooms = caps.setdefault('shades', {}).setdefault('rooms', {})
    existing_shades = {sid for sids in shades_rooms.values() for sid in sids}

    for cover_id in categories['covers']:
        if cover_id in existing_shades:
            continue
        room = infer_room(cover_id, known_rooms)
        if room:
            shades_rooms.setdefault(room, [])
            if cover_id not in shades_rooms[room]:
                shades_rooms[room].append(cover_id)
                updates.append(f"  shades/{room}: +{cover_id}")

    # --- Music speakers (only clearly speaker-like entities) ---
    speakers = caps.setdefault('music', {}).setdefault('speakers', {})
    existing_speaker_entities = set()
    for spk in speakers.values():
        if isinstance(spk, dict):
            existing_speaker_entities.add(spk.get('ha_entity', ''))
        elif isinstance(spk, str):
            existing_speaker_entities.add(spk)

    for mp_id in categories['media_players']:
        if mp_id in existing_speaker_entities:
            continue
        if 'speaker' in mp_id or 'sonos' in mp_id:
            room = infer_room(mp_id, known_rooms)
            if room and room not in speakers:
                speakers[room] = {"ha_entity": mp_id}
                updates.append(f"  music/speakers/{room}: +{mp_id}")

    # --- Climate ---
    thermostats = caps.setdefault('climate', {}).setdefault('thermostats', {})
    existing_climate = set(thermostats.values())

    for climate_id in categories['climate']:
        if climate_id in existing_climate:
            continue
        name = climate_id.split('.', 1)[1] if '.' in climate_id else climate_id
        if name not in thermostats:
            thermostats[name] = climate_id
            updates.append(f"  climate: +{climate_id}")

    # --- Vacuum ---
    vacuum_devices = caps.setdefault('vacuum', {}).setdefault('devices', {})
    existing_vacuums = set()
    for vac in vacuum_devices.values():
        if isinstance(vac, dict):
            existing_vacuums.add(vac.get('entity', ''))

    for vac_id in categories['vacuum']:
        if vac_id in existing_vacuums:
            continue
        name = vac_id.split('.', 1)[1] if '.' in vac_id else vac_id
        if name not in vacuum_devices:
            entry = {"entity": vac_id, "routines": {}}
            for btn_id in categories.get('buttons', []):
                if name in btn_id:
                    btn_name = btn_id.split('.', 1)[1].replace(f'{name}_', '')
                    entry['routines'][btn_name] = btn_id
            vacuum_devices[name] = entry
            updates.append(f"  vacuum: +{vac_id}")

    # --- Appliances (switches/buttons that share a device name, e.g. dishwasher) ---
    appliances = caps.setdefault('appliances', {})
    existing_appliance_entities = set()
    for app in appliances.values():
        if isinstance(app, dict):
            existing_appliance_entities.update(app.values())

    # Find switch/button pairs that share a common device name (excluding known devices like s8)
    known_device_prefixes = set()
    for vac in vacuum_devices.values():
        if isinstance(vac, dict):
            name = vac.get('entity', '').split('.', 1)[-1]
            if name:
                known_device_prefixes.add(name)

    appliance_candidates = {}
    for sw_id in categories.get('switches', []):
        if sw_id in existing_appliance_entities:
            continue
        name = sw_id.split('.', 1)[1] if '.' in sw_id else sw_id
        # Extract device name (e.g. "dishwasher" from "dishwasher_power")
        for suffix in ['_power', '_switch', '_toggle']:
            if name.endswith(suffix):
                device = name[:-len(suffix)]
                if device not in known_device_prefixes and device not in appliances:
                    appliance_candidates.setdefault(device, {})
                    appliance_candidates[device][suffix.lstrip('_')] = sw_id

    for btn_id in categories.get('buttons', []):
        if btn_id in existing_appliance_entities:
            continue
        name = btn_id.split('.', 1)[1] if '.' in btn_id else name
        for device in list(appliance_candidates.keys()):
            if name.startswith(device + '_'):
                action = name[len(device) + 1:]
                appliance_candidates[device][action] = btn_id

    for device, entities in appliance_candidates.items():
        if device not in appliances:
            appliances[device] = entities
            updates.append(f"  appliances/{device}: +{list(entities.values())}")

    # --- Auto-discover new device domains (fan, lock, humidifier, etc.) ---
    # Domains that are non-actionable infrastructure -- skip these
    skip_domains = {
        'sensor', 'binary_sensor', 'automation', 'person', 'zone', 'sun',
        'weather', 'input_boolean', 'input_number', 'input_select',
        'input_text', 'input_datetime', 'counter', 'timer', 'group',
        'script', 'device_tracker', 'update', 'number', 'select', 'text',
        'image', 'calendar', 'tts', 'stt', 'conversation', 'wake_word',
        'persistent_notification', 'event', 'tag',
    }

    for domain, entity_list in categories.get('other', {}).items():
        if domain in skip_domains:
            continue

        # Collect entity IDs already in this domain's section
        section = caps.get(domain, {})
        existing_in_section = set()
        _collect_ids(section, existing_in_section)

        new_entities = [e for e in entity_list if e not in existing_in_section]
        if not new_entities:
            continue

        # Create/update section, grouping by room where possible
        section = caps.setdefault(domain, {})
        for eid in new_entities:
            room = infer_room(eid, known_rooms)
            key = room or 'unassigned'
            if isinstance(section.get(key), list):
                if eid not in section[key]:
                    section[key].append(eid)
            else:
                section.setdefault(key, []).append(eid)
            updates.append(f"  {domain}/{key}: +{eid}")

    # Update timestamp
    caps['_last_updated'] = datetime.now().isoformat()

    with open(CAPABILITIES_FILE, 'w') as f:
        json.dump(caps, f, indent=2)
        f.write('\n')

    if updates:
        print(f"Updated capabilities.json ({len(updates)} changes):")
        for u in updates:
            print(u)
    else:
        print("capabilities.json: no new entities to add")


def main():
    """Main entry point."""
    print("Fetching entities from Home Assistant...")
    entities = get_entities()
    
    print(f"Found {len(entities)} entities")
    categories = categorize_entities(entities)
    
    # Load existing inventory
    try:
        with open(INVENTORY_FILE) as f:
            inventory = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        inventory = {"capabilities": {}}
    
    # Update entity lists
    inventory["lastUpdated"] = datetime.now().isoformat()
    inventory["entityCounts"] = {
        "lights": len(categories["lights"]),
        "media_players": len(categories["media_players"]),
        "climate": len(categories["climate"]),
        "covers": len(categories["covers"]),
        "vacuum": len(categories["vacuum"]),
        "cameras": len(categories["cameras"])
    }
    inventory["allEntities"] = categories
    
    # Save
    with open(INVENTORY_FILE, "w") as f:
        json.dump(inventory, f, indent=2)

    print(f"Updated {INVENTORY_FILE}")
    print(f"  Lights: {len(categories['lights'])}")
    print(f"  Media players: {len(categories['media_players'])}")
    print(f"  Covers: {len(categories['covers'])}")
    print(f"  Cameras: {len(categories['cameras'])}")

    # Merge new entities into capabilities.json (update, not overwrite)
    update_capabilities(categories)

    # Regenerate suggestions for new/changed capabilities
    from generate_suggestions import generate
    generate()


if __name__ == "__main__":
    main()
