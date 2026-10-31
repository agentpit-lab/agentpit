#!/usr/bin/env python3
"""PreToolUse hook (Agent|Workflow): tell the model the pace in plain words, and hold launches at a stop.

Does nothing while pace is off (`paceline on` turns it on). Runs `pace.py status --json --no-merge` and decides
two things: a verdict (allow, ask or deny) and the text the model reads.

The mode is `status["plan"]["effective_mode"]`; only the exact value `limit` is limit mode (which already folds
in `mode inform --until-part-end`). A missing plan object, an error or anything else counts as `inform`, so a
broken plan never adds text.

- `inform` mode: the model reads nothing, except the hold text when a safety stop holds the launch.
- `limit` mode: the model reads the line (`pace_line`, the plan's share, the recent rate and each safety stop in
  reach) before every launch, and the hold text (`hold_text`) when a stop or the plan gate holds it. With no
  weekly reading the hook stays silent.

The hook returns "ask", so the owner confirms the launch in a permission prompt, when:

- free memory is below the profile floor (`min_free_commit_mb`), measured live on every call, only while the
  memory guard is on (`memory_guard`, off by default; with it off, memory is not measured here);
- the five-hour window is at its stop (`five_hour_stop_pct`), or the weekly limit is exhausted, both only while
  the usage data is fresh;
- the weekly reserve (`pace reserve:`): the week's spend is at or past 100 minus the reserve before its
  release instant (`paceline reserve`), in `inform` and `limit` mode alike, on fresh or stale data;
- the week plan, only in `limit` mode (and not while `mode inform --until-part-end` holds), on fresh data,
  for a planned part whose spend is over its share pro rata (`pace plan:`); the first such launch of a
  part names the command that stops these prompts (to the owner, in the prompt's reason).

While the owner's away marker is set (`/paceline:away`, cleared by their next message) no one can answer
a prompt, so a launch that would ask is denied instead. The same holds in the permission modes where no
person answers the prompt: `auto` (the auto-mode classifier approves it and the launch runs, so the stop would
only warn), `bypassPermissions` and `dontAsk` (`permission_mode` of the hook input).

`main` works in one order, so no text failure can drop a stop: status (or the hook's own status), the reasons,
the verdict, then the text inside its own `try`. A text that fails falls back to the hold text's lead plus
"Do not retry a declined launch." (with a verdict) or to nothing (without one), and is noted in the debug log.
The owner-facing texts (`permissionDecisionReason`, `systemMessage`, the description prefix of the prompt)
stay technical. The model-facing `additionalContext` is the line or the hold text alone, in plain words.

Parallel launches of one assistant message run their gates before any SubagentStart, so each gate also
takes a short-lived `launch` hold, keyed by the tool_use_id, under the same lock that reads the
count (`pace.py status --launch-key`). Gate N therefore counts the launches of gates 1 to N-1, and the
hold ends on its own after pace.py's LAUNCH_TTL (60 s), or earlier when a SubagentStart of the
same session takes it over (`hold acquire --kind subagent`). A denied launch releases it again.

When pace.py gives no answer (error, timeout, unreadable output) the hook measures the free memory itself and
applies the floor, with the guard on; without a breach it allows silently. With neither text nor verdict the
hook prints nothing. PACELINE_SCRIPT overrides the pace.py path, for tests.
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
    """Drop the launch hold of a refused launch; the hold's own expiry covers a failure here."""
    argv = [sys.executable, PACE, "hold", "release", "--key", launch_key, "--no-merge"]
    if session:
        argv += ["--session", session]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as error:
        note_failure("hook.pre_agent.release", error=error)
        return
    if out.returncode not in (0, 4):
        note_failure("hook.pre_agent.release", exit_code=out.returncode, stderr=out.stderr.strip()[-300:])


def floor_gb(mb):
    """A floor in GB with one decimal, dropped when whole: 2048 gives 2, 1500 gives 1.5 (never a whole number
    above a fractional free figure, which would make a hold read as a mistake)."""
    return f"{mb / 1024:.1f}".removesuffix(".0")


UNKNOWN_NOTE = " (highest of the accounts on this machine; this session's account is unknown)"


