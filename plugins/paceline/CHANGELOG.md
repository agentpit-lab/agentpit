# Changelog

## 0.6.0 - 2026-10-31

### Added

- A week plan. Each day has two parts, its working hours (☼) and the off-hours after them (☾), and each
  part has a mark from 0 to 2 in steps of 0.25: `off` 0, `light` 0.5, `normal` 1, `heavy` 1.5. What is left
  of the week after the reserve is shared among the remaining parts by their marks, and a part's share is
  fixed when the part starts. By default working hours are `normal` and off-hours `off`.
- `paceline plan [--json]` shows the week to the reset: each day's marks, shares and a bar against the even
  pace, the current part with its spend against its share, cut parts with their hours, what is unplanned,
  and, once `paceline calibrate` has led you to set `five_hour_to_week_pct` to a measured value, a warning
  when a share is more than the part's five-hour windows can deliver (with the default 10.0 no warning shows).
- `paceline plan set DAY [--work V] [--off-hours V]` marks a day, `paceline plan reset [DAY]` removes marks,
  and `paceline plan template [show | set WEEKDAY [--work V] [--off-hours V] | reset [WEEKDAY]]` sets the marks every week starts from.
- `paceline mode [inform | limit] [--until-part-end]`. `inform`, the default, only shows the plan; `limit`
  makes an Agent or Workflow launch ask first when the current part has spent more than 1 point above its
  share by now. The first question of a part names `paceline mode inform --until-part-end`, which stops the
  questions until the part ends. While the away marker is set, the question is a denial.
- `paceline profile set work_hours HH:MM-HH:MM` sets the working hours (09:00-18:00 by default), in steps of
  15 minutes; the new hours apply from the end of the running part. The working hours belong to the week plan
  of each account, not to the profile.
- The settings pane has a Calendar tab: a card per date with its ☼ and ☾ marks, shares and a bar on a grey track, where −
  and + change a part that has not started; an ended part shows its spend, or `not recorded`; the days that have ended fold into one line that unfolds when pressed. The tabs
  sit in the pane header, with Refresh apart on the right. The pane and `paceline settings` show the working hours.
