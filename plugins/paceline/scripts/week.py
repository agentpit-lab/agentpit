"""The week as a calendar: parts of a day, their weights, the spread of what is left, the even pace.

Pure functions over plain data: no file reads, no clock, datetimes come in as arguments. A datetime
that goes in or comes out as an instant is timezone-aware UTC; a local time is the same instant in the
profile's zone. Standard library only; nothing imports pace.py from here. The plan file's shape and the
"which kind of part holds now" rule live here too, so the merge (five_burn) and status (pace) read a
stored plan one way; limits_file supplies only its ISO parser and the same-week constant.
"""
import dataclasses
import datetime as dt
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limits_file  # noqa: E402

UTC = dt.timezone.utc
DAY = dt.timedelta(days=1)
HOUR = dt.timedelta(hours=1)
WEEK = 7 * DAY

DEFAULT_WORK_HOURS = "09:00-18:00"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MARK_NAMES = {"off": 0.0, "day-off": 0.0, "light": 0.5, "normal": 1.0, "heavy": 1.5}
MARK_STEP = 0.25
MARK_MAX = 2.0
DEFAULT_MARKS = {"work": 1.0, "off": 0.0}
# A part is over its share by more than this many percentage points before the gate acts (readings come
# in whole points).
TOLERANCE_PCT = 1.0
MINUTES_STEP = 15
MIN_SPAN = 60
# The floor of a day's bar scale, so a full reserve never gives a zero scale.
MIN_SCALE = 0.01


def parse_work_hours(text):
    """"HH:MM-HH:MM" -> (start_minutes, end_minutes) of the local day; ValueError names the broken rule."""
    match = (re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", text)
             if isinstance(text, str) else None)
    if not match:
        raise ValueError(f"work_hours must look like HH:MM-HH:MM, e.g. {DEFAULT_WORK_HOURS}; got {text!r}")
    h0, m0, h1, m1 = (int(g) for g in match.groups())
    if h0 > 23 or h1 > 23 or m0 > 59 or m1 > 59:
        raise ValueError(f"work_hours times must be 00:00 to 23:59; got {text!r}")
    start, end = h0 * 60 + m0, h1 * 60 + m1
    if start % MINUTES_STEP or end % MINUTES_STEP:
        raise ValueError(f"work_hours minutes must be a multiple of {MINUTES_STEP}; got {text!r}")
    if start >= end:
        raise ValueError("work_hours must start before it ends within one calendar day; hours that cross "
                         f"midnight or an empty range are refused; got {text!r}")
    if end - start < MIN_SPAN:
        raise ValueError(f"work_hours must cover at least 1 hour of working time; got {text!r}")
    if 1440 - (end - start) < MIN_SPAN:
        raise ValueError(f"work_hours must leave at least 1 hour of off-hours; got {text!r}")
    return start, end


def format_work_hours(work_hours):
    start, end = _minutes(work_hours)
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def _minutes(work_hours):
    return parse_work_hours(work_hours) if isinstance(work_hours, str) else tuple(work_hours)


def parse_mark(value):
    """A mark name (off, day-off, light, normal, heavy) or a number in steps of 0.25 within [0, 2]."""
    if isinstance(value, bool):
        raise ValueError("a mark is a name (off, light, normal, heavy) or a number from 0 to 2 in steps of 0.25")
    if isinstance(value, str):
        key = value.strip().lower()
        if key in MARK_NAMES:
            return MARK_NAMES[key]
        try:
            number = float(key)
        except ValueError:
            raise ValueError(f"mark {value!r} is not a name (off, light, normal, heavy) "
                             "or a number from 0 to 2 in steps of 0.25") from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise ValueError(f"mark {value!r} is not a name or a number")
    if not math.isfinite(number) or not 0 <= number <= MARK_MAX:
        raise ValueError(f"a mark must be within 0 to {MARK_MAX:g}; got {value!r}")
    if (number / MARK_STEP) % 1:
        raise ValueError(f"a mark must be a multiple of {MARK_STEP}; got {value!r}")
    return number


