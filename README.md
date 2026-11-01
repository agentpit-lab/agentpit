# agentpit

A Claude Code plugin marketplace. Its plugins work in the CLI and in Claude Code Desktop, on Windows,
macOS, and Linux.

| Plugin | What it does |
|---|---|
| [PaceLine](plugins/paceline/README.md) | Keeps a subscription's usage limits in shape: a per-hour budget against the five-hour and weekly limits, and an optional reader of the account's exact usage. |
| [PitBoard](plugins/pitboard/README.md) | A status row: context against the auto-compaction point, the prompt cache, the five-hour and weekly limits with their pace, and more on a click or hover. |
| [PitCrew](plugins/pitcrew/README.md) | Keeps a session's context and prompt cache in shape: context-watch warns the agent before auto-compaction and snapshots the session at each compaction, and cache-watch keeps an idle session's one-hour prompt cache warm. It carries these two watchers out of PaceLine and reads the settings PaceLine kept for them. |
| [Stint](plugins/stint/README.md) | A queue of unattended work: queue items, leave, and `/stint:run` works them one at a time while `stint next` answers start, wait or stop. With PaceLine it obeys PaceLine's advice; without it, it stops at its own week and five-hour limits. It carries the queue out of PaceLine and reads the queue PaceLine kept. |
| [dbWise](plugins/dbwise/README.md) | Gives a session MCP tools for PostgreSQL databases. Each database alias has an access level, `read` (the default), `diagnose` or `write`, and each level may have its own login; a call never does more than the alias's level allows, and at `read` and `diagnose` a database role that holds more than its level allows is refused. |

Documentation: [agentpit-lab.github.io/agentpit](https://agentpit-lab.github.io/agentpit/) has a getting-started page for each plugin, a budget simulator for PaceLine, a row anatomy page for PitBoard, and for each plugin the how-to guides, reference, explanation, troubleshooting and limitations as interactive pages. The Markdown in each plugin's `docs/` folder is generated from those pages.

## Install

In Claude Code:

```
/plugin marketplace add agentpit-lab/agentpit
/plugin install paceline@agentpit
/plugin install pitboard@agentpit
/plugin install pitcrew@agentpit
/plugin install stint@agentpit
/plugin install dbwise@agentpit
```

Then start a new session. Each plugin's README says what it needs and how to turn its functions on
and off. PitBoard works alone; with PaceLine installed, it also shows the readings other sessions
saw. PitCrew works alone; PaceLine 0.6.0 and earlier carry their own copy of its two watchers, so with one of those installed beside it, see PitCrew's limitations. Stint works alone; with PaceLine installed, it obeys PaceLine's advice. PaceLine 0.6.0 and earlier carry their own queue, so with one of those installed beside it, see Stint's limitations. dbWise works alone.

To get a newer release, run `/plugin marketplace update agentpit`.

## Requirements

- Claude Code 2.1.287 or later.
- paceline, pitcrew, stint: Python 3.9 or later on `PATH` as `python3` or `python`.
- dbwise: Python 3.12 to 3.14 on `PATH` in the environment Claude Code starts in, and access to PyPI on the first
  start, when it builds its own Python environment. Native Windows ARM64 Python is not supported.

## For agents

Read the Markdown files below, which are generated from the HTML pages under `docs/` and on the
documentation site; those are interactive pages for people.

Each plugin lives in `plugins/<name>/`: `README.md`, `CHANGELOG.md`, `docs/`, and its code
(`.claude-plugin/`, `hooks/`; PaceLine, PitBoard, PitCrew and Stint also `types/`; PaceLine, PitCrew and Stint also `bin/`, `scripts/`, `skills/`;
PaceLine and Stint also `templates/`; dbWise also `bin/`, `scripts/`, `servers/`, `skills/`, `examples/`, `requirements.txt`).

| Question | File in `plugins/<name>/` |
|---|---|
| What the plugin does, and how to turn it on | `README.md` |
| What it cannot do, and what still works | `docs/limitations.md` |
| Exact commands, options, exit codes, files, and formats | `docs/reference.md` |
| How a number is computed | `docs/explanation.md` |
| How to do a task | `docs/how-to.md` |
| What a symptom means | `docs/troubleshooting.md` |
| Limits look wrong after a `/login` to another account | `docs/how-to.md#use-two-accounts-on-one-machine`, then `docs/troubleshooting.md#status-says-account-unknown` (PaceLine; PitBoard reads PaceLine's file) |
| Two warnings on every prompt, or two snapshots after a compaction (PitCrew beside PaceLine 0.6.0 or earlier) | `docs/troubleshooting.md#every-prompt-gets-two-warnings` |
| Why a queue run waits or stops (Stint) | `docs/troubleshooting.md#the-queue-stops-at-once`, `docs/troubleshooting.md#the-queue-waits-with-no-usage-reading`, and the order in `docs/reference.md#how-next-decides-with-paceline` |
| Whether pace fits a user (PaceLine only) | `docs/explanation.md#who-pace-is-for` |
| Which tool an alias level may call (dbWise only) | `docs/reference.md#levels-and-tools` |
| Why a database role was refused (dbWise only) | `docs/reference.md#the-role-gate`, then `docs/troubleshooting.md#the-role-check-refused-the-alias` |
| The server shows only `dbwise_status` (dbWise only) | `docs/troubleshooting.md#the-server-shows-only-dbwise_status` |

Commands and skills:

- `paceline` runs in the session's Bash tool. The skills `/paceline:pace`, `/paceline:away`,
  and `/paceline:usage-watch` run its commands.
- `pitcrew` runs in the session's Bash tool. The skills `/pitcrew:context-watch` and `/pitcrew:cache-watch` run its commands; they carry the two watchers that PaceLine 0.6.0 and earlier also have.
- `/pitboard` opens a pane with every card of PitBoard's row.
- `dbwise` runs in the session's Bash tool. The skills `/dbwise:config` and `/dbwise:doctor` set up aliases and
  find why one does not work. Until its Python environment is built, the server `plugin:dbwise:postgres`
  offers one tool, `dbwise_status`.
- `stint` runs in the session's Bash tool. The skills `/stint:queue` and `/stint:run` run its commands. `stint next` exits 0 to start the next item, 10 to wait, and 11 to stop.
- Follow the `pace:` line before an Agent or Workflow launch, and the confirmation at a hard limit,
  as "The confirmation at a hard limit" in `plugins/paceline/docs/explanation.md` describes.

## License

[MIT](LICENSE)
