"""The PaceLine week plan: the plan file's schema, normalisation, pruning and save, the weekly reserve's
figures, the arithmetic that fixes a part, and the plan view `paceline plan` prints.

Stdlib only. It imports base.py, limits_file.py, five_burn.py and week.py, never pace.py; pace.py keeps the
commands and re-exports every name here.
"""
# Patches must target the defining module: pace.py re-exports the names defined here, so a patch of
# `pace.X` does not reach code in this module that reads X (tests/test_patch_targets.py checks it).
import contextlib
import dataclasses
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limits_file  # noqa: E402
import week  # noqa: E402
from base import (  # noqa: E402
    DAY, HOUR, PROFILE_DEFAULTS, WEEK, NoWeekError, PaceError, _is_number, iso_ms, load_zone, read_json, roll_bucket,
    used_at, write_json,
)
from five_burn import fmt_local  # noqa: E402
from limits_file import FUTURE_SLACK, NO_PARTITION, parse_iso, safe_iso  # noqa: E402


PLAN_VERSION = 1
PLAN_MODES = ("inform", "limit")
# The weekly reserve of an account: a size in whole percent (0 turns it off), an optional name, and the
# weekday it is released on (None: the last day before the reset).
RESERVE_DEFAULTS = {"pct": 8, "name": None, "release": None}
RESERVE_PCT_MAX = 50
RESERVE_PCT_RANGE = f"a whole 0-{RESERVE_PCT_MAX} % of the week, 0: off"
RESERVE_RELEASE_RANGE = "mon..sun, or last for the last day before the reset"
RESERVE_NAME_RANGE = "1-32 printable characters, empty: none"
RESERVE_NAME_MAX = 32
PLAN_KINDS = week.PLAN_KINDS
PLAN_ENTRY_KEYS = week.PLAN_ENTRY_KEYS
PLAN_STATUS_PART_KEYS = ("kind", "day", "start", "end", "end_local", "share_pct", "spent_pct", "allowed_pct", "over",
                         "unplanned", "estimated")
KIND_GLYPH = {"work": "☼", "off": "☾"}
MARK_LABEL = {0.0: "off", 0.5: "light", 1.0: "normal", 1.5: "heavy"}
ISO_DAY = week.ISO_DAY
BAR_WIDTH = 12


def reserve_name_ok(name):
    """Whether `name` is a valid reserve name: 1-RESERVE_NAME_MAX printable characters, not all blank."""
    return 1 <= len(name) <= RESERVE_NAME_MAX and name.isprintable() and name.strip() != ""


def plan_path(sd, key):
    """<state>/plan-<key>.json; a key that is not 16 lower-case hex digits names plan-unknown.json."""
    return sd / week.plan_file_name(key)


def new_plan():
    return {"version": PLAN_VERSION, "work_hours": week.DEFAULT_WORK_HOURS, "mode": "inform", "inform_until": None,
            "template": {}, "days": {}, "fixed": [], "reserve": dict(RESERVE_DEFAULTS)}


def _marks_of(value):
    return {k: value[k] for k in PLAN_KINDS if k in value} if isinstance(value, dict) else None


_plan_entry_ok = week.plan_entry_ok


def _reserve_of(value):
    """The reserve dict of a stored value, field by field: one that does not fit falls to its default."""
    out = dict(RESERVE_DEFAULTS)
    if not isinstance(value, dict):
        return out
    pct = value.get("pct")
    if isinstance(pct, float) and pct.is_integer():
        pct = int(pct)
    if isinstance(pct, int) and not isinstance(pct, bool) and 0 <= pct <= RESERVE_PCT_MAX:
        out["pct"] = pct
    name = value.get("name")
    if isinstance(name, str) and reserve_name_ok(name):
        out["name"] = name
    if value.get("release") in week.WEEKDAYS:
        out["release"] = value["release"]
    return out


