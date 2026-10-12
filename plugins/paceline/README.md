# paceline

paceline is a Claude Code plugin that protects two things: a session's context and a subscription's
usage limits. It has four functions, each turned on and off on its own:

| Function | What it adds to a session | Default |
|---|---|---|
| context-watch | A one-line context gauge on every prompt, warnings before auto-compaction, and a snapshot of the session at each compaction. | On |
| cache-watch | Wakes an idle session shortly before its one-hour prompt cache expires, so the cache is kept. | Off in every session until you turn it on there |
| pace | A per-hour budget against the five-hour and weekly limits, advice before every Agent and Workflow launch, subagent reservations, and a night queue. | Off until you run `/paceline:pace on` |
| usage-watch | Reads the account's exact usage and its model-scoped weekly limits through Claude Code every 125 seconds, so the limits stay current while sessions are idle. Needs a claude.ai login and an open session; reads an endpoint outside Anthropic's published API ([limitations](docs/limitations.md#usage-watch)). | Off until you run `/paceline:usage-watch on` |

pace pays off when the weekly limit runs out before the reset, when many agents, Workflows, or night
queues run, or when you want to use the whole week's limit; [Do you need pace?](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/getting-started.html#fit)
and [Who pace is for](docs/explanation.md#who-pace-is-for) help you decide.

One more part, the limits writer, is always on. It records the limit readings Claude Code reports
to plugins in a limits file, `rate-limits.json`, that pace and other plugins, such as a status
line, read. It adds nothing to the conversation.

## Install

```
/plugin install paceline@agentpit
```

Then start a new session.

Requirements: Claude Code 2.1.287 or later, and Python 3.9 or later on `PATH` as `python3` or
`python`. paceline runs on Windows, macOS, and Linux and needs only the Python standard library.

## First run

context-watch works from the first prompt. To turn pace on, run this in the session:

```
/paceline:pace on
```

Then run `/paceline:pace status` to see the budget, and check the time zone with
`paceline profile check`. The `paceline` command is on the `PATH` only in the session's Bash tool,
so run it there or ask the agent to.

## Contents

- [Getting started](docs/getting-started.html): the four functions, setup, and where pace shows
  itself. Online:
  [getting started](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/getting-started.html).
- [Budget simulator](docs/budget-simulator.html): move usage, resets, and the clock, and see the
  budget and the night queue's decision. Online:
  [budget simulator](https://agentpit-lab.github.io/agentpit/plugins/paceline/docs/budget-simulator.html).
- [How-to guides](docs/how-to.md): run a night queue, declare a plan, hold the cache, recover after
  a compaction, keep usage current, change a profile setting.
- [Reference](docs/reference.md): commands, exit codes, profile keys, environment variables, files,
  the limits file, and the inbox.
- [Explanation](docs/explanation.md): how the budget, the forecast, and the night queue's decisions
  are computed, and where the numbers come from.
- [Troubleshooting](docs/troubleshooting.md): what to check when pace looks inactive, the budget
  looks wrong, or the data is stale.
- [Limitations](docs/limitations.md): what the plugin cannot do, and what still works.
