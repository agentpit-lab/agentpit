#!/usr/bin/env python3
"""SessionStart hook: put the one thing the owner should be asked about this session in front of Claude.

- A time zone hint, while the profile counts in UTC and this machine is in another zone.

Does nothing while pace is off (`paceline on` turns it on). Silent when nothing applies; never
blocks startup, since every pace.py call has a timeout and any failure is skipped.
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
TIMEOUT_SEC = 8


# The session id of the hook's payload; every pace.py call names it, so its account is the session's.
SESSION = None


def pace_json(*argv):
    session = [f"--session={SESSION}"] if SESSION else []
    try:
        out = subprocess.run([sys.executable, PACE, *argv, *session, "--json"], capture_output=True, text=True,
                             encoding="utf-8", timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as error:
        note_failure("hook.session_start", argv=list(argv), error=error)
        return {}
    if out.returncode != 0:
        note_failure("hook.session_start", argv=list(argv), exit_code=out.returncode, stderr=out.stderr.strip()[-300:])
        return {}
    try:
        parsed = json.loads(out.stdout)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def timezone_lines():
    hint = pace_json("profile", "check").get("hint")
    return [f"Pace: {hint} Tell the owner once."] if hint else []


def main():
    on = pace_on()
    # Drained even while pace is off: an unread pipe can block the writer.
    global SESSION
    try:
        payload = sys.stdin.buffer.read()
    except (OSError, ValueError):
        payload = b""
    try:
        parsed = json.loads(payload.decode("utf-8"))
        session = parsed.get("session_id") if isinstance(parsed, dict) else None
        SESSION = session if isinstance(session, str) and limits_file and limits_file.SESSION_ID.fullmatch(session) else None
    except (ValueError, UnicodeDecodeError):
        SESSION = None
    if not on or too_old():
        return 0
    trace("hook.session_start")
    lines = []
    try:
        lines += timezone_lines()
    except Exception as error:  # noqa: BLE001 - a hook must never fail the session
        note_failure("hook.session_start", step=timezone_lines.__name__, error=error)
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
