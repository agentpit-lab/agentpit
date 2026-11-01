"""The unattended-work advice: one pure function from a status answer to "may work start, how much and how many".

`build(status, max_agents)` takes the dict `pace.py status --json` prints (the full form, the no-week form, or
the pre-agent hook's own status) and returns the advice dict of format version 1. `line()` is the one-line form.

The advice names the safety stops the pre-agent hook shares (memory, five-hour window, weekly limit, weekly
reserve), the data conditions (no usage, stale) and the week plan's conditions (no share, share used), each as a
coded reason with an effect (stop, wait or limit). The action follows from the effects: stop beats wait beats
start. The budget is the whole remainder of the current part's fixed share.

Pure: no file is read or written. A missing or wrongly typed field counts as absent and the build never raises.
A later version adds fields without changing the meaning of these; a changed meaning raises VERSION.
Standard library only, plus five_burn (itself standard library only).
"""
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import five_burn  # noqa: E402

VERSION = 1
DEFAULT_AGENTS = 3  # the agent count when the host's max_heavy_runs is not known
SAFETY_CODES = ("memory", "weekly", "weekly_scoped", "five_hour", "reserve")
UNKNOWN_NOTE = " (highest of the accounts on this machine; this session's account is unknown)"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _num(value):
    """The value when it is a finite number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _instant(text):
    """An ISO 8601 text as an aware UTC datetime, None when it is not one."""
    if not isinstance(text, str):
        return None
    try:
        when = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _text(value, default=""):
    return value if isinstance(value, str) and value else default


def floor_gb(mb):
    """A floor in GB with one decimal, dropped when whole: 2048 gives 2, 1500 gives 1.5 (never a whole number
    above a fractional free figure, which would make a hold read as a mistake)."""
    return f"{mb / 1024:.1f}".removesuffix(".0")


def reason(code, effect, text, until=None, command=None):
    return {"code": code, "effect": effect, "text": text, "until": until, "command": command}


def stop_figures(status):
    """(figures, note): where the five-hour and weekly stops read their numbers.

    The status itself, or, while this session's account has no weekly record (the data is stale), the most
    restrictive fresh figures of any account that the status carries as `stops` (pace.restrictive_stops) with
    a note saying so; (None, "") when it carries none, and there is nothing to stop on."""
    if _dict(status.get("data")).get("stale") is not True:
        return status, ""
    unknown = status.get("stops")
    return (unknown, UNKNOWN_NOTE) if isinstance(unknown, dict) else (None, "")


def budget_of(status):
    """The part's remainder: share - spent - outstanding holds, never below 0; 0 without a fixed share."""
    share = _num(_dict(status.get("budget_part")).get("share_pct"))
    if share is None:
        return 0.0
    spent = _num(_dict(status.get("budget_part")).get("spent_pct")) or 0.0
    holds = _num(status.get("outstanding_holds_pct")) or 0.0
    return round(max(0.0, share - spent - holds), 3)


def safety_reasons(status):
    """Rows 1 to 4: memory, five-hour window, weekly limit, weekly reserve, in the hook's order."""
    out = []
    try:
        host = _dict(status.get("host"))
        free, floor = _num(host.get("free_commit_mb")), _num(host.get("min_free_commit_mb"))
        if free is not None and floor is not None and free < floor:
            out.append(reason("memory", "stop", f"free memory {free / 1024:.1f} GB < {floor_gb(floor)} GB, "
                                                "a runaway process can crash the host"))
    except Exception:  # noqa: BLE001 - one damaged figure never hides the others
        pass
    try:
        figures, note = stop_figures(status)
        if figures is not None:
            five = _dict(figures.get("five_hour"))
            used, threshold = _num(five.get("used_pct")), _num(five.get("threshold_pct"))
            if used is not None and threshold is not None and used >= threshold:
                out.append(reason("five_hour", "wait",
                                  f"5h window {used:.0f}% >= {threshold:.0f}% stop, "
                                  f"resets {_text(five.get('reset_local'), 'later')}{note}",
                                  until=_text(five.get("reset"), None)))
            weekly = _dict(figures.get("weekly"))
            if (_num(weekly.get("effective_pct")) or 0) >= 100:
                plain = _num(weekly.get("seven_day_pct"))
                out.append(reason("weekly" if plain is not None and plain >= 100 else "weekly_scoped", "stop",
                                  f"weekly limit exhausted{note}", until=_text(weekly.get("reset"), None)))
    except Exception:  # noqa: BLE001
        pass
    try:
        reserve = status.get("reserve")
        if isinstance(reserve, dict) and reserve.get("over") is True:
            out.append(reason("reserve", "wait",
                              f"weekly {reserve['label']} kept until {reserve['release_text']}: "
                              f"week used {reserve['used_pct']:.0f} % >= {reserve['stop_at_pct']:.0f} %",
                              until=_text(reserve.get("release_at"), None)))
    except Exception:  # noqa: BLE001 - a broken reserve object must not change the verdict
        pass
    return out


def data_reasons(status):
    """Rows 5 and 6: no usage reading at all, or one that is stale."""
    if status.get("no_usage"):
        return [reason("no_usage", "wait", str(status["no_usage"]))]
    data = _dict(status.get("data"))
    if data.get("stale") is True:
        age = _num(data.get("age_min"))
        return [reason("stale", "wait", f"usage data stale ({age:.0f} min)" if age is not None
                                         else "usage data stale")]
    return []


def part_word(part):
    return "working-hours" if _dict(part).get("kind") == "work" else "off-hours"


