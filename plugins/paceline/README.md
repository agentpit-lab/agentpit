# PaceLine

PaceLine is a Claude Code plugin that protects a subscription's usage limits. It has two functions,
each turned on and off on its own:

| Function | What it adds to a session | Default |
|---|---|---|
| pace | A per-hour budget against the five-hour and weekly limits, planned across the week, in limit mode a plain pace line for the model before every Agent and Workflow launch, safety stops that make a launch ask, or refuse it in `auto` and `bypassPermissions` mode (the five-hour stop, the weekly reserve until its release day, the weekly limit used up), subagent holds. | Off until you run `/paceline:pace on` |
| usage-watch | Reads the account's exact usage and its model-scoped weekly limits through Claude Code every 125 seconds, so the limits stay current while sessions are idle. Needs a claude.ai login and an open session; reads an endpoint outside Anthropic's published API ([limitations](docs/limitations.md#usage-watch)). | Off until you run `/paceline:usage-watch on` |

context-watch and cache-watch, which PaceLine once carried, moved to the PitCrew plugin
(`/plugin install pitcrew@agentpit`); see its
[getting started](https://agentpit-lab.github.io/agentpit/plugins/pitcrew/docs/getting-started.html).

The night queue moved to the Stint plugin (`/plugin install stint@agentpit`). Stint runs a queue of unattended
work and obeys `paceline advice`; `paceline queue` and `paceline auto` now print where they went and exit with
status 2.

pace pays off when the weekly limit runs out before the reset, when many agents, Workflows, or
unattended runs work, or when you want to use the whole week's limit; [Do you need pace?](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/getting-started.html#fit)
and [Who pace is for](docs/explanation.md#who-pace-is-for) help you decide.

One more part, the limits writer, is always on. It records the limit readings Claude Code reports
to plugins in a limits file, `rate-limits.json`, that pace and other plugins, such as a status
line, read. It adds nothing to the conversation. It keeps each signed-in account's readings apart,
so after a `/login` to another account pace shows that account's limits; see
[Use two accounts on one machine](docs/how-to.md#use-two-accounts-on-one-machine).

## Install

```
/plugin install paceline@agentpit
```

Then start a new session.

Requirements: Claude Code 2.1.287 or later, and Python 3.9 or later on `PATH` as `python3` or
`python`. PaceLine runs on Windows, macOS, and Linux and needs only the Python standard library.
PaceLine does not need PitBoard; with PitBoard installed, its row also shows the readings PaceLine
records.

## First run

pace is off until you turn it on. To turn pace on, run this in the session:

```
/paceline:pace on
```

Then run `/paceline:pace status` to see the budget, and check the time zone with
`paceline profile check`. The `paceline` command is on the `PATH` only in the session's Bash tool,
so run it there or ask the agent to.

`/paceline:settings` shows every setting and switch in one place, and `/paceline:settings pane` opens
a pane that changes them. Pace has one five-hour stop, `five_hour_stop_pct` (95 % by default, in
working hours and off-hours alike): from that usage every Agent or Workflow launch asks first. Change
it with `paceline profile set five_hour_stop_pct 90`.

## Contents

- [Getting started](docs/getting-started.html): the two functions, setup, and where pace shows
  itself. Online:
  [getting started](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/getting-started.html).
- [Budget simulator](docs/budget-simulator.html): move usage, resets, and the clock, and see the
  budget and the advice's decision. Online:
  [budget simulator](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/budget-simulator.html).
- [How-to guides](docs/how-to.md): plan the week, keep usage current, use two accounts, change a profile setting.
  Online page:
  [how-to guides](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/how-to.html).
- [Explanation](docs/explanation.md): how the budget, the forecast, and the advice
  are computed, and where the numbers come from.
  Online page:
  [explanation](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/explanation.html).
- [Reference](docs/reference.md): commands, exit codes, profile keys, environment variables, files,
  the limits file, and the inbox.
  Online page:
  [reference](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/reference.html).
- [Troubleshooting](docs/troubleshooting.md): what to check when pace looks inactive, the budget
  looks wrong, or the data is stale.
  Online page:
  [troubleshooting](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/troubleshooting.html).
- [Limitations](docs/limitations.md): what the plugin cannot do, and what still works.
  Online page:
  [limitations](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/limitations.html).