- `paceline status --json` has `plan` (the mode and the current part's share, spend and state) and
  `budget_part`; in `limit` mode the `pace:` line before a launch says whether the part is on plan, over its share or unplanned.
- `paceline queue add` says when tonight's off-hours share is 0, with the command that plans some.
- The budget simulator takes a week plan and the working hours.
- The weekly reserve is a safety stop outside the plan. Until its release instant, an Agent or Workflow
  launch asks when the week's usage is at or past 100% minus the reserve (92% with the default 8%), in
  `inform` and `limit` mode alike; with the away marker on, the launch is denied. The reserve is 8% by
  default, one per account, kept in the plan file.
- `paceline reserve [PCT] [--release DAY] [--name TEXT] [--json]` shows or sets the reserve: its size (0 to
  50, 0 turns it off), the weekday it is released on (`mon` to `sun`, or `last`, the last day before the
  reset, the default) and a name.
- `paceline hold acquire`, `renew`, `release`, `release-session`, `list`, `prune` and `reconcile` manage
  holds, the budget bookings that were called reservations.
- The settings pane and `paceline settings` show and set `reserve_pct`, `reserve_release` and `reserve_name`
  beside the working hours.
- A `reserve` object in `paceline status --json` and `paceline plan --json`: `pct`, `name`, `release`,
  `release_at`, `release_text`, `release_at_local`, `release_day`, `reset_text`, `released`, `kept_pct`,
  `stop_at_pct`, `used_pct`, `over` and `text`, with an error form when the reserve cannot be computed. `paceline status` and `paceline plan` print it as
  `reserve 8 % kept, opens Fri 30 09:00`.
- A `PaceLine` button opens the settings pane, as `/paceline:settings pane` does, and a second click
  closes it. It reads `[ PaceLine ]` in the Desktop footer. With PitBoard it
  follows PitBoard's button in the CLI row and in the Desktop footer; without PitBoard it sits alone in
  the Desktop footer, and the CLI has none.
- The week plan file, `week-plan-<key>.json`, for readers such as PitBoard: the current part, its fixed
  share, the reserve and the later parts. It is written while pace is on, by the commands that fix or
  change the plan and by the limits merge when the file no longer holds the current part or its week (also when the
  weekly window resets ahead of schedule or its reset moves later), and documented in the reference.

### Changed

- A launch that would ask at a stop (hard limit, weekly reserve, plan in `limit` mode) is denied instead when
  the session's permission mode is `auto`, `bypassPermissions` or `dontAsk`, as it already is while the away
  marker is set: no one answers the prompt, and in `auto` the classifier approved it, so the launch ran past
  the stop. The reason tells the owner that switching the session to the default mode lets them confirm it.
- The budget per hour follows the week plan: it is the current part's share less what the part has spent,
  over the hours left in the part. The day and night hours, the weekday and weekend weights and the weekend
  cap are gone. The `paceline status` line names the norm of the part, `working-hours norm` or `off-hours
  norm`.
- The night queue's allowance is tonight's off-hours share. Off-hours are marked 0 by default, so a night
  queue has no allowance until you mark the night, for example `paceline plan set today --off-hours 0.5`; the
  fixed 5% night base is gone. A night run ends when the off-hours end, at the next working start (09:00 by
  default, was 08:00).
- With the default plan the budget runs in the working hours, 09:00-18:00 on every day of the week,
  Saturday and Sunday included; it was 08:00-22:00 with the weekend capped at 20% of the week. Outside the
  working hours the budget is 0 until you mark the off-hours, for example
  `paceline plan set today --off-hours 0.5`.
- The reserve is released whole at the start of working hours of its release day, in the profile's time
  zone, and kept again after the next reset; it was released gradually. The plan shares what is left after
  the reserve, and from the release instant the released reserve is spread over the remaining parts by
  their weights. The reserve is set per account, no longer by the profile or a spending style.
- "Reservation" is "hold" in commands, texts, ids and JSON keys: `paceline hold` replaces the subcommands of
  `paceline reserve`, and `active_reservations`, `launch_reservation` and `outstanding_reservations_pct` are
  `active_holds`, `launch_hold` and `outstanding_holds_pct`. A queue item's `reservation_id` is `hold_id`,
  `reservation` in the `--json` output of `paceline hold acquire` and `renew` is `hold`, ids start with `H-`
  instead of `R-`, and the `parameter` of a `paceline calibrate` proposal is `reserve`, not `reserve_pct`.
- Holds live in the new `pace-holds.json`. The old `pace-registry.json` is no longer read and may be
  deleted, so `paceline calibrate`'s subagent and task evidence starts again. Restart sessions started
  before the update.
- `paceline calibrate` proposes a new reserve with a `paceline reserve` command, judged by the usage at the
  reserve's release instant.
- `paceline simulate` replays the week through the week plan: the operator spends in working hours, a night
  queue spends the off-hours share, and the working hours and weekday template come from your plan file.
- A profile from 0.5.0 loses `day_hours`, `night_hours`, `weekday_weight`, `weekend_weight`,
  `weekend_cap_pct` and its declared plans when it is read; nothing is converted. `paceline profile set` of one
  of those keys exits 2.
- `paceline doctor` shows the account each source would give: the session's, the variables' and the state
  file's.
- One five-hour stop, `five_hour_stop_pct`, 95% by default, in working hours and off-hours alike, replaces
  `five_hour_threshold_day` (80%), `five_hour_threshold_night` (95%) and `five_hour_hard_stop` (95%). From it
  every Agent or Workflow launch asks first, the night queue waits and the burn rate is projected. In working
  hours the stop moves from 80% to 95%, so the day capacity and the reach of a part change; PitBoard's `5h limit`
  card shows the threshold 95 in working hours too. A profile with `five_hour_hard_stop: 97` loads with the stop
  at 95; nothing is converted.
- The `pace:` line before a launch is plain words and goes to the model only in `limit` mode. In `inform` mode
  the model reads nothing, except a short text when a safety stop holds the launch. When a stop or the plan gate
  holds a launch, the text says what holds it, what happens to the model's launches and that a declined launch
  is not to be retried. It names no model and no agent count, and states a stop only when usage is within reach
  of it. With no weekly reading the hook says nothing.
- Host-memory protection is the profile switch `memory_guard`, off by default. While it is off, memory is
  measured only where a person reads it (`paceline status` without a launch key, `paceline doctor`), and no
  launch, night allowance or `paceline auto` answer depends on it; `min_free_commit_mb`, `commit_reserve_mb`,
  `heavy_job_mb` and `max_heavy_runs` apply only while it is on, and `host` in `paceline status --json` shows
  `guard`, `free_commit_mb`, `min_free_commit_mb` and `max_heavy_runs`. To keep the free-memory floor of
  earlier versions, turn it on with `paceline profile set memory_guard true` right after installing.
- `paceline calibrate`, `paceline merge`, `paceline doctor` and `paceline simulate` are listed under Service
  commands in the reference. `paceline simulate` replays the current profile, or the defaults, for each week
  shape and queue choice; its rows lose `style`, `throttled_h` and `paused_h`.
- `paceline status --json` shows `five_hour_to_week_pct`, the share of the weekly limit one five-hour window
  uses, at the top level; the `five_hour` object no longer holds `hard_stop_pct`, and `threshold_pct` is the
  one stop.
- The budget simulator page no longer shows agents or a model; it shows what the model reads in `limit` mode.
- The recommendation in `paceline status --json` is `{blocked, reasons}`. `paceline auto` keeps
  `max_parallel_agents` and no longer names a model; the night-queue skill leaves the model of an item to the
  orchestrator and reads the model-scoped buckets from `weekly.scoped`.
- `paceline off` writes the switch first, loads no usage and removes the week plan files under the pace lock,
  or without it when the lock stays busy; `paceline on` now takes the pace lock to publish the plan.
- With pace on, the hooks module fixes the current part within about 15 s of each boundary while a session
  is open; a part no longer waits for the first PaceLine command.

### Removed

- `paceline plan add`, `list`, `find`, `modify`, `cancel`, `confirm` and `tomorrow`, and the session-start
  reminder of unconfirmed plans. Use `paceline plan set` and the template.
- The `weekend` object and `current_plan` in `paceline status --json`.
- The profile keys `reserve_pct`, `reserve_decay_start` and `reserve_release_min_hours`, and the gradual
  release of the reserve. A profile that holds them loses them when it is read; nothing is converted, and
  `paceline profile set` of one of them exits 2.
- `paceline reserve acquire`, `renew`, `release`, `release-session`, `list`, `prune` and `reconcile`: they
  exit 2 and name `paceline hold`.
- `reserve_pct`, `reserve_release_start` and `reserve_release_start_local` in `paceline status --json`, and
  `reserve_pct` in `paceline plan --json`; read the `reserve` object instead.
- The spending styles (`careful`, `balanced`, `full`): `paceline profile style`, the pane's Spending style
  tier, `pace-styles.json` (a leftover file is never read) and the week preview in `paceline settings` and
  the pane; `paceline simulate --style` and `--preview`.
- The throttle and the choice of models: the profile keys `models`, `routine_models`, `throttle_weekly_pct` and
  `scoped_exhausted_pct`, `allowed_models` and `recommendation.model`. PaceLine names no model in the line, in
  `paceline status` or in `paceline auto`; the model-scoped buckets left the `paceline status` text and stay in
  `paceline status --json` under `weekly.scoped`.
- The Spending style block of `paceline settings`; the pane's tier of that name went with the styles.
- The Thursday-Friday leftover suggestion at session start (`leftover_suggest_pct`, `suggest_leftover_tasks`).
  The leftover pool (`paceline queue --pool`) stays.
- The five-hour burn ask and the agent count in the line and in `paceline status`
  (`max_parallel_agents`, `background_paused` and `five_hour_burn_limited` of the recommendation).
- The profile keys `five_hour_threshold_day`, `five_hour_threshold_night`, `five_hour_hard_stop`, `models`,
  `routine_models`, `throttle_weekly_pct`, `scoped_exhausted_pct` and `leftover_suggest_pct`: a profile that
  holds them loses them when it is read; nothing is converted, and `paceline profile set` of one of them
  exits 2.

### Fixed

- Limit readings are filed under the account of the session's own credential, which PaceLine asks Claude
  Code's profile endpoint for. A `claude` started from a Claude Code Desktop terminal tab or a session's
  shell inherited the Desktop account's variables and filed its readings, and its usage-watch readings, under
  the Desktop account, so the two accounts' weekly figures took turns in one partition. Until the session's
  account is known, no reading is written and pace shows no figures; when the profile request is refused or
  fails, a terminal session takes the account from `.claude.json` and a Desktop session writes nothing.
  Readings already in the history stay where they are.
- A hold taken with `paceline hold acquire` or `paceline queue start` from Claude Code's Bash
  tool on Windows was dropped at the next pace call: the Git Bash launcher had already exited, so the
  owner fell back to that short-lived shell. The owner is now the Claude Code process that `CLAUDE_PID`
  names when the parent chain breaks, and a hold with no owner to find lives until it is released
  or its lease ends. `paceline doctor` shows the owner and its source: `doctor --json`'s process probe has `claude_pid_env`,
  `owner_pid` and `owner_source` in place of `fallback_pid` (#117).

## 0.5.0 - 2026-10-26

### Added

- `/paceline:settings` shows every PaceLine setting and switch in tiers (your day, this machine, the
  watchers, the spending style), marks a value that differs from its default with a "●", and counts
  the Advanced settings that differ. `/paceline:settings pane` opens a pane that edits a value with
  the same checks as `paceline profile set`, switches pace, usage-watch, away and context-watch, and
  shows a preview of each spending style. `/paceline:settings advanced [topic]` lists the Advanced
  settings. The hooks module answers the command before the model runs;
  without the module the skill runs `paceline settings`, which prints the same settings.
- `paceline settings` (with `--json`) prints every setting and switch with its value, its default
  and where it comes from. It changes nothing.
- Three spending styles, careful, balanced and full, each a set of nine profile values that move
  together. `paceline profile style NAME` takes one; `pace-styles.json` in the state directory
  overrides a style's values by hand, and a value it cannot use is ignored with a warning. A profile
  whose nine values match no style exactly, as an existing profile with any of them changed does,
  shows as `custom`.
- `paceline simulate` replays a week hour by hour through the real budget code for each style, week
  shape (on track, heavy start, light start, away Thursday and Friday) and night queue, and prints
  how much of the weekly limit each leaves unused and how often the hook would ask. It is a model: one
  reading per hour, no plans or reservations. `--preview --cached` is the view's selection, kept in
  `pace-simulate.json`.
- Two profile keys. `timezone_mode` is `machine` (the profile counts in this machine's time zone) or
  `fixed`. `reserve_release_min_hours` is the number of hours before the weekly reset by which the
  reserve starts to release at the latest.

### Changed

- A new profile follows the machine's time zone (`timezone_mode` `machine`); an existing profile
  migrates to `fixed` and keeps its zone. `paceline profile set timezone NAME` fixes a zone and
  says so; `paceline profile set timezone_mode machine` follows the machine again.
- The reserve is released no later than `reserve_release_min_hours` before the reset, 54 by default
  (careful 0, balanced 54, full 54), so the release no longer starts only on the configured weekday
  and hour when that is later. Existing profiles migrate to 54. `paceline profile set
  reserve_release_min_hours 0` restores the earlier start.
- On a weekend the cap grows by the reserve released since the weekend began, so the released
  reserve can be spent then. `paceline status --json` shows it as `weekend.reserve_released_pct`, and
  adds `reserve_release_start` and `reserve_release_start_local` at the top level.
- `paceline calibrate` decides whether to raise `reserve_pct` from what was left when the reserve was
  released, not from what was unused at the reset, which the weekend rule now spends down to about 1%.
  A week with no reading by then is skipped, and the evidence gains `left_at_release_pct`.
- cache-watch keeps its defaults (`lead_minutes`, `max_wakes`) in `<state>/cache-watch.json`, shared by
  every installation, instead of `defaults.json` in each installation's data directory. Values kept
  in the old file are not carried over.
- The settings view and the inbox merge still find `python` when `python3` is missing, also when the
  engine's rejection of `python3` is worded in a way the module does not recognise: a read tries the
  next interpreter after such a rejection, and a write stays final and shows the engine's message.
- The Ukrainian documentation pages use the impersonal register.
- Eleven profile keys that tune the implementation are fixed at their former defaults and are no
  longer settings: `lease_ttl_min`, `agent_lease_ttl_min`, `stale_threshold_min`,
  `usage_snapshot_max_age_sec`, `auto_refresh_five_hour_pct`, `auto_refresh_cache_sec`,
  `calibration_min_days`, `gate_fix_attempts`, `capacity_warn_ratio`, `heavy_weight` and
  `night_base_pct`. `host_night_capacity_pct` is removed: the night allowance is no longer capped by
  a set share, and still drops to 0 while free memory is low. A stored profile loses these twelve keys
  when it is read, without a message; `paceline profile set` refuses them as unknown keys. The
  Advanced settings list 6 entries instead of 18, and `paceline calibrate` no longer proposes a night
  base.

## 0.4.11 - 2026-10-22

### Fixed

- The how-to page gains "Seat PaceLine as your organization's plugin": scripts for Windows, macOS and
  Linux, shipped in the marketplace's `tools/`, that seat PaceLine and PitBoard past Claude Code's
  sec-default guard, with the console key they need and how to verify, update and undo. The
  limitations page points there and no longer says that `claude plugin list` shows the plugin with
  `Scope: managed`.

## 0.4.10 - 2026-10-20

### Changed

- context-watch no longer adds its context line to every prompt unless you turn it on with
  `paceline context-watch config --line on`. The warnings at the set levels and the snapshots at each
  compaction are unchanged. An existing settings file that has `line` keeps its value.
- The `night-queue` skill tells the agent to give the filled morning report in its reply and not to
  write it to a file.

## 0.4.9 - 2026-10-20

### Added

- The `night-queue` skill (`/paceline:night-queue`) tells the agent how to work a queue the operator
  left running: start the run, obey the exit code of `paceline auto` before each item, report failures
  and deferrals, and fill the morning report.
- The `pace` skill takes a `plan` argument and triggers when the operator states a plan change in
  words: it records the change with `paceline plan add`, finds a plan to move or cancel with
  `paceline plan find`, and reports the new budget per hour.

## 0.4.8 - 2026-10-18

### Fixed

- The limitations page corrects the administrator's workaround for Claude Code's sec-default guard,
  now tested on a Team organization. Naming the plugin in managed `appendPlugins` does not lift the
  guard while you install PaceLine yourself from the public marketplace. Seating it as the
  organization's plugin does: a `directory` marketplace in managed `extraKnownMarketplaces`, with
  `enabledPlugins` and `appendPlugins` naming it; where the organization has server-managed
  settings, the keys go into the admin console.

## 0.4.7 - 2026-10-18

### Changed

- The hooks module clears the away marker; the `UserPromptSubmit` command hook `away.py hook prompt`
  is gone, so one Python process fewer starts on every prompt. Only your own message clears the
  marker now: a prompt typed in the terminal or in Claude Code Desktop, sent through Remote Control,
  or sent from Slack. A prompt from a schedule, a loop, a notification, another session, a channel,
  `claude -p` or the Agent SDK leaves it set; if you work only through `claude -p` or the Agent SDK,
  clear it with `paceline away off`.
- The hooks module checks the inbox as soon as Claude Code starts, as well as every 15 seconds. The
  `Stop` and `SessionStart` command hooks that ran `limits_merge.py` are gone, so no Python process
  starts after a turn for the merge.

## 0.4.6 - 2026-10-18

### Fixed

- The hooks read their input as UTF-8 whatever the locale's code page. On Windows with a non-UTF-8
  code page such as cp1252 or cp1251, a hook input holding non-ASCII text, for example a Cyrillic
  prompt or a path under a non-ASCII user profile, failed to decode or decoded garbled, and the hook
  silently did nothing: no pace line, gate or reservation for an agent launch, no release at session
  end, an away marker the operator's message did not clear, a cache-watch that ignored the prompt or
  the compaction, and context-watch cleanup that skipped a project with a non-ASCII path.

## 0.4.5 - 2026-10-18

### Changed

- The limitations page says that context-watch does not run where an organization manages Claude
  Code (Team and Enterprise logins, and machines with managed settings): Claude Code's sec-default
  guard holds back the events it runs on. It gives an administrator's workaround for a small team,
  not tested. A fix is planned for a later version.

## 0.4.4 - 2026-10-18

### Fixed

- The reference lists `away` among the commands that do not take the common options; `paceline away`
  accepts only `--session`.

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
