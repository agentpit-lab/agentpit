---
name: night-queue
description: >
  Work the night queue: run the items the operator queued with `paceline queue add`, one at a time,
  obeying what `paceline auto` answers (start, wait or stop) for the budget, agents and models, then
  fill the morning report. Use when the operator asks to run or work the night queue, or leaves a
  queue running and goes away.
argument-hint: "(no arguments)"
allowed-tools:
  - Bash(paceline *)
---

Run only when the operator has left a queue running. Start the run once with
`paceline auto --start-run < /dev/null`.

Before each item run `paceline auto --json < /dev/null` and obey its exit code and parameters
without re-deciding them. Read `next_item.id`, `next_item.source`, `max_parallel_agents`,
`allowed_models`, `budget_for_next_item_pct` and `wait_until` from its JSON; the text output lacks
them.

- `0` start: run `paceline queue start <id> < /dev/null` for `next_item.id`, run the item with
  `max_parallel_agents`, `allowed_models` and `budget_for_next_item_pct`, then
  `paceline queue done <id> < /dev/null`. When `next_item.source` is `pool` (IDs `L1`, `L2`, ...),
  add `--pool` to `queue start`, `done`, `fail` and `defer`.
- `10` wait: hold until `wait_until`, then run `paceline auto --json` again. When `wait_until` is
  null (waiting for running items or dependencies), run it again after a few minutes.
- `11` stop: end the run.

Stale usage data (over 30 minutes) makes `auto` answer wait: take no new item, let running items
finish.

Run the item `auto` names, not the next one in the list. Each time the item's checks fail, run
`paceline queue fail <id> --reason '<why>' < /dev/null`, then fix them; the third failure defers the
item and every item that depends on it. Defer an item that needs the operator's permission with
`paceline queue defer <id> --reason '<why>' < /dev/null`, then move on to the next independent item.
Stop only when `paceline auto` says stop.

In the morning fill `${CLAUDE_PLUGIN_ROOT}/templates/morning-report.md` from
`paceline queue report < /dev/null`: done, deferred and why, questions for the operator, spend
against estimates, silent interruptions. Give the filled report in your reply to the operator; do
not write it to a file.

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py"` with the same
arguments.
