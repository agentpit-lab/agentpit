# agentpit

A Claude Code plugin marketplace. Its plugins work in the CLI and in Claude Code Desktop, on Windows,
macOS, and Linux.

| Plugin | What it does |
|---|---|
| [paceline](plugins/paceline/README.md) | Keeps a session's context and a subscription's usage limits in shape: a context gauge with warnings before auto-compaction, a keeper for an idle session's prompt cache, a per-hour budget against the five-hour and weekly limits, and an optional reader of the account's exact usage. |
| [pitboard](plugins/pitboard/README.md) | A status row: context against the auto-compaction point, the prompt cache, the five-hour and weekly limits with their pace, and more on a click or hover. |

Documentation: [agentpit-lab.github.io/agentpit](https://agentpit-lab.github.io/agentpit/) has a getting-started page for each plugin, a budget simulator for paceline, and a row anatomy page for pitboard; the Markdown references and limitations are in each plugin's `docs/` folder.

## Install

In Claude Code:

```
/plugin marketplace add agentpit-lab/agentpit
/plugin install paceline@agentpit
/plugin install pitboard@agentpit
```

Then start a new session. Each plugin's README says what it needs and how to turn its functions on
and off. pitboard works alone; with paceline installed, it also shows the readings other sessions
saw.

To get a newer release, run `/plugin marketplace update agentpit`.

## Requirements

- Claude Code 2.1.287 or later.
- paceline: Python 3.9 or later on `PATH` as `python3` or `python`.

## For agents

Documentation site: <https://agentpit-lab.github.io/agentpit/>.

Each plugin lives in `plugins/<name>/`: `README.md`, `CHANGELOG.md`, `docs/`, and its code
(`.claude-plugin/`, `hooks/`, `types/`; paceline also `bin/`, `scripts/`, `skills/`, `templates/`).

| Question | File in `plugins/<name>/` |
|---|---|
| What the plugin does, and how to turn it on | `README.md` |
| What it cannot do, and what still works | `docs/limitations.md` |
| Exact commands, options, exit codes, files, and formats | `docs/reference.md` |
| How a number is computed | `docs/explanation.md` |
| How to do a task | `docs/how-to.md` |
| What a symptom means | `docs/troubleshooting.md` |
| Whether pace fits a user (paceline only) | `docs/getting-started.html#fit` |

Commands and skills:

- `paceline` runs in the session's Bash tool. The skills `/paceline:pace`, `/paceline:cache-watch`,
  `/paceline:context-watch`, and `/paceline:usage-watch` run its commands.
- `/pitboard` opens a pane with every card of pitboard's row.
- In a night queue, `paceline auto` exits 0 to start the next item, 10 to wait, and 11 to stop.
- Follow the `pace:` line before an Agent or Workflow launch, and the confirmation at a hard limit,
  as "The confirmation at a hard limit" in `plugins/paceline/docs/explanation.md` describes.

## License

[MIT](LICENSE)
