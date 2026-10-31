---
name: away
description: >
  Tell paceline that the operator is away from the keyboard: on, off or status. While the marker is
  on, a subagent or Workflow launch that would ask for confirmation, at a hard limit, at the weekly
  reserve's stop line, or when the plan in
  limit mode is over its share, is denied instead, since no one is there to
  answer and an unanswered prompt lets the prompt cache expire. The marker is shared by all sessions and cleared by the
  operator's next message in any session. Use when the operator says they are leaving, going away or
  that the sessions should work alone, or asks to clear or check that.
argument-hint: "on | off | status"
allowed-tools:
  - Bash(paceline away *)
---

Arguments: `$ARGUMENTS` (empty means `on`).

The operator's request is the approval: run the command at once, without asking first. Run exactly
one command through the Bash tool:

- `on`: `paceline away on --session ${CLAUDE_SESSION_ID} < /dev/null`
- `off`: `paceline away off < /dev/null`
- `status`: `paceline away status < /dev/null`

If `paceline` is not found, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/paceline.py" away` with the
same arguments.

Then tell the operator, in one or two lines and in their language, what the command printed:

- `on`: the marker is set for all sessions; a launch at a hard limit, past the weekly reserve's stop line, or while the plan in limit mode is over its share, is denied instead of asked; their next message in any session clears it, so they need not
  turn it off.
- `off`, `status`: the printed line.

Limits: the marker changes nothing while no hard limit holds, the week's spend is short of the reserve's stop line and the plan is not over its share in limit mode.
It has no expiry: a session that is open but never receives a message from the operator keeps
the marker on. Only the operator's own message clears it: a prompt from a schedule, a loop, a
notification, another session, a channel, `claude -p` or the Agent SDK leaves it set, so an operator
who works only through `claude -p` or the Agent SDK clears it with `paceline away off`.

With or without the marker, the same stops are denied in the `auto`, `bypassPermissions` and `dontAsk` permission modes, where no one answers the prompt. The plugin's `docs/limitations.md` lists every limit.
