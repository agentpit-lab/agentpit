---
name: pace
description: >
  Turn pace on or off, show its status, a diagnosis or its profile, or change a profile setting.
  Pace turns the five-hour and weekly limits into a per-hour budget; while it is on, its hooks put
  a pace line before every Agent and Workflow launch, ask for confirmation at a hard limit and over the
  recommended agent count while the five-hour window burns toward its threshold, hold a
  reservation for each subagent, and at session start remind of unconfirmed plans, suggest
  leftover tasks on Thursday and Friday when the forecast leaves part of the weekly limit unused,
  and hint while the profile counts in UTC and the machine is in another zone. Use when the
  operator asks to turn pace on or off, to see the budget, to change a pace setting such as the
  time zone or the day hours, or to check why pace misbehaves.
argument-hint: "on | off | status | doctor | profile | profile set KEY VALUE"
allowed-tools:
  - Bash(paceline *)
---

Arguments: `$ARGUMENTS` (empty means `status`).

The operator's request is the approval: run the command at once, without asking first. Run exactly
one command through the Bash tool:

- `on`: `paceline on < /dev/null`
- `off`: `paceline off < /dev/null`
- `status`: `paceline status < /dev/null`
- `doctor`: `paceline doctor < /dev/null`
- `profile`: `paceline profile < /dev/null`
- `profile set KEY VALUE`: `paceline profile set KEY 'VALUE' < /dev/null` (VALUE in single quotes, as JSON for a list or a number, for example `'[8, 24]'`)

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py"` with the same
arguments.

Then tell the operator, in one or two lines, what the command printed:

- `on`, `off`: the new state and what the hooks now do, as printed.
- `status`: the budget per hour, the recommended agents and model, and any warning, as printed.
- `doctor`: the probes that are not `ok`, with their hints; `paceline doctor --json` shows the
  evidence.
- `profile`: the keys the operator asked about, as printed.
- `profile set`: the change and the new budget line as printed, or the refusal and its reason.