def normalise_plan(raw):
    """A plan dict from a stored JSON object: a value that does not fit falls to its default, never an error."""
    plan = new_plan()
    plan["work_hours"] = week.plan_work_hours(raw)
    if raw.get("mode") in PLAN_MODES:
        plan["mode"] = raw["mode"]
    if safe_iso(raw.get("inform_until")) is not None:
        plan["inform_until"] = raw["inform_until"]
    template = raw.get("template")
    if isinstance(template, dict):
        for name in week.WEEKDAYS:
            marks = _marks_of(template.get(name))
            if marks is not None:
                plan["template"][name] = marks
    days = raw.get("days")
    if isinstance(days, dict):
        for day, value in days.items():
            marks = _marks_of(value)
            if marks is not None and ISO_DAY.fullmatch(str(day)):
                with contextlib.suppress(ValueError):
                    dt.date.fromisoformat(day)
                    plan["days"][day] = marks
    fixed = raw.get("fixed")
    if isinstance(fixed, list):
        for entry in fixed:
            if _plan_entry_ok(entry):
                plan["fixed"].append({k: entry[k] for k in PLAN_ENTRY_KEYS})
    plan["reserve"] = _reserve_of(raw.get("reserve"))
    return plan


def load_plan(sd, key):
    """The plan of the account with this key, normalised; the defaults when there is no file yet.

    A file that is not one JSON object is a PaceError and is never rewritten."""
    path = plan_path(sd, key)
    missing = object()
    raw = read_json(path, missing)
    if raw is missing:
        return new_plan()
    if not isinstance(raw, dict):
        raise PaceError(f"{path.name} must hold one JSON object; left untouched, fix it by hand")
    version = raw.get("version")
    if _is_number(version) and version > PLAN_VERSION:
        raise PaceError(f"{path.name} is version {version:g}, newer than the {PLAN_VERSION} this PaceLine reads; "
                        "left untouched, update PaceLine")
    return normalise_plan(raw)


def no_week_error(usage):
    """The refusal of a command that needs the week when the usage holds no weekly reading."""
    if ((usage or {}).get("account") or {}).get("reason") == NO_PARTITION:
        return NoWeekError("no usage data yet for the signed-in account: it fills after the first answer in a "
                           "session with paceline loaded")
    return NoWeekError("no usage data yet: rate-limits.json has no weekly reading; it fills after the first "
                       "answer in a session with paceline loaded")


def week_of(usage, now):
    """(plain weekly used %, reset instant) of the usage's weekly reading, or None without one."""
    record = (usage or {}).get("record")
    bucket = record.get("seven_day") if isinstance(record, dict) else None
    if not isinstance(bucket, dict):
        return None
    used, reset, _rolled = roll_bucket(bucket, now, WEEK)
    return None if reset is None else (used, reset)


def this_week_entries(plan, reset):
    return week.this_week_entries(plan["fixed"], reset)


def prune_plan(plan, now, reset, tz):
    """Drop what the week no longer needs: entries of earlier weeks, dated marks of days that ended before
    the week began, and a passed inform_until."""
    # Only entries of earlier weeks go; one of another week that is not earlier stays on disk, ignored on read.
    earlier = reset - WEEK + dt.timedelta(seconds=limits_file.SAME_WEEK_TOLERANCE_SEC)
    plan["fixed"] = [e for e in plan["fixed"] if parse_iso(e["week_reset"]) > earlier]
    work_start = week.parse_work_hours(plan["work_hours"])[0]
    week_start = reset - WEEK
    for day in list(plan["days"]):
        if week.local_instant(dt.date.fromisoformat(day) + DAY, work_start, tz) <= week_start:
            del plan["days"][day]
    until = safe_iso(plan["inform_until"])
    if until is not None and until <= now:
        plan["inform_until"] = None


def save_plan(sd, key, plan, now, reset=None, tz=None):
    """Write the plan file atomically; the caller holds pace.lock. With the week known, prune first.

    A run dated past the wall clock is a what-if and writes nothing."""
    if now > limits_file.wall_clock() + FUTURE_SLACK:
        return
    if reset is not None:
        prune_plan(plan, now, reset, tz)
    data = {k: plan[k] for k in ("version", "work_hours", "mode", "inform_until", "template", "days", "fixed")}
    data["reserve"] = _reserve_of(plan.get("reserve"))
    write_json(plan_path(sd, key), data)


@dataclasses.dataclass(frozen=True)
class ReserveState:
    """The weekly reserve of one week: its size, name, release weekday and release instant (UTC, None when
    the reserve is off or could not be computed); `error` says why it could not be."""
    pct: int
    name: object
    release: object
    release_at: object
    error: object = None

    def kept(self, t):
        """The reserve held at `t`, in percent."""
        return week.kept(t, self.pct, self.release_at)


