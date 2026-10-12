# paceline

paceline is a Claude Code plugin that protects two things: a session's context and a subscription's usage
limits. It has four functions, each turned on and off on its own:

| Function | What it adds to a session | Default |
|---|---|---|
| context-watch | A one-line context gauge on every prompt, warnings before auto-compaction, and a snapshot of the session at each compaction. | On |
| cache-watch | Wakes an idle session shortly before its one-hour prompt cache expires, so the cache is kept. | Off in every session until you turn it on there |
| pace | A per-hour budget against the five-hour and weekly limits, advice before every Agent and Workflow launch, subagent reservations, and a night queue. | Off until you run `paceline on` |
| usage-watch | Reads the account's exact usage and its model-scoped weekly limits through Claude Code every 125 seconds, so the limits file stays current while sessions are idle. | Off until you run `paceline usage-watch on` |

One more part, the limits writer, is always on. It records the limit readings Claude Code reports
to plugins in a file that pace and other plugins (such as a status line) read. It adds nothing to
the conversation.

## Install

```
/plugin install paceline@agentpit
```

Then start a new session.

Requirements: Claude Code 2.1.287 or later, Python 3.9 or later on `PATH` as `python3` or
`python`. paceline runs on Windows, macOS, and Linux and needs only the Python standard library.

The `paceline` command is on the Bash tool's `PATH` inside a session. Elsewhere, run
`<plugin dir>/bin/paceline.cmd` (Windows) or `python <plugin dir>/scripts/paceline.py`.

## context-watch

An agent does not see how full its context is, so auto-compaction comes without warning. Whatever
the agent had not written down survives only as the compaction summary. context-watch gives the
agent what a status line gives you:

- **Every prompt** carries one line, for example `context: 612k / 967k auto-compact point (63%)`.
  The figures are Claude Code's own estimate against the point where it compacts. With
  auto-compaction off, the line measures against the window.
- **Warnings.** When the estimate first passes 85% and then 95% of that point, the agent is told
  once per level to write its recovery point to the task's status file. Warnings can come
  mid-turn, after a batch of tool calls. The levels reset after each compaction.
- **Snapshot.** At every compaction of the main conversation (automatic or `/compact`), paceline
  writes `.claude/paceline/<session id>.md` in the project. It holds your last 3 prompts, the
  agent's last 2 replies, the last 5 tool calls with their inputs cut to 200 characters (no
  results), and every file the agent wrote or edited. The folder has its own `.gitignore` (`*`),
  so git never sees it.
- **Recovery.** The summarizer is asked to keep the snapshot path. Right after the compaction, the
  agent is told to read the snapshot and the task's status files before its next step.
- **Never in the way.** context-watch never vetoes, delays, or starts a compaction. Subagent
  compactions are left alone. Any error in context-watch leaves the turn as it was.

Cost: the line takes about 15 tokens per prompt. Snapshots older than 14 days are removed when a
session starts in the project.

| Command | Effect |
|---|---|
| `paceline context-watch status [--project DIR]` | The settings and the project's snapshot folder. |
| `paceline context-watch config [--on\|--off] [--line on\|off] [--levels 85,95] [--days N]` | Show or change the settings. `--off` turns the line, the warnings, and the snapshots off. `--line off` hides only the line. Levels are ascending whole percents from 50 to 99. Snapshots are kept 1 to 90 days. Changes apply from the next prompt. |

The skill `/paceline:context-watch` runs the same commands.

## cache-watch

When a session sits idle for an hour, its prompt cache expires, and the next turn writes the whole
context to the cache again. cache-watch keeps the cache of one session you choose:

1. In that session, run `/paceline:cache-watch on`. It starts only when the session's main thread
   writes one-hour cache entries. It refuses a session with five-minute entries, or with no cache
   entry yet, and says why.
2. After every turn, a `Stop` hook waits in the background. If nothing else happens, it wakes the
   session `lead` minutes before the cache would expire. The woken turn is a request, so it keeps
   the cache for another hour. The agent posts one line, such as
   `Cache kept until 15:30 · wake 1 of 5 · reply stop to stop keeping it`, sends it as a push
   notification when it can, and ends the turn. Wakes come every `60 - lead` minutes.
3. Any message you send in the session resets the count. After `max` wakes in a row without a
   message from you, watching stops. Reply `stop`, or run `/paceline:cache-watch off`, to stop
   earlier. Ending the session stops it too.

