# Stint

Stint keeps a queue of unattended work for Claude Code and decides, before each item, whether it
may start. You queue items, leave, and `/stint:run` works them one at a time. It works in the CLI and
in Claude Code Desktop, on Windows, macOS, and Linux.

- **With PaceLine** installed, Stint obeys `paceline advice`: the budget for the next item, the number
  of agents, the waits and the safety stops all come from PaceLine's week plan. Each running item is a
  `queue` hold in PaceLine's registry, so PaceLine's advice counts it.
- **Without PaceLine**, Stint stops at its own limits: the week at 85 % (`stint config --week-stop`)
  and the five-hour window at 95 % (`--five-hour-stop`). It reads them from the limits Claude Code hands
  to a session, which a module of this plugin writes to `state/stint/limits.json`.

## Install

```
/plugin install stint@agentpit
```

Requirements:

- Claude Code 2.1.287 or later.
- Python 3.9 or later on the `PATH`, as `python3` or `python`, for the `stint` command.

## First run

```
stint add --title "Add the export command" --repo my-repo --estimate 2.5
stint next --start-run
```

`stint next` prints one line and exits `0` (start), `10` (wait) or `11` (stop):
`next: START Q1 | budget 5.00% | agents <=3`. In a session, `/stint:queue` adds and changes items and
`/stint:run` works the queue and fills a morning report. With `--json`, every command prints its payload.

Items run in the order queued; `--depends Q1` waits for an item, and an item that is not `--independent`
never runs beside another. `stint fail` counts a failed attempt and defers the item at the third.

State lives in `state/stint` under `$CLAUDE_CONFIG_DIR`, else `~/.claude` (`STINT_STATE_DIR` overrides
it). Until Stint writes its own `queue.json`, it reads the queue PaceLine 0.6.0 and earlier kept in
`state/paceline/night-queue.json`, and never changes that file. PaceLine's leftover pool is not carried
over. `STINT_PACELINE=off` ignores PaceLine; a path names the program to call instead of the `paceline`
on the `PATH`.

`scripts/night-power.ps1` (Windows) turns off sleep on AC power and sets Windows Update active hours for
a night run; run it without switches for a dry run.

## Contents

- [Getting started](docs/getting-started.html), [how-to guides](docs/how-to.md),
  [reference](docs/reference.md), [troubleshooting](docs/troubleshooting.md) and
  [limitations](docs/limitations.md), in `docs/`.