def marks_for(day, plan):
    """The marks {"work", "off"} of a local date: its dated entry, else the weekday template, else 1 and 0.

    A key missing from the dated entry, or holding a value parse_mark refuses, falls to the template, then
    to the default, key by key.
    """
    plan = plan if isinstance(plan, dict) else {}
    days = plan.get("days") if isinstance(plan.get("days"), dict) else {}
    template = plan.get("template") if isinstance(plan.get("template"), dict) else {}
    dated = days.get(day.isoformat())
    weekday = template.get(WEEKDAYS[day.weekday()])
    out = {}
    for kind, default in DEFAULT_MARKS.items():
        for source in (dated, weekday):
            if not isinstance(source, dict) or source.get(kind) is None:
                continue
            try:
                out[kind] = parse_mark(source[kind])
            except ValueError:
                continue
            break
        else:
            out[kind] = default
    return out


def local_instant(day, minutes, tz):
    """The UTC instant of a local wall time. A time inside a DST gap is the first instant after the gap;
    a time in a repeated hour is its first occurrence (fold=0)."""
    naive = dt.datetime.combine(day, dt.time(0)) + dt.timedelta(minutes=minutes)
    late = naive.replace(tzinfo=tz, fold=0).astimezone(UTC)
    if late.astimezone(tz).replace(tzinfo=None) == naive:
        return late
    # In a gap: fold=0 reads the offset from before the jump (the instant is too late), fold=1 the one
    # after it (too early); the jump lies between them, so search for the first second that reads the
    # new offset.
    early = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)
    after = naive.replace(tzinfo=tz, fold=1).utcoffset()
    lo, hi = 0, int((late - early).total_seconds())
    while lo < hi:
        mid = (lo + hi) // 2
        if (early + dt.timedelta(seconds=mid)).astimezone(tz).utcoffset() == after:
            hi = mid
        else:
            lo = mid + 1
    return early + dt.timedelta(seconds=lo)


def kind_at(local, work_hours):
    """"work" inside the working hours of the local time's day, else "off".

    Compared as instants against the boundaries parts() uses, so a repeated fall-back hour and a gap agree
    with it; the neighbouring dates are tried because a boundary moved by a gap can leave its date.
    """
    start, end = _minutes(work_hours)
    tz, now = local.tzinfo, local.astimezone(UTC)
    day = local.date()
    for d in (day - DAY, day, day + DAY):
        if local_instant(d, start, tz) <= now < local_instant(d, end, tz):
            return "work"
    return "off"


# ---------------------------------------------------------------- the stored plan

PLAN_KINDS = ("work", "off")
PLAN_ENTRY_KEYS = ("day", "kind", "start", "end", "week_reset", "share_pct", "unplanned", "used_at_start",
                   "estimated", "left_at_start", "weights", "prompted", "fixed_at", "mark", "full_hours")
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def plan_file_name(key):
    """plan-<key>.json; a key that is not 16 lower-case hex digits names plan-unknown.json."""
    valid = isinstance(key, str) and limits_file.ACCOUNT_KEY.fullmatch(key)
    return f"plan-{key if valid else limits_file.UNKNOWN_ACCOUNT}.json"


def plan_work_hours(raw):
    """The working hours of a stored plan object, in the canonical form; the default when absent or bad."""
    stored = raw.get("work_hours") if isinstance(raw, dict) else None
    if isinstance(stored, str):
        try:
            return format_work_hours(stored)
        except ValueError:
            pass
    return DEFAULT_WORK_HOURS


