---
name: pace
description: >
  Turn pace on or off, or show its status or a diagnosis. Pace turns the five-hour and weekly
  limits into a per-hour budget; while it is on, its hooks put a pace line before every Agent and
  Workflow launch, ask for confirmation at a hard limit, hold a reservation for each subagent and
  remind at session start of unconfirmed plans. Use when the operator asks to turn pace on or off,
  to see the budget, or to check why pace misbehaves.
argument-hint: "on | off | status | doctor"
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

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py"` with the same
arguments.

Then tell the operator, in one or two lines, what the command printed:

- `on`, `off`: the new state and what the hooks now do, as printed.
- `status`: the budget per hour, the recommended agents and model, and any warning, as printed.
- `doctor`: the probes that are not `ok`, with their hints; `paceline doctor --json` shows the
  evidence.