def plan_reasons(status, budget):
    """Rows 7 to 10, on fresh data with the week's figures: plan error, nothing planned, no share, share used."""
    if status.get("plan_error"):
        return [reason("plan_error", "stop", str(status["plan_error"]))]
    part = status.get("budget_part")
    if not isinstance(part, dict):
        return []
    weekly = _dict(status.get("weekly"))
    share = _num(part.get("share_pct"))
    following = part.get("next_planned") if isinstance(part.get("next_planned"), dict) else None
    reset = _text(weekly.get("reset"), None)
    if share is None or (share == 0 and following is None):
        return [reason("unplanned", "stop",
                       f"nothing is planned until the weekly reset at {_text(weekly.get('reset_local'), 'later')[:16]}",
                       until=reset)]
    start_local = _text(following.get("start_local"), "later")[:16] if following else None
    if share == 0:
        day = _text(part.get("day"))
        command = (f"paceline plan set {day} --work 0.5" if part.get("kind") == "work"
                   else f"paceline plan set {day} --off-hours 0.5")
        return [reason("share_zero", "wait",
                       f"this {part_word(part)} part has no share: nothing is planned until {start_local}. "
                       f"To plan some now: {command}", until=_text(following.get("start"), None), command=command)]
    if share > 0 and budget == 0:
        spent = _num(part.get("spent_pct")) or 0.0
        holds = _num(status.get("outstanding_holds_pct")) or 0.0
        text = f"this {part_word(part)} part's share is used: {spent:.1f} % of {share:.1f} %"
        if holds > 0:
            text += f", {holds:.1f} % held"
        if following:
            return [reason("share_used", "wait",
                           f"{text}; the next planned part starts {start_local}",
                           until=_text(following.get("start"), None))]
        return [reason("share_used", "stop", f"{text}; nothing more is planned before the weekly reset",
                       until=reset)]
    return []


def burn_reason(status, agents):
    """Row 11 and the capped agent count: (reason or None, agents). The reason is `limit` while the capped
    count is above 0, else `wait` until the five-hour reset."""
    five = _dict(status.get("five_hour"))
    burn, reset = five.get("burn"), five.get("reset")
    if not (isinstance(burn, dict) and burn.get("limited") is True and reset is not None):
        return None, agents
    try:
        capped = five_burn.cap(agents, burn)
        text = five_burn.reason(burn, five.get("reset_local"))
    except Exception:  # noqa: BLE001 - a burn that cannot be read does not cap
        return None, agents
    if capped > 0:
        return reason("five_hour_burn", "limit", text), capped
    return reason("five_hour_burn", "wait", text, until=_text(reset, None)), 0


def build(status, max_agents=6):
    """The advice dict for a status dict; never raises, whatever the dict holds."""
    status = status if isinstance(status, dict) else {}
    data = _dict(status.get("data"))
    budget = 0.0
    try:
        budget = budget_of(status)
    except Exception:  # noqa: BLE001
        pass
    reasons = safety_reasons(status)
    reasons += data_reasons(status)
    fresh = not status.get("no_usage") and data.get("stale") is not True
    if fresh:
        try:
            reasons += plan_reasons(status, budget)
        except Exception:  # noqa: BLE001 - a damaged plan figure never hides the safety stops
            pass
    effects = {r["effect"] for r in reasons}
    action = "stop" if "stop" in effects else "wait" if "wait" in effects else "start"
    agents = 0
    if action == "start":
        heavy = _num(_dict(status.get("host")).get("max_heavy_runs"))
        cap = _num(max_agents)
        agents = max(1, min(int(cap) if cap is not None else 6,
                            int(heavy) if heavy is not None else DEFAULT_AGENTS))
        burn, agents = burn_reason(status, agents)
        if burn:
            reasons.append(burn)
            if burn["effect"] == "wait":
                action = "wait"
    wait_until = None
    if action == "wait":
        untils = [(when, r["until"]) for r in reasons if r["effect"] == "wait"
                  for when in [_instant(r["until"])] if when is not None]
        wait_until = max(untils, key=lambda pair: pair[0])[1] if untils else None
    part = status.get("budget_part")
    return {"advice_version": VERSION, "computed_at": _text(status.get("now"), None),
            "account": _text(_dict(data.get("account")).get("key"), None), "action": action,
            "budget_pct": budget, "agents": agents, "wait_until": wait_until, "reasons": reasons,
            "part": None if not isinstance(part, dict) else
            {"kind": part.get("kind"), "day": part.get("day"), "start": part.get("start"), "end": part.get("end"),
             "share_pct": part.get("share_pct"), "spent_pct": part.get("spent_pct"),
             "holds_pct": status.get("outstanding_holds_pct")},
            "stale": data.get("stale") is True or bool(status.get("no_usage"))}


def line(advice, tz_text=None):
    """The one line `paceline advice` prints. `tz_text` turns an ISO instant into the profile's local text."""
    reasons = advice.get("reasons") or []
    texts = "; ".join(r["text"] for r in reasons)
    action = advice.get("action")
    if action == "start":
        out = f"advice: START | budget {advice['budget_pct']:.2f}% | agents <={advice['agents']}"
        return out + "".join(f" | {r['text']}" for r in reasons if r["effect"] == "limit")
    if action == "wait":
        until = advice.get("wait_until")
        if until is None:
            return f"advice: WAIT | {texts}"
        local = tz_text(until) if tz_text else until.replace("T", " ")
        return f"advice: WAIT until {local[:16]} | {texts}"
    return f"advice: STOP | {texts}"
