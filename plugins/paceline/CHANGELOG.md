# Changelog

## 0.4.3 - 2026-10-17

### Changed

- The hooks module passes the TypeScript check against Claude Code's engine types. The change is in
  type annotations only; behaviour is the same.

## 0.4.2 - 2026-10-17

### Fixed

- With two accounts in use at once, each account gets its own usage-watch reading every 125 seconds.
  The gate and the inbox file are per account, `<state>/usage-watch.state-<key>.json` and
  `<state>/inbox/usage-watch-<key>.json`, `<key>` being the account key (`unknown` for a session with
  no account); before, a reading of one account closed the gate for the other, so one account's
  partition could go without a reading. Two sessions of one account still share one request per
  interval. The shared `usage-watch.state.json` and `inbox/usage-watch.json` of earlier releases are
  removed by the next merge; the switch `usage-watch.json` stays one file for all accounts.
- `paceline usage-watch status` reports the account it resolves, with a new line `account: <key>`
  (or `account: unknown`) after the switch line, and reads that account's state file.

## 0.4.1 - 2026-10-17

### Added

- `paceline status --account KEY` reports the account whose key starts with `KEY` (at least 4 hex digits
  of a key in `rate-limits.json`) instead of the signed-in one. No match, or several, exits with code 2
  and lists the candidate keys. The option belongs to `status`; the gates and the night queue keep using
  the account of the session.
- When the signed-in account came from `.claude.json` and another account holds a reading under 30
  minutes old, `status` adds `other account <8 digits> active; --account <8 digits>` for each such
  account, and `--json` lists them under `data.other_accounts`.

### Changed

- Each account keeps its own five-hour burn snapshot, `<state>/five-hour-burn-<key>.json`, `<key>` being
  the snapshot's account key, so with two accounts in use at once each file stays current. A snapshot
  without an account is not written. `<state>/five-hour-burn.json` is no longer written, and a leftover
  one is removed. The format of the snapshot is unchanged. PitBoard reads the per-account file, so update
  PaceLine and PitBoard together: a PitBoard of an earlier release finds no snapshot and projects the
  Threshold row to 100% from its own samples.

### Fixed

- A Claude Code Desktop session on one account no longer tags and reads under the account of a terminal
  login on another. When the process has `CLAUDE_CODE_ACCOUNT_UUID` and `CLAUDE_CODE_ORGANIZATION_UUID`,
  as a Desktop session does, the account key comes from them and `.claude.json` is read only without
  them. After a `/login` or `/logout` in such a session the PaceLine hooks treat the account as
  `account unknown` until the session restarts; `paceline status` and the gates run from it still
  report the account the session started with.

## 0.4.0 - 2026-10-17

### Added

- The recommendation weighs the burn rate of the five-hour window. When the rate measured over the last
  90 minutes would reach the day or night threshold before the window resets, `max_parallel_agents`
  falls to the share of the rate that the budget covers, and the reason names the rate, the budget and
  the time the threshold is reached. The status output gains `five_hour.burn` and
  `recommendation.five_hour_burn_limited`.
- The Agent and Workflow hook gates a launch while the five-hour burn rate would reach the threshold
  before the window resets and the usage data is fresh, but only when the launch exceeds the agent cap
  (active reservations at or above `max_parallel_agents`, a cap of 0 included); within the cap it adds
  the advice line only. Over the cap it asks for confirmation, like at a hard limit, with the burn reason
  behind `pace 5h burn:`. While the owner's away marker is on it denies the launch instead, since no one
  can answer a prompt, and tells the model not to retry and to continue within the cap or wait. A hard
  limit (low memory, the five-hour threshold, the weekly limit) asks the same way and is denied while the
  marker is on, with a note to wait for the limit to clear. `paceline status --json` gains `away`.
- The same hook takes a `launch` reservation for every launch it allows or asks about, keyed by the tool
  call id and ending on its own after 60 seconds, in the same lock hold that reads the reservation count
  (`paceline status --launch-key`). Agent and Workflow launches issued in one message therefore count each
  other against the agent cap, where every gate used to see only the reservations of agents already
  started. A denied launch holds none. The start of an agent closes the oldest launch of its session, so a
  started agent counts once, as a subagent. The kind never reaches the history that calibration reads.
- `paceline away on|off|status` and the skill `/paceline:away` set, clear and show the away marker, one
  file (`<state>/away.json`) that all sessions share. The owner's next message in any session clears it;
  a cache-watch wake and the `/paceline:away` command itself do not. A `UserPromptSubmit` hook does the
  clearing.
- `<state>/five-hour-burn.json`, a snapshot of the signed-in account's five-hour window for other readers
  (PitBoard among them): `computed_at`, `account`, `used_pct`, `resets_at`, `threshold_pct`,
  `budget_per_hour_pct`, `rate_per_hour_pct`, `threshold_at`, `threshold_at_local`, `base_at`, `span_min`
  and `limited`, the rate fields `null` while no rate is measured. It is written while pace is on, by
  every command that computes the status on the live clock and by the limits merge after it wrote the
  limits file; PaceLine never reads it back.
- `five_hour.burn` of the status output gains `budget_share`, the budget over the rate (`null` without a
  rate), which `max_parallel_agents` is scaled by.
- `auto` applies the night threshold to the burn rate. A burn that would reach it before the window
  resets lowers `max_parallel_agents` and adds the burn reason; when the cap gives 0, `auto` waits
  (exit 10) until the five-hour reset.

## 0.3.4 - 2026-10-16

### Changed

- Cache-watch wakes the session no later than 52.5 minutes after the last request, so the wake comes
  before Claude Code's idle compaction (about 54 minutes) whatever the `lead` setting.
- The documentation pages describe the new wake time and what happens after the last wake.

## 0.3.3 - 2026-10-16

### Added

- The Ukrainian documentation pages ship with the plugin in `docs/uk/`.
- Every documentation page has an En/Uk switch that opens the page in the other language.
- The En/Uk switch remembers the reader's choice, and the first visit follows the browser's language.

### Changed

- The documentation pages mark product and command names as untranslated, so a translation
  tool leaves them as written.

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
