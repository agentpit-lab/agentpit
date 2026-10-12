#!/usr/bin/env python3
"""SessionStart hook: put what the owner should be asked about this session in front of Claude.

- Plans that start tomorrow and are not confirmed yet.
- The leftover-pool suggestion, when the forecast leaves part of the weekly limit unused.

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


def pace_json(*argv):
    try:
        out = subprocess.run([sys.executable, PACE, *argv, "--json"], capture_output=True, text=True,
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


def plan_lines():
    plans = pace_json("plan", "tomorrow").get("plans") or []
    if not plans:
        return []
    parts = []
    for p in plans:
        phrase = p.get("phrase") or ""
        part = f"{p.get('id', '?')} {p.get('type', '?')} {p.get('start_local', '')} .. {p.get('end_local', '')}"
        parts.append(part + (f' ("{phrase}")' if phrase else ""))
    return ["Pace: unconfirmed plan change(s) start tomorrow — " + "; ".join(parts)
            + ". Ask the owner once whether the plan still holds, then run `paceline plan confirm <id>`."]


def leftover_lines():
    status = pace_json("status", "--no-merge")
    if not status.get("suggest_leftover_tasks"):
        return []
    forecast = status.get("forecast_unused_pct")
    share = f"{forecast}%" if forecast is not None else "a meaningful share"
    return [f"Pace: forecast leaves {share} of the weekly limit unused before reset. Propose a leftover task "
            "pool to the owner; on approval run `paceline queue --pool approve`."]


def main():
    on = pace_on()
    # Drained even while pace is off: an unread pipe can block the writer.
    try:
        sys.stdin.read()
    except (OSError, ValueError):
        pass
    if not on or too_old():
        return 0
    trace("hook.session_start")
    lines = []
    for source in (plan_lines, leftover_lines):
        try:
            lines += source()
        except Exception as error:  # noqa: BLE001 - a hook must never fail the session
            note_failure("hook.session_start", step=source.__name__, error=error)
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
