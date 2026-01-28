#!/bin/bash
# Jarvis Poll - detects room occupancy transitions
# Runs at checkIntervalMinutes interval via launchd
# Only makes cheap sensor reads - no vision API calls

cd /Users/andrewturko/clawd/skills/jarvis-mode

# Check if Jarvis is enabled and in active hours
IS_ENABLED=$(python3 scripts/jarvis.py status 2>&1 | jq -r '.enabled // false')
IS_ACTIVE_HOURS=$(python3 scripts/jarvis.py status 2>&1 | jq -r '.activeHours // false')

if [ "$IS_ENABLED" != "true" ] || [ "$IS_ACTIVE_HOURS" != "true" ]; then
    exit 0
fi

# Run poll to detect occupancy transitions (cheap - just binary sensors)
POLL_RESULT=$(python3 scripts/jarvis.py poll 2>&1)

# Extract transitions
HAS_TRANSITIONS=$(echo "$POLL_RESULT" | jq -r '.hasTransitions // false')

# If transitions detected, trigger webhook for Claude to analyze
if [ "$HAS_TRANSITIONS" = "true" ]; then
    # Send webhook - Claude will respect cooldowns when doing vision analysis
    curl -X POST \
        -H "Authorization: Bearer jarvis-motion-2026" \
        -H "Content-Type: application/json" \
        -d "{\"transitions\": true}" \
        http://localhost:18789/hooks/jarvis/poll \
        2>&1 >> /tmp/jarvis-poll-cron.log
fi
