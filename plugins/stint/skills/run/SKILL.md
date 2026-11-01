---
name: run
description: >
  Work the queue: run the items the operator queued with `stint add`, one at a time, obeying what
  `stint next` answers (start, wait or stop) for the budget and agents, then fill the morning report.
  Use when the operator asks to run or work the queue, or leaves a queue running and goes away.
argument-hint: "(no arguments)"
allowed-tools:
  - Bash(stint *)
  - Bash(paceline *)
  - Bash(python *stint.py*)
---

Run only when the operator has left a queue running. Start the run once with
`stint next --start-run < /dev/null`.

Before each item run `stint next --json < /dev/null` and obey its exit code and parameters
without re-deciding them. Read `next_item.id`, `max_parallel_agents`, `budget_for_next_item_pct`,
`wait_until`, `source` and `limits` from its JSON; the text output lacks them.

`source` says whose limits decided. `paceline`: PaceLine's advice (`advice` in the JSON) gave the
budget, the agents, the waits and the safety stops. `stint`: PaceLine is not installed or gave no
usable advice, and Stint's own stops applied, read from the limits Claude Code hands to a session
(`limits` holds the week and five-hour figures, their stops and the reading's age). Read `reasons` for
why it waits or stops.

`next` names no model. Choose the model of each item yourself. With PaceLine, read the model-scoped
weekly buckets from `weekly.scoped` of `paceline status --json < /dev/null`: a model whose scoped weekly
limit is at 100 % cannot run, so give its items to another model or defer them. Without PaceLine no
scoped figures exist: choose by the item's needs.

- `0` start: run `stint start <id> < /dev/null` for `next_item.id`, run the item with
  `max_parallel_agents` and `budget_for_next_item_pct`, then `stint done <id> < /dev/null`.
- `10` wait: hold until `wait_until`, then run `stint next --json` again. When `wait_until` is null
  (waiting for running items or dependencies, or for a first usage reading), run it again after a few
  minutes.
- `11` stop: end the run.

When an advice reason has the code `share_zero` (the current part has no share), tell the operator its
`command`, which plans some, in the morning report; do not run it yourself. A wait that would last past
the end of the run arrives as stop.

Stale usage data (over 30 minutes) makes `next` answer wait: take no new item, let running items finish.

Run the item `next` names, not the next one in the list. Each time the item's checks fail, run
`stint fail <id> --reason '<why>' < /dev/null`, then fix them; the third failure defers the item and
every item that depends on it. Defer an item that needs the operator's permission with
`stint defer <id> --reason '<why>' < /dev/null`, then move on to the next independent item. Stop only
when `stint next` says stop.

In the morning fill `${CLAUDE_PLUGIN_ROOT}/templates/morning-report.md` from `stint report < /dev/null`:
done, deferred and why, questions for the operator, spend against estimates, silent interruptions. Give
the filled report in your reply to the operator; do not write it to a file.

If `stint` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/stint.py"` with the same arguments.
