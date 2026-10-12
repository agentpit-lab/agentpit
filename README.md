# agentpit

A Claude Code plugin marketplace. Its plugins work in the CLI and in Claude Code Desktop, on Windows,
macOS, and Linux.

| Plugin | What it does |
|---|---|
| [paceline](plugins/paceline/README.md) | Keeps a session's context and a subscription's usage limits in shape: a context gauge with warnings before auto-compaction, a keeper for an idle session's prompt cache, a per-hour budget against the five-hour and weekly limits, and an optional reader of the account's exact usage. |
| [pitboard](plugins/pitboard/README.md) | A status row: context against the auto-compaction point, the prompt cache, the five-hour and weekly limits with their pace, and more on a click or hover. |

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

## License

[MIT](LICENSE)
