---
name: context-watch
description: >
  Show or change context-watch, which tells the agent how full its context is against the
  auto-compaction point, warns at set levels so it can save a recovery point, and snapshots the
  session at each compaction. The per-prompt context line is off by default. Use when the operator
  asks to see context-watch's settings or where its snapshots are, to turn it on or off, to hide or
  show the context line, or to change the warning levels or how long snapshots are kept.
argument-hint: "status | config [--on|--off] [--line on|off] [--levels 85,95] [--days N]"
allowed-tools:
  - Bash(paceline context-watch *)
---

Arguments: `$ARGUMENTS` (empty means `status`).

The operator's request is the approval: run the command at once, without asking first. Run exactly
one command through the Bash tool:

- `status`: `paceline context-watch status --project "<the session's project directory>" < /dev/null`
- `config`: `paceline context-watch config [--on|--off] [--line on|off] [--levels 85,95] [--days N] < /dev/null`

Pass only the options the operator gave. The context line, which `--line on` adds to every prompt,
is off by default. If `paceline` is not found, run
`python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py" context-watch` with the same arguments.

Then tell the operator, in one or two lines and in their language, what the command printed. After
a `config` change, add that it applies from the next prompt.