def reserve_state(plan, reset, tz):
    """The one source of the reserve figure for the week ending at `reset`; never raises.

    `plan` is the plan dict or None for the default plan. A missing or unfit `reserve` reads as a plan file
    does. Any exception gives a failed state: the default size, no release instant (kept 0 everywhere) and
    an `error` naming the exception type."""
    try:
        plan = new_plan() if plan is None else plan
        reserve = _reserve_of(plan.get("reserve"))
        pct, release = reserve["pct"], reserve["release"]
        at = None if pct == 0 else week.release_at(reset, tz, plan["work_hours"], release)
        if at is not None and (not isinstance(at, dt.datetime) or at.tzinfo is None):
            raise TypeError("the release instant is not an aware datetime")
        return ReserveState(pct, reserve["name"], release, at)
    except Exception as exc:
        return failed_reserve_state(exc)


def failed_reserve_state(exc):
    """The state of a reserve that could not be computed: the default size for the nominal rates, nothing
    kept anywhere, and `error` naming the exception type."""
    return ReserveState(RESERVE_DEFAULTS["pct"], None, None, None,
                        f"the reserve could not be computed ({type(exc).__name__})")


RESERVE_TEXT_OFF = "no reserve (0 %)"
RESERVE_TEXT_ERROR = "reserve unknown: the stop is off"


def _whole(value):
    """A percentage as JSON shows it: an int when it is whole, else rounded to 3 places."""
    value = float(value)
    return int(value) if value.is_integer() else round(value, 3)


def day_text(t, tz):
    """The local weekday, day of month and clock of an instant, as `paceline plan` prints days: Fri 30 09:00."""
    local = t.astimezone(tz)
    return f"{week.WEEKDAYS[local.weekday()].capitalize()} {local.day} {local:%H:%M}"


def reserve_label(pct, name):
    """The reserve as the texts name it: `reserve 8 %`, or `reserve "tests" 8 %` with a name."""
    return f'reserve "{name}" {pct} %' if name else f"reserve {pct} %"


def reserve_error_form(message):
    return {"error": message, "over": False, "kept_pct": 0, "text": RESERVE_TEXT_ERROR}


def reserve_figures(now, state, seven_used, reset, tz):
    """The `reserve` object of `status --json` and `plan --json` for the week ending at `reset`.

    `seven_used` is the plain weekly figure the stop compares: `over` is true before the release instant
    when it has reached 100 - pct, with no tolerance. A failed state gives the error form."""
    if state.error:
        return reserve_error_form(state.error)
    used = _whole(seven_used)
    at = state.release_at
    off = state.pct == 0 or at is None
    released = not off and now >= at
    kept = state.kept(now)
    over = not off and not released and seven_used >= 100 - state.pct
    # No release candidate this week: the release instant is the reset, so the reserve never opens.
    kept_to_reset = not off and at == reset
    obj = {"pct": state.pct, "name": state.name, "release": state.release,
           "release_at": None if off else iso_ms(at),
           "release_at_local": None if off else fmt_local(at, tz),
           "release_day": None if off else at.astimezone(tz).date().isoformat(),
           "release_text": None if off else day_text(at, tz),
           "reset_text": day_text(reset, tz), "label": reserve_label(state.pct, state.name), "released": released, "kept_to_reset": kept_to_reset,
           "opens_clock": None if off or released or kept_to_reset else f"{at.astimezone(tz):%H:%M}",
           "kept_pct": _whole(kept), "stop_at_pct": None if off else 100 - state.pct, "used_pct": used, "over": over}
    obj["text"] = reserve_text(obj)
    return obj


def reserve_text(obj):
    """The one text of a `reserve` object: what `status`, `plan` and the settings pane print for it."""
    if obj.get("error"):
        return RESERVE_TEXT_ERROR
    if obj["pct"] == 0:
        return RESERVE_TEXT_OFF
    label = obj["label"]
    if obj["released"]:
        return f"{label} released {obj['release_text']}, kept again from the reset {obj['reset_text']}"
    if obj["kept_to_reset"]:
        text = f"{label} kept to the reset {obj['reset_text']}"
    else:
        text = f"{label} kept, opens {obj['release_text']}"
    if obj["over"]:
        text += f"; launches ask (week used {obj['used_pct']:g} %)"
    return text


