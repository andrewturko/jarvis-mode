# Jarvis Mode

Proactive home intelligence for [Clawdbot](https://github.com/clawdbot/clawdbot). Observes cameras, tracks room occupancy, and offers contextual suggestions like a thoughtful home assistant.

## Features

- **Occupancy Tracking**: Polls person detection sensors to track room state
- **Transition Detection**: Detects when rooms become empty or occupied
- **Auto Actions**: Optionally turn off lights in empty rooms automatically
- **Smart Suggestions**: Context-aware suggestions based on time, activity, and home state
- **Cooldown Protection**: Rate limits vision API calls to prevent spam
- **Web UI**: Control panel for settings and manual checks

## Requirements

- [Clawdbot](https://github.com/clawdbot/clawdbot) installed and running
- Home Assistant with:
  - Camera entities (UniFi Protect, etc.)
  - Person detection sensors (UniFi AI, etc.)
  - Light entities
- Environment variables:
  - `HA_URL`: Home Assistant URL (e.g., `http://homeassistant.local:8123`)
  - `HA_TOKEN`: Long-lived access token

## Installation

1. Copy to your Clawdbot skills directory:
   ```bash
   cp -r jarvis-mode ~/clawd/skills/
   ```

2. Create config from example:
   ```bash
   cd ~/clawd/skills/jarvis-mode
   cp config.example.json config.json
   ```

3. Edit `config.json` with your camera entity IDs and person detection sensors

4. Refresh home inventory:
   ```bash
   python3 scripts/refresh-inventory.py
   ```

5. Start the UI server:
   ```bash
   ./serve-ui.sh
   # or: python3 scripts/jarvis_server.py
   ```

6. Add cron job for polling (via Clawdbot):
   ```
   Schedule: */5 * * * *
   Task: Run jarvis.py poll and handle transitions
   ```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `checkIntervalMinutes` | 5 | How often to poll room state |
| `cooldownMinutes` | 30 | Cooldown between vision API calls per room |
| `motionCooldownMinutes` | 10 | Shorter cooldown for motion-triggered checks |
| `motionAware` | true | Only check rooms with detected motion |
| `instantAlerts` | true | Trigger on person detection events |
| `quietMode` | true | Only message when actionable |
| `autoActions.enabled` | false | Automatically act (lights off, etc.) |
| `activeHours` | 7-23 | Hours when observation is active |

## CLI Commands

```bash
# Status
python3 scripts/jarvis.py status

# Enable/disable
python3 scripts/jarvis.py enable
python3 scripts/jarvis.py disable

# Poll for state changes
python3 scripts/jarvis.py poll

# Handle transitions
python3 scripts/jarvis.py handle-empty <room>
python3 scripts/jarvis.py handle-occupied <room>

# Manual snapshot (bypasses cooldown)
python3 scripts/jarvis.py snapshot <room> --manual

# Get full context
python3 scripts/jarvis.py context <room> --manual

# Check room lights
python3 scripts/jarvis.py room-lights <room>
```

## Web UI

Access at `http://localhost:8088` when server is running.

- Toggle Jarvis mode on/off
- View room occupancy state
- Manual room checks
- Adjust timing settings
- View recent observations

## Home Assistant Automation

See `ha_automation.yaml` for an example automation that triggers Jarvis on person detection.

## Files

| File | Description |
|------|-------------|
| `config.json` | User configuration (create from example) |
| `state.json` | Runtime state (auto-generated) |
| `home-inventory.json` | Device inventory (auto-generated) |
| `SKILL.md` | Clawdbot skill manifest |
| `ui/index.html` | Web control panel |
| `scripts/jarvis.py` | Main observation engine |
| `scripts/jarvis_server.py` | Web UI + webhook server |

## License

MIT
