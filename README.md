# Jarvis Mode

*"Good evening, sir. I've noticed you've settled into the living room. Shall I dim the lights and queue up some evening jazz?"*

Jarvis Mode transforms [OpenClaw](https://github.com/openclaw/openclaw) into a proactive home intelligence system — observing, anticipating, and suggesting like Tony Stark's AI companion. It doesn't wait to be asked; it notices what's happening and offers help at exactly the right moment.

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
- **Any OpenClaw channel**: Telegram, Discord, iMessage, etc.

### Dynamic Suggestion Catalog

Suggestions auto-generate from your home's actual capabilities. When `refresh-inventory.py` discovers devices from Home Assistant, it updates `config/capabilities.json` and regenerates suggestion templates.

**How it works:**

```
Life model (context → needs → capability types)
    +
Capabilities (what devices exist in your home)
    +
Human catalog (hand-authored suggestion templates)
    ↓
Generated suggestions fill gaps the human catalog doesn't cover
```

The life model defines that `gaming` needs `[entertainment, comfort]`, which maps to capability types `[tv, music, lighting, climate, shades]`. If your home has those devices and the human catalog doesn't have gaming entries, templates are auto-generated:
- *"Want some music while you game?"*
- *"TV for gaming?"*
- *"Lights for gaming?"*

Music favorites tagged with contexts (e.g., "Chill House Radio" → `guests_over`) get their own entries automatically.

Generated entries have lower weight than hand-authored ones, so human entries always take priority. Add a hand-written entry for any context/capability and the generator stops filling that gap.

### Behavioral Learning

Jarvis learns your patterns over time — no manual programming required.

**How it works:**
- A background service logs every Home Assistant state change (lights, switches, media, climate, shades, etc.)
- Pattern analyzer identifies recurring behaviors:
  - **Time patterns**: "Sonos starts playing around 6pm" / "Thermostat drops to 68 at 10pm"
  - **Sequence patterns**: "When you enter the kitchen, coffee maker turns on within 2 min"
  - **Context chains**: "Friday + living room + 8pm → movie mode (dim lights, Apple TV, close shades)"

**What emerges:**

| Time Collected | What Jarvis Learns |
|----------------|-------------------|
| 1-3 days | Basic time-of-day patterns |
| 1 week | Action sequences and room transitions |
| 2 weeks | Contextual preferences (evening vs morning routines) |
| 1 month | Anomaly awareness ("why is everything on at 3am?") |

**How predictions flow:**
```
Observation → Pattern Match → Confidence Score → Natural Suggestion
```

Works across everything OpenClaw can control:
- **Lights**: "Getting dark — want me to bring up the counter lights?"
- **Music**: "You usually put on jazz around now. Chill House?"
- **Climate**: "Heading to bed? I can drop the thermostat to 68."
- **Shades**: "Sun's going down — close the bedroom shades?"
- **Media**: "Friday night, settling in — movie mode?"
- **Scenes**: "Looks like a work-from-home day. Focus mode?"

High-confidence patterns (70%+) become proactive offers; lower-confidence ones inform context without forcing suggestions.

**Storage:** ~10-20MB/month. Auto-prunes events older than 90 days.

**CLI:**
```bash
jarvis.py events --hours 24     # View collected events
jarvis.py patterns              # View learned patterns
jarvis.py patterns --predict    # Get predictions for now
```

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

- [OpenClaw](https://github.com/openclaw/openclaw) installed and running
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
   cp config/config.example.json config/config.json
   # Edit config/config.json with your camera entities and sensors
   ```

3. **Register with OpenClaw** (auto-configures hooks + cron)
   ```bash
   python3 scripts/jarvis.py setup
   ```
   This automatically:
   - Adds webhook handlers to OpenClaw config
   - Creates the polling cron job
   - Uses `notifyChannel` from your config

4. **Restart OpenClaw gateway** to apply hooks
   ```bash
   openclaw gateway restart
   ```

5. **Discover your devices** (also generates dynamic suggestion catalog)
   ```bash
   python3 scripts/refresh-inventory.py
   ```

6. **Start the UI server**
   ```bash
   python3 scripts/jarvis_server.py
   # Access at http://localhost:8088
   ```

7. **Optional: Home Assistant automation**

   Import `config/ha_automation.yaml` for instant alerts on person detection.

## Configuration

### config/config.json

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
│                     OpenClaw Agent                       │
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
           │ (CLI + engine)  │
           └────────┬────────┘
                    │
    ┌───────────────┼───────────────┐
    │               │               │
┌───▼───┐     ┌─────▼─────┐   ┌─────▼─────┐
│ Poll  │     │ Snapshot  │   │  Context  │
│ State │     │ + Vision  │   │ Inference │
└───────┘     └───────────┘   └─────┬─────┘
                                    │
                          ┌─────────▼─────────┐
                          │  intelligence/    │
                          │  ├ context        │
                          │  ├ suggestions    │
                          │  ├ silence logic  │
                          │  └ observations   │
                          └─────────┬─────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
             ┌──────▼──────┐ ┌─────▼─────┐  ┌──────▼──────┐
             │ Capabilities│ │  Catalog  │  │  Generated  │
             │  (devices)  │ │ (human)   │  │  (auto)     │
             └─────────────┘ └───────────┘  └─────────────┘
                    │
           ┌────────▼────────┐
           │  Home Assistant │
           │ (Cameras, Lights│
           │  Sensors, etc.) │
           └─────────────────┘
```

## Voice Module (Experimental)

Voice control via UniFi camera microphones. Say "Hey Jarvis" to activate.

### Setup

1. **Install dependencies**
   ```bash
   cd voice
   pip install -r requirements.txt
   ```

2. **Configure cameras** in `config/voice-config.json`:
   ```json
   {
     "unifi_protect": {
       "nvr_ip": "192.168.1.1",
       "rtsp_port": 7447
     },
     "cameras": {
       "kitchen": {
         "rtsp_path": "your_camera_id",
         "speaker": "Kitchen"
       }
     }
   }
   ```

3. **Get camera RTSP paths** from UniFi Protect console

4. **Run the voice service**
   ```bash
   python3 voice/service.py
   ```

### Testing

```bash
# Test audio capture from camera
python3 voice/service.py --test-audio --room kitchen

# Test wake word from system mic
python3 voice/service.py --test-wake
```

### Architecture

```
Camera Mic → RTSP → ffmpeg → Wake Word → STT → OpenClaw → TTS → Sonos
```

### Requirements

- ffmpeg (system): `brew install ffmpeg`
- openwakeword: Wake word detection
- faster-whisper: Local speech-to-text

## Privacy & Security

- **Local processing**: All logic runs on your machine
- **No cloud vision**: Uses your configured vision model (local or API)
- **Snapshots deleted**: Camera images cleaned up after analysis
- **You control scope**: Choose which cameras, rooms, hours
- **Disable anytime**: Master toggle in UI or config

## Extending Jarvis

### Adding New Rooms

1. Add camera + sensor to `config/config.json`:
   ```json
   "cameras": {
     "office": {
       "entity_id": "camera.office",
       "enabled": true,
       "motionSensor": "binary_sensor.office_person_detected"
     }
   }
   ```

2. Refresh inventory — auto-discovers lights, adds to capabilities, regenerates suggestions:
   ```bash
   python3 scripts/refresh-inventory.py
   ```

### Adding Suggestion Templates

Hand-authored suggestions go in `config/suggestion-catalog.json`. Add entries per context:

```json
{
  "action": "play_office_focus_music",
  "type": "ambiance",
  "intent": "offer",
  "requires": {"capability": "music", "state": "music_not_playing"},
  "priority": "low",
  "base_weight": 1.0,
  "cooldown_hours": 4,
  "examples": ["Focus music while you work?", "Want something in the background?"]
}
```

For contexts you don't hand-author, `generate_suggestions.py` fills gaps automatically from capabilities + life-model. Hand-authored entries always take priority.

### Adding New Contexts

Add the context to `config/life-model.json` with signals, typical needs, and transitions. The suggestion generator will auto-create entries for it based on your home's capabilities.

## Project Structure

```
jarvis-mode/
├── config/                        # Configuration (checked into git)
│   ├── config.json                  # Settings (cameras, intervals, toggles)
│   ├── life-model.json              # Context definitions, needs, capability types
│   ├── suggestion-catalog.json      # Hand-authored suggestion templates
│   ├── capabilities.json            # Home device capabilities (auto-updated)
│   ├── hooks.json                   # OpenClaw webhook definitions
│   └── ha_automation.yaml           # Home Assistant automation for motion
│
├── data/                          # Runtime data (gitignored)
│   ├── state.json                   # Current room states, observations
│   ├── patterns.json                # Learned suggestion acceptance patterns
│   ├── preferences.json             # User preferences (stated + observed)
│   ├── generated-suggestions.json   # Auto-generated catalog entries
│   ├── home-inventory.json          # Raw HA entity dump
│   ├── events.db                    # Event history for pattern analysis
│   └── snapshots/                   # Camera images (temporary)
│
├── scripts/                       # All Python source
│   ├── jarvis.py                    # CLI + observation engine
│   ├── jarvis_server.py             # Web UI + health API
│   ├── life_context.py              # Facade over intelligence/
│   ├── generate_suggestions.py      # Dynamic catalog generation
│   ├── refresh-inventory.py         # HA entity discovery + capability update
│   ├── core/                        # Foundation modules
│   │   ├── paths.py                   # Single source of truth for all file paths
│   │   ├── config.py                  # Config dataclasses
│   │   ├── state_manager.py           # Atomic state file operations
│   │   ├── logger.py                  # Structured logging
│   │   └── metrics.py                 # Observability metrics
│   ├── intelligence/                # Context inference engine
│   │   ├── context_inference.py       # Activity/context detection
│   │   ├── suggestion_engine.py       # Suggestion generation + filtering
│   │   ├── silence_logic.py           # When to speak vs stay quiet
│   │   ├── observation_tracker.py     # Observation recording
│   │   └── activity_chains.py         # Multi-room activity chains
│   └── services/                    # External integrations
│       ├── ha_service.py              # Home Assistant API
│       ├── preference_store.py        # Preference learning
│       └── temporal_learner.py        # Time-based pattern learning
│
├── tests/                         # Pytest suite
├── ui/                            # Web control panel
├── voice/                         # Voice module (experimental)
├── templates/                     # Hook message templates
└── docs/                          # Documentation
```

## Documentation

- **[Skill Definition](SKILL.md)** - Skill capabilities, tone guide, and integration reference
- **[What's New](docs/WHATS_NEW.md)** - Enhancement history
- **[Testing Guide](docs/TESTING_GUIDE.md)** - Testing instructions

See [docs/README.md](docs/README.md) for the full index.

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
