#!/usr/bin/env python3
"""SessionEnd hook: release this session's pace reservations (subagent, workflow, gate, queue).

Does nothing while pace is off; a reservation left when pace is turned off mid-session expires by
its lease. Claude Code gives all SessionEnd hooks 1.5 seconds together, so the hook first writes an
end marker, <state>/ended-sessions/<session id>.json, then releases in this process. When it is cut
off or the state lock stays busy, the next pace call sweeps the marker. Fire and forget: never blocks
or fails the session's exit, so every error is swallowed.
"""
import datetime as dt
import json
import os
import re
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


LOCK_TIMEOUT_SEC = 0.5


def write_marker(sd, session, now):
    folder = sd / "ended-sessions"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", session)
    limits_file.write_json(folder / f"{name}.json", {"session_id": session, "ended_at": limits_file.iso_ms(now)})


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
        sd = limits_file.paceline_dir()
        now = limits_file.parse_iso(os.environ.get("PACELINE_NOW")) or dt.datetime.now(dt.timezone.utc)
        write_marker(sd, session, now)
        # Imported only now: the marker is on disk before the slowest step starts.
        import pace
        pace.end_session(sd, session, now, lock_timeout=LOCK_TIMEOUT_SEC)
    except Exception as error:  # noqa: BLE001 - the marker stays for the next pace call
        note_failure("hook.session_end", error=error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
