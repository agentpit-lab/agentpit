# paceline reference

Commands, exit codes, profile keys, environment variables, files, and the formats other programs
read and write. The four generated tables come from the code; the notes under each table are
hand-written.

In this page, `<state>` is the state directory: `$PACELINE_STATE_DIR`, else `<config>/state/paceline`,
where `<config>` is `$CLAUDE_CONFIG_DIR`, else `~/.claude`.

## Commands

<!-- generated:begin commands -->
| Command | What it does |
|---|---|
| `paceline on` | turn the pace hooks on |
| `paceline off` | turn the pace hooks off (every command still runs) |
| `paceline status` | interactive one-line recommendation |
| `paceline auto` | autonomous parameters for the next queue item |
| `paceline calibrate` | propose parameter corrections from history |
| `paceline profile` | print, change or check the profile (creates it with the detected time zone) |
| `paceline profile show` | print the profile (the default) |
| `paceline profile set` | change one key, with checks |
| `paceline profile check` | the time zone against this machine's, with a hint |
| `paceline doctor` | diagnose pace on this machine; read-only without --live |
| `paceline merge` | merge the sessions' readings from the inbox into rate-limits.json |
| `paceline plan` | declared future plan changes |
| `paceline plan add` |  |
| `paceline plan list` |  |
| `paceline plan find` |  |
| `paceline plan modify` |  |
| `paceline plan cancel` |  |
| `paceline plan confirm` |  |
| `paceline plan tomorrow` |  |
| `paceline reserve` | reservation registry |
| `paceline reserve acquire` |  |
| `paceline reserve renew` |  |
| `paceline reserve release` |  |
| `paceline reserve release-session` |  |
| `paceline reserve prune` |  |
| `paceline reserve list` |  |
| `paceline reserve reconcile` |  |
| `paceline queue` | night queue and leftover pool (--pool) |
| `paceline queue add` |  |
| `paceline queue list` |  |
| `paceline queue report` |  |
| `paceline queue approve` |  |
| `paceline queue clear-done` |  |
| `paceline queue start` |  |
| `paceline queue done` |  |
| `paceline queue defer` |  |
| `paceline queue fail` |  |
| `paceline queue retry` |  |
| `paceline queue remove` |  |
| `paceline cache-watch` | keep an idle session's one-hour prompt cache alive: on\|off\|status\|config |
| `paceline context-watch` | context against the auto-compaction point: config\|status |
| `paceline usage-watch` | read the account's exact usage through Claude Code: on\|off\|status |
<!-- generated:end -->

Inside a session, the `paceline` command is on the `PATH` of the Bash tool. Elsewhere, run
`<plugin dir>/bin/paceline.cmd` on Windows, or `python <plugin dir>/scripts/paceline.py`.

### Common options

Every command except `cache-watch`, `context-watch`, and `usage-watch` accepts these options:

- `--json`: print the full result as JSON.
- `--model ID`: the current model ID. It selects a model-scoped weekly bucket when the limits file
  has one. Default: `$PACELINE_MODEL`, else `$ANTHROPIC_MODEL`.
- `--session ID`: the Claude Code session ID. Default: `$CLAUDE_SESSION_ID`.
- `--no-merge`: use the limits file as it is, without merging the inbox first.
- `--now TIME`: an ISO 8601 time to use as the clock, for what-if runs and tests. Default:
  `$PACELINE_NOW`, else the real time.
- `--state-dir DIR`: the state directory for this command.

### Status and switches

- `paceline status` prints one line, for example:

  ```
  7d 34% reset 2026-10-03 03:00+00:00 | budget 1.45%/h (weekday norm 1.03) | agents <=3 model opus | 5h 23%/80 | night 15.2% | data 1 min old
  ```

  The fields, in order: weekly usage with any model-scoped buckets and the local reset time; the
  budget per hour against the norm for the same kind of hour (the weekday rate is added when it
  differs from the budget); `forecast unused`, when there is enough history; the advised parallel
  agents and model; five-hour usage against the current threshold; the night allowance; the age of
  the data, with `STALE` in front when it is older than `stale_threshold_min`. Further fields appear
  only when they apply: `host memory unknown`, `pace hooks off (paceline on)`, a `WARNING` that the
  rest of the week is near the day hours' capacity, the reasons for a block, pause, or throttle,
  unconfirmed plans that start tomorrow, and the leftover-pool suggestion. The
  [explanation](explanation.md) says how each number is computed.
