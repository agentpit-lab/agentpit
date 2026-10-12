#!/usr/bin/env python3
"""SessionEnd hook: release this session's pace reservations (subagent, workflow, gate, queue).

Does nothing while pace is off; a reservation left when pace is turned off mid-session expires by
its lease. Fire and forget: never blocks or fails the session's exit, so every error is swallowed.
PACELINE_SCRIPT overrides the pace.py path, for tests.
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
        session = (json.load(sys.stdin) or {}).get("session_id")
    except (ValueError, AttributeError, OSError):
        return 0
    if not session or too_old():
        return 0
    trace("hook.session_end")
    try:
        out = subprocess.run([sys.executable, PACE, "reserve", "release-session", "--session", session],
                             capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as error:
        note_failure("hook.session_end", error=error)
        return 0
    if out.returncode != 0:
        note_failure("hook.session_end", exit_code=out.returncode, stderr=out.stderr.strip()[-300:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
