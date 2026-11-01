"""Shared helpers of PaceLine's scripts: the errors, the profile defaults, time and file helpers, and the
usage arithmetic, so that plan.py and pace.py can both use them and neither imports the other.

Stdlib only. pace.py re-exports every name here, so code and tests that reach them through pace keep working.
"""
# Patches must target the defining module: pace.py re-exports the names defined here, so a patch of
# `pace.X` does not reach code in this module that reads X (tests/test_patch_targets.py checks it).
import datetime as dt
import json
import math
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import five_burn  # noqa: E402
import limits_file  # noqa: E402
from limits_file import parse_iso  # noqa: E402


UTC = dt.timezone.utc
HOUR = dt.timedelta(hours=1)
DAY = dt.timedelta(days=1)
WEEK = dt.timedelta(days=7)


PROFILE_DEFAULTS = {
    "version": 1,
    "timezone": "UTC",
    "timezone_mode": "machine",
    "timezone_confirmed": False,
    "five_hour_stop_pct": 95.0,
    "five_hour_to_week_pct": 10.0,
    "subagent_estimate_pct": 1.0,
    "memory_guard": False,
    "min_free_commit_mb": 2048,
    "commit_reserve_mb": 2048,
    "heavy_job_mb": 6000,
    "max_heavy_runs": 3,
    "max_agents": 6,
}


class PaceError(Exception):
    pass


class NoWeekError(PaceError):
    """A command that needs the week found no weekly reading (the account is unknown, or has no data yet)."""


def load_zone(key, force_builtin=False):
    try:
        return five_burn.load_zone(key, force_builtin)
    except five_burn.ZoneUnavailable as error:
        raise PaceError(str(error)) from None


def iso_ms(d):
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def read_json(path, default):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as error:
        raise PaceError(f"{path.name} is not valid JSON ({error}); left untouched, fix it by hand")


def write_json(path, data, compact=False):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    body = json.dumps(data, separators=(",", ":")) if compact else json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(body, encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def scoped_match(scoped, model):
    if not model:
        return []
    m = model.lower()
    return [s for s in scoped or [] if isinstance(s.get("name"), str) and s["name"].lower() in m]


def effective_used(record, model):
    values = [float(record["seven_day"]["used_percentage"])]
    values += [float(s["used_percentage"]) for s in scoped_match(record.get("scoped"), model)]
    return max(values)


def used_at(history, t, reset, model):
    """Effective weekly usage at instant t inside the window ending at reset; None when history has no point."""
    if t <= reset - WEEK:
        return 0.0
    best = None
    for record in history:
        if record["_at"] > t:
            break
        r = parse_iso(record["seven_day"].get("resets_at"))
        if limits_file.same_week(r, reset):
            best = record
    return effective_used(best, model) if best else None


def roll_bucket(bucket, now, period):
    """(used, reset, rolled): a past reset is rolled forward and the usage of a window that has reset is 0."""
    if not bucket:
        return 0.0, None, False
    used = float(bucket.get("used_percentage") or 0)
    reset = parse_iso(bucket.get("resets_at"))
    if reset is None or reset > now:
        return used, reset, False
    if period is None:
        return 0.0, None, True
    while reset <= now:
        reset += period
    return 0.0, reset, True


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
