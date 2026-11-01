# Changelog

## 0.1.0 - 2026-11-01

### Added

- Stint as a plugin of its own: the queue of unattended work that PaceLine 0.6.0 and earlier kept as
  `paceline queue` and `paceline auto`. Commands `stint add`, `list`, `report`, `start`, `done`, `fail`,
  `defer`, `retry`, `remove`, `clear-done`, `config` and `next`, and the `/stint:run` and `/stint:queue`
  skills.
- `stint next` answers start, wait or stop (exit 0, 10, 11). With PaceLine it obeys `paceline advice`
  (budget, agents, waits and safety stops) and holds each running item in PaceLine's registry. Without
  PaceLine it stops at its own limits: the week at 85 % and the five-hour window at 95 %
  (`stint config` changes them), read from the limits a module of the plugin hands over from the
  status line.
- The queue is read from `state/paceline/night-queue.json` until Stint writes its own `queue.json` in
  `state/stint`; PaceLine's file is never changed.
- `stint add` flags tonight's off-hours share of 0 while the person is there.
- The report template and `night-power.ps1` moved with the queue; `night-power.ps1 -Disable` also reads
  the state file PaceLine's copy wrote.

### Not carried over

- PaceLine's leftover pool (`paceline queue --pool`): queue such items as ordinary items in the last
  night before the weekly reset.