**Cost.** Each wake is one full-context turn: a cache read of the whole context plus a one-line
reply, counted against your five-hour limit. With the defaults, that is at most five turns per
absence.

| Command | Effect |
|---|---|
| `paceline cache-watch on --session ID --data-dir DIR [--lead MIN] [--max N]` | Watch this session. The flags override the defaults for this session only. |
| `paceline cache-watch off --session ID --data-dir DIR` | Stop watching this session. |
| `paceline cache-watch status --data-dir DIR` | Every watched session: on or off and why, wakes so far, cache expiry, next wake. |
| `paceline cache-watch config --data-dir DIR [--lead MIN] [--max N]` | Show or change the defaults: `lead` 5 minutes (1 to 30), `max` 5 wakes (1 to 20). |

The skill `/paceline:cache-watch on|off|status|config` fills in the session ID and the data
directory (`${CLAUDE_PLUGIN_DATA}`) for you.

Use cache-watch in interactive sessions (terminal, Desktop, Remote Control). A headless
`claude -p` run with watching on does not exit while the hook waits for the next wake. A session
that is not watched is unaffected: the hook returns at once.

**Push to the phone.** The line appears in the session either way. A push needs Remote Control on
for the session, the Claude mobile app signed in, and "Push when Claude decides" enabled in
`/config`.

**In Desktop.** Wakes that follow each other with no message from you show as one collapsed block
with only the last line visible; expand it to see the earlier wakes.

## usage-watch

Claude Code reports to plugins only the five-hour and weekly readings that a session's own requests
carry, and only while some session makes requests. usage-watch reads the account's own figures, the
ones `/usage` shows, with the model-scoped weekly limits, so the limits file stays current while
every session is idle.

```
paceline usage-watch on
paceline usage-watch off
paceline usage-watch status
```

Or use the skill `/paceline:usage-watch on|off|status`. usage-watch is off until you turn it on. The
switch holds for every session that uses the same Claude Code configuration directory and takes
effect within 10 seconds, without a restart.

While it is on and any session with paceline is open, in the terminal or in Claude Code Desktop, busy
or idle:

- At session start, and then whenever 125 seconds have passed since the last reading, paceline asks
  for the account's usage. The sessions of one configuration directory share that pace through
  `<state>/usage-watch.state.json`. Two sessions that ask in the same second may both send; the
  second gets an HTTP 429, which costs one interval. After any other error, paceline waits 10 minutes.
