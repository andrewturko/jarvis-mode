# Jarvis Testing Guide

## ✅ System Status

Run the automated test:
```bash
./test_system.sh
```

This verifies:
- Gateway is running
- Poll job is loaded
- Jarvis server is running
- Configuration is correct
- Webhooks are working

## Manual Tests

### 1. Test Poll Detection (Cheap Sensor Reads)

```bash
cd ~/clawd/skills/jarvis-mode
python3 scripts/jarvis.py poll
```

**Expected Output:**
```json
{
  "polled": true,
  "currentOccupancy": {
    "kitchen": false,
    "living_room": false,
    "dining": false
  },
  "transitions": [
    {
      "room": "kitchen",
      "transition": "occupied",
      "previous": "empty",
      "current": "occupied"
    }
  ],
  "hasTransitions": true
}
```

**What This Tests:**
- Binary sensor reads (no API costs)
- Transition detection logic
- State persistence

---

### 2. Test Motion Webhook (Instant Alerts)

```bash
curl -X POST \
  -H "Authorization: Bearer jarvis-motion-2026" \
  -H "Content-Type: application/json" \
  -d '{"room":"kitchen"}' \
  http://localhost:18789/hooks/jarvis/motion
```

**Expected Output:**
```json
{"ok":true,"runId":"abc123..."}
```

**What This Tests:**
- Webhook endpoint
- Claude agent activation
- Vision analysis (respects cooldown)
- Telegram delivery

**Verify:** Check Telegram for Jarvis response

---

### 3. Test Poll Webhook (Scheduled Detection)

```bash
curl -X POST \
  -H "Authorization: Bearer jarvis-motion-2026" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://localhost:18789/hooks/jarvis/poll
```

**What This Tests:**
- Poll webhook endpoint
- Claude handles transitions
- Same flow as automatic poll

---

### 4. Manual Check (UI Button / Bypass Cooldown)

```bash
curl -X POST http://localhost:8088/api/check/kitchen
```

**What This Tests:**
- UI integration
- Manual override of cooldown
- Immediate analysis

---

### 5. Full Realistic Flow

**Scenario:** Walk into kitchen, then leave

1. Start in empty room:
   ```bash
   python3 scripts/jarvis.py poll
   # Shows kitchen empty
   ```

2. Enter kitchen (wait for sensor to update ~10s)

3. Run poll again:
   ```bash
   python3 scripts/jarvis.py poll
   # Should show: transition "occupied", hasTransitions: true
   ```

4. Webhook fires automatically after 5min OR trigger manually:
   ```bash
   curl -X POST -H "Authorization: Bearer jarvis-motion-2026" \
     http://localhost:18789/hooks/jarvis/poll
   ```

5. Check Telegram for Jarvis observation

---

## Monitoring

### Watch Poll Cron Logs
```bash
tail -f /tmp/jarvis-poll-cron.log
```

**Expected:** Every 5 minutes, if transitions detected:
```
{"ok":true,"runId":"..."}
```

### Check Recent Sessions
```bash
openclaw sessions list | grep "hook.*jarvis" | head -10
```

### View Status
```bash
python3 scripts/jarvis.py status | jq
```

Shows:
- enabled, activeHours, checkInterval
- Room states with last check times
- Recent observations

### View Recent Observations
```bash
python3 scripts/jarvis.py status | jq '.recentObservations'
```

---

## Troubleshooting

### Poll Not Running
```bash
# Check if loaded
launchctl list | grep jarvis

# View logs
cat /tmp/jarvis-poll-cron-error.log

# Reload
launchctl unload ~/Library/LaunchAgents/com.jarvis.poll.plist
launchctl load -w ~/Library/LaunchAgents/com.jarvis.poll.plist
```

### Webhooks Failing
```bash
# Check gateway is running
curl http://localhost:18789/api/health

# Check hooks are registered
cat ~/.openclaw/openclaw.json | jq '.hooks.mappings[] | select(.id | contains("jarvis"))'

# Restart gateway
openclaw gateway restart
```

### No Detections
```bash
# Check sensors are working
python3 scripts/jarvis.py motion kitchen
# Should return: {"room": "kitchen", "motion": false} or true

# Check active hours
python3 scripts/jarvis.py status | jq '{enabled, activeHours, activeHoursConfig}'
```

### Cooldown Blocking
```bash
# View room states
python3 scripts/jarvis.py status | jq '.roomStates'

# Manual override (bypasses cooldown)
curl -X POST http://localhost:8088/api/check/kitchen
```

---

## Expected Behavior

### Poll (Every 5 Minutes)
1. Reads binary sensors (cheap)
2. Detects transitions (empty↔occupied)
3. If transitions: triggers webhook
4. Claude analyzes (respects cooldown)
5. Sends to Telegram if actionable

### Motion (Instant via HA)
1. HA detects person
2. HA automation POSTs to `/jarvis/motion`
3. Claude analyzes immediately (respects cooldown)
4. Sends to Telegram

### Cooldowns
- **Regular analysis**: 30 minutes between vision calls per room
- **Motion-triggered**: 10 minutes between motion events per room
- **Manual checks**: Bypass all cooldowns

### Active Hours
- Default: 0-24 (always active)
- Configurable in UI
- Outside hours: poll doesn't run, webhooks ignored
