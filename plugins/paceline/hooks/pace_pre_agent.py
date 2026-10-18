#!/usr/bin/env python3
"""PreToolUse hook (Agent|Workflow): put the pace recommendation in front of every launch.

Does nothing while pace is off (`paceline on` turns it on). Runs `pace.py status --json --no-merge`
and adds one advice line to Claude's context. When a hard limit holds, or the five-hour window burns
toward its threshold, it returns "ask" instead, so the owner confirms the launch in a permission prompt:

- free memory below the profile floor (`min_free_commit_mb`), measured live on every call;
- the five-hour window at the background threshold (80 % by day, 95 % at night), or the weekly
  limit exhausted, both only while the usage data is fresh;
- the five-hour burn rate that reaches the threshold before the window resets (`pace 5h burn:`), only
  while the usage data is fresh and only when the launch exceeds the recommended agent count (active
  reservations at or above `max_parallel_agents`).

Parallel launches of one assistant message run their gates before any SubagentStart, so each gate also
takes a short-lived `launch` reservation, keyed by the tool_use_id, in the same lock hold that reads the
count (`pace.py status --launch-key`). Gate N therefore counts the launches of gates 1 to N-1, and the
reservation ends on its own after pace.py's LAUNCH_TTL (60 s), or earlier when a SubagentStart of the
same session takes it over (`reserve acquire --kind subagent`). A denied launch releases it again.

While the owner's away marker is set (`/paceline:away`, cleared by their next message) no one can answer
a prompt, so a launch that would ask, for a hard limit or for the burn, is denied instead.

Everything else stays advice. Any failure of pace.py (error, timeout, no data) allows silently.
PACELINE_SCRIPT overrides the pace.py path, for tests.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
try:
    import limits_file
except Exception:  # noqa: BLE001 - the hook still works, untraced, when the module can't load
    limits_file = None
try:
    import five_burn
except Exception:  # noqa: BLE001 - without the module the hook keeps its hard limits and skips the burn ask
    five_burn = None


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


def pace_status(session, launch_key=None):
    argv = [sys.executable, PACE, "status", "--json", "--no-merge"]
    if launch_key:
        argv += ["--launch-key", launch_key]
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


def release_launch(session, launch_key):
    """Drop the launch reservation of a refused launch; the reservation's own expiry covers a failure here."""
    argv = [sys.executable, PACE, "reserve", "release", "--key", launch_key, "--no-merge"]
    if session:
        argv += ["--session", session]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as error:
        note_failure("hook.pre_agent.release", error=error)
        return
    if out.returncode not in (0, 4):
        note_failure("hook.pre_agent.release", exit_code=out.returncode, stderr=out.stderr.strip()[-300:])


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


def burn_limits(status):
    if five_burn is None or (status.get("data") or {}).get("stale"):
        return []
    five = status.get("five_hour") or {}
    burn = five.get("burn") or {}
    if burn.get("limited") is not True:
        return []
    return [five_burn.reason(burn, five.get("reset_local") or "later")]


def over_cap(status):
    """True when this launch would exceed the recommended agent count, a cap of 0 included."""
    cap = (status.get("recommendation") or {}).get("max_parallel_agents")
    return cap is None or (status.get("active_reservations") or 0) >= cap


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
        # Claude Code writes UTF-8; sys.stdin would decode it with the locale's code page.
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        return 0
    if too_old():
        return 0
    trace("hook.pre_agent", tool=payload.get("tool_name"))
    session = payload.get("session_id")
    launch_key = payload.get("tool_use_id") or uuid.uuid4().hex
    status = pace_status(session, launch_key)
    if not status:
        return 0
    line = advice(status)
    limits = hard_limits(status)
    # Within the cap a burn is advice only, so it joins a hard limit's reasons only over the cap.
    burn = burn_limits(status) if over_cap(status) else []
    out = {"hookEventName": "PreToolUse", "additionalContext": line}
    result = {"hookSpecificOutput": out}
    verdict = None
    if limits or burn:
        # With the owner away no one answers a prompt, and an unanswered one holds the turn until the prompt
        # cache expires.
        verdict = "deny" if (status.get("away") or {}).get("on") is True else "ask"
    if verdict == "deny" and status.get("launch_reservation"):
        release_launch(session, launch_key)
    if verdict:
        why = "; ".join(limits + burn)
        reason = f"pace {'hard limit' if limits else '5h burn'}: {why}"
        if verdict == "deny":
            out.update(permissionDecision="deny",
                       permissionDecisionReason=f"{reason}. {payload.get('tool_name')} launch refused: "
                                                "the owner is away and no one can confirm it.")
            if limits:
                follow_up = "Do not retry it; wait for the limit to clear."
            else:
                follow_up = "It exceeds the recommended agent count. Do not retry it; continue within the cap or wait."
            out["additionalContext"] = (f"{line}\npace: this launch was refused by the pace gate because the owner is "
                                        f"away and {why}. {follow_up}")
        else:
            out.update(permissionDecision="ask",
                       permissionDecisionReason=f"{reason}. Launch {payload.get('tool_name')} anyway?")
            out["additionalContext"] = (f"{line}\npace: the owner was asked to confirm this launch because {why}. "
                                        "If the owner declined, do not retry; wait for the limit to clear or ask the owner.")
        # Some prompt surfaces show neither the reason nor a system message, only the tool input,
        # so the reason also goes in front of the description the prompt displays.
        tool_input = payload.get("tool_input")
        if verdict == "ask" and isinstance(tool_input, dict) and isinstance(tool_input.get("description"), str):
            out["updatedInput"] = dict(tool_input, description=f"[{reason}] {tool_input['description']}")
        result["systemMessage"] = reason
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
