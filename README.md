# Jarvis Mode

*"Good evening, sir. I've noticed you've settled into the living room. Shall I dim the lights and queue up some evening jazz?"*

Jarvis Mode transforms [Clawdbot](https://github.com/clawdbot/clawdbot) into a proactive home intelligence system — observing, anticipating, and suggesting like Tony Stark's AI companion. It doesn't wait to be asked; it notices what's happening and offers help at exactly the right moment.

## The Vision

Most smart home setups are reactive: you ask, they do. Jarvis Mode flips that. It:

- **Observes** your home through cameras and sensors
- **Understands** context — time of day, who's home, what's happening
- **Anticipates** needs before you voice them
- **Acts** (or suggests) with the discretion of a thoughtful assistant

The goal isn't automation for automation's sake. It's having an AI that genuinely *gets it* — knows when to speak up, when to act quietly, and when to stay out of the way.

## Features

### Occupancy Intelligence
- **Real-time tracking**: Knows which rooms are occupied via person detection
- **Transition detection**: Notices when you enter or leave a room
- **Presence patterns**: Learns your routines over time
- **Multi-person awareness**: Distinguishes between household members

### Contextual Suggestions
- **Lighting**: *"Getting dark outside — want me to bring up the lights?"*
- **Music**: *"Quiet evening. Some background jazz?"*
- **Climate**: *"It's warming up. Cool it down a few degrees?"*
- **Media**: *"Settling in on the couch. Apple TV?"*
- **Shades**: *"Morning sun's coming in. Open the bedroom shades?"*

### Auto Actions (Optional)
When enabled, Jarvis acts without asking:
- Turns off lights in empty rooms
- Pauses music when everyone leaves
- Adjusts climate based on occupancy
- Announces actions so you know what happened

### Web Control Panel
Full-featured UI for configuration and monitoring:
- **Master toggle**: Enable/disable with one tap
- **Room status**: Live occupancy state for each camera
- **Manual checks**: Trigger analysis on demand
- **Timing controls**: Adjust intervals, cooldowns, active hours
- **Detection modes**: Toggle motion-aware, instant alerts, quiet mode
- **Recent activity**: See what Jarvis has observed

### Smart Rate Limiting
- **Cooldown protection**: Prevents API spam on vision calls
- **Motion gating**: Only analyzes rooms with activity
- **Active hours**: Respects quiet time (configurable)
- **Manual override**: Your requests always bypass cooldown

### Deep Integration
- **Home Assistant**: Full entity control (lights, climate, media, shades)
- **UniFi Protect**: Camera snapshots + person detection
- **Control4**: AV routing and whole-home audio
- **Sonos**: Music control and favorites
- **Any Clawdbot channel**: Telegram, Discord, iMessage, etc.

## Example Interactions

### Evening Wind-Down
```
[7:23 PM] Jarvis detects: Andrew in living room, lights at 100%

"You're settling in for the evening but these lights are still 
on full blast. Movie mode?"

> Yeah, that'd be great

"Done. Dimmed to 20% and queued up Chill House Radio."
```

### Morning Routine
```
[7:15 AM] Jarvis detects: Motion in kitchen, no lights, no music

"Good morning. It's 58° outside — bit crisp. Coffee weather. 
Want me to open the shades and put on some morning jazz?"
```

### Empty Room Cleanup
```
[Auto mode enabled]

[9:45 PM] Jarvis detects: Kitchen emptied, lights still on

*Turns off kitchen lights*

"Turned off the kitchen lights — looked like you were done in there."
```

### Working From Home
```
[2:30 PM] Jarvis detects: Andrew at desk in dining room, focused

*Stays quiet — recognizes focused work*

[5:45 PM] Same position, 3+ hours later

"You've been heads-down for a while. Good stopping point soon? 
I can start the evening playlist when you're ready."
```

### Leaving Home
```
[Detects all rooms empty]

"Looks like everyone's out. Want me to:
• Set the thermostat to away mode
• Turn off all lights  
• Lock up

Or I can just keep an eye on things."
```

### Late Night
```
[11:30 PM] Motion in kitchen

*Checks but stays quiet — late night, probably just getting water*

[Only speaks if something seems off]
```

## Installation

### Prerequisites

- [Clawdbot](https://github.com/clawdbot/clawdbot) installed and running
- Home Assistant with:
  - Camera entities (UniFi Protect recommended)
  - Person detection binary sensors
  - Light, climate, media entities
- Environment variables configured:
  ```bash
  export HA_URL="http://homeassistant.local:8123"
  export HA_TOKEN="your-long-lived-access-token"
  ```

### Setup

1. **Install the skill**
   ```bash
   cd ~/clawd/skills
   git clone https://github.com/andrewturko/jarvis-mode.git
   cd jarvis-mode
   ```

2. **Configure**
   ```bash
   cp config.example.json config.json
   # Edit config.json with your camera entities and sensors
   ```

3. **Register with Clawdbot** (auto-configures hooks + cron)
   ```bash
   python3 scripts/jarvis.py setup
   ```
   This automatically:
   - Adds webhook handlers to Clawdbot config
   - Creates the polling cron job
   - Uses `notifyChannel` from your config.json

4. **Restart Clawdbot gateway** to apply hooks
   ```bash
   clawdbot gateway restart
   ```

5. **Discover your devices**
   ```bash
   python3 scripts/refresh-inventory.py
   ```

6. **Start the UI server**
   ```bash
   ./serve-ui.sh
   # Access at http://localhost:8088
   ```

7. **Optional: Home Assistant automation**
   
   Import `ha_automation.yaml` for instant alerts on person detection.

## Configuration

### config.json

```json
{
  "enabled": true,
  "checkIntervalMinutes": 5,
  "cooldownMinutes": 30,
  "motionCooldownMinutes": 10,
  "motionAware": true,
  "instantAlerts": true,
  "quietMode": true,
  "autoActions": {
    "enabled": false,
    "announceActions": true
  },
  "activeHours": {
    "start": 7,
    "end": 23
  },
  "cameras": {
    "kitchen": {
      "entity_id": "camera.kitchen",
      "enabled": true,
      "motionSensor": "binary_sensor.kitchen_person_detected"
    }
  },
  "suggestions": {
    "music": true,
    "lighting": true,
    "tv": true,
    "climate": true
  },
  "notifyChannel": "telegram"
}
```

### Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | false | Master on/off switch |
| `checkIntervalMinutes` | 5 | Polling frequency for state changes |
| `cooldownMinutes` | 30 | Min time between vision API calls per room |
| `motionCooldownMinutes` | 10 | Shorter cooldown for motion triggers |
| `motionAware` | true | Only check rooms with detected activity |
| `instantAlerts` | true | Trigger immediately on person detection |
| `quietMode` | true | Only speak when there's something actionable |
| `autoActions.enabled` | false | Act automatically vs. suggest |
| `autoActions.announceActions` | true | Notify when auto-acting |
| `activeHours.start/end` | 7/23 | Operating hours (24h format) |

## Web UI

Access the control panel at `http://localhost:8088`

### Dashboard
- **Status indicator**: Shows if Jarvis is active
- **Room cards**: Live occupancy for each camera (Occupied/Empty badges)
- **Last poll time**: When state was last checked
- **Quick stats**: Active hours, intervals, cooldowns

### Detection Mode Toggles
- **Motion-Aware**: Only analyze rooms with activity
- **Instant Alerts**: Trigger on person detection events
- **Smart Quiet**: Suppress non-actionable messages
- **Auto Actions**: Enable automatic responses

### Timing Controls
- **Check Interval**: How often to poll (1-60 min)
- **Analysis Cooldown**: Rate limit for vision API (10-120 min)
- **Motion Cooldown**: Faster cooldown for motion triggers (5-60 min)
- **Active Hours**: When Jarvis operates

### Actions
- **Check Now**: Analyze all rooms immediately
- **Per-room Check**: Analyze specific room
- **Refresh Inventory**: Re-scan Home Assistant devices

## CLI Reference

```bash
# Status & Control
jarvis.py status              # Full status JSON
jarvis.py enable              # Turn on
jarvis.py disable             # Turn off

# Polling & Transitions
jarvis.py poll                # Check all rooms for state changes
jarvis.py handle-empty <room>     # Process empty room (lights off, etc.)
jarvis.py handle-occupied <room>  # Process occupied room (suggestions)

# Snapshots & Context
jarvis.py snapshot <room>           # Take snapshot (respects cooldown)
jarvis.py snapshot <room> --manual  # Bypass cooldown
jarvis.py context <room>            # Full context: snapshot + home state
jarvis.py context <room> --manual   # Bypass cooldown

# Utilities
jarvis.py room-lights <room>  # Check which lights are on
jarvis.py occupancy           # Current occupancy all rooms
jarvis.py home-state          # Full home state from HA
jarvis.py cleanup             # Delete old snapshots
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Clawdbot Agent                       │
│  (Receives observations, decides actions/suggestions)    │
└─────────────────────┬───────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        │                           │
┌───────▼───────┐          ┌────────▼────────┐
│  Cron (Poll)  │          │  HA Automation  │
│  Every 5 min  │          │ (Motion trigger)│
└───────┬───────┘          └────────┬────────┘
        │                           │
        └───────────┬───────────────┘
                    │
           ┌────────▼────────┐
           │   jarvis.py     │
           │ (State engine)  │
           └────────┬────────┘
                    │
    ┌───────────────┼───────────────┐
    │               │               │
┌───▼───┐     ┌─────▼─────┐   ┌─────▼─────┐
│ Poll  │     │ Snapshot  │   │  Handle   │
│ State │     │ + Vision  │   │Transitions│
└───────┘     └───────────┘   └───────────┘
                    │
           ┌────────▼────────┐
           │  Home Assistant │
           │ (Cameras, Lights│
           │  Sensors, etc.) │
           └─────────────────┘
```

## Privacy & Security

- **Local processing**: All logic runs on your machine
- **No cloud vision**: Uses your configured vision model (local or API)
- **Snapshots deleted**: Camera images cleaned up after analysis
- **You control scope**: Choose which cameras, rooms, hours
- **Disable anytime**: Master toggle in UI or config

## Extending Jarvis

### Adding New Rooms

1. Add camera + sensor to `config.json`:
   ```json
   "cameras": {
     "office": {
       "entity_id": "camera.office",
       "enabled": true,
       "motionSensor": "binary_sensor.office_person_detected"
     }
   }
   ```

2. Map lights in `scripts/jarvis.py` `room_lights_map`

3. Refresh inventory: `python3 scripts/refresh-inventory.py`

### Adding New Auto-Actions

Edit `handle_empty_room()` or `handle_occupied_room()` in `jarvis.py`:

```python
def handle_empty_room(room_name, dry_run=False):
    # Add: pause music in empty room
    # Add: set thermostat to eco
    # Add: close shades at night
    ...
```

### Custom Suggestion Logic

Edit `handle_occupied_room()` to add context-aware suggestions:

```python
def handle_occupied_room(room_name):
    # Add: TV suggestions based on time
    # Add: Meal-time suggestions
    # Add: Weather-based recommendations
    ...
```

## Contributing

PRs welcome! Areas of interest:
- Additional auto-actions
- Better activity recognition
- Multi-person handling
- Voice announcement integration
- Calendar-aware suggestions

## License

MIT

---
