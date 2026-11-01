---
name: pace
description: >
  Turn pace on or off, show its status, a diagnosis or its profile, change a profile setting, or
  record a plan change. Pace turns the five-hour and weekly limits into a per-hour budget; while it is on, its hooks ask
  for confirmation before an Agent or Workflow launch at a hard limit and at the weekly reserve's stop line, and,
  in limit mode, when the current part of the week plan is over its share; in limit mode they also tell the model
  the pace in one plain line before every launch (in inform mode the model reads nothing unless a stop holds the
  launch); they place a hold for each subagent, and at session start hint while the profile counts in UTC and the machine is in
  another zone. Use when the
  operator asks to turn pace on or off, to see the budget, to change a pace setting such as the
  time zone, the five-hour stop or the working hours, or to check why pace misbehaves, or states a plan change in words
  ("away Thursday-Friday", "tomorrow is a heavy day").
argument-hint: "on | off | status | doctor | profile | profile set KEY VALUE | simulate | plan"
allowed-tools:
  - Bash(paceline *)
---

Arguments: `$ARGUMENTS` (empty means `status`).

The operator's request is the approval: run the command at once, without asking first. Run exactly
one command through the Bash tool (`plan` may need two; see below):

- `on`: `paceline on < /dev/null`
- `off`: `paceline off < /dev/null`
- `status`: `paceline status < /dev/null`
- `doctor`: `paceline doctor < /dev/null`
- `profile`: `paceline profile < /dev/null`
- `profile set KEY VALUE`: `paceline profile set KEY 'VALUE' < /dev/null` (VALUE in single quotes, as JSON for a number or a switch, for example `'true'` for `memory_guard`)
- `simulate`: `paceline simulate < /dev/null` (a service command that replays the profile through a model week; the operator may add `--week`, `--night-queue` or `--defaults`; see below)
- `plan`: a plan change the operator states in words or dates; see below.

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py"` with the same
arguments.

Profile settings the operator may ask about:

- `timezone_mode`: `machine` (the default for a new profile) counts in this machine's time zone; `fixed` counts in the profile's `timezone`. Setting `timezone` fixes the zone and says so; `paceline profile set timezone_mode machine` follows the machine again. A profile that existed before 0.5.0 is `fixed`.
- `five_hour_stop_pct` (95 by default, 0-100) is the five-hour usage from which each Agent or Workflow launch asks first, in working hours and off-hours alike.
- `memory_guard` (off by default): `paceline profile set memory_guard true` turns the host-memory floor on, so that below `min_free_commit_mb` of free memory a launch asks first and Stint's run stops (it obeys `paceline advice`); while it is off, memory is not measured. `min_free_commit_mb`, `commit_reserve_mb`, `heavy_job_mb` and `max_heavy_runs` apply only while it is on.
- The weekly reserve is not a profile key: it is kept per account in the plan file (8 % by default, released whole at the start of working hours of its release day, by default the last day before the reset). `paceline status` and `paceline plan` print it as `reserve 8 % kept, opens Fri 30 09:00` (`kept to the reset Sat 31 05:00` in a week with no release candidate); `paceline calibrate` may propose a new size. `paceline reserve [PCT] [--release mon..sun|last] [--name TEXT]` shows or sets it (`reserve 10 --release fri --name tests`; `0` turns it off), and before the release a launch asks once the week's usage reaches 100 less the reserve (the reserve stop); budget bookings for long runs are `paceline hold`, not `paceline reserve`.

`simulate` replays a week through the budget with the operator's profile (`--defaults`: the built-in defaults); it is a model, so say so when reporting its figures, and show the table as printed. To see or change every setting in one place, point the operator to `/paceline:settings` (`/paceline:settings pane` opens an editable pane); `paceline settings` prints the same settings.

`plan`: the week is a calendar of parts, each day's working hours (☼) and off-hours (☾), with a mark and a share of what is left. When the operator states a plan change in words, mark the days with `paceline plan set DAY --work V --off-hours V < /dev/null` (give one or both options). DAY is `today`, `tomorrow`, `mon` to `sun` (the next such day) or `YYYY-MM-DD`; V is `off`, `light`, `normal`, `heavy`, or a number from 0 to 2 in steps of 0.25. "Away" is `--work off --off-hours off`; "a heavy day" is `--work heavy`. `paceline plan` shows the week, `paceline plan reset [DAY] < /dev/null` removes marks, `paceline plan template set WEEKDAY --work V` sets a weekday for every week, `paceline mode inform|limit [--until-part-end]` shows or sets the mode, and `paceline profile set work_hours HH:MM-HH:MM` sets the working hours. A mark for the part that is running splits it at once: the part ends now with its share and spending, and the rest starts now with a share from what is left of the week by the new mark; the command prints the split line, so pass it on (while the away marker is on the part is not split and the command says so). A mark for a part that has ended changes nothing this week, and the command says so. When the model's context carries a PaceLine line saying the operator is working in off-hours that this week's plan gives no share, ask the operator once whether to plan it (`light`, `normal` or `heavy`); on an answer run `paceline plan set DAY --off-hours V < /dev/null` with the date the line names, and on a no run `paceline plan offer --refuse < /dev/null`, which stops the question until the part ends. A day that owns no part of the current week exits 2: ask for another day.

Then tell the operator, in one or two lines, what the command printed:

- `on`, `off`: the new state and what the hooks now do, as printed.
- `status`: the budget per hour and any warning, as printed.
- `doctor`: the probes that are not `ok`, with their hints; `paceline doctor --json` shows the
  evidence.
- `profile`: the keys the operator asked about, as printed.
- `profile set`: the change and the new budget line as printed, or the refusal and its reason.
- `simulate`: the table as printed, and that it is a model.
- `plan`: after `plan set` or `reset`, the day and marks as printed and any line saying a mark changes
  nothing this week; after `plan` alone, the week as printed; after `mode`, the mode as printed.
