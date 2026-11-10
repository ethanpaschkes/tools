#!/bin/bash
# Makes Photo drop start by itself when you log in to this Mac, and restart if it stops.
#   ./install.sh          install and start
#   ./install.sh remove   stop and remove
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
label="local.photo-drop"
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

launchctl bootout "$domain/$label" 2>/dev/null || true

if [ "${1:-}" = "remove" ]; then
  rm -f "$plist"
  echo "Photo drop is stopped and will not start at login any more."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$here/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$here</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/photo-drop.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/photo-drop.log</string>
</dict>
</plist>
PLIST

launchctl bootstrap "$domain" "$plist"
echo "Photo drop is running and will start at every login."
echo "Log: $HOME/Library/Logs/photo-drop.log"
