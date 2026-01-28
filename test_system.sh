#!/bin/bash
# Test script to verify Jarvis system is working

cd /Users/andrewturko/clawd/skills/jarvis-mode

echo "=== JARVIS SYSTEM TEST ==="
echo

echo "1. Checking if services are running..."
echo "   Gateway:"
if curl -s http://localhost:18789/api/health > /dev/null 2>&1; then
    echo "   ✓ Clawdbot Gateway is running"
else
    echo "   ✗ Gateway NOT running (run: clawdbot gateway start)"
fi

echo "   Poll Job:"
if launchctl list | grep -q "com.jarvis.poll"; then
    echo "   ✓ Poll launchd job is loaded"
else
    echo "   ✗ Poll job NOT loaded (run: launchctl load ~/Library/LaunchAgents/com.jarvis.poll.plist)"
fi

echo "   Jarvis Server:"
if curl -s http://localhost:8088/api/health > /dev/null 2>&1; then
    echo "   ✓ Jarvis server is running"
else
    echo "   ✗ Jarvis server NOT running (run: ./serve-ui.sh)"
fi
echo

echo "2. Checking configuration..."
CONFIG=$(python3 scripts/jarvis.py status 2>/dev/null)
ENABLED=$(echo "$CONFIG" | jq -r '.enabled // false')
ACTIVE_HOURS=$(echo "$CONFIG" | jq -r '.activeHours // false')
INTERVAL=$(echo "$CONFIG" | jq -r '.checkInterval // 0')

echo "   Enabled: $ENABLED"
echo "   Active Hours: $ACTIVE_HOURS"
echo "   Check Interval: ${INTERVAL}m"
echo "   Active Hours Config: $(echo "$CONFIG" | jq -r '.activeHoursConfig | "\(.start)-\(.end)"')"
echo

echo "3. Testing poll detection (cheap sensor reads)..."
POLL_RESULT=$(python3 scripts/jarvis.py poll 2>&1)
HAS_TRANSITIONS=$(echo "$POLL_RESULT" | jq -r '.hasTransitions // false')
echo "   Poll result: $HAS_TRANSITIONS transitions detected"
if [ "$HAS_TRANSITIONS" = "true" ]; then
    echo "   Transitions:"
    echo "$POLL_RESULT" | jq -r '.transitions[] | "   - \(.room): \(.transition)"'
fi
echo

echo "4. Testing webhook endpoints..."
echo "   Testing /jarvis/poll webhook:"
WEBHOOK_RESULT=$(curl -s -X POST \
    -H "Authorization: Bearer jarvis-motion-2026" \
    -H "Content-Type: application/json" \
    -d '{}' \
    http://localhost:18789/hooks/jarvis/poll 2>&1)

if echo "$WEBHOOK_RESULT" | grep -q '"ok":true'; then
    echo "   ✓ Poll webhook working (runId: $(echo "$WEBHOOK_RESULT" | jq -r '.runId // "unknown"' 2>/dev/null))"
else
    echo "   ✗ Poll webhook failed: $WEBHOOK_RESULT"
fi
echo

echo "5. Checking recent logs..."
echo "   Poll cron log (last 5 lines):"
if [ -f /tmp/jarvis-poll-cron.log ]; then
    tail -5 /tmp/jarvis-poll-cron.log | sed 's/^/   /'
else
    echo "   (no log yet - poll hasn't run)"
fi
echo

echo "   Poll cron errors:"
if [ -f /tmp/jarvis-poll-cron-error.log ] && [ -s /tmp/jarvis-poll-cron-error.log ]; then
    tail -5 /tmp/jarvis-poll-cron-error.log | sed 's/^/   /'
else
    echo "   (no errors)"
fi
echo

echo "6. Testing manual motion trigger (simulates HA automation)..."
echo "   Simulating motion in kitchen..."
MOTION_RESULT=$(curl -s -X POST \
    -H "Authorization: Bearer jarvis-motion-2026" \
    -H "Content-Type: application/json" \
    -d '{"room":"kitchen"}' \
    http://localhost:18789/hooks/jarvis/motion 2>&1)

if echo "$MOTION_RESULT" | grep -q '"ok":true'; then
    echo "   ✓ Motion webhook triggered (check Telegram for Jarvis response)"
    echo "   runId: $(echo "$MOTION_RESULT" | jq -r '.runId // "unknown"' 2>/dev/null)"
else
    echo "   ✗ Motion webhook failed: $MOTION_RESULT"
fi
echo

echo "=== TEST SUMMARY ==="
echo "✓ If all services are running and webhooks return ok:true, system is working"
echo "✓ Check Telegram for actual Jarvis responses"
echo "✓ Monitor logs with: tail -f /tmp/jarvis-poll-cron.log"
echo "✓ Check session activity: clawdbot sessions list | grep jarvis"
