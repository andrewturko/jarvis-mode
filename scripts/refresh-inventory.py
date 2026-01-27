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

SKILL_DIR = Path(__file__).parent.parent
INVENTORY_FILE = SKILL_DIR / "home-inventory.json"

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
        except:
            pass
    
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
        "buttons": []
    }
    
    for entity in entities:
        eid = entity.get("entity_id", "")
        
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
    
    return categories


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
    except:
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


if __name__ == "__main__":
    main()
