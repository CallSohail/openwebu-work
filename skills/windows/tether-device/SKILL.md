---
name: tether-device
description: How to work on the user's own computer when it is connected to Open WebUI through Tether (listed under "Your devices" in the terminal menu). Use whenever the selected terminal is the user's PC through Tether, when the user mentions Tether, their device, their computer from another device or phone, connection drops, the working folder, or showing files in tabs.
---

# Working on the user's PC through Tether

Follow `windows-safe-terminal` as well. This skill adds what is different when the terminal is the user's own computer, linked through Tether.

## What Tether means for you

- The terminal is the user's **real, daily computer**, not a sandbox. Their documents, projects, logins and running apps are on it. Be at least as careful as on a production server.
- Tether starts Open Terminal in the user's home folder (`C:\Users\Pret`). The work folder is still `C:\Users\Pret\Desktop\Tether`. Change to it before working, or use the working folder the user selected in the chat's folder picker if they chose one.
- The user may be talking to you **from their phone or another computer**. They can't see the PC's screen. When you open a window (Word, Notepad, a browser), say so, and offer a screenshot instead of assuming they can see it.
- Commands run as the signed-in Windows user, with no administrator rights. Never try to elevate.

## Connection drops

- Tether can reconnect in the middle of a command. If a tool result says the connection was interrupted and the outcome is unknown, **check first** whether the command finished (does the file exist, is the process still running, what is in the log), then decide.
- Never automatically repeat a delete, move, rename, send, install or git push after an interruption. Ask the user.
- If the terminal is offline ("Tether is offline"), tell the user to check that the PC is on and awake, Tether is running, and its status shows **Connected**. Don't retry in a loop.

## Long work while the user is away

- Run long jobs (downloads, builds, big data processing) in the background, write their output to a log file in the work folder, and tell the user the log path.
- Keep the PC usable: don't start CPU-heavy work, full-screen apps or audio unless asked.
- Don't change power, sleep, lock-screen, network or display settings.

## Showing results

- Show created or changed files with `display_file`. In Open WebUI they open in a tab next to the chat, where the user can view and edit them, also from their phone.
- Keep file names short and descriptive so they are easy to find with Ctrl+P (quick open) and in the Files tab.
- For images and charts, save PNG files and display them. For documents, also give the full path.

## Privacy

- Don't open, read, list or summarize personal folders (Documents, Pictures, Downloads, Desktop outside the work folder, email, browser data) unless the user names the exact path and asks.
- Don't copy files from the PC to the internet (uploads, pastebins, public repos) without explicit confirmation of what and where.
- Never reveal or print API keys, tokens, passwords or `.env` contents, even if you come across them.

## Direct HTTP access

Tether keeps Open Terminal private by default. Don't tell the user to enable **Allow HTTP access** or **All interfaces** in Tether's settings unless they ask for another app to connect directly. If they do, warn that those connections are not encrypted and the API key changes when Open Terminal restarts.
