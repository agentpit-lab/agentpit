# PitCrew

PitCrew keeps a Claude Code session's context and prompt cache in shape, with two watchers.
It works in the CLI and in Claude Code Desktop, on Windows, macOS, and Linux.

- **context-watch** (on by default) tells the agent how full its context is against the
  auto-compaction point. It warns at 85 % and 95 % so the agent can save a recovery point, and it
  writes a snapshot of the session at each compaction to `.claude/pitcrew/<session id>.md` in the
  project. A per-prompt context line is available and off by default.
- **cache-watch** (per session, on request) wakes an idle session a few minutes before its
  one-hour prompt cache expires, so the next turn reads the context from the cache instead of
  writing it again. It stops after a set number of wakes without a reply, at a compaction, and when
  the cache has already expired.

## Install

```
/plugin install pitcrew@agentpit
```

Requirements:

- Claude Code 2.1.287 or later.
- Python 3.9 or later on the `PATH`, as `python3` or `python`, for the hooks and the `pitcrew` command.

## First run

Start a new session; the hooks load at session start. context-watch is on and needs no setup:
when the context first passes 85 % and then 95 % of the auto-compaction point, the agent is told
once per level to save its recovery point, and each compaction writes a snapshot that the agent
reads right after it. `/pitcrew:context-watch status` shows the settings and the snapshot folder.
cache-watch is off until you run `/pitcrew:cache-watch on` in a session you are about to leave
idle; the session then wakes before its cache expires, up to 5 times in a row without a reply.
Both are also commands, which run in the session's Bash tool: `pitcrew context-watch config|status`
and `pitcrew cache-watch on|off|status|config`.

Settings live in `state/pitcrew` under `$CLAUDE_CONFIG_DIR`, else `~/.claude` (`PITCREW_STATE_DIR`
overrides it). Until PitCrew writes a settings file, it reads the one PaceLine kept in
`state/paceline`, and never changes PaceLine's files. PaceLine 0.6.0 and earlier carry their own
copy of both watchers, so with one of them installed beside PitCrew, every prompt past a warning
level gets two warnings; see [the limitation](docs/limitations.md#paceline-060-or-earlier-runs-its-own-copy).

## Contents

- [Getting started](docs/getting-started.html): what runs at once, the cache-watch switch, and how
  to choose between cache-watch and idle compaction, in a browser
  ([online](https://agentpit-lab.github.io/agentpit/plugins/pitcrew/docs/getting-started.html)).
- [How-to guides](docs/how-to.md): hold the cache while away, recover after a compaction, change
  context-watch's levels, line and snapshot days, choose between cache-watch and idle compaction,
  run only one copy of the watchers beside PaceLine.
  Online page:
  [how-to guides](https://agentpit-lab.github.io/agentpit/plugins/pitcrew/docs/how-to.html).
- [Reference](docs/reference.md): commands, settings, skills, files and folders, environment
  variables, exit codes, hooks, requirements.
  Online page:
  [reference](https://agentpit-lab.github.io/agentpit/plugins/pitcrew/docs/reference.html).
- [Troubleshooting](docs/troubleshooting.md): problems by symptom, each with cause, check, and fix.
  Online page:
  [troubleshooting](https://agentpit-lab.github.io/agentpit/plugins/pitcrew/docs/troubleshooting.html).
- [Limitations](docs/limitations.md): what the plugin cannot do, and what still works.
  Online page:
  [limitations](https://agentpit-lab.github.io/agentpit/plugins/pitcrew/docs/limitations.html).