def stop_figures(status):
    """(figures, note): where the five-hour and weekly stops read their numbers.

    The status itself, or, while this session's account has no weekly record (the data is stale), the most
    restrictive fresh figures of any account that the status carries as `stops` (pace.restrictive_stops) with
    a note saying so; None when it carries none, and there is nothing to stop on. The one place that makes
    the choice, for the gates and the hold text."""
    if not (status.get("data") or {}).get("stale"):
        return status, ""
    unknown = status.get("stops")
    return (unknown, UNKNOWN_NOTE) if isinstance(unknown, dict) else (None, "")


def hard_stops(status):
    """The hard stops that hold, as (kind, reason) pairs: kind is memory, five_hour, weekly or weekly_scoped.

    One chokepoint for the gates and the hold text: `weekly` is week used at 100, `weekly_scoped` is only the
    session's model's bucket at 100 (`effective_pct` tests the higher of the two)."""
    stops = []
    host = status.get("host") or {}
    commit, floor = host.get("free_commit_mb"), host.get("min_free_commit_mb")
    if commit is not None and floor is not None and commit < floor:
        stops.append(("memory", f"free memory {commit / 1024:.1f} GB < {floor_gb(floor)} GB, "
                                "a runaway process can crash the host"))
    figures, note = stop_figures(status)
    if figures is None:
        return stops
    five = figures.get("five_hour") or {}
    used, threshold = five.get("used_pct"), five.get("threshold_pct")
    if used is not None and threshold is not None and used >= threshold:
        stops.append(("five_hour", f"5h window {used:.0f}% >= {threshold:.0f}% stop, "
                                   f"resets {five.get('reset_local') or 'later'}{note}"))
    weekly = figures.get("weekly") or {}
    if (weekly.get("effective_pct") or 0) >= 100:
        week = weekly.get("seven_day_pct")
        plain = isinstance(week, (int, float)) and week >= 100
        stops.append(("weekly" if plain else "weekly_scoped", f"weekly limit exhausted{note}"))
    return stops


def hard_limits(status):
    return [reason for _kind, reason in hard_stops(status)]


def own_status():
    """A minimal status measured by the hook itself, for when `pace.py status --json` gave nothing.

    It holds only the host view (pace.host_view: the free memory measured by `memory_mb`, nothing with the
    guard off, and the floor of the profile, null with the guard off), so the memory floor still applies when it
    is on. A profile file that exists but cannot be read or parsed counts as the guard on with the default floor
    (pace.guard_profile), and the read failure is logged. None when pace.py can't be loaded."""
    try:
        import pace
        sd = limits_file.paceline_dir()
        profile, read_error = pace.guard_profile(sd)
        if read_error is not None:
            note_failure("hook.pre_agent.profile", error=read_error)
        try:
            away = pace.away.read(sd)
        except Exception:  # noqa: BLE001 - without the marker the owner is taken as present
            away = {}
        return {"host": pace.host_view(profile, pace.memory_mb(profile, False)), "data": {"stale": True},
                "away": away}
    except Exception as error:  # noqa: BLE001 - the hook must never fail a launch
        note_failure("hook.pre_agent.own_status", error=error)
        return None


def plan_limits(status):
    """The plan reason (a list of one text, or none): the part is over its share in `limit` mode.

    Nothing without a plan object, in `inform` mode, for an unplanned part, or on stale data."""
    plan = status.get("plan")
    if not isinstance(plan, dict) or plan.get("effective_mode") != "limit":
        return []
    part = plan.get("part")
    if not isinstance(part, dict) or part.get("over") is not True or part.get("unplanned") is not False:
        return []
    if (status.get("data") or {}).get("stale") is True:
        return []
    try:
        kind = "working-hours" if part.get("kind") == "work" else "off-hours"
        text = (f"{kind} part over this part's share: spent {part['spent_pct']:.1f} % of {part['share_pct']:.1f} % "
                f"(pro rata {part['allowed_pct']:.1f} % by now)")
        if part.get("estimated") is True:
            text += " (share estimated)"
        if plan.get("first_prompt") is True:
            text += f"; stop these prompts until {part['end_local'][11:16]}: paceline mode inform --until-part-end"
    except (KeyError, TypeError, ValueError):
        return []
    return [text]


def reserve_limits(status):
    """The reserve reason (a list of one text, or none): the week's spend is past the reserve's stop line.

    One reason only when `status["reserve"]["over"]` is True. A missing or malformed object, a missing
    field or any exception gives none, so the hard limits never depend on it."""
    try:
        reserve = status.get("reserve")
        if not isinstance(reserve, dict) or reserve.get("over") is not True:
            return []
        return [f"weekly {reserve['label']} kept until {reserve['release_text']}: "
                f"week used {reserve['used_pct']:.0f} % >= {reserve['stop_at_pct']:.0f} %"]
    except Exception:  # noqa: BLE001 - a broken reserve object must not change the verdict
        return []


