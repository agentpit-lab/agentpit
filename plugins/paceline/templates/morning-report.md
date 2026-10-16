# Night queue report template

One report per night run. Fill it from `paceline queue report` plus the queue and pool state; do
not hand-wave a section — every line traces to a queue item, a reservation, or a status
timestamp. Keep every section; when one has nothing to report, say so in one line.

## Done

- `<item id>` — `<title>` (`<repo>`): `<one line on the outcome>`

## Deferred

- `<item id>` — `<title>` (`<repo>`): deferred after `<attempts>` attempts, reason `<reason from
  queue report>`. Dependents deferred with it: `<ids, or "none">`.

## Questions for the owner

- `<question>` — why it couldn't be resolved without them (missing decision, blocked on
  permission, ambiguous plan match, etc).

## Spend vs. estimates

| Item | Estimate % | Actual % | Delta |
| --- | --- | --- | --- |
| `<id>` | `<estimate_pct>` | `<actual_pct>` | `<actual - estimate>` |

Totals from `queue report` since `run.started_at`; note the median actual/estimate ratio when
`paceline calibrate` has one.

## Silent interruptions

Detected by gaps in this run's own status timestamps (a queue item last touched more than its
own expected duration ago with no `done`/`deferred` transition, or a task stuck `running` past
the run's end). For each: `<item id>`, last known timestamp, what the queue did about it
(retried, deferred, left running).

## Night budget

- Allowance: `<run.allowance_pct>` % · Surplus: `<run.surplus_pct>` % · Pool spent:
  `<run.pool_spent_pct>` % (only if the leftover pool was approved for this window)
- Stopped because: `<the reasons from the last paceline auto call, verbatim: the last | segment of its STOP line, or reasons with --json>`