- `paceline auto [--start-run]` prints the parameters for the next night-queue item, for example
  `auto: START Q3 | budget 4.20% | agents <=3 models opus,sonnet,haiku`, and exits 0 (`start`), 10
  (`wait`), or 11 (`stop`). `--start-run` begins tonight's run: it fixes the night allowance and the
  weekly usage it counts from. Running it again in the same night keeps that run. With `--json`,
  the result has `action`, `next_item`, `budget_for_next_item_pct`, `max_parallel_agents`,
  `max_heavy_runs`, `allowed_models`, `wait_until`, and `reasons`.
- `paceline on` and `paceline off` write the pace switch, `<state>/pace.json`.
- `paceline calibrate` reads the limits history and the completed reservations and proposes new
  values for `five_hour_to_week_pct`, `reserve_pct`, `weekend_cap_pct`, `night_base_pct`, and
  `subagent_estimate_pct`, plus a ratio of actual to estimated cost for your task estimates. It
  needs `calibration_min_days` days of history and changes nothing.
- `paceline profile` prints the profile; `show` is the default. `paceline profile set KEY VALUE`
  changes one key with checks (see [Profile keys](#profile-keys)). `paceline profile check`
  compares the profile's time zone with this machine's and prints a hint when they differ.
- `paceline merge [--verbose]` merges the inbox into the limits file now. It prints nothing unless
  you pass `--verbose` or `--json`.
- `paceline doctor [--live] [--bundle PATH]` diagnoses paceline on this machine. It runs these
  probes: `runtime`, `paths`, `switch`, `inbox`, `limits`, `process`, `memory`, `timezone`, `hooks`,
  and `debug_log`, each `ok`, `warn`, or `fail`, with a hint for some. `--json` shows the evidence of
  each probe. Without `--live` it changes nothing; `--live` also runs one merge. `--bundle` writes a
  zip with the report, the debug log, the profile, the pace switch, the limits file, the last 50
  history lines, and the last 20 reservations. The report and the bundle write your home directory
  as `~`. The bundle holds the plan phrases you typed, reservation labels, and session IDs.

### Plans

- `paceline plan add --type away|heavy --from DATE --to DATE [--weight W] [--phrase TEXT]` declares
  a future absence (`away`) or heavy day (`heavy`). A date is `YYYY-MM-DD`, `today`, `tomorrow`, or
  an ISO 8601 date and time, in the profile's time zone. A bare date as `--to` includes that whole
  day. The weight defaults to 0 for `away` and to `heavy_weight` for `heavy`. `--phrase` keeps your
  own words, so a later `find --text` can match them. Plan IDs are `P1`, `P2`, and so on.
- `paceline plan list` lists every declared plan.
- `paceline plan find [--text TEXT] [--on DATE] [--type away|heavy]` lists the plans whose phrase
  contains the text, that overlap that day, or that have that type. It exits 0 for one match, 3 for
  several, and 4 for none.
- `paceline plan modify ID [--from DATE] [--to DATE] [--weight W] [--phrase TEXT]` changes a plan
  and clears its confirmation.
- `paceline plan cancel ID` removes a plan.
- `paceline plan confirm ID` marks a plan confirmed today. The session-start reminder then stops for
  it. The budget does not change.
- `paceline plan tomorrow` lists the plans that start tomorrow and are not confirmed today.

`add`, `modify`, and `cancel` end their line with the new budget, for example
`plan P4 away 2026-10-16 00:00+02:00 .. 2026-10-17 00:00+02:00 added; budget 1.21%/h, night 5.0%`.
Plans that have ended are removed by the next command.

### Reservations

- `paceline reserve acquire --kind subagent|workflow|gate|queue [--estimate PCT] [--label TEXT]
  [--key KEY] [--pid PID] [--item ID]` holds a reservation. `--estimate` is the expected cost in
  percent of the weekly limit; it is required for every kind except `subagent`, which defaults to
  `subagent_estimate_pct`. With `--key`, a session holds one reservation per key, and a repeated
  acquire renews it. `--pid` is the owning Claude Code process; the default is the nearest parent
  process whose name starts with `claude`. Reservation IDs are `R-` and eight hex digits.
- `paceline reserve renew ID` renews a lease. It exits 4 when the reservation has expired or was
  released.
- `paceline reserve release [ID] [--key KEY] [--actual PCT]` releases one reservation, named by ID
  or by key. The actual cost defaults to the change in weekly usage since the reservation was
  acquired. It exits 4 when there is nothing to release.
- `paceline reserve release-session [--target-session ID]` releases every reservation of a session,
  by default the one in `--session`.
- `paceline reserve prune` drops reservations whose lease expired or whose process is gone.
- `paceline reserve list` lists the active reservations.
- `paceline reserve reconcile` summarizes completed reservations by kind: count, estimated and
  actual sums, and the median ratio of actual to estimate.

A reservation is dropped when its lease expires (`lease_ttl_min` without a key, `agent_lease_ttl_min`
with one) or when its process is gone. A completed reservation records `overlapped` when another
reservation ran at the same time: its measured cost is a change in account-wide weekly usage, so
parallel work inflates it.

### Night queue and leftover pool

Every `paceline queue` command works on the night queue, or with `--pool` on the leftover pool.
Queue item IDs are `Q1`, `Q2`, and so on; pool item IDs are `L1`, `L2`, and so on. An item is
`pending`, `running`, `done`, or `deferred`.

- `add --title TEXT --repo NAME --estimate PCT [--independent] [--depends ID,ID]` adds an item.
  `--independent` lets it run beside other independent items. Every dependency must already
  exist.
- `list` lists the items. `report` summarizes the current run: done, deferred, running, and pending
  items, with the sums of their estimates and actual costs.
- `approve` (pool only) approves the pool for the current weekly window.
- `start ID [--pid PID]` marks a pending item `running` and holds a `queue` reservation for it.
- `done ID [--actual PCT]` marks it `done`; the actual cost defaults to the change in weekly usage
  since it started.
- `fail ID [--reason TEXT]` counts one failed fix of the item's checks. At `gate_fix_attempts`
  failures, the item is deferred.
- `defer ID [--reason TEXT]` defers the item; the default reason is `needs owner permission`.
- `retry ID` puts an item back to `pending` and resets its failure count.
- `remove ID` deletes an item. It is refused while a pending or running item depends on it.
- `clear-done` deletes the `done` items.

Deferring an item, by `defer` or by the last `fail`, also defers every pending or running item that
depends on it.

### cache-watch

- `paceline cache-watch on --session ID --data-dir DIR [--lead MIN] [--max N]` watches one session.
  It exits 3 and prints `not started` with the reason when it finds no transcript for the session,
  no one-hour cache entry yet, or five-minute cache entries. cache-watch holds only the one-hour
  prompt cache; when the main thread switches to five-minute entries while watching, it stops
  itself, and `status` shows the session as `off (five-minute cache)`. See
  [limitations](limitations.md#cache-watch).
- `paceline cache-watch off --session ID --data-dir DIR` stops watching a session.
- `paceline cache-watch status --data-dir DIR` lists every watched session: on or off and why
  (`operator`, `five-minute cache`, `no reply after N wakes`, `compacted`), the wakes so far, the
  cache expiry, and the next wake. It removes the state of sessions whose
  transcript has not changed for 24 hours.
- `paceline cache-watch config --data-dir DIR [--lead MIN] [--max N]` shows or changes the defaults:
  `lead` is 5 minutes (1 to 30), and `max` is 5 wakes (1 to 20). `--lead` and `--max` on `on` apply
  to that session only.

The data directory is the plugin's `${CLAUDE_PLUGIN_DATA}`. The skill `/paceline:cache-watch`
fills in the session ID and the data directory.

### context-watch

- `paceline context-watch status [--project DIR]` prints the settings, the project's snapshot folder
  with its number of snapshots, and the settings file.
- `paceline context-watch config [--on|--off] [--line on|off] [--levels 85,95] [--days N]` shows or
  changes the settings. `--off` turns off the line, the warnings, and the snapshots. `--line off`
  hides only the line. Levels are ascending whole percents from 50 to 99. Snapshots are kept 1 to 90
  days. Changes apply from the next prompt.

Defaults: on, line on, warnings at 85% and 95%, snapshots kept 14 days.

### usage-watch

- `paceline usage-watch on` and `paceline usage-watch off` write the switch,
  `<state>/usage-watch.json`.
- `paceline usage-watch status` prints the switch, the last attempt, the last success, and the last
  error. Times are ISO 8601 in UTC, or `never`.

### Skills

Each skill runs the matching command for you:

- `/paceline:pace on|off|status|doctor|profile|profile set KEY VALUE`
- `/paceline:cache-watch on|off|status|config`
- `/paceline:context-watch status|config`
- `/paceline:usage-watch on|off|status`

## Exit codes

<!-- generated:begin exit-codes -->
| Code | Name |
|---|---|
| 0 | EXIT_OK |
| 1 | EXIT_ERROR |
| 2 | EXIT_USAGE |
| 3 | EXIT_SEVERAL |
| 4 | EXIT_NONE |
| 10 | EXIT_WAIT |
| 11 | EXIT_STOP |
<!-- generated:end -->

- `0`: success. `paceline merge` also exits 0 when another merge holds the lock.
- `1`: an error, with the message on stderr as `paceline: ...`. Examples: no usage data yet, an
  unknown plan or item ID, a state file that is not valid JSON (paceline leaves it for you to fix),
  or `pace.lock` busy for 20 seconds. A refused `cache-watch` or `context-watch` value also exits 1.
- `2`: an invalid command line, or a value that `paceline profile set` refused. A refused value
  changes nothing.
- `3`: `plan find` found several plans. `cache-watch on` uses 3 for `not started`.
- `4`: `plan find` found no plan, or `reserve renew` or `reserve release` found no reservation.
- `10` and `11`: `auto` says wait or stop.

## Profile keys

<!-- generated:begin profile-keys -->
| Key | Default |
|---|---|
| `agent_lease_ttl_min` | `240` |
| `auto_refresh_cache_sec` | `125` |
| `auto_refresh_five_hour_pct` | `85.0` |
| `calibration_min_days` | `14` |
| `capacity_warn_ratio` | `0.9` |
| `commit_reserve_mb` | `2048` |
| `day_hours` | `[8, 22]` |
| `five_hour_hard_stop` | `95.0` |
| `five_hour_threshold_day` | `80.0` |
| `five_hour_threshold_night` | `95.0` |
| `five_hour_to_week_pct` | `10.0` |
| `gate_fix_attempts` | `3` |
| `heavy_job_mb` | `6000` |
| `heavy_weight` | `2.0` |
| `host_night_capacity_pct` | `null` |
| `lease_ttl_min` | `30` |
| `leftover_suggest_pct` | `10.0` |
| `max_agents` | `6` |
| `max_heavy_runs` | `3` |
| `min_free_commit_mb` | `2048` |
| `models` | `["opus", "sonnet", "haiku"]` |
| `night_base_pct` | `5.0` |
| `night_hours` | `[0, 8]` |
| `reserve_decay_start` | `{"weekday": 4, "hour": 18}` |
| `reserve_pct` | `8.0` |
| `routine_models` | `["sonnet", "haiku"]` |
| `scoped_exhausted_pct` | `95.0` |
| `stale_threshold_min` | `30` |
| `subagent_estimate_pct` | `1.0` |
| `throttle_weekly_pct` | `85.0` |
| `timezone` | `"UTC"` |
| `usage_snapshot_max_age_sec` | `300` |
| `weekday_weight` | `1.0` |
| `weekend_cap_pct` | `20.0` |
| `weekend_weight` | `1.0` |
<!-- generated:end -->

The profile is `<state>/pace-profile.json`. The first `paceline` command creates it. A new profile
takes this machine's time zone when Python can use that zone here, and records it as confirmed;
otherwise it keeps `UTC`. Keys missing from the file are filled with their defaults when it is
read. `version`, `plans`, `next_plan_id`, and `timezone_confirmed` are kept by paceline and are not
in the table.

`paceline profile set KEY VALUE` reads `VALUE` as JSON when it parses, else as text. The value must
have the key's type and range: percentages from 0 to 100, counts and sizes not negative, hours as
`[start, end]` with `0 <= start < end <= 24`, `reserve_decay_start` as a weekday from 0 to 6 and an
hour from 0 to 23, model lists not empty, and a time zone Python can use here. A refused value exits
2 with the reason on stderr. On success it prints `KEY: OLD -> NEW` and the `paceline status` line.
Setting `timezone` also sets `timezone_confirmed` to true. You can still edit the file by hand; the
checks do not apply to a hand edit.

What each key does:

- `timezone`: the IANA zone every wall-clock rule uses, for example `America/New_York`.
- `day_hours`: the day hours, start inclusive, end exclusive. Only day hours get a budget.
- `night_hours`: the night hours, when the night queue runs. An hour in neither range is an off hour
  and gets no budget.
- `weekday_weight`, `weekend_weight`: the relative weight of a weekday and a weekend day hour.
- `weekend_cap_pct`: the most of the week, in percent, that the weekend day hours may spend together.
- `reserve_pct`: the share of the week held back until the reserve starts to fall.
- `reserve_decay_start`: when the reserve starts to fall, as `{"weekday": W, "hour": H}` in local
  time; Monday is 0. The default is Friday 18:00.
- `five_hour_threshold_day`, `five_hour_threshold_night`: five-hour usage, in percent, at which
  background work pauses and an Agent or Workflow launch asks you first.
- `five_hour_hard_stop`: five-hour usage at which the recommendation starts nothing new.
- `five_hour_to_week_pct`: the share of the week that one full five-hour window costs. It sets the
  most any hour can get: this value divided by 5, in percent of the week per hour.
  `paceline calibrate` proposes a measured value.
- `heavy_weight`: the default weight of a `heavy` plan.
- `night_base_pct`: the night allowance before any surplus is added.
- `host_night_capacity_pct`: an optional cap on the night allowance; `null` means none.
- `lease_ttl_min`, `agent_lease_ttl_min`: the lease of a reservation without and with a key, in
  minutes.
- `subagent_estimate_pct`: the estimated cost of one subagent, in percent of the week.
- `stale_threshold_min`: data older than this many minutes is stale.
- `usage_snapshot_max_age_sec`: a command that finds the data older than this many seconds merges
  the inbox first.
- `capacity_warn_ratio`: `paceline status` warns when what remains of the week reaches this share of
  what the remaining day hours can spend.
- `auto_refresh_five_hour_pct`, `auto_refresh_cache_sec`: at or above this five-hour usage, `auto`
  merges the inbox first when the data is older than this many seconds.
- `min_free_commit_mb`: free memory, in MiB, below which no new work starts.
- `commit_reserve_mb`, `heavy_job_mb`, `max_heavy_runs`: the heavy runs allowed are
  `min(max_heavy_runs, (free memory - commit_reserve_mb) // heavy_job_mb)`, and never fewer than 0.
- `max_agents`: the most parallel agents pace recommends.
- `models`: model families in order of preference.
- `routine_models`: the models for routine work while the recommendation throttles.
- `throttle_weekly_pct`: weekly usage from which the recommendation throttles, while more than 24
  hours remain to the reset.
- `scoped_exhausted_pct`: a model-scoped weekly bucket at this level removes its model from the
  allowed models. Model-scoped buckets come only from usage-watch.
- `leftover_suggest_pct`: the forecast unused share above which pace suggests a leftover pool.
- `gate_fix_attempts`: failed fixes before a queue item is deferred.
- `calibration_min_days`: the days of history `paceline calibrate` needs.

## Environment variables

<!-- generated:begin env -->
| Variable |
|---|
| `ANTHROPIC_MODEL` |
| `CLAUDE_CONFIG_DIR` |
| `CLAUDE_PLUGIN_DATA` |
| `CLAUDE_PROJECT_DIR` |
| `CLAUDE_SESSION_ID` |
| `HOME` |
| `PACELINE_DEBUG` |
| `PACELINE_FREE_COMMIT_MB` |
| `PACELINE_MODEL` |
| `PACELINE_NOW` |
| `PACELINE_NO_MERGE` |
| `PACELINE_SCRIPT` |
| `PACELINE_STATE_DIR` |
| `PACELINE_USAGE_FILE` |
| `USERPROFILE` |
<!-- generated:end -->

- `PACELINE_STATE_DIR`: moves every file under `<state>`. It is meant for tests and for a second,
  isolated setup. Set it where Claude Code starts, so that the plugin's module and its scripts use
  the same directory.
- `PACELINE_DEBUG`: `1` traces one command, like the `debug.on` flag file.
- `PACELINE_MODEL`, `ANTHROPIC_MODEL`: the default for `--model`, in that order.
- `PACELINE_NOW`: the default for `--now`.
- `PACELINE_NO_MERGE`: any value stops commands from merging the inbox first.
- `PACELINE_USAGE_FILE`: read the usage record from this file instead of the limits file. For tests.
- `PACELINE_FREE_COMMIT_MB`: replaces the measured free memory. For tests.
- `PACELINE_SCRIPT`: the path to `pace.py` for the hooks. For tests.
- `HOME`, `USERPROFILE`: the plugin's module finds `~` from them when it resolves `<state>`.
- `CLAUDE_CONFIG_DIR`, `CLAUDE_SESSION_ID`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR`: set by
  Claude Code.

paceline also reads `TZ` when it creates a pace profile: a zone name such as `Europe/Madrid` is the
first source of the time zone. A POSIX rule such as `CET-1CEST` is ignored.

## Files

| File | Contents |
|---|---|
| `<state>/rate-limits.json` | The limits file. |
| `<state>/rate-limits.history.jsonl` (+ `.1`) | One limits record per line, appended when a value changes, rotated to `.1` past 1 MiB. |
| `<state>/inbox/` | Limit readings, one file per writer and session. |
| `<state>/inbox/usage-watch.json` | usage-watch's latest exact reading. |
| `<state>/inbox.seen` | The inbox state last handed to a merge, so open sessions do not merge it twice. |
| `<state>/limits.lock` | The lock of the merge. |
| `<state>/pace.json` | The pace switch, `{"on": true}` or `{"on": false}`. Missing means off. |
| `<state>/pace-profile.json` | The pace profile, with the declared plans. |
| `<state>/pace-registry.json` | Active reservations and the last 500 completed ones. |
| `<state>/night-queue.json` | The night queue: the current run, the last 14 runs, and the items. |
| `<state>/leftover-pool.json` | The leftover pool: when and for which weekly window it was approved, and the items. |
| `<state>/pace.lock` | The lock of the pace files. |
| `<state>/debug.on` | The debug flag file. |
| `<state>/debug.log` (+ `.1`) | The debug log, rotated to `.1` past 1 MiB. |
| `<state>/night-power-state.json` | Power settings saved by `night-power.ps1 -Enable`. |
| `<state>/context-watch.json` | context-watch settings: `on`, `line`, `levels`, `snapshot_days`. |
| `<state>/usage-watch.json` | The usage-watch switch, `{"on": true}` or `{"on": false}`. Missing means off. |
| `<state>/usage-watch.state.json` | usage-watch's `last_attempt`, `last_success` (milliseconds since 1970), and `last_error`. It holds no token. |
| `${CLAUDE_PLUGIN_DATA}/cache-watch/<session id>.json` | cache-watch state of one session. |
| `${CLAUDE_PLUGIN_DATA}/cache-watch/defaults.json` | cache-watch defaults: `lead_minutes`, `max_wakes`. |
| `<project>/.claude/paceline/<session id>.md` | context-watch snapshots. The folder has its own `.gitignore` (`*`), so git ignores it. |

paceline replaces a JSON state file through a temporary file and a rename.

## The limits file (for readers)

`<state>/rate-limits.json` holds the newest limit readings from every session on this machine. A
status line plugin or any other program may read it. Its format:

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
- Either bucket may be absent. Ignore fields you do not know.
- A bucket's `source` is `payload` (a session's reading) or `usage-endpoint` (an exact reading of
  the account's usage). The top-level `source` is that of the newest bucket.
- `scoped`, when present, is a list of `{"name", "used_percentage", "resets_at"?}` model-scoped
  weekly buckets. It comes only with an exact weekly reading and is replaced by the next one. It is
  dropped when `seven_day` moves to another window without one.
- The file is replaced through a temporary file and a rename, so a reader never sees a partial
  file. It is written only when it changes.
- `<state>/rate-limits.history.jsonl` holds one such record per line.

pace dates the file by its oldest live bucket, and judges freshness from that time.

## The inbox (for writers)

paceline's module writes one reading per session to `<state>/inbox/<session id>.json` after each
model turn when the readings change, and an unchanged reading at most once a minute. Another plugin
may drop readings there in the same format. Name your files `<plugin>-<id>.json`, one per writer
and session, and overwrite your own file.

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

A writer that reads the account's own usage, the figures `/usage` shows, marks its reading as exact
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

- Any `source` other than `usage-endpoint`, or none, marks an ordinary reading.
- The merge takes the readings in the order they were observed, whatever their file names.
- An exact reading replaces the bucket of the same name, whatever its window and number, unless the
  bucket in the file is an exact reading observed later, or an ordinary reading observed more than
  15 minutes later. An ordinary reading lags the account by one request, so it does not outrank an
  exact reading taken seconds or minutes before it. Inbox files are read at every merge, so the
  15 minutes keep an old exact reading from undoing much newer ones. An exact reading whose window
  has passed does not replace a bucket of a window still running.
- For 15 minutes after an exact reading, ordinary readings leave that bucket alone, unless its
  window has passed. After that the merge rule applies again, so keep posting fresh exact readings
  for your numbers to stay.
- `scoped` is read only from an exact reading with a `seven_day` bucket. Its entries need a string
  `name` and a numeric `used_percentage`. When that weekly bucket wins, the file's `scoped` becomes
  this list, or is removed when the list is empty or absent.

The merge runs at session start and after each turn, in the background. While any session with
paceline is open, busy or idle, it also runs within about 15 seconds of a new or changed inbox file.
A pace command runs it when it finds the data older than `usage_snapshot_max_age_sec`. The merge
holds `<state>/limits.lock`, so writers take turns. paceline needs no status line plugin.

## Hooks

| Hook | When | What it does |
|---|---|---|
| Module: `UserPromptSubmit`, `PostToolBatch`, `session.compact`, `SessionStart` after a compaction | Every prompt, tool batch, and compaction | context-watch. Does nothing while context-watch is off. |
| Module: `session.measure` | Once per model turn | Writes the session's inbox reading. Always on. |
| Module: `session.start` timer | Every 15 seconds while the session is open | Lists `<state>/inbox/`, and runs `limits_merge.py` only when the inbox changed since the last merge. Always on. |
| Module: `session.start` timer | At session start, then every 10 seconds while the session is open | usage-watch. Reads its switch; while it is on and 125 seconds have passed, asks Claude Code for the account's usage. |
| `Stop`: `cache_watch.py hook stop` | After every turn | Waits for the next wake of a watched session; returns at once for any other session. |
| `UserPromptSubmit`: `cache_watch.py hook prompt` | Every prompt | Resets the wake count of a watched session. |
| `PreCompact`, `SessionStart` (`compact`): `cache_watch.py hook compact` | Before and after a compaction, manual or automatic | Turns a watched session off with the reason `compacted`, which also ends its waiting `Stop` hook. |
| `SessionEnd`: `cache_watch.py hook session-end` | Session end | Removes the session's cache-watch state. |
| `SessionStart`: `context_watch.py hook session-start` | Session start | Removes old snapshots while context-watch is on. |
| `Stop`, `SessionStart` (`startup`, `resume`, `clear`): `limits_merge.py` | After every turn, at session start | Merges the inbox in the background. Always on. |
| `PreToolUse` (`Agent`, `Workflow`): `pace_pre_agent.py` | Before each launch | Adds the `pace:` line; asks you at a hard limit. |
| `SubagentStart`, `SubagentStop`: `pace_subagent.py` | Subagent start and stop | Acquires and releases the subagent's reservation. |
| `SessionStart` (`startup`, `resume`): `pace_session_start.py` | Session start | Adds unconfirmed plans, the leftover-pool suggestion, and the time zone hint. |
| `SessionEnd`: `pace_session_end.py` | Session end | Releases the session's reservations. |

The four pace hooks exit at once, with no output, while pace is off. While a function is off, its
command hooks still start one Python process per matching event. The command hooks run `python3`
when it works, else `python`.