def plan_entry_ok(entry):
    """True when a stored entry has every key with a usable value; an entry that fails is dropped on read."""
    safe_iso = limits_file.safe_iso
    if not isinstance(entry, dict) or any(k not in entry for k in PLAN_ENTRY_KEYS):
        return False
    if entry["kind"] not in PLAN_KINDS or any(safe_iso(entry[k]) is None for k in ("start", "end", "week_reset")):
        return False
    if not (isinstance(entry["day"], str) and ISO_DAY.fullmatch(entry["day"])):
        return False
    if not is_number(entry["used_at_start"]) or not (entry["share_pct"] is None or is_number(entry["share_pct"])):
        return False
    if not is_number(entry["mark"]) or not is_number(entry["full_hours"]):
        return False
    try:
        dt.date.fromisoformat(entry["day"])
    except ValueError:
        return False
    if safe_iso(entry["start"]) >= safe_iso(entry["end"]):
        return False
    if any(not isinstance(entry[k], bool) for k in ("prompted", "estimated", "unplanned")):
        return False
    weights = entry["weights"]
    return (isinstance(weights, dict) and is_number(weights.get("part")) and is_number(weights.get("total"))
            and (entry["left_at_start"] is None or is_number(entry["left_at_start"])))


def this_week_entries(entries, reset):
    """The entries (already valid) whose week_reset is the same week as `reset`."""
    return [e for e in entries if limits_file.same_week(limits_file.safe_iso(e["week_reset"]), reset)]


def roll_week(reset, now):
    """A weekly reset that has passed, rolled forward by whole weeks to the next one after `now`."""
    while reset <= now:
        reset += WEEK
    return reset


def kind_now(raw_plan, now, tz, reset=None):
    """The kind ("work" or "off") of the part that holds `now`, read from a stored plan object (or None).

    A work_hours change applies from the end of the running part, so the valid stored entry of this week
    that holds now decides (the last one when several do), else the plan's working hours. Without `reset`
    the week filter is skipped. The one rule the five-hour threshold follows, in the merge and in status."""
    raw = raw_plan if isinstance(raw_plan, dict) else {}
    fixed = [e for e in raw.get("fixed") or [] if plan_entry_ok(e)]
    if reset is not None:
        fixed = this_week_entries(fixed, reset)
    inside = [e for e in sorted(fixed, key=lambda e: limits_file.safe_iso(e["start"]))
              if limits_file.safe_iso(e["start"]) <= now < limits_file.safe_iso(e["end"])]
    if inside:
        return inside[-1]["kind"]
    return kind_at(now.astimezone(tz), plan_work_hours(raw))


@dataclasses.dataclass(frozen=True)
class Part:
    day: dt.date          # the local date whose working start opens it
    kind: str             # "work" | "off"
    start: dt.datetime    # UTC, clipped to the week
    end: dt.datetime
    full_hours: float     # the unclipped real length (a DST night is 14 or 16 h with 09:00-18:00)
    hours: float          # the length inside the week
    mark: float
    weight: float         # mark * hours / full_hours


def parts(window_start, reset, tz, work_hours, plan=None):
    """The parts overlapping [window_start, reset), oldest first.

    `plan` is the plan file's dict (`days`, `template`; see marks_for) or None for the default marks;
    `window_start` and `reset` are UTC instants, `tz` the profile's zone, `work_hours` the string or the
    (start, end) minutes. A day runs from one working start to the next; its off-hours follow its
    working end, so a week that starts at night begins with a cut off-hours part of the earlier date.
    """
    w0, w1 = _minutes(work_hours)
    day = window_start.astimezone(tz).date() - DAY
    last = reset.astimezone(tz).date() + DAY
    out = []
    while day <= last:
        s = local_instant(day, w0, tz)
        e = local_instant(day, w1, tz)
        nxt = local_instant(day + DAY, w0, tz)
        marks = marks_for(day, plan)
        for kind, a, b in (("work", s, e), ("off", e, nxt)):
            lo, hi = max(a, window_start), min(b, reset)
            if hi <= lo:
                continue
            full = (b - a) / HOUR
            hours = (hi - lo) / HOUR
            out.append(Part(day, kind, lo, hi, full, hours, marks[kind], marks[kind] * hours / full))
        day += DAY
    return out


