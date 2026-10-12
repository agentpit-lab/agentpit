# Changelog

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
