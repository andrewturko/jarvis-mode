#!/usr/bin/env python3
"""
Jarvis Mode Server
- Serves the UI on /
- Handles webhooks for motion triggers
- Provides API for status/settings
"""

import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from jarvis import (
    get_status, get_config, save_json, is_enabled, is_active_hours,
    should_check_room, record_observation, CONFIG_FILE, SKILL_DIR,
    HA_URL, HA_TOKEN
)

PORT = int(os.environ.get('JARVIS_PORT', '8088'))
UI_DIR = SKILL_DIR / "ui"

# Telegram notification (via Clawdbot gateway)
# (GATEWAY_URL defined below near notify_clawdbot)


class JarvisHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)
    
    def log_message(self, format, *args):
        pass  # Suppress logging
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        # API endpoints
        if path == '/api/status':
            self.send_json(get_status())
            return

        if path == '/api/health':
            self.send_json({"status": "ok", "port": PORT})
            return

        # Voice service status
        if path == '/api/voice/status':
            self.send_json(get_voice_status())
            return

        if path == '/api/voice/cameras':
            self.send_json(get_voice_cameras())
            return
        
        # Serve static files (UI)
        super().do_GET()
    
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        # Read body
        content_length = int(self.headers.get('Content-Length', 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except:
                pass
        
        # Motion webhook: POST /webhook/motion/<room>
        # or POST /webhook/motion with {"room": "kitchen"}
        if path.startswith('/webhook/motion'):
            room = None
            parts = path.split('/')
            if len(parts) >= 4:
                room = parts[3]
            else:
                room = body.get('room')
            
            if not room:
                self.send_json({"error": "Missing room"}, 400)
                return
            
            result = handle_motion_trigger(room)
            self.send_json(result)
            return
        
        # Manual check: POST /api/check/<room> or /api/check (all rooms)
        if path.startswith('/api/check'):
            parts = path.split('/')
            room = parts[3] if len(parts) >= 4 else body.get('room', 'all')
            result = handle_manual_check(room)
            self.send_json(result)
            return
        
        # Toggle: POST /api/toggle
        if path == '/api/toggle':
            config = get_config()
            config['enabled'] = body.get('enabled', not config.get('enabled', False))
            save_json(CONFIG_FILE, config)
            self.send_json({"enabled": config['enabled']})
            return
        
        # Update setting: POST /api/setting
        if path == '/api/setting':
            key = body.get('key')
            value = body.get('value')
            if not key:
                self.send_json({"error": "Missing key"}, 400)
                return
            config = get_config()
            config[key] = value
            save_json(CONFIG_FILE, config)
            
            # Sync cron if check interval changed
            if key == 'checkIntervalMinutes':
                sync_result = sync_polling_cron(value)
                self.send_json({"updated": key, "value": value, "cronSync": sync_result})
                return
            
            self.send_json({"updated": key, "value": value})
            return
        
        # Refresh inventory: POST /api/refresh-inventory
        if path == '/api/refresh-inventory':
            result = refresh_home_inventory()
            self.send_json(result)
            return

        # Voice service control
        if path == '/api/voice/start':
            result = control_voice_service('start')
            self.send_json(result)
            return

        if path == '/api/voice/stop':
            result = control_voice_service('stop')
            self.send_json(result)
            return

        # Voice config update
        if path == '/api/voice/config':
            key = body.get('key')
            value = body.get('value')
            if not key:
                self.send_json({"error": "Missing key"}, 400)
                return
            result = update_voice_config(key, value)
            self.send_json(result)
            return

        self.send_json({"error": "Unknown endpoint"}, 404)


def get_voice_status():
    """Check if voice service is running via launchctl."""
    import subprocess

    LAUNCHD_LABEL = "com.jarvis.voice"

    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_LABEL],
            capture_output=True,
            text=True
        )
        running = result.returncode == 0

        # Get log file info
        log_path = "/tmp/jarvis-voice.log"
        error_log_path = "/tmp/jarvis-voice-error.log"

        logs = ""
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    logs = f.read()
            except:
                pass

        # Get wake word config
        wake_word_model = "hey_jarvis_v0.1"
        wake_threshold = 0.5
        try:
            voice_config_path = SKILL_DIR / "voice-config.json"
            if voice_config_path.exists():
                with open(voice_config_path) as f:
                    config = json.load(f)
                wake_word_model = config.get("wake_word", {}).get("model", "hey_jarvis_v0.1")
                wake_threshold = config.get("wake_word", {}).get("threshold", 0.5)
        except:
            pass

        return {
            "running": running,
            "label": LAUNCHD_LABEL,
            "logs": logs,
            "logPath": log_path,
            "errorLogPath": error_log_path,
            "wakeWord": wake_word_model,
            "wakeThreshold": wake_threshold
        }
    except Exception as e:
        return {
            "running": False,
            "error": str(e)
        }