def reserve_section(now, plan, seven_used, reset, tz):
    """(the state, the `reserve` object, the exception that made it the error form or None).

    One place for the failure rule of the reserve: a failed state or a raise in reserve_figures gives the
    error form and a state that keeps nothing, and the caller's other figures go on."""
    state = reserve_state(plan, reset, tz)
    try:
        return state, reserve_figures(now, state, seven_used, reset, tz), None
    except Exception as error:  # noqa: BLE001 - the stop is off then; the hard limits never read it
        state = failed_reserve_state(error)
        return state, reserve_error_form(state.error), error


def effective_mode(plan, now):
    """(mode in force, inform_until while it still holds): `limit` reads as `inform` until inform_until."""
    until = safe_iso(plan["inform_until"])
    active = until is not None and now < until
    return ("inform" if active and plan["mode"] == "limit" else plan["mode"]), (plan["inform_until"] if active else None)


def part_entry(now, profile, usage, plan, tz, week_info, state=None):
    """(the entry of the part that holds `now`, whether it is new), without writing anything.

    A stored entry holding `now` is returned as is. Otherwise the entry is derived from the usage history by
    the arithmetic that fixes a part at its start; `new` is True only for an entry the plan file should gain
    (the clock earlier than a stored entry gives an unplanned view-only entry). (None, False) when no part
    of the week holds `now`. `state` is the reserve state of the week (the plan's own when None)."""
    used_now, reset = week_info
    if state is None:
        state = reserve_state(plan, reset, tz)
    week_start = reset - WEEK
    mine = this_week_entries(plan, reset)
    inside = [e for e in mine if parse_iso(e["start"]) <= now < parse_iso(e["end"])]
    if inside:
        return inside[-1], False
    if any(parse_iso(e["start"]) > now for e in mine):
        # A clock earlier than a stored entry (a process that waited on the lock) fixes nothing and writes
        # nothing: the view takes the part of the unanchored week at `now`, unplanned, and never fails.
        part = next((q for q in week.parts(week_start, reset, tz, plan["work_hours"], plan)
                     if q.start <= now < q.end), None)
        if part is None:
            return None, False
        return ({"day": part.day.isoformat(), "kind": part.kind, "start": iso_ms(part.start), "end": iso_ms(part.end),
                 "week_reset": iso_ms(reset), "share_pct": None, "unplanned": True, "used_at_start": used_now,
                 "estimated": True, "left_at_start": week.left(used_now, state.kept(part.start)),
                 "weights": {"part": part.weight, "total": part.weight}, "prompted": False, "fixed_at": iso_ms(now),
                 "mark": part.mark, "full_hours": part.full_hours}, False)
    anchor = max([week_start] + [parse_iso(e["end"]) for e in mine if parse_iso(e["end"]) <= now])
    current, later = week.split_at(week.parts(anchor, reset, tz, plan["work_hours"], plan), now)
    if current is None:
        return None, False
    history = usage.get("history") or []
    start = current.start
    used_start, estimated = used_at(history, start, reset, None), False
    if used_start is None:
        estimated = True
        used_start = next((float(r["seven_day"].get("used_percentage") or 0) for r in history
                           if r["_at"] > start and _in_week(r, reset)), used_now)
    left_start = week.left(used_start, state.kept(start))
    chain = [current] + later
    entry = {"day": current.day.isoformat(), "kind": current.kind, "start": iso_ms(current.start),
             "end": iso_ms(current.end), "week_reset": iso_ms(reset),
             "share_pct": week.fix_share(left_start, chain), "unplanned": False, "used_at_start": used_start,
             "estimated": estimated, "left_at_start": left_start,
             "weights": {"part": current.weight, "total": sum(p.weight for p in chain)},
             "prompted": False, "fixed_at": iso_ms(now), "mark": current.mark, "full_hours": current.full_hours}
    entry["unplanned"] = entry["share_pct"] is None
    return entry, True


def fix_part(sd, key, now, profile, usage, plan, tz, week_info):
    """The stored entry of the part that holds `now`, fixing and appending it first when none does.

    The caller holds pace.lock and passes the loaded plan, which gains the entry. Returns None when no part
    of the week holds `now`."""
    entry, new = part_entry(now, profile, usage, plan, tz, week_info)
    # A run dated past the wall clock is a what-if: it computes the entry but leaves the file alone.
    if new and now <= limits_file.wall_clock() + FUTURE_SLACK:
        plan["fixed"].append(entry)
        save_plan(sd, key, plan, now, week_info[1], tz)
    return entry


