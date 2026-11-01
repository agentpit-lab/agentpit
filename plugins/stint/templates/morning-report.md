# Stint report template

One report per run. Fill it from `stint report` plus the queue state; do
not hand-wave a section — every line traces to a queue item, a hold, or a status
timestamp. Keep every section; when one has nothing to report, say so in one line.

## Done

- `<item id>` — `<title>` (`<repo>`): `<one line on the outcome>`

## Deferred

- `<item id>` — `<title>` (`<repo>`): deferred after `<attempts>` attempts, reason `<reason from
  stint report>`. Dependents deferred with it: `<ids, or "none">`.

## Questions for the owner

- `<question>` — why it couldn't be resolved without them (missing decision, blocked on
  permission, ambiguous plan match, etc).

## Spend vs. estimates

| Item | Estimate % | Actual % | Delta |
| --- | --- | --- | --- |
| `<id>` | `<estimate_pct>` | `<actual_pct>` | `<actual - estimate>` |

Totals from `stint report` since `run.started_at`; note the median actual/estimate ratio when
`paceline calibrate` has one (with PaceLine).

## Silent interruptions

Detected by gaps in this run's own status timestamps (a queue item last touched more than its
own expected duration ago with no `done`/`deferred` transition, or a task stuck `running` past
the run's end). For each: `<item id>`, last known timestamp, what the queue did about it
(retried, deferred, left running).

## Budget

- Budget at start: `<run.allowance_pct>` % (`<run.source>`: PaceLine's advice for the current part of the week
  plan, or Stint's own budget, the week stop less the week's use)
- Stopped because: `<the reasons from the last stint next call, verbatim: the last | segment of its STOP line, or reasons with --json>`
