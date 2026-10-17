---
name: away
description: >
  Tell paceline that the operator is away from the keyboard: on, off or status. While the marker is
  on, a subagent or Workflow launch that would ask for confirmation, at a hard limit or over the
  recommended agent count in the five-hour burn state, is denied instead, since no one is there to
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

- `on`: the marker is set for all sessions; a launch at a hard limit, or over the agent cap while the
  five-hour window burns too fast, is denied instead of asked; their next message in any session clears it, so they need not
  turn it off.
- `off`, `status`: the printed line.

Limits: the marker changes nothing while no hard limit holds and launches stay within the agent cap.
It has no expiry: a session that is open but never receives a message from the operator keeps
the marker on. A prompt from a schedule or a loop clears it too. The plugin's `docs/limitations.md`
lists every limit.
