---
name: settings
description: >
  Show every PaceLine setting and switch with its current value, and change one. Prints the three
  switches (pace, usage-watch, away), the settings in tiers (your day, this machine) and how many Advanced settings differ from their default; a
  value that differs is marked with a "●". The pane (`pane`) edits a value with the same checks as
  `paceline profile set` and plans the week in its Calendar tab. Use when the operator asks what is set, which settings PaceLine has, what a
  setting is now or by default, or wants to look at or change settings in one place.
argument-hint: "[pane | advanced [topic]]"
allowed-tools:
  - Bash(paceline *)
---

Arguments: `$ARGUMENTS`.

This skill is the fallback for a session where PaceLine's hooks module does not run; where it runs,
`/paceline:settings` is answered before this text is read, and `pane` opens an editable pane. The
operator's request is the approval: run the command at once, without asking first.

Show the settings: run `paceline settings < /dev/null` through the Bash tool and show what it
printed. It lists every profile setting with its value, a `*` before each value that differs from its
default, the three watcher settings, the four switches and the time zone mode.
If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py" settings` instead.

- With `advanced` and an optional topic, run the same plain `paceline settings` (the command takes no
  `advanced` or topic argument) and show from its output only the settings that sit under Advanced in
  the pane:
  the profile settings other than the time zone, the working hours, the five-hour stop, the memory guard and
  its limits, and the agent limit. Name the topic's settings if one was given.
- With `pane`, say that the pane needs PaceLine's hooks module, which does not run in this session, and
  show the text above instead.

Change a value only when the operator asks. Run `paceline settings --json < /dev/null` first for the
current value, default and range, then one command from this table with the value as JSON, and tell
the operator what it printed, in their language:

| Change | Command |
|---|---|
| A profile setting | `paceline profile set KEY 'VALUE' < /dev/null` |
| The five-hour stop | `paceline profile set five_hour_stop_pct '90' < /dev/null` |
| The memory guard | `paceline profile set memory_guard 'true' < /dev/null` (a JSON boolean; `'false'` turns it off) |
| The time zone follows the machine, or stays fixed | `paceline profile set timezone_mode machine\|fixed < /dev/null` |
| A fixed time zone | `paceline profile set timezone NAME < /dev/null` |
| context-watch or cache-watch settings | They moved to the PitCrew plugin: `/pitcrew:context-watch`, `/pitcrew:cache-watch` |
| pace | `paceline on < /dev/null`, `paceline off < /dev/null` |
| usage-watch | `paceline usage-watch on\|off < /dev/null` |
| away | `paceline away on --session ${CLAUDE_SESSION_ID} < /dev/null`, `paceline away off < /dev/null` |

A refused value prints the reason and changes nothing; pass it on. The `pace` skill covers `profile set`
in more detail.