def get_voice_cameras():
    """Get list of cameras enabled for voice commands."""
    try:
        voice_config_path = SKILL_DIR / "voice-config.json"
        if not voice_config_path.exists():
            return {"cameras": []}

        with open(voice_config_path) as f:
            config = json.load(f)

        cameras = []
        for room, cam_config in config.get('cameras', {}).items():
            if cam_config.get('enabled', False):
                cameras.append({
                    "room": room,
                    "name": cam_config.get('name', room),
                    "speaker": cam_config.get('speaker', room.replace('_', ' ').title())
                })

        return {"cameras": cameras}
    except Exception as e:
        return {"cameras": [], "error": str(e)}


def control_voice_service(action):
    """Start or stop the voice service via launchctl."""
    import subprocess

    LAUNCHD_LABEL = "com.jarvis.voice"

    try:
        # Use HOME env var instead of getlogin() - more reliable in LaunchAgent context
        home_dir = os.environ.get('HOME', os.path.expanduser('~'))
        plist_path = f"{home_dir}/Library/LaunchAgents/{LAUNCHD_LABEL}.plist"

        if action == 'start':
            cmd = ["launchctl", "load", "-w", plist_path]
            success_msg = "Voice service started"
        elif action == 'stop':
            cmd = ["launchctl", "unload", "-w", plist_path]
            success_msg = "Voice service stopped"
        else:
            return {"ok": False, "error": "Invalid action"}

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        # Determine success based on action
        if action == 'start':
            # For start: success if returncode 0, or if already loaded
            is_success = result.returncode == 0 or "already loaded" in result.stderr.lower()
        else:  # stop
            # For stop: success if returncode 0, or if service was not found (already stopped)
            is_success = result.returncode == 0 or "could not find" in result.stderr.lower()

        if is_success:
            return {"ok": True, "message": success_msg}
        else:
            return {"ok": False, "error": result.stderr or result.stdout or f"Failed to {action} voice service"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def update_voice_config(key, value):
    """Update voice configuration (wake word, threshold, etc.)."""
    try:
        voice_config_path = SKILL_DIR / "voice-config.json"

        if not voice_config_path.exists():
            return {"ok": False, "error": "Voice config file not found"}

        with open(voice_config_path) as f:
            config = json.load(f)

        # Update the config based on key
        if key == "wakeWord":
            if "wake_word" not in config:
                config["wake_word"] = {}
            config["wake_word"]["model"] = value
        elif key == "wakeThreshold":
            if "wake_word" not in config:
                config["wake_word"] = {}
            config["wake_word"]["threshold"] = value
        else:
            return {"ok": False, "error": f"Unknown key: {key}"}

        # Save updated config
        save_json(voice_config_path, config)

        # Check if service is running - if so, suggest restart
        import subprocess
        result = subprocess.run(
            ["launchctl", "list", "com.jarvis.voice"],
            capture_output=True,
            text=True
        )
        needs_restart = result.returncode == 0

        return {
            "ok": True,
            "updated": key,
            "value": value,
            "needsRestart": needs_restart,
            "message": f"Updated {key}. Restart voice service for changes to take effect." if needs_restart else f"Updated {key}"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def refresh_home_inventory():
    """Run the inventory refresh script."""
    import subprocess
    try:
        env = os.environ.copy()
        env["HA_URL"] = HA_URL
        env["HA_TOKEN"] = HA_TOKEN
        result = subprocess.run(
            ["python3", str(SKILL_DIR / "scripts" / "refresh-inventory.py")],
            capture_output=True,
            text=True,
            timeout=30,
            env=env
        )
        if result.returncode == 0:
            return {"ok": True, "message": "Inventory refreshed"}
        else:
            return {"ok": False, "error": result.stderr}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def handle_manual_check(room):
    """Handle manual check request from UI."""
    config = get_config()
    
    if room == 'all':
        rooms = list(config.get('cameras', {}).keys())
    else:
        rooms = [room]
    
    # Record pending observations so UI shows immediate feedback
    for r in rooms:
        record_observation(r, {
            "activity": "analyzing...",
            "summary": "🔄 Analysis in progress...",
            "pending": True
        })
    
    # Manual check bypasses cooldown and motion-aware settings
    for r in rooms:
        notify_clawdbot(r, manual=True)
    
    return {
        "checking": rooms,
        "message": f"Analysis requested for: {', '.join(rooms)}"
    }


def handle_motion_trigger(room):
    """Handle motion detection trigger from HA."""
    
    # Check if Jarvis is enabled
    if not is_enabled():
        return {"triggered": False, "reason": "jarvis disabled", "room": room}
    
    # Check instant alerts setting
    config = get_config()
    if not config.get('instantAlerts', False):
        return {"triggered": False, "reason": "instant alerts disabled", "room": room}
    
    # Check active hours
    if not is_active_hours():
        return {"triggered": False, "reason": "outside active hours", "room": room}
    
    # Check cooldown
    result = should_check_room(room, trigger="motion")
    if not result["should_check"]:
        return {"triggered": False, "reason": result["reason"], "room": room}
    
    # Motion triggered! Notify Clawdbot to do the analysis
    notify_clawdbot(room)
    
    return {
        "triggered": True,
        "room": room,
        "message": f"Motion in {room} - analysis requested"
    }


GATEWAY_URL = os.environ.get('CLAWDBOT_GATEWAY', 'http://127.0.0.1:18789')
GATEWAY_HOOK_TOKEN = os.environ.get('CLAWDBOT_HOOK_TOKEN', 'jarvis-motion-2026')
CRON_JOB_NAME = 'jarvis-poll'


def sync_polling_cron(interval_minutes):
    """Update the launchd poll interval to match config."""
    import subprocess

    try:
        # Call the sync script to update launchd plist
        result = subprocess.run(
            [f"{SKILL_DIR}/scripts/sync_poll_interval.sh"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return {"synced": True, "interval": interval_minutes}
        else:
            return {"synced": False, "error": result.stderr or "Failed to sync"}
    except Exception as e:
        return {"synced": False, "error": str(e)}


def notify_clawdbot(room, manual=False):
    """Call Clawdbot gateway webhook to trigger analysis."""
    import urllib.request
    
    hook_path = "/hooks/jarvis/check" if manual else "/hooks/jarvis/motion"
    
    try:
        data = json.dumps({"room": room}).encode()
        req = urllib.request.Request(
            f"{GATEWAY_URL}{hook_path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GATEWAY_HOOK_TOKEN}"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"Webhook triggered: {result}", file=sys.stderr)
            return result
    except Exception as e:
        print(f"Failed to notify Clawdbot: {e}", file=sys.stderr)
        return {"error": str(e)}


def run_server():
    server = HTTPServer(('0.0.0.0', PORT), JarvisHandler)
    print(f"Jarvis server running on http://localhost:{PORT}")
    print(f"  UI: http://localhost:{PORT}/")
    print(f"  API: http://localhost:{PORT}/api/status")
    print(f"  Webhook: POST http://localhost:{PORT}/webhook/motion/<room>")
    server.serve_forever()


if __name__ == '__main__':
    run_server()
