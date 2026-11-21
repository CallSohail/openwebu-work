# TV Kiosk Server

Turn your **TCL 50DP60** (or any HDMI TV) into a screen that boots straight into
**your own web server / dashboard**, controlled from your phone — without touching
the TV's locked firmware.

The TV stays 100% stock and safe. All the custom software runs on a small computer
plugged into the TV's **HDMI** port.

---

## Why this instead of custom TV firmware?

Your TV uses the **MS6586 chassis** running a locked, signed Linux (firmware
`V8-S586T02-LF1V424`). Its USB updater only accepts files signed by TCL's private
key — any hand-built file fails the `software file check` and can brick the set.
So we don't fight the TV. We put a computer *in front of* it and control that.

---

## What you need (hardware)

Pick ONE small computer to act as the brain:

| Option | Rough cost | Notes |
|--------|-----------|-------|
| **Raspberry Pi 4 / 5** (+ power, microSD) | ~$50–80 | Cleanest fit for this guide |
| Any old **mini-PC / laptop** with Linux | $0 if you have one | Works the same |

Plus:
- 1x **HDMI cable** (box → TV)
- The box and your phone on the **same Wi-Fi**

Install **Raspberry Pi OS (64-bit)** or any Debian/Ubuntu on the box first.

---

## Install (on the box, not the TV)

1. Copy this `tv-kiosk-server/` folder onto the box (USB stick, `scp`, or
   `git clone` your repo).
2. Run:

   ```bash
   cd tv-kiosk-server
   bash install.sh
   ```

   It installs Chromium + `cage`, copies files to `/opt/tv-kiosk`, and enables two
   services that start on every boot.

3. Plug the box into the TV's HDMI and switch the TV to that HDMI input.
   The TV shows a "TV Kiosk ready" screen.

---

## Use it

From your **phone** (same Wi-Fi) open:

```
http://<box-ip>:8080
```

(the installer prints the exact IP; or run `hostname -I` on the box).

- Type any URL → it appears fullscreen on the TV instantly.
- Tap a **preset** for one-touch switching.

Point it at your own server, e.g. `http://localhost:3000` (OpenWebUI running on
the same box) or `https://evegpt.univ-evry.fr`.

---

## Customize presets

Edit the presets in `control_server.py` (the `DEFAULT_PRESETS` list), **or** create
`~/.tv-kiosk/presets.txt` on the box with one entry per line:

```
OpenWebUI|http://localhost:3000
My dashboard|http://192.168.1.50:8000
EveGPT|https://evegpt.univ-evry.fr
```

Then: `sudo systemctl restart tv-control`.

---

## Run your own server on the same box (optional)

Because the box is a full Linux machine, you can host the thing it displays.
Example — run OpenWebUI locally with Docker, then set the TV to `http://localhost:3000`:

```bash
docker run -d -p 3000:8080 --name openwebui \
  -v openwebui:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

Now the TV shows your own OpenWebUI, and you control it from your phone. This is the
"my own server on my TV" goal — done safely.

---

## Files

| File | What it does |
|------|--------------|
| `install.sh` | One-shot setup: packages, services, autostart |
| `kiosk-run.sh` | Launches Chromium fullscreen, relaunches on URL change |
| `control_server.py` | Phone control panel (stdlib Python, port 8080) |
| `systemd/tv-kiosk.service` | Starts the fullscreen browser on boot |
| `systemd/tv-control.service` | Starts the control panel on boot |

## Troubleshooting

```bash
journalctl -u tv-kiosk -f     # browser / display logs
journalctl -u tv-control -f   # control panel logs
sudo systemctl restart tv-kiosk tv-control
```

- **Black screen / wrong TV input:** make sure the TV is on the HDMI port the box
  is plugged into.
- **Can't reach :8080 from phone:** confirm both are on the same Wi-Fi and use the
  box's real IP (`hostname -I`).

## Security note

The control panel is meant for your **private home/office network only**. Don't
forward port 8080 to the internet.
