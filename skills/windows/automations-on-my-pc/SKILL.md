---
name: automations-on-my-pc
description: Create, change, test and manage scheduled automations that run on the user's PC through Open WebUI and Tether, such as daily reports, file clean-ups, website checks, backups and reminders. Use when the user asks to do something every day, week or hour, at a set time, on a schedule, automatically, or to list, pause or delete automations.
---

# Automations on the user's PC

Follow `windows-safe-terminal` and `tether-device` as well.

An automation runs a prompt on a schedule with the chat's model, terminal and working folder. On the user's PC it only works while the PC is **on, awake and connected through Tether**. Open WebUI can notify the user when it finishes, also on their desktop through Tether.

## Before creating one

1. **Run the task once by hand in this chat** and show the result. Only schedule it after the user confirms the result is what they want.
2. Confirm the schedule in plain words, including the timezone. The user is in France, so use Europe/Paris times unless they say otherwise. Example: "Every weekday at 08:30, Paris time. OK?"
3. Write the automation prompt so it works with **no conversation history**. Make it self-contained: the exact folder, file names, what to produce, and where to save it.

## Writing the schedule (RRULE)

| Schedule | rrule |
| --- | --- |
| Every day at 09:00 | `DTSTART:20261109T090000\nRRULE:FREQ=DAILY` |
| Weekdays at 08:30 | `DTSTART:20261109T083000\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` |
| Every Monday at 08:00 | `DTSTART:20261109T080000\nRRULE:FREQ=WEEKLY;BYDAY=MO` |
| Every hour | `RRULE:FREQ=HOURLY;INTERVAL=1` |
| Once, at a set time | `DTSTART:20261110T140000\nRRULE:FREQ=DAILY;COUNT=1` |
| First day of each month at 09:00 | `DTSTART:20261201T090000\nRRULE:FREQ=MONTHLY;BYMONTHDAY=1` |

Use a DTSTART date in the future. Prefer daily or weekly over very frequent schedules. Anything more often than every 30 minutes needs a clear reason.

## What automations may do

Good unattended tasks:
- Collect information (websites, APIs, files) and write a report into the work folder, named with the date: `report_2026-11-09.md`.
- Check something and only report changes (a page, a price, disk space, a log file).
- Create a backup copy or zip of the work folder.

Never schedule, even if asked, without first warning the user and getting explicit confirmation of the exact action:
- Deleting, overwriting or moving files. Prefer moving to an `archive` folder with a log over deleting.
- Sending emails or messages, posting online, submitting forms, or purchases.
- Installing or updating software, or git pushes.

## Inside the automation prompt

Add these lines to every automation prompt:
- "Work in C:\Users\Pret\Desktop\Tether."
- "If something fails, write the error to automation-errors.log in that folder and stop. Don't retry destructive steps."
- "End with a 3-line summary of what was done."

## Managing

- When asked "what automations do I have", list them with name, schedule in plain words, status and next run.
- To change a schedule or prompt, update the existing automation instead of creating a duplicate.
- Pausing is safer than deleting. Ask before deleting an automation.