- The answer becomes one exact reading with the model-scoped limits in
  `<state>/inbox/usage-watch.json` (see [The inbox (for writers)](#the-inbox-for-writers)). paceline
  merges it into the limits file within about 15 seconds.
- pace and status line plugins then see the exact five-hour and weekly figures and the model-scoped
  limits, and pace's `--model` and `scoped_exhausted_pct` take effect.

**How the request is made.** paceline asks Claude Code to send it. Claude Code adds the session's own
login, and only for `https://api.anthropic.com`; paceline never sees, reads, or stores a token. With
an API key, a gateway, a third-party provider, or no login, there is no such login, and usage-watch
does nothing. While `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is set, Claude Code refuses the
request; usage-watch records `network error` and asks again after 10 minutes.

**The endpoint is undocumented.** It is the one behind `/usage`, and Anthropic does not document it
for plugins. Its answer can change or stop. paceline then records the HTTP status or
`unexpected response`, and the limits file falls back to the session readings. `paceline usage-watch
status` shows the last attempt, the last success, and the last error.

## pace

pace turns the five-hour and weekly limits into a per-hour budget and a recommendation for how
much background work to start.

### Turning it on and off

```
paceline on
paceline off
```

Or use the skill `/paceline:pace on|off|status|doctor`.

While pace is off, its hooks add nothing to a session, and every `paceline` command still works
when you run it. Turning pace on makes the hooks do the following:

- Put a `pace:` advice line in front of every Agent and Workflow launch: allowed parallel agents,
  model, five-hour usage, and budget against the normal pace.
- Ask you to confirm a launch at a hard limit: free memory below `min_free_commit_mb`, or, with
  fresh data only, the five-hour window at its background threshold or the weekly limit used up.
  Without a person at the prompt (`claude -p` without a permission host), Claude Code denies the
  launch.
- Hold a reservation for each running subagent, and release a session's reservations when it
  ends.
- At session start, remind the agent of plans that start tomorrow and are not confirmed, and of
  a leftover-task pool when the week is forecast to end with usage unused.

### What it computes

All wall-clock rules use the profile time zone (UTC by default). The weekly window ends at the
reported weekly reset and starts 7 days earlier. Each hour of the window is one of these kinds:

- `night`: 00:00 to 08:00 (`night_hours`).
- `work`: Monday to Friday, 08:00 to 22:00 (`day_hours`).
- `weekend`: Saturday and Sunday, 08:00 to 22:00.
- `off`: every other hour (22:00 to 24:00 by default). It gets no budget.

From these, pace derives:

- **Reserve**: `reserve_pct` (8%) held back until Friday 18:00 before the reset, then falling
  linearly to 0 at the reset.
- **Per-hour budget**: what remains of the week minus the reserve, split over the remaining day
  hours by their weights. Each hour is capped at its physical capacity, `five_hour_to_week_pct / 5`
  percent of the week (2%/h by default). Weekend day hours get at most `weekend_cap_pct` (20%) in
  total. Past absences create no lag: the split starts from what remains now.
- **Normal pace**: the same split over the whole week. The recommendation compares the budget
  with the normal pace of the same day type.
- **Night allowance**: a base (`night_base_pct`, 5%) plus any surplus over the normal pace,
  limited by the night's capacity and by what the next day needs.
- **Five-hour thresholds**: background work pauses at 80% of the five-hour window by day and 95%
  at night; nothing new starts at 95%.
- **Memory**: below `min_free_commit_mb` (2048 MB) of free memory, no new work starts. Free memory
  is free commit on Windows, available memory on macOS and Linux.
- **Forecast**: the share of the week left unused at the reset if the last 24 hours' burn rate
  continues.

Declared plans change the weight of day hours: an `away` plan sets it to 0, a `heavy` plan
multiplies it (by 2 by default).

### Commands

| Command | Effect |
|---|---|
| `paceline status [--json]` | One-line recommendation, for example `7d 34% reset 2026-10-03 03:00+00:00 \| budget 1.45%/h (weekday norm 1.03) \| agents <=3 model opus \| 5h 23%/80 \| night 15.2% \| data 1 min old`. |
| `paceline auto [--start-run] [--json]` | Parameters for the next night-queue item: `start` (exit 0), `wait` (10) or `stop` (11). `--start-run` begins tonight's run and fixes its allowance; it is idempotent per night. |
| `paceline on`, `paceline off` | Turn the pace hooks on or off. |
| `paceline plan add --type away\|heavy --from D --to D [--weight W] [--phrase TEXT]` | Declare a future absence or heavy day. Dates are local; a bare `--to` date includes that day. |
| `paceline plan list\|find\|modify\|cancel\|confirm\|tomorrow` | Inspect or change declared plans. `find` exits 0 for one match, 3 for several, 4 for none. |
| `paceline reserve acquire\|renew\|release\|release-session\|prune\|list\|reconcile` | The reservation registry. Hooks reserve for subagents; reserve by hand for gates and queue items. |
| `paceline queue [--pool] add\|list\|report\|approve\|clear-done\|start\|done\|defer\|fail\|retry\|remove` | The night queue, or with `--pool` the leftover pool. |
| `paceline calibrate` | Propose profile corrections from at least 14 days of history. Nothing is applied. |
| `paceline profile [show\|set KEY VALUE\|check]` | Print the profile (`show`, the default), change one key with checks (`set`), or compare its time zone with this machine's (`check`). See [Pace settings](#pace-settings). |
| `paceline merge [--verbose]` | Merge the inbox into the limits file now. |
| `paceline doctor [--live] [--bundle PATH] [--json]` | Diagnose paceline on this machine. `--live` also runs one merge; `--bundle` writes the report, the debug log, and the state to a zip. |

Every pace command accepts `--json`, `--model`, `--session`, `--no-merge`, `--now`, and
`--state-dir`. `--json` prints the full result as JSON.

Exit codes: 0 success; 1 an error (for example no usage data yet, an unknown ID, or a corrupt
state file), with the message on stderr as `paceline: ...`; 2 an invalid command line; 3 and 4
from `plan find`, and 4 when `reserve renew` or `reserve release` finds no reservation; 10 and 11
from `auto`.

### What pace cannot see

- Without usage-watch, only the five-hour and weekly windows. Claude Code reports no model-scoped
  weekly limit to plugins, so a model's own weekly limit is not seen, and `--model` has no effect.
- Without usage-watch, the data is as of the last turn of any session on this machine. Usage from
  another machine or from claude.ai shows up only after the next local turn. With usage-watch on, it
  is at most about two and a half minutes old while any session is open.
- The limits come from a Pro or Max login; a session that reports no limits gives pace no data.

### Unattended night runs

A night queue runs while you sleep, so the machine must not sleep or reboot under it.

- Windows: `scripts/night-power.ps1` turns off sleep on AC power and moves the Windows Update
  active hours over the night. Without a switch it is a dry run. `-Enable` and `-Disable` change
  and restore the settings and must run in an elevated PowerShell. The previous values are saved
  in `<state>/night-power-state.json`.
- macOS: run the session under `caffeinate -i -s`, for example `caffeinate -i -s claude`.
- Linux: run it under `systemd-inhibit --what=sleep`, for example
  `systemd-inhibit --what=sleep claude`.

`templates/morning-report.md` is a template for the report the agent leaves after a night run.

## Pace settings

The profile is `<state>/pace-profile.json`. The first `paceline` command creates it with the
defaults below, except `timezone`: a new profile takes this machine's time zone when Python can
use it here, and records that choice as confirmed. The zone comes from `TZ` when it holds a zone
name, then on Windows from ICU (Windows 10 1903 and later), on macOS and Linux from the target of
`/etc/localtime` or from `/etc/timezone`. When nothing is detected, or Python here has no time zone
data for the detected zone, the profile keeps `UTC`. An existing profile keeps its zone. Keys missing
from the file are filled with their defaults when it is read.

- `paceline profile` prints the profile.
- `paceline profile set KEY VALUE` changes one key. `VALUE` is read as JSON when it parses, else
  as text: `paceline profile set timezone America/New_York`,
  `paceline profile set day_hours '[8, 24]'`. The value must have the key's type and range: percentages in 0-100, counts and sizes
  not negative, hours as `[start, end]` with `0 <= start < end <= 24`, and a time zone Python can
  use here. A refused value exits 2 with the reason on stderr and changes nothing. On success it
  prints `KEY: OLD -> NEW` and the `paceline status` line. `version`, `plans`, `next_plan_id`, and
  `timezone_confirmed` are kept by paceline and cannot be set.
- `paceline profile check` compares the profile's zone with this machine's. While the profile
  counts in `UTC`, `timezone_confirmed` is false, and another zone is detected, it prints a hint
  with the `profile set timezone` command to run, and the pace `SessionStart` hook passes that
  hint on once per session. `paceline profile set timezone UTC` keeps UTC and silences the hint.

Editing the file by hand still works; the checks of `profile set` do not apply to a hand edit.

Set these first: `timezone`, `day_hours`, `night_hours`, `models`, and the memory floor
(`min_free_commit_mb` and `commit_reserve_mb`).

| Key | Default | Meaning |
|---|---|---|
| `version` | `1` | Profile format version. |
| `timezone` | `"UTC"` | IANA zone for every wall-clock rule, for example `"America/New_York"`. A new profile takes the detected zone. |
| `timezone_confirmed` | `false` | Set by paceline: true once the zone was detected for a new profile or set with `paceline profile set timezone`. While it is false and the zone is `UTC`, `paceline profile check` hints at the detected zone. |
| `day_hours` | `[8, 22]` | Day hours, start inclusive, end exclusive. |
| `night_hours` | `[0, 8]` | Night hours for the queue. |
| `weekday_weight`, `weekend_weight` | `1.0`, `1.0` | Relative weight of a weekday and a weekend day hour. |
| `weekend_cap_pct` | `20.0` | Most of the week the weekend day hours may spend. |
| `reserve_pct` | `8.0` | Share of the week held back until the reserve decays. |
| `reserve_decay_start` | `{"weekday": 4, "hour": 18}` | When the reserve starts to fall: Friday (Monday is 0) at 18:00. |
| `five_hour_threshold_day`, `five_hour_threshold_night` | `80.0`, `95.0` | Five-hour usage at which background work pauses. |
| `five_hour_hard_stop` | `95.0` | Five-hour usage at which nothing new starts. |
| `five_hour_to_week_pct` | `10.0` | Share of the week one full five-hour window costs; `paceline calibrate` proposes a measured value. |
| `heavy_weight` | `2.0` | Default weight of a `heavy` plan. |
| `night_base_pct` | `5.0` | Night allowance before surplus. |
| `host_night_capacity_pct` | `null` | Optional cap on the night allowance. |
| `lease_ttl_min`, `agent_lease_ttl_min` | `30`, `240` | Lease of a reservation without and with a key. |
| `subagent_estimate_pct` | `1.0` | Estimated cost of one subagent, in percent of the week. |
| `stale_threshold_min` | `30` | Data older than this is stale; hard limits on the windows then do not apply. |
| `usage_snapshot_max_age_sec` | `300` | Data older than this makes a command merge the inbox first. |
| `capacity_warn_ratio` | `0.9` | Warn when what remains reaches this share of the day capacity. |
| `auto_refresh_five_hour_pct`, `auto_refresh_cache_sec` | `85.0`, `125` | Near the five-hour ceiling, `auto` merges data older than this many seconds. |
| `min_free_commit_mb` | `2048` | Free memory below which no new work starts. |
| `commit_reserve_mb`, `heavy_job_mb`, `max_heavy_runs` | `2048`, `6000`, `3` | Heavy runs allowed: `min(max_heavy_runs, (free - commit_reserve_mb) // heavy_job_mb)`. |
| `max_agents` | `6` | Most parallel agents recommended. |
| `models` | `["opus", "sonnet", "haiku"]` | Model families in order of preference. |
| `routine_models` | `["sonnet", "haiku"]` | Models for routine work while throttled. |
| `throttle_weekly_pct` | `85.0` | Weekly usage from which the recommendation throttles. |
| `scoped_exhausted_pct` | `95.0` | A model-scoped bucket at this level excludes its model (needs usage-watch on). |
| `leftover_suggest_pct` | `10.0` | Forecast unused share from which a leftover pool is suggested. |
| `gate_fix_attempts` | `3` | Failed gate fixes before a queue item is deferred. |
| `calibration_min_days` | `14` | History needed by `paceline calibrate`. |

The memory floor and the working hours are estimates, not measurements on a typical machine. If
`ask` prompts appear at most Agent launches, lower `min_free_commit_mb`; if your day is longer,
widen `day_hours`.

## The limits file and the inbox

### The limits file (for readers)

`<state>/rate-limits.json` holds the newest limit readings from every session on this machine. A
status line plugin or any other reader may read it. Its format:

```json
{
  "observed_at": "2026-10-11T09:30:00.000Z",
  "source": "payload",
  "session_id": "<session that gave the newest reading>",
  "model": "<its model id or null>",
  "five_hour": {"used_percentage": 42.5, "resets_at": "2026-10-11T12:00:00.000Z",
                "observed_at": "2026-10-11T09:30:00.000Z", "source": "payload"},
  "seven_day": {"used_percentage": 18, "resets_at": "2026-10-16T03:00:00.000Z",
                "observed_at": "2026-10-11T09:28:00.000Z", "source": "payload"}
}
```

- `observed_at` is the newest bucket time; each bucket carries its own. Times are ISO 8601 in UTC.
- `resets_at` is rounded to the minute and may be `null`.
- Either bucket may be absent. Readers ignore fields they do not know.
- A bucket's `source` is `payload` (a session's reading) or `usage-endpoint` (an exact reading of
  the account's usage, from a writer that provides one); the top-level `source` is that of the
  newest bucket.
- Optional `scoped`: a list of `{"name", "used_percentage", "resets_at"?}` model-scoped weekly
  buckets. It comes only with an exact weekly reading and is replaced by the next one; it is dropped
  when `seven_day` moves to another window without one.
- The file is replaced through a temp file and a rename, so a reader never sees a partial file.
  It is written only when it changes.
- `<state>/rate-limits.history.jsonl` holds one such record per line, appended when a value
  changes, and rotated to `.1` past 1 MiB.

### The inbox (for writers)

paceline's module writes one reading per session to `<state>/inbox/<session id>.json` after each
model turn, when the readings change, and an unchanged reading at most once a minute. Another
plugin may drop readings there in the same format; name the files `<plugin>-<id>.json`, one per
writer and session, and overwrite your own file.

```json
{
  "observed_at": "2026-10-11T09:30:00.000Z",
  "session_id": "<session id>",
  "model": "<model id or null>",
  "five_hour": {"used_percentage": 42.5, "resets_at": "2026-10-11T12:00:00.000Z"},
  "seven_day": {"used_percentage": 18, "resets_at": "2026-10-16T03:00:00.000Z"}
}
```

Rules:

- `observed_at` is required and dates both buckets. A bucket needs a numeric `used_percentage`;
  `resets_at` is ISO 8601 or `null`.
- A reading from the future, or with a reset later than its window allows, is skipped.
- A half-written file reads as nothing until the next merge. A file older than 7 days is deleted.
- Merge rule: the later window wins; a window that has passed loses to any reading; within one
  window the higher reading wins; a tie keeps the later one.

A writer that reads the account's own usage (the figures `/usage` shows) marks its reading as exact
and may add the model-scoped weekly limits:

```json
{
  "observed_at": "2026-10-11T09:30:00.000Z",
  "source": "usage-endpoint",
  "session_id": null,
  "model": null,
  "five_hour": {"used_percentage": 41, "resets_at": "2026-10-11T12:00:00.000Z"},
  "seven_day": {"used_percentage": 18, "resets_at": "2026-10-16T03:00:00.000Z"},
  "scoped": [{"name": "Fable", "used_percentage": 7, "resets_at": "2026-10-16T03:00:00.000Z"}]
}
```

- `source` other than `usage-endpoint`, or none, means an ordinary reading.
- The merge takes the readings in the order they were observed, whatever their file names.
- An exact reading replaces the bucket of the same name, whatever its window and number, unless the
  bucket in the file is an exact reading observed later, or an ordinary reading observed more than
  15 minutes later. An ordinary reading lags the account by one request, so it does not outrank an
  exact reading taken seconds or minutes before it. Inbox files are read at every merge, so the
  15 minutes keep an old exact reading from undoing much newer ones. An exact reading whose window
  has passed does not replace a bucket of a window still running.
- For 15 minutes after an exact reading, ordinary readings leave that bucket alone, unless its
  window has passed. After that the merge rule above applies again, so keep posting fresh exact
  readings for your numbers to stay.
- `scoped` is read only from an exact reading with a `seven_day` bucket; entries need a string
  `name` and a numeric `used_percentage`. When that weekly bucket wins, the file's `scoped` becomes
  this list, or goes when the list is empty or absent.

The merge runs asynchronously at session start and after each turn, within about 15 seconds of a
new or changed inbox file while any session with paceline is open, busy or idle, and whenever a pace
command finds the data older than `usage_snapshot_max_age_sec`. It holds `<state>/limits.lock`, so
writers take turns. paceline needs no status line plugin.

## Files

`<state>` is `$PACELINE_STATE_DIR`, else `<config>/state/paceline`, where `<config>` is
`$CLAUDE_CONFIG_DIR`, else `~/.claude`.

| File | Contents |
|---|---|
| `<state>/rate-limits.json`, `rate-limits.history.jsonl` (+ `.1`) | The limits file and its history. |
| `<state>/inbox/` | Limit readings, one file per writer and session. |
| `<state>/limits.lock` | Lock of the merge. |
| `<state>/inbox.seen` | The inbox state last handed to a merge, so open sessions do not merge it twice. |
| `<state>/pace.json` | The pace switch, `{"on": true}` or `{"on": false}`. Missing means off. |
| `<state>/pace-profile.json`, `pace-registry.json`, `night-queue.json`, `leftover-pool.json`, `pace.lock` | Pace profile, reservations, night queue, leftover pool, and their lock. |
| `<state>/debug.on`, `debug.log` (+ `.1`) | Debug tracing flag and log. |
| `<state>/night-power-state.json` | Power settings saved by `night-power.ps1 -Enable`. |
| `<state>/context-watch.json` | context-watch settings. |
| `<state>/usage-watch.json` | The usage-watch switch, `{"on": true}` or `{"on": false}`. Missing means off. |
| `<state>/usage-watch.state.json` | usage-watch's last attempt, last success and last error. No token. |
| `${CLAUDE_PLUGIN_DATA}/cache-watch/` | cache-watch state, one file per watched session, plus `defaults.json`. |
| `<project>/.claude/paceline/<session id>.md` | context-watch snapshots, with their own `.gitignore`. |

## Environment variables

| Variable | Effect |
|---|---|
| `PACELINE_STATE_DIR` | Moves every file under `<state>`. Meant for tests and for a second, isolated setup: set it where Claude Code starts, or the module and the scripts use different directories. |
| `PACELINE_DEBUG` | `1` traces one command, like the `debug.on` flag file. |
| `PACELINE_MODEL`, `ANTHROPIC_MODEL` | Default for `--model`. |
| `PACELINE_NOW` | Default for `--now`. |
| `PACELINE_NO_MERGE` | Any value: commands do not merge the inbox first. |
| `PACELINE_USAGE_FILE` | Read the usage record from this file instead. For tests. |
| `PACELINE_FREE_COMMIT_MB` | Override the measured free memory. For tests. |
| `PACELINE_SCRIPT` | Path to `pace.py` for the hooks. For tests. |
| `TZ` | A zone name such as `Europe/Madrid` (not a POSIX rule such as `CET-1CEST`) is the first source of the time zone a new pace profile takes. |
| `CLAUDE_CONFIG_DIR`, `CLAUDE_SESSION_ID`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR` | Claude Code's own; read as Claude Code sets them. |

## Footprint

| Hook | When | What it does |
|---|---|---|
| Module: `UserPromptSubmit`, `PostToolBatch`, `session.compact`, `SessionStart` | Every prompt, tool batch, compaction, start after compaction | context-watch. Does nothing while context-watch is off. |
| Module: `session.measure` | Once per model turn | Writes the inbox reading. Always on. |
| Module: `session.start` timer | Every 15 s while the session is open | Lists `<state>/inbox/`; runs `limits_merge.py` only when the inbox changed since the last merge. Always on. |
| Module: `session.start` timer | At session start, then every 10 s while the session is open | usage-watch. Reads its switch; while it is on and 125 s have passed, asks Claude Code for the account's usage. |
| `Stop`: `cache_watch.py hook stop` | After every turn | Returns at once for a session that is not watched. |
| `UserPromptSubmit`: `cache_watch.py hook prompt` | Every prompt | Returns at once for a session that is not watched. |
| `SessionEnd`: `cache_watch.py hook session-end` | Session end | Removes the session's cache-watch state. |
| `SessionStart`: `context_watch.py hook session-start` | Session start | Removes old snapshots while context-watch is on. |
| `Stop`, `SessionStart` (`startup`, `resume`, `clear`): `limits_merge.py` | After every turn, at session start | Asynchronous merge of the inbox. Always on. |
| `PreToolUse` (`Agent`, `Workflow`), `SubagentStart`, `SubagentStop`, `SessionStart` (`startup`, `resume`), `SessionEnd`: pace hooks | As named | Exit at once, with no output, while pace is off. While pace is on, the `SessionStart` hook adds unconfirmed plans, the leftover-pool suggestion, and a time zone hint while the profile counts in UTC and this machine is in another zone. |

While a function is off, its hooks still start one Python process per matching event.
Uninstalling the plugin removes `${CLAUDE_PLUGIN_DATA}`. `<state>` and the project snapshots stay
until you delete them.

## Debugging

1. Create the flag file `<state>/debug.on`.
2. Use Claude Code as usual for a while.
3. Run `paceline doctor --live --bundle ~/paceline-doctor.zip`.
4. Delete the flag file.

While the flag exists, paceline and its hooks append one JSON line per step to `<state>/debug.log`.
A hook writes its failures there even without the flag. The log and the bundle write your home
directory as `~`. The bundle holds the plan phrases you typed, reservation labels, and session IDs.

## Known limits

- Windows has no `tzdata` by default. Without it, paceline knows only UTC and a few built-in
  zones: CET (for example `Europe/Paris`, `Europe/Berlin`) and EET (for example `Europe/Kyiv`,
  `Europe/Helsinki`) with EU summer time rules. Any other zone needs `pip install tzdata`.
- The macOS memory and process lookups (`kern.memorystatus_level`, the process table) are tested
  with mocks only, not on a real Mac with a Claude login.
- Without usage-watch, readings refresh only with a model turn in a local session. With no session
  running, the data ages and pace marks it stale after 30 minutes, usage-watch or not.
- Without usage-watch, only the five-hour and weekly windows are reported to plugins; model-scoped
  weekly limits are not seen.
- usage-watch reads an endpoint Anthropic does not document for plugins; it can change or stop
  answering.
- A measured cost is a change in account-wide weekly usage, so parallel work inflates it; the
  `overlapped` flag on a reservation marks this.
- `paceline` resolves by name only in the Bash tool.