def entry_part(entry):
    """The week.Part a stored entry fixed."""
    start, end = parse_iso(entry["start"]), parse_iso(entry["end"])
    return week.Part(dt.date.fromisoformat(entry["day"]), entry["kind"], start, end, float(entry["full_hours"]),
                     (end - start) / HOUR, float(entry["mark"]), float(entry["weights"]["part"]))


def anchored_parts(plan, now, reset, tz):
    """The parts of the week as the stored plan reads them: stored entries as fixed, then the parts the
    current hours give from the last stored end (or the week's start) on."""
    week_start = reset - WEEK
    mine = sorted(this_week_entries(plan, reset), key=lambda e: parse_iso(e["start"]))
    stored = [entry_part(e) for e in mine]
    anchor = max([week_start] + [p.end for p in stored if p.start <= now])
    tail = week.parts(anchor, reset, tz, plan["work_hours"], plan)
    head = [p for p in week.parts(week_start, anchor, tz, plan["work_hours"], plan)
            if not any(p.start < s.end and s.start < p.end for s in stored)]
    return sorted(head + stored + [p for p in tail if not any(p.start < s.end and s.start < p.end for s in stored)],
                  key=lambda p: p.start)


def _in_week(record, reset):
    return limits_file.same_week(safe_iso(record["seven_day"].get("resets_at")), reset)


def spread_later(now, plan, entry, used_now, reset, tz, state):
    """(left now, spent in the current part, the parts after it, what is left to spread, their shares): the
    figures of the plan view and of the week plan file, from one place so the two cannot drift."""
    left_now = week.left(used_now, state.kept(now))
    spent = max(0.0, used_now - float(entry["used_at_start"]))
    later = week.parts(parse_iso(entry["end"]), reset, tz, plan["work_hours"], plan)
    rest, shares = week.spread(left_now, entry["share_pct"], spent, later)
    return left_now, spent, later, rest, shares


def _r3(value):
    return None if value is None else round(value, 3)


