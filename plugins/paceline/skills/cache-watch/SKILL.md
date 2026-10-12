---
name: cache-watch
description: >
  Keep this session's one-hour prompt cache alive while the operator is away: on, off, status and
  config of cache-watch for the current session. While it is on and the session is idle, the session
  wakes a few minutes before its cache expires, keeps it, posts one line and a push, and stops after a
  set number of wakes without a reply. Use when the operator asks to keep, watch or hold the cache of
  this session, to stop doing that, or to see which sessions are watched.
argument-hint: "on [--lead MIN] [--max N] | off | status | config [--lead MIN] [--max N]"
allowed-tools:
  - Bash(paceline cache-watch *)
---

Arguments: `$ARGUMENTS` (empty means `status`).

The operator's request is the approval: run the command at once, without asking first. Run exactly one command through the Bash tool, with the values below written in place; the Bash
tool has no `CLAUDE_PLUGIN_DATA`, so keep the quoted path as written:

- `on`: `paceline cache-watch on --session ${CLAUDE_SESSION_ID} --data-dir "${CLAUDE_PLUGIN_DATA}" [--lead MIN] [--max N] < /dev/null`
- `off`: `paceline cache-watch off --session ${CLAUDE_SESSION_ID} --data-dir "${CLAUDE_PLUGIN_DATA}" < /dev/null`
- `status`: `paceline cache-watch status --data-dir "${CLAUDE_PLUGIN_DATA}" < /dev/null`
- `config`: `paceline cache-watch config --data-dir "${CLAUDE_PLUGIN_DATA}" [--lead MIN] [--max N] < /dev/null`

Pass `--lead` and `--max` only when the operator gave them. If `paceline` is not found, run
`python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py" cache-watch` with the same arguments.

Then tell the operator, in one or two lines and in their language, what the command printed:

- `on` succeeded: watching is on, the cache expiry and the next wake time; the session wakes only
  while idle; any message from them resets the count; replying `stop` turns it off. A push reaches
  the phone only with Remote Control on and "Push when Claude decides" enabled in `/config`.
- `on` printed `not started`: the reason as printed. Cache-watch keeps only one-hour cache entries,
  and only the main thread's entries count.
- `off`, `status`, `config`: the printed lines, unchanged in substance.

Each wake is one turn: a cache read of the whole context, counted against the five-hour limit. After
`on` succeeds, mention that in the same reply.

Limits: cache-watch keeps only one-hour cache entries and only the main thread's cache, so a resumed
subagent whose cache expired starts cold; a five-minute main thread is refused even when its
subagents write one-hour entries; it does nothing in `claude -p` runs. The plugin's
`docs/limitations.md` lists every limit.