def release_at(reset, tz, work_hours, weekday):
    """The UTC instant the weekly reserve is released: the working start of its release day, in the week
    that ends at `reset`.

    A candidate is the local working start of the date of a working-hours part of the week, kept when
    reset - 7 d <= start < reset. With `weekday` None the latest candidate whose working hours end at or
    before the reset (the last day whole inside the week); with a weekday from WEEKDAYS the latest candidate
    on that weekday. With no candidate (a DST change can leave the reset's own weekday without one) the
    reset itself, so the reserve is kept to the end of the week. A weekday outside WEEKDAYS is a ValueError.
    """
    if weekday is not None and weekday not in WEEKDAYS:
        raise ValueError(f"release weekday must be one of {', '.join(WEEKDAYS)}; got {weekday!r}")
    w0, w1 = _minutes(work_hours)
    week_start = reset - WEEK
    best = None
    for part in parts(week_start, reset, tz, work_hours):
        if part.kind != "work":
            continue
        start = local_instant(part.day, w0, tz)
        if not week_start <= start < reset:
            continue
        if weekday is None:
            if local_instant(part.day, w1, tz) > reset:
                continue
        elif WEEKDAYS[part.day.weekday()] != weekday:
            continue
        if best is None or start > best:
            best = start
    return reset if best is None else best


def kept(t, pct, release_at):
    """The reserve held at `t`, in percent: `pct` before the release instant, 0 from it; 0 without one."""
    if release_at is None:
        return 0.0
    return float(pct) if t < release_at else 0.0


def split_at(parts, t):
    """(the part holding t, the parts after it); with no part holding t, (None, the parts that start after t)."""
    for i, part in enumerate(parts):
        if part.start <= t < part.end:
            return part, list(parts[i + 1:])
    return None, [part for part in parts if part.start > t]


def left(used, reserve):
    """What the week can still spend, in percent: max(0, 100 - used - reserve)."""
    return max(0.0, 100.0 - used - reserve)


def fix_share(left_at_start, from_current):
    """The share of the first part of `from_current` (it and every later part of the week), fixed at its
    start: left * w_p / sum(w_q). None (unplanned) when the weights sum to 0."""
    total = sum(p.weight for p in from_current)
    if not from_current or total <= 0:
        return None
    return left_at_start * from_current[0].weight / total


def spread(left_now, fixed_share, spent_in_part, later_parts):
    """(rest, shares of the later parts) while the current part holds `fixed_share`.

    rest = max(0, left - max(0, F - spent)) goes to the later parts by weight; every later share is 0
    when their weights sum to 0. An unplanned current part (fixed_share None) holds back nothing.
    """
    held = max(0.0, fixed_share - spent_in_part) if fixed_share is not None else 0.0
    rest = max(0.0, left_now - held)
    total = sum(p.weight for p in later_parts)
    if total <= 0:
        return rest, [0.0 for _ in later_parts]
    return rest, [rest * p.weight / total for p in later_parts]


def even_pace(left_now, now, reset):
    """Percent per day: what is left over the days to the reset, at least one day."""
    return left_now / max(1.0, (reset - now) / DAY)


def bar_scale(even, reserve_pct):
    """The top of a day's bar: twice the even pace, kept between the nominal even pace and twice that."""
    nominal = max((100.0 - reserve_pct) / 7.0, MIN_SCALE)
    return min(max(2.0 * even, nominal), 2.0 * nominal)


def allowed(share, start, end, t):
    """The part of a fixed share that may be spent by `t`, pro rata by time."""
    return share * min(1.0, max(0.0, (t - start) / (end - start)))


def is_over(spent, allowed_now):
    """Over the share pro rata by more than the 1-point tolerance."""
    return spent > allowed_now + TOLERANCE_PCT


def windows(hours):
    """How many five-hour windows fit into `hours`, the last one counted whole."""
    return math.ceil(hours / 5)


def reach(hours, five_hour_to_week_pct, threshold_pct):
    """What the five-hour windows fitting into `hours` can deliver to the week, in percent."""
    return windows(hours) * five_hour_to_week_pct * threshold_pct / 100
