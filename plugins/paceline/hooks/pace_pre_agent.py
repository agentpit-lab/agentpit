#!/usr/bin/env python3
"""PreToolUse hook (Agent|Workflow): put the pace recommendation in front of every launch.

Does nothing while pace is off (`paceline on` turns it on). Runs `pace.py status --json --no-merge`
and adds one advice line to Claude's context. When a hard limit holds, it returns "ask" instead, so
the owner confirms the launch in a permission prompt:

- free memory below the profile floor (`min_free_commit_mb`), measured live on every call;
- the five-hour window at the background threshold (80 % by day, 95 % at night), or the weekly
  limit exhausted, both only while the usage data is fresh.

Everything else stays advice. Any failure of pace.py (error, timeout, no data) allows silently.
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
TIMEOUT_SEC = 3


def pace_status(session):
    argv = [sys.executable, PACE, "status", "--json", "--no-merge"]
    if session:
        argv += ["--session", session]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as error:
        note_failure("hook.pre_agent", error=error)
        return None
    if out.returncode != 0:
        note_failure("hook.pre_agent", exit_code=out.returncode, stderr=out.stderr.strip()[-300:])
        return None
    try:
        return json.loads(out.stdout)
    except ValueError as error:
        note_failure("hook.pre_agent", error=error, stdout=out.stdout[:300])
        return None


def hard_limits(status):
    reasons = []
    host = status.get("host") or {}
    commit, floor = host.get("free_commit_mb"), host.get("min_free_commit_mb")
    if commit is not None and floor is not None and commit < floor:
        reasons.append(f"free memory {commit / 1024:.1f} GB < {floor / 1024:.0f} GB, a runaway process can crash the host")
    if (status.get("data") or {}).get("stale"):
        return reasons
    five = status.get("five_hour") or {}
    used, threshold = five.get("used_pct"), five.get("threshold_pct")
    if used is not None and threshold is not None and used >= threshold:
        reasons.append(f"5h window {used:.0f}% >= {threshold:.0f}% background threshold, "
                       f"resets {five.get('reset_local') or 'later'}")
    if ((status.get("weekly") or {}).get("effective_pct") or 0) >= 100:
        reasons.append("weekly limit exhausted")
    return reasons


def advice(status):
    rec = status.get("recommendation") or {}
    five = status.get("five_hour") or {}
    data = status.get("data") or {}
    parts = [f"agents <={rec.get('max_parallel_agents')} (active reservations {status.get('active_reservations', 0)})",
             f"model {rec.get('model')} of {'/'.join(rec.get('allowed_models') or []) or 'none'}",
             f"5h {five.get('used_pct', 0):.0f}%/{five.get('threshold_pct', 0):.0f}",
             f"budget {status.get('budget_per_hour_pct', 0):.2f}%/h vs norm {status.get('normal_per_hour_pct') or 0:.2f}"]
    if data.get("stale"):
        parts.append(f"STALE data {data.get('age_min')} min old")
    if (data.get("account") or {}).get("state") == "unknown":
        parts.append("account unknown")
    parts += rec.get("reasons") or []
    return "pace: " + " | ".join(parts)


def main():
    if not pace_on():
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if too_old():
        return 0
    trace("hook.pre_agent", tool=payload.get("tool_name"))
    status = pace_status(payload.get("session_id"))
    if not status:
        return 0
    line = advice(status)
    limits = hard_limits(status)
    out = {"hookEventName": "PreToolUse", "additionalContext": line}
    result = {"hookSpecificOutput": out}
    if limits:
        why = "; ".join(limits)
        reason = f"pace hard limit: {why}"
        out.update(permissionDecision="ask",
                   permissionDecisionReason=f"{reason}. Launch {payload.get('tool_name')} anyway?")
        # Some prompt surfaces show neither the reason nor a system message, only the tool input,
        # so the reason also goes in front of the description the prompt displays.
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict) and isinstance(tool_input.get("description"), str):
            out["updatedInput"] = dict(tool_input, description=f"[{reason}] {tool_input['description']}")
        out["additionalContext"] = (f"{line}\npace: the owner was asked to confirm this launch because {why}. "
                                    "If the owner declined, do not retry; wait for the limit to clear or ask the owner.")
        result["systemMessage"] = reason
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