def build_plan_view(access):
    """The plan view of section 3: what `paceline plan --json` prints."""
    ctx, profile, plan, tz, entry = access.ctx, access.profile, access.plan, access.tz, access.entry
    now, (used_now, reset) = ctx.now, access.week
    week_start = reset - WEEK
    history = access.usage.get("history") or []
    hours_text = plan["work_hours"]
    ratio = float(profile["five_hour_to_week_pct"])
    # The profile cannot tell a measured ratio from its default, so the default means "not calibrated".
    ratio_estimated = profile["five_hour_to_week_pct"] == PROFILE_DEFAULTS["five_hour_to_week_pct"]
    state, reserve, reserve_error = reserve_section(now, plan, used_now, reset, tz)
    if reserve_error is not None:
        limits_file.trace_failure("reserve.plan", getattr(ctx, "sd", None), error=reserve_error,
                                  reason=reserve["error"])
    left_now, spent, later, rest, shares = spread_later(now, plan, entry, used_now, reset, tz, state)
    even = week.even_pace(left_now, now, reset)
    scale = week.bar_scale(even, state.pct)
    e_start, e_end = parse_iso(entry["start"]), parse_iso(entry["end"])
    entry_day = dt.date.fromisoformat(entry["day"])
    full = anchored_parts(plan, now, reset, tz)
    mine = this_week_entries(plan, reset)
    current = entry_part(entry)
    share = entry["share_pct"]
    later_weight = sum(p.weight for p in later)
    raw_share = {}

    def make(part, state, share_value, found, spent_value=None, allowed_value=None, over=None):
        reach = week.reach(part.hours, ratio, float(profile["five_hour_stop_pct"]))
        obj = {"day": part.day.isoformat(), "kind": part.kind, "start": iso_ms(part.start), "end": iso_ms(part.end),
               "start_local": fmt_local(part.start, tz), "end_local": fmt_local(part.end, tz),
               "hours": round(part.hours, 3), "full_hours": round(part.full_hours, 3),
               "cut": part.hours < part.full_hours - 1e-9, "mark": part.mark, "weight": round(part.weight, 6),
               "state": state, "share_pct": _r3(share_value),
               "fixed": found is not None and (state != "current" or access.stored(found)),
               "unplanned": bool(found["unplanned"]) if found else False,
               "estimated": bool(found["estimated"]) if found else False,
               "spent_pct": _r3(spent_value), "allowed_pct": _r3(allowed_value), "over": over,
               "reach_pct": _r3(reach),
               # The reach warning rests on the window ratio: no calibration, no warning (spec R8).
               "unreachable_windows": (week.windows(part.hours)
                                       if not ratio_estimated and share_value is not None and share_value > reach
                                       else None)}
        raw_share[id(obj)] = share_value
        return obj

    items, before = [], []
    for part in full:
        if part.end <= e_start:
            found = next((e for e in mine if parse_iso(e["start"]) == part.start and e["kind"] == part.kind), None)
            ended_spent = None
            if found is not None:
                at_end = used_at(history, part.end, reset, None)
                ended_spent = None if at_end is None else max(0.0, at_end - float(found["used_at_start"]))
            # Ended parts of earlier dates only fill `week_days`; `days` starts at the current part's date.
            (items if part.day >= entry_day else before).append(
                make(part, "ended", found["share_pct"] if found else None, found, ended_spent))
    allowed = week.allowed(share, e_start, e_end, now) if share is not None else None
    over = week.is_over(spent, allowed) if allowed is not None else False
    current_obj = make(current, "current", share, entry, spent, allowed, over)
    items.append(current_obj)
    for part, later_share in zip(later, shares):
        items.append(make(part, "later", later_share, None))

    def joined(pieces):
        """One object for the pieces a split left of a part (the same day and kind, oldest first): it runs from the
        first piece's start to the last one's end, and spans their hours, weights, shares and spending; the rest
        (state, allowance, flags) is the last piece's. `segments` lists the pieces for a view that draws the cut."""
        if len(pieces) == 1:
            return pieces[0]
        first, last = pieces[0], pieces[-1]

        def total(key):
            values = [p[key] for p in pieces if p[key] is not None]
            return round(sum(values), 3) if values else None

        # Each split hands the rest of the part the whole share still unplanned, so the pieces' shares overlap
        # and their sum overstates it (seen live: 36 % twice read as 72 %). The share is the last piece that
        # has one; a current part keeps the last piece's share (None when it has none, as the gates read it) and
        # spending, which its share and pro rata measure.
        shares = [p["share_pct"] for p in pieces if p["share_pct"] is not None]
        if last["state"] == "current":
            share = last["share_pct"]
        else:
            share = shares[-1] if shares else None
        obj = dict(last, start=first["start"], start_local=first["start_local"],
                   hours=round(sum(p["hours"] for p in pieces), 3), weight=round(sum(p["weight"] for p in pieces), 6),
                   share_pct=share,
                   spent_pct=last["spent_pct"] if last["state"] == "current" else total("spent_pct"),
                   segments=[dict(p) for p in pieces])
        obj["cut"] = obj["hours"] < obj["full_hours"] - 1e-9
        raw_share[id(obj)] = raw_share[id(last)]
        return obj

    def day_list(objs, first):
        by_date = {}
        for obj in objs:
            by_date.setdefault(obj["day"], {}).setdefault(obj["kind"], []).append(obj)
        by_date = {d: {k: joined(v) for k, v in kinds.items()} for d, kinds in by_date.items()}
        out, day = [], first
        last = max(dt.date.fromisoformat(o["day"]) for o in objs)
        while day <= last:
            slot = by_date.get(day.isoformat(), {})
            total = sum(raw_share[id(o)] or 0.0 for o in slot.values() if o["state"] in ("current", "later"))
            out.append({"date": day.isoformat(), "weekday": week.WEEKDAYS[day.weekday()],
                        "work": slot.get("work"), "off": slot.get("off"), "share_pct": _r3(total),
                        "above_scale": total > scale})
            day += DAY
        return out

    days = day_list(items, entry_day)
    # The whole week, from the first date that owns a part: the calendar tab draws a column for each.
    week_days = day_list(before + items, min(dt.date.fromisoformat(o["day"]) for o in before + items))
    unplanned = bool(entry["unplanned"]) and later_weight <= 0
    mode, until = effective_mode(plan, now)
    return {"account": access.key or limits_file.UNKNOWN_ACCOUNT, "file": str(plan_path(ctx.sd, access.key)),
            "now": iso_ms(now), "timezone": profile["timezone"], "work_hours": hours_text, "mode": plan["mode"],
            "effective_mode": mode, "inform_until": until,
            "week": {"start": iso_ms(week_start), "reset": iso_ms(reset), "reset_local": fmt_local(reset, tz)},
            "used_pct": _r3(used_now), "reserve": reserve,
            "left_pct": _r3(left_now), "even_pct_per_day": _r3(even), "bar_scale_pct": _r3(scale),
            "rest_pct": _r3(rest), "unplanned_pct": _r3(left_now) if unplanned else None,
            "expires_at": iso_ms(reset) if unplanned else None,
            "ratio_estimated": ratio_estimated,
            "current": current_obj, "days": days, "week_days": week_days}


