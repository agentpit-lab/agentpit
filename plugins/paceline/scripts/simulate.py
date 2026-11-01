"""paceline simulate: replay one weekly window through PaceLine's own budget code.

One window, hour by hour (168 steps). Each hour builds an exact reading, passes the hours so far as the
history and runs the real pace.compute, pace.recommend, week.kind_at and the pre-agent hook's
hard_limits and reserve_limits. The simulated operator spends the hour budget times the week shape's factor in working
hours, and with a night queue the off-hours allowance. The week plan is the account's working hours and
weekday marks (dated days are ignored); each part is fixed from the simulated history at its start. It is
a model: one reading per hour, a rolling five-hour sum instead of Claude Code's fixed windows, no
holds, the night queue spends its whole allowance.

Imported by pace.py only inside the command's handler. Standard library only; no clock beyond choosing
the reset, no randomness, no host probe; nothing is written except the cache, and only with --cached.
"""
import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

# Bump MODEL_VERSION whenever the fields of a result row change (simulate_week's return value and the table
# read them); a cached file of another version is no cache.
MODEL_VERSION = 3
ROW_TEXT = ("week",)
ROW_NUMBERS = ("unused_pct", "used_pct", "night_pct", "asks_h", "blocked_h", "peak_five_hour_pct")
STEPS = 168
# The pins .ci/pace_golden.py pins the budget simulator to (a test requires them equal).
PINS = {"commit_mb": 65536, "five_hour_reset_hours": 3, "data_age_min": 0, "data_source": "exact"}
WEEKS = ("on-track", "heavy-start", "light-start", "away-thu-fri")
WEEK_CHOICES = WEEKS + ("all",)
QUEUE_CHOICES = ("no", "yes", "both")
CACHE_FILE = "pace-simulate.json"
CACHE_ENTRIES = 8
HOOK = Path(__file__).resolve().parent.parent / "hooks" / "pace_pre_agent.py"
MODEL_NOTE = "a model: one reading per hour, working and off-hours from the week plan, no holds"


def _pace():
    return importlib.import_module("pace")


