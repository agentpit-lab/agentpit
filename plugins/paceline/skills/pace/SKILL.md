---
name: pace
description: >
  Turn pace on or off, show its status, a diagnosis or its profile, change a profile setting, or
  record a plan change. Pace turns the five-hour and weekly limits into a per-hour budget; while it is on, its hooks put
  a pace line before every Agent and Workflow launch, ask for confirmation at a hard limit and over the
  recommended agent count while the five-hour window burns toward its threshold, hold a
  reservation for each subagent, and at session start remind of unconfirmed plans, suggest
  leftover tasks on Thursday and Friday when the forecast leaves part of the weekly limit unused,
  and hint while the profile counts in UTC and the machine is in another zone. Use when the
  operator asks to turn pace on or off, to see the budget, to change a pace setting such as the
  time zone or the day hours, or to check why pace misbehaves, or states a plan change in words
  ("away Thursday-Friday", "tomorrow is a heavy day").
argument-hint: "on | off | status | doctor | profile | profile set KEY VALUE | plan"
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
- `profile set KEY VALUE`: `paceline profile set KEY 'VALUE' < /dev/null` (VALUE in single quotes, as JSON for a list or a number, for example `'[8, 24]'`)
- `plan`: a plan change the operator states in words or dates; see below.

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py"` with the same
arguments.

`plan`: when the operator states a plan change in words, record it with `paceline plan add --type away|heavy --from DATE --to DATE --phrase
'PHRASE' < /dev/null` (PHRASE is their words; add `--weight N` for a heavy day). DATE is `YYYY-MM-DD`, `today`,
`tomorrow`, or an ISO datetime in the profile's time zone; a bare `--to` date includes that day. To move, shorten or cancel a plan, first run `paceline plan find --on DATE --type TYPE < /dev/null`
(add `--text 'PHRASE'` only with words from the original phrase, since it is a substring match), then act on its exit code: `0` is one match, so run `paceline plan
modify ID --from DATE --to DATE < /dev/null` or `paceline plan cancel ID < /dev/null` at once; `3` is
several, so ask the operator which; `4` is none, so ask before guessing.

Then tell the operator, in one or two lines, what the command printed:

- `on`, `off`: the new state and what the hooks now do, as printed.
- `status`: the budget per hour, the recommended agents and model, and any warning, as printed.
- `doctor`: the probes that are not `ok`, with their hints; `paceline doctor --json` shows the
  evidence.
- `profile`: the keys the operator asked about, as printed.
- `profile set`: the change and the new budget line as printed, or the refusal and its reason.
- `plan`: after `plan add`, `modify` or `cancel`, the new budget per hour from the command's output
  line, for example `budget 1.21%/h, night 5.0%`; after `find`, the matches as printed.