def mark_label(value):
    """A mark's name, or its multiple with a trailing ×; hooks/settings.ts markLabel prints the same."""
    return MARK_LABEL.get(float(value), f"{float(value):g}×")


def _clock(local_text):
    return local_text[11:16]


def plan_text(view, profile):
    """The week as the text strip of `paceline plan`."""
    scale = view["bar_scale_pct"] or 0.0
    reserve = view["reserve"]
    # The release day's row says when the kept reserve opens (`opens_clock` is null when it does not).
    suffix = ({reserve["release_day"]: f" · reserve opens {reserve['opens_clock']}"}
              if reserve.get("opens_clock") else {})
    lines = [f"week to {view['week']['reset_local']}, work hours {view['work_hours']}, mode {view['mode']}"]
    for day in view["days"]:
        date = dt.date.fromisoformat(day["date"])
        segments = []
        for kind in PLAN_KINDS:
            part = day[kind]
            if part is None:
                continue
            text = KIND_GLYPH[kind]
            if part["cut"]:
                length = f"{round(part['hours'], 1):g}"
                text += f" {_clock(part['start_local'])}-{_clock(part['end_local'])} ({length} h)"
            share = part["share_pct"]
            # An ended part that carried no weight reads as the spec's example, "☾ 0 · spent 3.0 %".
            text += " 0" if part["state"] == "ended" and float(part["mark"]) == 0 else f" {mark_label(part['mark'])}"
            if share is not None:
                text += f" {share:.1f} %"
            elif part["state"] != "ended" or part["fixed"]:
                text += " unplanned"
            if part["state"] == "current":
                if share is None:
                    text += f" ◀ now spent {part['spent_pct']:.1f}"
                else:
                    # A part joined from split halves whose current half has no share has no pro rata.
                    text += f" ◀ now spent {part['spent_pct']:.1f} of {share:.1f}"
                    if part["allowed_pct"] is not None:
                        text += f" (pro rata {part['allowed_pct']:.1f})"
                if part["estimated"]:
                    text += " estimated"
            elif part["state"] == "ended" and part["spent_pct"] is not None:
                text += f" · spent {part['spent_pct']:.1f} %"
            if part["unreachable_windows"]:
                text += f" not reachable in {part['unreachable_windows']} windows"
            segments.append(text)
        filled = min(BAR_WIDTH, round(day["share_pct"] / scale * BAR_WIDTH)) if scale > 0 else 0
        cells = ["█"] * filled + ["░"] * (BAR_WIDTH - filled)
        if scale > 0:
            cells[min(BAR_WIDTH - 1, round(view["even_pct_per_day"] / scale * BAR_WIDTH))] = "┃"
        bar = "▕" + "".join(cells) + "▏"
        lines.append(f"{week.WEEKDAYS[date.weekday()].capitalize()} {date.day}  " + "  ".join(segments)
                     + f"  {bar}" + (" ▲" if day["above_scale"] else "") + suffix.get(day["date"], ""))
    lines += ["", view["reserve"]["text"], f"even pace {view['even_pct_per_day']:.2f} %/day (\u2503 on the bars)",
              f"bar top {view['bar_scale_pct']:.1f} %/day"]
    if view["unplanned_pct"] is not None:
        lines.append(f"{view['unplanned_pct']:.1f} % unplanned, expires at {view['week']['reset_local']}")
    if view["effective_mode"] != view["mode"] and view["inform_until"]:
        lines.append(f"inform until {fmt_local(parse_iso(view['inform_until']), load_zone(view['timezone']))}")
    return "\n".join(lines)
