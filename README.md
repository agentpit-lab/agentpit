# agentpit

A Claude Code plugin marketplace. Its plugins work in the CLI and in Claude Code Desktop, on Windows,
macOS, and Linux.

| Plugin | What it does |
|---|---|
| [PaceLine](plugins/paceline/README.md) | Keeps a session's context and a subscription's usage limits in shape: a context gauge with warnings before auto-compaction, a keeper for an idle session's prompt cache, a per-hour budget against the five-hour and weekly limits, and an optional reader of the account's exact usage. |
| [PitBoard](plugins/pitboard/README.md) | A status row: context against the auto-compaction point, the prompt cache, the five-hour and weekly limits with their pace, and more on a click or hover. |
| [dbWise](plugins/dbwise/README.md) | Gives a session MCP tools for PostgreSQL databases. Each database alias has an access level, `read` (the default), `diagnose` or `write`, and each level may have its own login; a call never does more than the alias's level allows, and at `read` and `diagnose` a database role that holds more than its level allows is refused. |

Documentation: [agentpit-lab.github.io/agentpit](https://agentpit-lab.github.io/agentpit/) has a getting-started page for each plugin, a budget simulator for PaceLine, a row anatomy page for PitBoard, and for each plugin the how-to guides, reference, explanation, troubleshooting and limitations as interactive pages. The Markdown in each plugin's `docs/` folder is generated from those pages.

## Install

In Claude Code:

```
/plugin marketplace add agentpit-lab/agentpit
/plugin install paceline@agentpit
/plugin install pitboard@agentpit
/plugin install dbwise@agentpit
```

Then start a new session. Each plugin's README says what it needs and how to turn its functions on
and off. PitBoard works alone; with PaceLine installed, it also shows the readings other sessions
saw. dbWise works alone.

To get a newer release, run `/plugin marketplace update agentpit`.

## Requirements

- Claude Code 2.1.287 or later.
- paceline: Python 3.9 or later on `PATH` as `python3` or `python`.
- dbwise: Python 3.12 to 3.14 on `PATH` in the environment Claude Code starts in, and access to PyPI on the first
  start, when it builds its own Python environment. Native Windows ARM64 Python is not supported.

## For agents

Read the Markdown files below, which are generated from the HTML pages under `docs/` and on the
documentation site; those are interactive pages for people.

Each plugin lives in `plugins/<name>/`: `README.md`, `CHANGELOG.md`, `docs/`, and its code
(`.claude-plugin/`, `hooks/`; PaceLine and PitBoard also `types/`; PaceLine also `bin/`, `scripts/`, `skills/`,
`templates/`; dbWise also `bin/`, `scripts/`, `servers/`, `skills/`, `examples/`, `requirements.txt`).

| Question | File in `plugins/<name>/` |
|---|---|
| What the plugin does, and how to turn it on | `README.md` |
| What it cannot do, and what still works | `docs/limitations.md` |
| Exact commands, options, exit codes, files, and formats | `docs/reference.md` |
| How a number is computed | `docs/explanation.md` |
| How to do a task | `docs/how-to.md` |
| What a symptom means | `docs/troubleshooting.md` |
| Limits look wrong after a `/login` to another account | `docs/how-to.md#use-two-accounts-on-one-machine`, then `docs/troubleshooting.md#status-says-account-unknown` (PaceLine; PitBoard reads PaceLine's file) |
| Whether pace fits a user (PaceLine only) | `docs/explanation.md#who-pace-is-for` |
| Which tool an alias level may call (dbWise only) | `docs/reference.md#levels-and-tools` |
| Why a database role was refused (dbWise only) | `docs/reference.md#the-role-gate`, then `docs/troubleshooting.md#the-role-check-refused-the-alias` |
| The server shows only `dbwise_status` (dbWise only) | `docs/troubleshooting.md#the-server-shows-only-dbwise_status` |

Commands and skills:

- `paceline` runs in the session's Bash tool. The skills `/paceline:pace`, `/paceline:away`,
  `/paceline:cache-watch`, `/paceline:context-watch`, and `/paceline:usage-watch` run its commands.
- `/pitboard` opens a pane with every card of PitBoard's row.
- `dbwise` runs in the session's Bash tool. The skills `/dbwise:config` and `/dbwise:doctor` set up aliases and
  find why one does not work. Until its Python environment is built, the server `plugin:dbwise:postgres`
  offers one tool, `dbwise_status`.
- In a night queue, `paceline auto` exits 0 to start the next item, 10 to wait, and 11 to stop.
- Follow the `pace:` line before an Agent or Workflow launch, and the confirmation at a hard limit,
  as "The confirmation at a hard limit" in `plugins/paceline/docs/explanation.md` describes.

## License

[MIT](LICENSE)
