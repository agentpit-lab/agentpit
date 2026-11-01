---
name: queue
description: >
  Manage the queue of unattended work: add items with `stint add`, list them, show the report, and
  retry, remove or clear items. Use when the operator wants to queue work for later, to see what is
  queued, or to change the queue. To run the queue, use /stint:run.
argument-hint: "[what to queue or change]"
allowed-tools:
  - Bash(stint *)
---

Queue work with `stint add --title '<what>' --repo '<repository>' --estimate <percent of the week>`,
adding `--independent` for an item that may run beside another independent item and
`--depends Q1,Q2` for items that must be done first. Without an estimate from the operator, estimate
the spend yourself and say so.

When the output of `stint add --json < /dev/null` has a `tonight_off_hint` that is not null, tonight's
off-hours share is 0 and the queue will not run tonight. Ask the operator at once whether to run the
command the hint names (`paceline plan set <date> --off-hours 0.5`), and run it only on their yes.

- `stint list < /dev/null`: every item with its status, estimate, dependencies and reason.
- `stint report < /dev/null`: done, deferred, running and pending items of the current run.
- `stint retry <id>`: put a deferred or failed item back to pending.
- `stint remove <id>`: remove an item; refused while a pending or running item depends on it.
- `stint clear-done`: remove the done items.
- `stint config`: the settings Stint applies without PaceLine (`--week-stop`, `--five-hour-stop`,
  `--agents` change them).

Append `< /dev/null` to every command. If `stint` is not found, run
`python "${CLAUDE_PLUGIN_ROOT}/scripts/stint.py"` with the same arguments.
