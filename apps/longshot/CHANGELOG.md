# Changelog

## [1.0.0] - 2026-11-13

### Added

- Bets opened by mention with a claim and a deadline, as a date or a span from `30min` to `20y`.
- Yes and No buttons that take, switch or withdraw a pick, with the card updated in place and picks closed at the deadline.
- A notice posted at the deadline, in the bet's thread, carrying the bet and the settle buttons.
- Settle: Yes, Settle: No, Void and Reopen, for the opener and admins.
- Points: 10 a head, the losing side's stakes split among the winners. A scoreboard per channel.
- `list`, `board`, `show <number>` and `help`.
- SQLite storage, a `/healthz` endpoint, credentials created once on first start, and tolerance for clocks up to 30 seconds apart.

### Compatibility

- Open WebUI 0.13.3 with Apps and Channels enabled; oix 0.8.0; Python 3.11 or newer. Checked in a browser against Open WebUI 0.13.3.
