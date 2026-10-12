# Changelog

## 0.2.1 - 2026-10-12

### Added

- Documentation in `docs/`: a getting-started page and a budget simulator that runs pace's own rules,
  also on GitHub Pages, and how-to, reference, explanation and troubleshooting guides.

### Changed

- The README is a short entry point; its details moved into `docs/`.

## 0.2.0 - 2026-10-12

### Added

- A new pace profile takes this machine's time zone: from `TZ`, on Windows from ICU (Windows 10
  1903 and later), on macOS and Linux from `/etc/localtime` or `/etc/timezone`. It keeps `UTC`
  when nothing is detected or Python here has no time zone data for the zone. An existing profile
  keeps its zone and gains the key `timezone_confirmed`, false.
- `paceline profile check`: the profile's zone against this machine's, with a hint while the
  profile counts in UTC and the machine is in another zone. While pace is on, the `SessionStart`
  hook passes the hint on.
- `paceline profile set KEY VALUE`: changes one profile key after checking its type and range,
  then prints the change and the `paceline status` line. A refused value exits 2 and changes
  nothing. `paceline profile show` is the former `paceline profile`, still the default.

## 0.1.1 - 2026-10-12

### Fixed

- An exact reading (usage-watch, or any writer marking `"source": "usage-endpoint"`) no longer loses
  to a session's ordinary reading observed a few seconds after it. The merge takes inbox readings in
  the order they were observed instead of by file name, and an ordinary reading replaces an exact one
  only when observed more than 15 minutes later.

## 0.1.0 - 2026-10-12

First release.

- context-watch: a context line on every prompt, warnings at 85% and 95% of the auto-compaction
  point, and a session snapshot in `.claude/paceline/` at each compaction. On by default.
- cache-watch: keeps an idle session's one-hour prompt cache alive with wakes before it expires.
  Off until turned on in a session.
- pace: a per-hour budget against the five-hour and weekly limits, advice before Agent and
  Workflow launches, subagent reservations, plans, and a night queue. Off until `paceline on`.
- usage-watch: asks Claude Code for the account's usage, the figures `/usage` shows, at session
  start and every 125 seconds per configuration directory, and writes it with the model-scoped
  weekly limits as an exact reading to the inbox. Claude Code adds the session's login to the
  request; paceline never sees a token. Off until `paceline usage-watch on`.
- The limits file `<state>/rate-limits.json`, written from the readings Claude Code reports to
  plugins and from the inbox `<state>/inbox/`, a documented input for other plugins. An inbox
  reading can be marked as an exact reading of the account's usage (`"source": "usage-endpoint"`)
  and carry model-scoped weekly limits (`scoped`).
- While any session with paceline is open, busy or idle, a new or changed inbox reading reaches the
  limits file within about 15 seconds.
- One `paceline` command for all four functions.
