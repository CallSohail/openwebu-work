# Chat Pets Changelog

All notable changes to Chat Pets are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-27

First version.

### Added

- Six pets with a job and their own lines: Plume the owl (mail), Noisette the squirrel (notes and memory), Filou the sheepdog (tasks), Roux the fox (research), Minuit the cat (calendar) and Sage the turtle (study).
- **Choose your pet:** a picker that invites people without a pet, and **Add your own** with a name, an animal, a colour, a job and an optional photo, shrunk to 128 px in the browser.
- The pet sits on the message box and moves with it, or in the corner where there is none. It can be dragged, or moved with the arrow keys.
- States: idle (breathing, blinking), watching while you type, thinking while the model answers, a hop when the answer is ready, wandering, and asleep after inactivity.
- A menu with four shortcuts per pet that fill the message box without sending, plus change pet, speech bubbles, animations, place, hide for an hour and remove.
- A daily greeting, and the odd remark when an answer is ready.
- The choice is saved in the account (`owui_pets`) and in the browser, follows the person to other devices, syncs between tabs, and retries a failed save when back online.
- Admin valves for the allowed pets, own pets and photos, a default pet, the invitation, the paw, bubbles and their frequency, sleep delay, shortcuts and shortcut overrides, default place, phones and language.
- French and English.
- 33 unit tests and 85 browser checks against a real Open WebUI 0.13.5.
