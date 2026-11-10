#!/bin/bash
# Shows the phone link as a QR code on this Mac's screen. Point the iPhone camera at it.
# The link holds the secret token, so it is shown as a picture and never printed.
set -euo pipefail
cd "$(dirname "$0")"

setting() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

token=$(setting PHOTO_DROP_TOKEN)
if [ -z "$token" ]; then
  echo "No token yet. Start the server once (./install.sh) and run this again."
  exit 1
fi
port=$(setting PHOTO_DROP_PORT)
host="$(scutil --get LocalHostName).local"
if [ "${1:-}" = "tailnet" ]; then
  host=$(tailscale ip -4)
fi

picture="${TMPDIR:-/tmp}/photo-drop-link.png"
printf 'http://%s:%s/k/%s/\n' "$host" "${port:-28765}" "$token" | swift qr.swift "$picture"
open "$picture"
echo "QR code opened. Scan it with the iPhone camera, then Share > Add to Home Screen."
echo "Delete the picture when you are done: rm \"$picture\""
