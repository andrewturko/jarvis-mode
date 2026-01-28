#!/bin/bash
# Sync poll interval from config to launchd plist

cd /Users/andrewturko/clawd/skills/jarvis-mode

# Get checkIntervalMinutes from config
INTERVAL_MINUTES=$(python3 -c "
import json
with open('config.json') as f:
    config = json.load(f)
print(config.get('checkIntervalMinutes', 5))
")

# Convert to seconds
INTERVAL_SECONDS=$((INTERVAL_MINUTES * 60))

# Update plist
PLIST_PATH="$HOME/Library/LaunchAgents/com.jarvis.poll.plist"

/usr/libexec/PlistBuddy -c "Set :StartInterval $INTERVAL_SECONDS" "$PLIST_PATH" 2>/dev/null

# Reload launchd job
launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load -w "$PLIST_PATH" 2>/dev/null

echo "Poll interval synced to ${INTERVAL_MINUTES} minutes (${INTERVAL_SECONDS} seconds)"
