---
name: desktop-control
description: Operate desktop applications through Open Terminal's desktop tools (screenshots, mouse, keyboard) when there is no command-line or file-based way to do the job. Use for apps without an API, guided tutorials with screenshots, usability walkthroughs and copying data out of legacy software. Covers when not to use it and the stop-and-ask points.
---

# Desktop control

Desktop control is the slowest and least reliable tool. Use it last.

## Prefer, in this order

1. Files: edit the document directly (office-documents).
2. Command line or the app's own CLI (`soffice --headless`, `git`, `winget`).
3. Browser tools for anything on a website.
4. Desktop control for native apps that offer nothing else.

## Requirements

Tether must have desktop viewing and control switched on (Settings, Open Terminal, Tools), the platform must support it, and the sandbox must be off. If the tools are missing, say which of these applies.

## How to act

- Screenshot first, describe what you see, then act. After every click or keystroke, screenshot again to confirm the result before the next step.
- Prefer keyboard shortcuts and menu paths over pixel-precise clicks.
- Move slowly through dialogs. Read every dialog fully before choosing a button.
- If the screen does not match what you expected twice in a row, stop and ask the user.

## Stop and hand over when

- a login, password, PIN, UAC prompt or payment screen appears
- a dialog would delete, overwrite, send, publish or install
- a licence agreement or cookie consent needs accepting
- the user is visibly typing or moving the mouse (they take priority)

## Tutorials and usability reports

Save step screenshots in the scratch folder as `step-01.png`, `step-02.png`. Write the tutorial in Markdown or Word with one step per screenshot, then keep only the tutorial and the screenshots it uses. Blur or crop names, emails and other personal data visible in screenshots before keeping them.
