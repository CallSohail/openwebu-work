#!/usr/bin/env bash
# kiosk-run.sh — launched inside the "cage" kiosk compositor.
# Reads the target URL from a state file and shows it fullscreen in Chromium.
# When the control server changes the URL it kills Chromium; this loop then
# relaunches it with the new URL. No sudo, no X config needed.

set -u

STATE_DIR="${TV_KIOSK_STATE_DIR:-$HOME/.tv-kiosk}"
URL_FILE="$STATE_DIR/url"
DEFAULT_URL="${TV_KIOSK_DEFAULT_URL:-http://localhost:8080/placeholder}"

mkdir -p "$STATE_DIR"
[ -f "$URL_FILE" ] || echo "$DEFAULT_URL" > "$URL_FILE"

# Find a Chromium binary (name differs across distros).
CHROME=""
for c in chromium chromium-browser chromium-browser-stable google-chrome chrome; do
  if command -v "$c" >/dev/null 2>&1; then CHROME="$c"; break; fi
done
if [ -z "$CHROME" ]; then
  echo "No Chromium/Chrome binary found. Run install.sh first." >&2
  sleep 10
  exit 1
fi

# A fresh profile dir keeps state predictable between reboots.
PROFILE="$STATE_DIR/chrome-profile"
mkdir -p "$PROFILE"

while true; do
  URL="$(cat "$URL_FILE" 2>/dev/null)"
  [ -z "$URL" ] && URL="$DEFAULT_URL"

  "$CHROME" \
    --kiosk \
    --app="$URL" \
    --user-data-dir="$PROFILE" \
    --ozone-platform=wayland \
    --start-fullscreen \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --check-for-update-interval=31536000 \
    --overscroll-history-navigation=0 \
    --password-store=basic

  # Chromium exited (crash, or control server asked for a URL change).
  # Small pause so a crash-loop doesn't spin the CPU, then relaunch.
  sleep 1
done
