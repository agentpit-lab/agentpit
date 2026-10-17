---
name: usage-watch
description: >
  Turn usage-watch on or off, or show its status. While it is on, paceline reads the account's
  exact usage, the figures /usage shows, through Claude Code every 125 seconds, with the
  model-scoped weekly limits, so pace and status rows stay current while sessions are idle. It
  needs a claude.ai login and an open session, and reads an endpoint outside Anthropic's published
  API. Use when the operator asks to turn usage-watch or the exact usage figures on or off, or asks
  why they are missing.
argument-hint: "on | off | status"
allowed-tools:
  - Bash(paceline *)
---

Arguments: `$ARGUMENTS` (empty means `status`).

The operator's request is the approval: run the command at once, without asking first. Run exactly
one command through the Bash tool:

- `on`: `paceline usage-watch on < /dev/null`
- `off`: `paceline usage-watch off < /dev/null`
- `status`: `paceline usage-watch status < /dev/null`

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py"` with the same
arguments.

Then tell the operator, in one or two lines, what the command printed: the state, and for `status`
the account it reports, the last success and the last error.

Limits: usage-watch is off until you run `/paceline:usage-watch on`; it reads
`https://api.anthropic.com/api/oauth/usage`, outside Anthropic's published API, so its answer can
change without notice; it reads nothing while no session with paceline is open, or without a
claude.ai login. The plugin's `docs/limitations.md` lists every limit.
