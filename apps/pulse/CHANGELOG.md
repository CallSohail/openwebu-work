# Changelog

## [0.1.1] - 2026-11-13

### Fixed

- **Connect** no longer fails with `App connection failed` when the app's computer clock is up to 30 seconds ahead of or behind the Open WebUI server. Connection proofs are now dated 30 seconds in the past.

## [0.1.0] - 2026-11-13

### Added

- Polls started by mention (`poll Question | A | B`), with one vote button per option, votes that can be moved or withdrawn, a card updated in place, a Markdown results download and a Close button for the creator and admins.
- A help card whose buttons show a private reply, a file download, an acknowledged slow job and a link button.
- Credentials created once on first start and printed with the App URL.
- Protocol tests against a stand-in Open WebUI.

### Compatibility

- Open WebUI 0.13.3 with Apps and Channels enabled; oix 0.8.0; Python 3.11 or newer. Checked in a browser against Open WebUI 0.13.3.