# The words for the share the model's recent rate would use, by ratio = what the share allows / recent rate.
FRACTIONS = ((0.15, "a small part of that"), (0.29, "about a quarter of that"), (0.42, "about a third of that"),
             (0.58, "about half of that"), (0.71, "about two thirds of that"),
             (0.88, "about three quarters of that"), (1.15, "about the same"))
FIVE_HOUR_NEAR_POINTS = 25   # the five-hour window is "in reach" of its stop within this many points
RESERVE_NEAR_POINTS = 10     # the reserve is "in reach" within this many points of its stop line
RESERVE_GOAL = "the reserve is kept for the owner's unplanned work, and work done in this session spends it too"


def part_word(part):
    return "working-hours" if part.get("kind") == "work" else "off-hours"


def hhmm(text):
    return text[11:16]


def kept_reserve(status):
    """The `reserve` object while it is kept (a size, not released, no error), else None."""
    reserve = status.get("reserve")
    if (not isinstance(reserve, dict) or reserve.get("error") or reserve.get("released")
            or not (reserve.get("pct") or 0) > 0 or not reserve.get("release_text")):
        return None
    return reserve


def reserve_for(reserve):
    """` for "name"` for a named reserve, else nothing (pace.reserve_label builds the whole label)."""
    return f' for "{reserve["name"]}"' if reserve.get("name") else ""


def stops_text(status, five_hour=True, why=None):
    """The safety stops in reach: the five-hour stop (not on stale figures) and the kept reserve."""
    asks = "is refused" if refused(why) else "asks the owner"
    five = status.get("five_hour") or {}
    burn = five.get("burn") if isinstance(five.get("burn"), dict) else None
    week = (status.get("weekly") or {}).get("seven_day_pct") or 0
    used = five.get("used_pct") or 0
    clauses = []
    stop = five.get("threshold_pct")
    limited = burn is not None and burn.get("limited") is True
    if five_hour and stop is not None and (used >= max(0, stop - FIVE_HOUR_NEAR_POINTS) or limited):
        clause = f"5-hour window {used:.0f} %"
        reaches = burn.get("threshold_at_local") or burn.get("threshold_at") if limited else None
        if reaches and five.get("reset_local"):
            clause += (f" and at this rate {stop:.0f} % at {hhmm(reaches)}, "
                       f"before its reset at {hhmm(five['reset_local'])}")
        clauses.append(f"{clause} (each launch {asks} from {stop:.0f} %)")
    reserve = kept_reserve(status)
    if reserve is not None and week >= reserve["stop_at_pct"] - RESERVE_NEAR_POINTS:
        clauses.append(f"{reserve['pct']:.0f} % of the week kept in reserve{reserve_for(reserve)} until "
                       f"{reserve['release_text']}, week {week:.0f} % used "
                       f"(each launch {asks} from {reserve['stop_at_pct']:.0f} % used)")
    if not clauses:
        return f"No safety stop is near: week {week:.0f} % used, 5-hour window {used:.0f} %."
    return "Safety stops: " + "; ".join(clauses) + "."


def share_sentence(status, part, over):
    """What the share allows per hour until the part ends, with the recent rate and, over plan, a fraction."""
    budget = status.get("budget_per_hour_pct") or 0
    end = hhmm(part["end_local"])
    if over and budget <= 0:
        return f"The share is used up; nothing more is planned until {end}."
    burn = (status.get("five_hour") or {}).get("burn")
    scale = status.get("five_hour_to_week_pct")
    lead, ratio = "The share allows", None
    if isinstance(burn, dict) and scale:
        rate = burn["rate_per_hour_pct"] * scale / 100
        lead = f"Last {burn['span_min']:.0f} min about {rate:.1f} %/h; the share allows"
        if rate > 0:
            ratio = budget / rate
    fraction = ""
    if over and ratio is not None:
        fraction = next((f", {words}" for limit, words in FRACTIONS if ratio < limit), "")
    return f"{lead} about {budget:.1f} %/h until {end}{fraction}."


