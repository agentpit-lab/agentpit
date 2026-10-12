#!/usr/bin/env python3
"""SubagentStart / SubagentStop hook: hold a pace reservation for the life of each subagent.

Does nothing while pace is off (`paceline on` turns it on).

SubagentStart acquires a `subagent` reservation keyed by the payload's agent_id, with the profile
estimate (`subagent_estimate_pct`). SubagentStop releases it, and pace.py records the measured cost.
A repeated start for the same agent (a resume) keeps the one reservation. Events without an
agent_type come from Claude Code's internal agents and are ignored. Leftovers after a crash go to
the SessionEnd hook and to pace.py's own pruning.

Prints nothing and always exits 0. PACELINE_SCRIPT overrides the pace.py path, for tests.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
try:
    import limits_file
except Exception:  # noqa: BLE001 - the hook still works, untraced, when the module can't load
    limits_file = None


def trace(event, **fields):
    if limits_file:
        limits_file.trace(event, **fields)


def note_failure(event, **fields):
    """A failure the hook swallows for the session's sake, written to the debug log even without the flag."""
    if limits_file:
        limits_file.trace_failure(event, **fields)


def pace_on():
    """The pace switch; a hook whose module can't load treats pace as off."""
    try:
        return bool(limits_file and limits_file.pace_enabled())
    except Exception:  # noqa: BLE001 - a broken switch must not fail the session
        return False


def too_old():
    reason = limits_file.require_python() if limits_file else None
    if reason:
        note_failure("hook.python", reason=reason)
    return bool(reason)


PACE = os.environ.get("PACELINE_SCRIPT") or str(Path(__file__).resolve().parent.parent / "scripts" / "pace.py")
TIMEOUT_SEC = 5


def main():
    if not pace_on():
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    agent_id, agent_type = payload.get("agent_id"), payload.get("agent_type")
    session, event = payload.get("session_id"), payload.get("hook_event_name")
    if not (agent_id and agent_type and session):
        return 0
    if event == "SubagentStart":
        argv = ["reserve", "acquire", "--kind", "subagent", "--key", agent_id, "--label", agent_type]
    elif event == "SubagentStop":
        argv = ["reserve", "release", "--key", agent_id]
    else:
        return 0
    if too_old():
        return 0
    trace("hook.subagent", hook_event=event, agent_type=agent_type)
    try:
        out = subprocess.run([sys.executable, PACE, *argv, "--session", session, "--no-merge"],
                             capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as error:
        note_failure("hook.subagent", hook_event=event, error=error)
        return 0
    # Exit 4 is a release that found no reservation, which a stop after a pruned start expects.
    if out.returncode not in (0, 4):
        note_failure("hook.subagent", hook_event=event, exit_code=out.returncode, stderr=out.stderr.strip()[-300:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
