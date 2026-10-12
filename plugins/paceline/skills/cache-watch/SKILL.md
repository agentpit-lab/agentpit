---
name: cache-watch
description: >
  Keep this session's one-hour prompt cache alive while the operator is away: on, off, status and
  config of cache-watch for the current session. While it is on and the session is idle, the session
  wakes a few minutes before its cache expires, keeps it, sends one line as a push, and stops after a
  set number of wakes without a reply or at a compaction. Use when the operator asks to keep, watch
  or hold the cache of this session, to stop doing that, or to see which sessions are watched.
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
  while idle; any message from them resets the count; replying `stop` turns it off, and so does a
  compaction (`status` then shows `off (compacted)`). Each wake shows its line once, as a push, or
  as a reply where the push tool is missing; the push reaches the phone only with Remote Control on
  and "Push when Claude decides" enabled in `/config`.
- `on` printed `not started`: the reason as printed. Cache-watch keeps only one-hour cache entries,
  and only the main thread's entries count.
- `off`, `status`, `config`: the printed lines, unchanged in substance.

After `on` succeeds, pass on its last two lines in the same reply, in one or two lines: a wake
keeps the cache for far less than writing the whole context again after it expired (give the
numbers `on` printed), and each wake still counts against the five-hour limit.

Limits: cache-watch keeps only one-hour cache entries and only the main thread's cache, so a resumed
subagent whose cache expired starts cold; a five-minute main thread is refused even when its
subagents write one-hour entries; a compaction turns it off; it does nothing in `claude -p` runs. The plugin's
`docs/limitations.md` lists every limit.