def pace_line(status, why=None):
    """The line the model reads before a launch in limit mode, or None when there is nothing to say.

    Pure: it reads only `status --json` fields. None without a weekly reading (`no_usage`)."""
    if status.get("no_usage"):
        return None
    head = "pace (limit mode): "
    over_asks = ("each agent launch is refused; do not retry a refused launch" if refused(why)
                 else "each agent launch asks the owner first; do not retry a declined launch")
    data = status.get("data") or {}
    if data.get("stale") is True:
        age = data.get("age_min")
        old = f"{age:.0f} min old" if isinstance(age, (int, float)) else "old"
        # Only what still checks a launch on stale figures is named: a reserve that is kept, the memory floor.
        checks = [name for name, on in (("the weekly reserve", kept_reserve(status) is not None),
                                        ("the memory floor", (status.get("host") or {}).get("guard") is True)) if on]
        checked = f"are checked only against {' and '.join(checks)}" if checks else "are not checked"
        return (f"{head}usage figures are {old}, so launches {checked} until fresh figures arrive. "
                f"{stops_text(status, five_hour=False, why=why)}")
    part = (status.get("plan") or {}).get("part")
    if not isinstance(part, dict):
        return None
    if part.get("unplanned") is True:
        return (f"{head}nothing is planned for this part, so launches are not checked against the plan. "
                f"{stops_text(status, why=why)}")
    stops = stops_text(status, why=why)
    if part.get("over") is True:
        over = part["spent_pct"] - part["allowed_pct"]
        return (f"{head}{over:.1f} % of the week over this {part_word(part)} part's share. "
                f"{share_sentence(status, part, True)} While over the share, {over_asks}. {stops}")
    return (f"{head}on plan. This {part_word(part)} part has used {part['spent_pct']:.1f} % of the week; its share "
            f"is {part['share_pct']:.1f} % until {hhmm(part['end_local'])}, and {part['allowed_pct']:.1f} % by now "
            f"would be on plan. {share_sentence(status, part, False)} Over the share, {over_asks}. {stops}")


UNATTENDED_MODES = ("auto", "bypassPermissions", "dontAsk")


def unattended_why(status, payload):
    """Why no one answers a prompt now: "away", the permission mode's name, or None (a person can answer)."""
    if (status.get("away") or {}).get("on") is True:
        return "away"
    mode = payload.get("permission_mode") if isinstance(payload, dict) else None
    return mode if mode in UNATTENDED_MODES else None


def mode_phrase(why):
    return f"{why} mode"


def refused(why):
    """True when the permission mode (not the away marker) makes the hook refuse a launch that would ask."""
    return why in UNATTENDED_MODES


def owner_reason(why, tool):
    """The owner-facing end of a denial's reason: what holds the launch and how to get past it."""
    if why == "away":
        return "the owner is away and no one can confirm it."
    if why == "auto":
        return (f"in auto mode the auto-mode classifier, not you, answers permission prompts. To launch {tool} "
                "anyway, switch this session to the default (Manual) mode, where you confirm it.")
    return (f"no one answers prompts in {mode_phrase(why)}. To launch {tool} anyway, switch this session to the "
            "default (Manual) mode, where you confirm it.")


def hold_lead(verdict, why=None):
    if verdict == "deny" and refused(why):
        return (f"pace: this launch was refused because in {mode_phrase(why)} no person confirms it. "
                "Tell the owner what holds it.")
    if verdict == "deny":
        return "pace: this launch was refused because the owner is away and cannot confirm it."
    return "pace: this launch needs the owner's confirmation."


