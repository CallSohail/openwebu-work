#!/usr/bin/env bash
# install.sh — set up the TV kiosk on a Raspberry Pi OS / Debian / Ubuntu box.
# Run it ON THE BOX (the computer plugged into the TV's HDMI), as a normal
# user with sudo. It installs Chromium + the "cage" kiosk compositor and
# registers two services that start on boot:
#   tv-kiosk    -> shows a URL fullscreen on the TV
#   tv-control  -> phone control panel on port 8080
#
# Usage:  bash install.sh

set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  echo "Run this as your normal user (it will call sudo itself), not as root." >&2
  exit 1
fi

USER_NAME="$(id -un)"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="/opt/tv-kiosk"

echo "==> Installing packages (chromium, cage)..."
sudo apt-get update
# Package names differ a little between Raspberry Pi OS and Ubuntu.
sudo apt-get install -y cage python3 || true
sudo apt-get install -y chromium || sudo apt-get install -y chromium-browser

echo "==> Copying project to $DEST ..."
sudo mkdir -p "$DEST"
sudo cp "$SRC_DIR/kiosk-run.sh" "$SRC_DIR/control_server.py" "$DEST/"
sudo chmod +x "$DEST/kiosk-run.sh"
sudo chown -R "$USER_NAME":"$USER_NAME" "$DEST"

echo "==> Installing systemd services (user: $USER_NAME)..."
# Fill the user into the unit files and install them.
sed "s/__USER__/$USER_NAME/g" "$SRC_DIR/systemd/tv-kiosk.service"   | sudo tee /etc/systemd/system/tv-kiosk.service   >/dev/null
sed "s/__USER__/$USER_NAME/g" "$SRC_DIR/systemd/tv-control.service" | sudo tee /etc/systemd/system/tv-control.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now tv-control.service
sudo systemctl enable --now tv-kiosk.service

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "==> Done."
echo "   The TV should show the kiosk shortly."
echo "   From your phone (same Wi-Fi) open:  http://${IP:-<this-box-ip>}:8080"
echo
echo "   Change what's on the TV any time from that page."
echo "   Logs:   journalctl -u tv-kiosk -f   /   journalctl -u tv-control -f"