def load_hook():
    """The pre-agent hook's module, loaded by path (its only import-time effect is adding scripts/ to sys.path)."""
    spec = importlib.util.spec_from_file_location("pace_pre_agent", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def week_factor(shape, step, local_weekday):
    """The share of the hour budget the operator spends in a day hour: step counts from the window's start."""
    if shape == "heavy-start":
        return 2.0 if step < 72 else 1.0
    if shape == "light-start":
        return 0.5 if step < 72 else 1.0
    if shape == "away-thu-fri":
        return 0.0 if local_weekday in (3, 4) else 1.0
    return 1.0


def simulation_plan(pace, ctx, defaults):
    """The plan the simulation runs on: the account's working hours, weekday marks and reserve, or the
    defaults (the default reserve too). Dated days and stored parts are left out; a plan file that
    cannot be read gives the defaults."""
    plan = pace.new_plan()
    if defaults:
        return plan
    try:
        stored = pace.load_plan(ctx.sd, pace.own_account(ctx.session, ctx.sd)[0])
    except pace.PaceError:
        return plan
    plan["work_hours"] = stored["work_hours"]
    plan["template"] = json.loads(json.dumps(stored["template"]))
    plan["reserve"] = json.loads(json.dumps(stored["reserve"]))
    return plan


def simulate_week(pace, hook, profile, tz, reset, shape, night_queue, plan):
    """One window ending at `reset`; returns the figures of one table row."""
    start = reset - pace.WEEK
    k = profile["five_hour_to_week_pct"]
    used, history, spends = 0.0, [], []
    row = {"night": 0.0, "asks_h": 0, "blocked_h": 0, "peak_five": 0.0}
    allowance, hours_left, night_span, previous = 0.0, 0, 0, None
    reset_text = pace.iso_ms(reset)
    for step in range(STEPS):
        now = start + dt.timedelta(hours=step)
        local = now.astimezone(tz)
        five = min(100.0, sum(spends[-5:]) / k * 100.0)
        record = {"seven_day": {"used_percentage": round(used, 4), "resets_at": reset_text},
                  "five_hour": {"used_percentage": round(five, 4),
                                "resets_at": pace.iso_ms(now + dt.timedelta(hours=PINS["five_hour_reset_hours"]))},
                  "scoped": [], "_at": now}
        history.append({"seven_day": dict(record["seven_day"]), "_at": now, "scoped": []})
        usage = {"record": record, "source": PINS["data_source"], "error": None, "history": list(history)}
        calc = pace.compute(now, profile, usage, None, PINS["commit_mb"], 0.0, plan=plan, entry=None)
        if calc.get("plan_error"):
            raise pace.PaceError(f"the simulation could not build its plan: {calc['plan_error']}")
        rec = pace.recommend(calc)
        status = dict(calc, recommendation=rec, active_holds=0)
        kind = pace.week.kind_at(local, plan["work_hours"])
        row["peak_five"] = max(row["peak_five"], five)
        if hook.hard_limits(status) or hook.reserve_limits(status):
            row["asks_h"] += 1
        spend = 0.0
        if kind == "work":
            row["blocked_h"] += rec["blocked"]
            if not rec["blocked"]:
                spend = min(calc["budget_per_hour_pct"] * week_factor(shape, step, local.weekday()), k / 5.0)
        elif night_queue:
            if previous != "off":
                allowance = calc["night"]["allowance_pct"]
                night_span = round(calc["night"]["hours"])
                hours_left = night_span
            if hours_left > 0 and not rec["blocked"]:
                spend = allowance / night_span
                hours_left -= 1
                row["night"] += spend
        previous = kind
        spend = max(0.0, min(spend, 100.0 - used))
        used += spend
        spends.append(spend)
    return {"unused_pct": round(100.0 - used, 1), "used_pct": round(used, 1),
            "night_pct": round(row["night"], 1),
            "asks_h": row["asks_h"],
            "blocked_h": int(row["blocked_h"]), "peak_five_hour_pct": round(row["peak_five"], 1)}


def next_monday(pace, now, tz):
    local = now.astimezone(tz).date()
    return pace.local_dt(local + dt.timedelta(days=7 - local.weekday()), 0, tz)


def pick_reset(pace, ctx, profile, tz, argument):
    """(reset, source): the argument, else the account's next weekly reset, else the next Monday 00:00 local."""
    if argument:
        try:
            reset = pace.parse_iso(argument)
        except ValueError:
            reset = None
        if reset is None:
            raise pace.PaceError(f"--reset {argument}: not an ISO 8601 instant")
        return reset, "argument"
    try:
        usage = ctx.get_usage(profile, allow_merge=False)
    except pace.PaceError:
        usage = {}
    record = usage.get("record")
    bucket = (record or {}).get("seven_day")
    if bucket:
        _used, reset, _rolled = pace.roll_bucket(bucket, ctx.now, pace.WEEK)
        if reset is not None:
            return reset, "account"
    return next_monday(pace, ctx.now, tz), "default"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(pace, profile, reset, source, weeks, queues, plan):
    internal = pace.PROFILE_INTERNAL_KEYS
    payload = {"model": MODEL_VERSION, "plugin": pace.plugin_version(), "python": list(sys.version_info[:2]),
               "profile": {k: v for k, v in profile.items() if k not in internal},
               "reset": pace.iso_ms(reset), "reset_source": source, "weeks": list(weeks), "queues": list(queues),
               "work_hours": plan["work_hours"], "template": plan["template"], "reserve": {k: plan["reserve"].get(k) for k in ("pct", "release")}}
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def read_cache(sd):
    """The cache's entries as [{"key", "results"}]; an unreadable or foreign file is no cache."""
    try:
        raw = json.loads((sd / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    entries = raw.get("entries") if isinstance(raw, dict) and raw.get("version") == MODEL_VERSION else None
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("key"), str)
            and isinstance(e.get("results"), list) and all(valid_row(r) for r in e["results"])]


def valid_row(row):
    """A result row with every field the table reads; anything else makes the entry a miss."""
    if not isinstance(row, dict) or not isinstance(row.get("night_queue"), bool):
        return False
    if not all(isinstance(row.get(k), str) for k in ROW_TEXT):
        return False
    return all(isinstance(row.get(k), (int, float)) and not isinstance(row.get(k), bool) for k in ROW_NUMBERS)


def cached_results(sd, key):
    for entry in read_cache(sd):
        if entry["key"] == key:
            return entry["results"]
    return None


def store_cache(pace, sd, key, results):
    entries = [e for e in read_cache(sd) if e["key"] != key]
    entries.append({"key": key, "results": results})
    pace.write_json(sd / CACHE_FILE, {"version": MODEL_VERSION, "entries": entries[-CACHE_ENTRIES:]}, compact=True)


def table(results):
    lines = [f"{'week':<13} {'queue':<5} {'unused':>8} {'night':>7} {'asks h':>6} "
             f"{'peak 5h':>8}"]
    for r in results:
        lines.append(f"{r['week']:<13} {'yes' if r['night_queue'] else 'no':<5} "
                     f"{r['unused_pct']:>6.1f} % {r['night_pct']:>5.1f} % "
                     f"{r['asks_h']:>6} {r['peak_five_hour_pct']:>6.1f} %")
    return lines


def read_own_profile(pace, sd):
    """The stored profile with defaults added; a missing file is the defaults, an unreadable one a PaceError."""
    raw = pace.load_stored_profile(sd / "pace-profile.json")[0]
    # The zone the profile counts in now: the machine's in machine mode; nothing is written back.
    raw["timezone"] = pace.effective_zone(raw, pace.detect_zone)[1]
    return raw


def main(ctx, pace=None):
    pace = pace or _pace()
    args = ctx.args
    sd = ctx.sd
    week_args = getattr(args, "week", None) or ["all"]
    queue_arg = getattr(args, "night_queue", None) or "both"
    weeks = [w for w in WEEKS if "all" in week_args or w in week_args]
    queues = {"no": [False], "yes": [True], "both": [False, True]}[queue_arg]

    if getattr(args, "defaults", False):
        profile = json.loads(json.dumps(pace.PROFILE_DEFAULTS))
        profile["timezone"] = "UTC"
        which = "defaults"
    else:
        profile = read_own_profile(pace, sd)
        which = "own"
    warnings = []
    tz = pace.load_zone(profile["timezone"])
    reset, source = pick_reset(pace, ctx, profile, tz, getattr(args, "reset", None))
    plan = simulation_plan(pace, ctx, which == "defaults")
    key = cache_key(pace, profile, reset, source, weeks, queues, plan)

    results, hit = None, False
    use_cache = getattr(args, "cached", False)
    if use_cache:
        results = cached_results(sd, key)
        hit = results is not None
    if results is None:
        hook = load_hook()
        results = []
        for shape in weeks:
            for queue in queues:
                row = {"week": shape, "night_queue": queue}
                row.update(simulate_week(pace, hook, profile, tz, reset, shape, queue, plan))
                results.append(row)
        if use_cache:
            try:
                store_cache(pace, sd, key, results)
            except OSError as error:
                warnings = warnings + [f"{CACHE_FILE}: not written ({error})"]

    payload = {
        "model": {"version": MODEL_VERSION, "steps": STEPS,
                  "pins": {"commit_mb": PINS["commit_mb"], "five_hour_reset_hours": PINS["five_hour_reset_hours"]}},
        "inputs": {"reset": pace.iso_ms(reset), "reset_local": pace.fmt_local(reset, tz), "reset_source": source,
                   "profile": which, "timezone": profile["timezone"], "work_hours": plan["work_hours"]},
        "results": results,
        "warnings": warnings,
        "cache": {"key": key, "hit": hit},
    }
    header = (f"Reset {reset.astimezone(tz).strftime('%a')} {pace.fmt_local(reset, tz)} ({source}) | zone "
              f"{profile['timezone']} | work hours {plan['work_hours']} | "
              f"{'the defaults' if which == 'defaults' else 'your profile'} | {MODEL_NOTE}")
    lines = [header, ""] + table(results)
    if warnings:
        lines += [""] + [f"warning: {w}" for w in warnings]
    pace.emit(args, "\n".join(lines), payload)
    return pace.EXIT_OK
