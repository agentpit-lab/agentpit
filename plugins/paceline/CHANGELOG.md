# Changelog

## 0.3.2 - 2026-10-16

### Changed

- The pages link Anthropic's prices for the cache-write and cache-read ratio that the cache-watch
  figures rest on.
- The how-to and limitations pages say that idle compaction, expected at about 54 minutes in a
  terminal session at or above its threshold, is expected to run before cache-watch's wake at about 55 minutes
  and that a compaction turns cache-watch off.
- The getting-started page and the README state that PaceLine works without PitBoard and what
  PitBoard shows with and without it.
- The documentation and this changelog write the plugin names PaceLine, PitBoard and dbWise in prose, and
  code-format commands and skill names.

## 0.3.1 - 2026-10-15

### Changed

- The five documents (how-to, reference, explanation, troubleshooting and limitations) are pages in
  `docs/` beside the getting-started page and the simulator, readable from a local install
  without the network. `docs/*.md` is generated from them.

## 0.3.0 - 2026-10-13

### Added

- Each limit reading is tagged with a one-way key of the signed-in account. PaceLine reads only the
  account and organization ids from `.claude.json` and stores a 16-hex-digit key, never the ids.
- `rate-limits.json` keeps one partition per account. Its top level mirrors the newest partition, so
  readers from before this version keep working.
- `paceline doctor` has an `account` probe that reports a state and a reason code only.

### Changed

- Pace, the status line, the pre-agent hint and the night queue read only the signed-in account's
  partition. Without one they show readings of an unknown account, marked; an account that cannot be
  resolved shows `account unknown`.
- A signed-out account's partition is kept up to 7 days. The newest partition is always kept, and
  its figures show as stale.
- The night queue stops with `account changed during the run` when another account signs in, and
  waits with `account unknown` while the account cannot be resolved.
- History lines are tagged with their account, one per changed partition.
- Readings taken during `/login` and `/logout` are held, so an old account's figures are not tagged
  with the new one.
- After updating, restart open sessions: a session still running 0.2.5 merges every account into the
  top level until it restarts. A history line such a session writes counts as untagged.

## 0.2.5 - 2026-10-12

### Fixed

- A session's pace reservations are released within Claude Code's 1.5-second budget for session-end
  hooks: the hook records the end and releases in its own process. When the budget cuts it off,
  the next pace call releases them (the `pace:` line before a launch, `paceline auto`, a session
  start).
- The leftover suggestion in `paceline status` prints the profile's `leftover_suggest_pct`; it
  printed `>10%` whatever the profile said.
- A waiting cache-watch reads only the transcript lines appended since its last step; it read the
  whole transcript every minute.
- A cache that expired before its wake, as when the computer slept, turns cache-watch off for the
  session (`status` shows `off (cache expired before the wake)`), and the next message brings a
  one-line notice. The watch used to stay `on` with nothing waiting.

### Changed

- cache-watch says first what a wake saves: `on` prints that a wake reads the context from the
  cache, with its size, while a rewrite after the expiry costs at least 20 times that read, and
  that each wake still counts against the five-hour limit. `status` and the skill say the same.

## 0.2.4 - 2026-10-12

### Fixed

- A compaction, manual or automatic, turns cache-watch off for the session (`status` shows
  `off (compacted)`): a wake armed before it would have kept nothing, since the compacted context
  starts a new cache.
- A wake shows its line once, as a push; it replies with the line only where the push tool is
  missing. It used to post both a reply and a push of the same line.

## 0.2.3 - 2026-10-12

### Added

- `docs/limitations.md` lists what PaceLine cannot do, grouped by where you meet it, each entry with
  what you see and what still works. The cache-watch and usage-watch how-tos state their limits
  before the steps, and the docs check fails when a listed limit is no longer stated where its
  record says.
- The getting-started page and the explanation have "Do you need pace?": five profiles by what
  PitBoard or `/usage` shows, and three questions that recommend a setup.

### Changed

- Steps that turn a function on or off or show its status use the slash commands
  `/paceline:pace`, `/paceline:usage-watch`, `/paceline:context-watch` and `/paceline:cache-watch`.
  The `paceline` command stays in the reference, the night queue, plan and profile guides, and
  troubleshooting.
- The skills, the help text and the plugin description state the same behaviour and limits as the
  docs: cache-watch keeps only the main thread's one-hour cache, usage-watch needs a claude.ai login
  and an open session and reads an endpoint outside Anthropic's published API, the state directory
  follows `CLAUDE_CONFIG_DIR`, and pace's session-start reminders include the leftover suggestion
  and the time zone hint.

## 0.2.2 - 2026-10-12

- The documentation pages draw the terminal in Claude Code's theme colours, light or dark with the
  page, as PitBoard's terminal row does.

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
  request; PaceLine never sees a token. Off until `paceline usage-watch on`.
- The limits file `<state>/rate-limits.json`, written from the readings Claude Code reports to
  plugins and from the inbox `<state>/inbox/`, a documented input for other plugins. An inbox
  reading can be marked as an exact reading of the account's usage (`"source": "usage-endpoint"`)
  and carry model-scoped weekly limits (`scoped`).
- While any session with PaceLine is open, busy or idle, a new or changed inbox reading reaches the
  limits file within about 15 seconds.
- One `paceline` command for all four functions.