def hold_text(status, verdict, reasons, why=None):
    """The text the model reads when a stop or the plan gate holds the launch (verdict "ask" or "deny").

    Pure. `reasons` is {"reserve": [...], "plan": [...]}, the hook's reason lists for the two gates that are not
    hard stops; the hard stops are read again from the status by `hard_stops`, so the gates and this text cannot
    disagree (a "limits" key, if given, is ignored)."""
    ask = verdict != "deny"
    need = ("asks the owner first" if ask else "is refused" if refused(why)
            else "needs the owner's confirmation")
    kinds = [kind for kind, _reason in hard_stops(status)]
    reserve_holds, plan_holds = bool(reasons.get("reserve")), bool(reasons.get("plan"))
    if ask and plan_holds and not kinds and not reserve_holds:
        text = pace_line(status, why)
        if text:
            return text
    figures = stop_figures(status)[0] or {}
    five = figures.get("five_hour") or {}
    weekly = figures.get("weekly") or {}
    reserve = status.get("reserve") if isinstance(status.get("reserve"), dict) else {}
    part = (status.get("plan") or {}).get("part") or {}
    sentences = []
    if "memory" in kinds:
        host = status["host"]
        sentences.append(f"Free memory is {host['free_commit_mb'] / 1024:.1f} GB, below the "
                         f"{floor_gb(host['min_free_commit_mb'])} GB floor set for this machine; a new heavy "
                         f"process can crash it. Until memory frees up, each agent launch {need}.")
    if "five_hour" in kinds:
        reset_at = f"at {hhmm(five['reset_local'])}" if five.get("reset_local") else "later"
        sentences.append(f"The 5-hour window is {five['used_pct']:.0f} % used and resets "
                         f"{reset_at}; until then each agent launch {need}, and at 100 % all work, "
                         "this session's included, stops until the reset.")
    if "weekly" in kinds or "weekly_scoped" in kinds:
        reset = reserve.get("reset_text") if not reserve.get("error") else None
        reset = reset or (weekly.get("reset_local") or "later")[:16]
        if "weekly" in kinds:
            sentences.append(f"The weekly limit is used up until the reset at {reset}; all work, this session's "
                             "included, stops until then.")
        else:
            sentences.append(f"The weekly limit of this session's model is used up until the reset at {reset}; "
                             f"work on that model stops until then, and each agent launch {need}.")
    if reserve_holds:
        sentences.append(f"The week is {reserve['used_pct']:.0f} % used, and the last {reserve['pct']:.0f} % is "
                         f"kept in reserve{reserve_for(reserve)} until {reserve['release_text']}; until then each "
                         f"agent launch {need}.")
    if plan_holds:
        sentences.append(f"This {part_word(part)} part is {part['spent_pct'] - part['allowed_pct']:.1f} % of the "
                         f"week over its share until {hhmm(part['end_local'])}.")
    goal = None
    if reserve_holds:
        goal = RESERVE_GOAL
    elif plan_holds:
        budget = status.get("budget_per_hour_pct") or 0
        goal = f"the share allows about {budget:.1f} %/h until {hhmm(part['end_local'])}"
    tail = "If the owner declines, do not retry the launch" if ask else "Do not retry the launch"
    tail += f": {goal}." if goal else "."
    return " ".join([hold_lead(verdict, why)] + sentences + [tail])


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
        # Whatever went wrong with status, the memory floor does not depend on it.
        status = own_status()
        if not status or not hard_limits(status):
            return 0
    limits = hard_limits(status)
    reserve = reserve_limits(status)
    plan_reasons = plan_limits(status)
    verdict = None
    why = None
    if limits or reserve or plan_reasons:
        # With the owner away no one answers a prompt, and an unanswered one holds the turn until the prompt
        # cache expires.
        # The same holds where the permission mode (auto, bypassPermissions) lets no person answer.
        why = unattended_why(status, payload)
        verdict = "deny" if why else "ask"
    if verdict == "deny" and status.get("launch_hold"):
        release_launch(session, launch_key)
    # The text comes after the verdict and in its own try: a failure here never drops a stop.
    try:
        if verdict:
            text = hold_text(status, verdict, {"reserve": reserve, "plan": plan_reasons}, why)
        else:
            plan = status.get("plan")
            limit_mode = isinstance(plan, dict) and plan.get("effective_mode") == "limit"
            text = pace_line(status, unattended_why(status, payload)) if limit_mode else None
    except Exception as error:  # noqa: BLE001 - the verdict stands, whatever the text did
        note_failure("hook.pre_agent.line", error=error)
        text = f"{hold_lead(verdict, why)} Do not retry a declined launch." if verdict else None
    if not verdict and not text:
        return 0
    out = {"hookEventName": "PreToolUse"}
    if text:
        out["additionalContext"] = text
    result = {"hookSpecificOutput": out}
    if verdict:
        joined = "; ".join(limits + reserve + plan_reasons)
        prefix = "pace hard limit" if limits else "pace reserve" if reserve else "pace plan"
        reason = f"{prefix}: {joined}"
        if verdict == "deny":
            out.update(permissionDecision="deny",
                       permissionDecisionReason=f"{reason}. {payload.get('tool_name')} launch refused: "
                                                + owner_reason(why, payload.get("tool_name")))
        else:
            out.update(permissionDecision="ask",
                       permissionDecisionReason=f"{reason}. Launch {payload.get('tool_name')} anyway?")
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
