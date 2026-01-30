#!/bin/bash
# Serve Jarvis Mode UI
cd "$(dirname "$0")/ui"
echo "🤖 Jarvis Mode UI: http://localhost:8088"
python3 -m http.server 8088
