"""paceline simulate: replay one weekly window through PaceLine's own budget code.

One window, hour by hour (168 steps). Each hour builds an exact reading, passes the hours so far as the
history and runs the real pace.compute, pace.recommend, pace.classify and the pre-agent hook's
hard_limits. The simulated operator spends the hour budget times the week shape's factor in day hours,
and with a night queue the night's allowance. It is a model: one reading per hour, a rolling five-hour
sum instead of Claude Code's fixed windows, no plans, no reservations, the night queue spends its whole
allowance.

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

# Bump MODEL_VERSION whenever the fields of a result row change (simulate_week's return value, the table
# and the preview read them); a cached file of another version is no cache.
MODEL_VERSION = 1
ROW_TEXT = ("style", "week")
ROW_NUMBERS = ("unused_pct", "used_pct", "weekend_day_pct", "night_pct", "asks_h", "throttled_h", "paused_h",
               "blocked_h", "peak_five_hour_pct")
STEPS = 168
# The pins .ci/pace_golden.py pins the budget simulator to (a test requires them equal).
PINS = {"commit_mb": 65536, "five_hour_reset_hours": 3, "data_age_min": 0, "data_source": "exact"}
STYLE_CHOICES = ("careful", "balanced", "full", "current", "all")
WEEKS = ("on-track", "heavy-start", "light-start", "away-thu-fri")
WEEK_CHOICES = WEEKS + ("all",)
QUEUE_CHOICES = ("no", "yes", "both")
CACHE_FILE = "pace-simulate.json"
CACHE_ENTRIES = 8
HOOK = Path(__file__).resolve().parent.parent / "hooks" / "pace_pre_agent.py"
MODEL_NOTE = "a model: one reading per hour, no plans or reservations"


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


def simulate_week(pace, hook, profile, tz, reset, shape, night_queue):
    """One window ending at `reset`; returns the figures of one table row."""
    start = reset - pace.WEEK
    k = profile["five_hour_to_week_pct"]
    n0, n1 = profile["night_hours"]
    used, history, spends = 0.0, [], []
    row = {"weekend_day": 0.0, "night": 0.0, "asks_h": 0, "throttled_h": 0, "paused_h": 0, "blocked_h": 0,
           "peak_five": 0.0}
    allowance, hours_left = 0.0, 0
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
        calc = pace.compute(now, profile, usage, None, PINS["commit_mb"], 0.0, [])
        rec = pace.recommend(calc, profile)
        status = dict(calc, recommendation=rec, active_reservations=0)
        kind = pace.classify(local, profile)
        row["peak_five"] = max(row["peak_five"], five)
        if hook.hard_limits(status):
            row["asks_h"] += 1
        spend = 0.0
        if kind in ("work", "weekend"):
            row["throttled_h"] += rec["throttle"]
            row["paused_h"] += rec["background_paused"]
            row["blocked_h"] += rec["blocked"]
            if not rec["blocked"]:
                spend = min(calc["budget_per_hour_pct"] * week_factor(shape, step, local.weekday()), k / 5.0)
            if kind == "weekend":
                row["weekend_day"] += spend
        elif kind == "night" and night_queue:
            if local.hour == n0:
                allowance = calc["night"]["allowance_pct"]
                hours_left = n1 - n0
            if hours_left > 0 and not rec["blocked"]:
                spend = allowance / (n1 - n0)
                hours_left -= 1
                row["night"] += spend
        spend = max(0.0, min(spend, 100.0 - used))
        used += spend
        spends.append(spend)
    return {"unused_pct": round(100.0 - used, 1), "used_pct": round(used, 1),
            "weekend_day_pct": round(row["weekend_day"], 1), "night_pct": round(row["night"], 1),
            "asks_h": row["asks_h"], "throttled_h": int(row["throttled_h"]), "paused_h": int(row["paused_h"]),
            "blocked_h": int(row["blocked_h"]), "peak_five_hour_pct": round(row["peak_five"], 1)}


def own_style_values(pace, profile):
    return {key: (json.loads(json.dumps(profile[key])), "profile") for key in pace.SPENDING_STYLE_KEYS}


def select_styles(pace, requested, profile, overrides):
    """[(name, {key: (value, source)})] in table order; 'all' is the three, plus current when the profile is custom."""
    requested = list(requested or ["all"])
    names = []
    for name in ("careful", "balanced", "full"):
        if name in requested or "all" in requested:
            names.append(name)
    if "current" in requested or ("all" in requested and pace.style_of(profile, overrides) == "custom"):
        names.append("current")
    return [(name, own_style_values(pace, profile) if name == "current" else pace.style_values(name, overrides))
            for name in names]


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


def cache_key(pace, profile, styles, reset, source, weeks, queues):
    internal = pace.PROFILE_INTERNAL_KEYS
    payload = {"model": MODEL_VERSION, "plugin": pace.plugin_version(), "python": list(sys.version_info[:2]),
               "profile": {k: v for k, v in profile.items() if k not in internal},
               "styles": {name: {key: value for key, (value, _s) in values.items()} for name, values in styles},
               "reset": pace.iso_ms(reset), "reset_source": source, "weeks": list(weeks), "queues": list(queues)}
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
    """A result row with every field the table and the preview read; anything else makes the entry a miss."""
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


def preview_from(results):
    """{style: {unused_pct, unused_with_queue_pct, asks_h}} from the on-track rows, or None without them."""
    out = {}
    for row in results:
        if row["week"] != "on-track" or row["night_queue"]:
            continue
        queued = next((r for r in results if r["style"] == row["style"] and r["week"] == "on-track"
                       and r["night_queue"]), None)
        out[row["style"]] = {"unused_pct": row["unused_pct"],
                             "unused_with_queue_pct": queued["unused_pct"] if queued else None,
                             "asks_h": row["asks_h"]}
    return out or None


def table(results):
    lines = [f"{'style':<9} {'week':<13} {'queue':<5} {'unused':>8} {'weekend':>8} {'night':>7} {'asks h':>6} "
             f"{'throttled h':>11} {'peak 5h':>8}"]
    for r in results:
        lines.append(f"{r['style']:<9} {r['week']:<13} {'yes' if r['night_queue'] else 'no':<5} "
                     f"{r['unused_pct']:>6.1f} % {r['weekend_day_pct']:>6.1f} % {r['night_pct']:>5.1f} % "
                     f"{r['asks_h']:>6} {r['throttled_h']:>11} {r['peak_five_hour_pct']:>6.1f} %")
    return lines


def read_own_profile(pace, sd):
    """The stored profile with defaults added; a missing file is the defaults, an unreadable one a PaceError."""
    raw = pace.load_stored_profile(sd / "pace-profile.json")[0]
    # The zone the profile counts in now: the machine's in machine mode; nothing is written back.
    raw["timezone"] = pace.effective_zone(raw, pace.detect_zone)[1]
    return raw


def cached_preview(ctx, pace=None):
    """(preview, inputs) of the view's selection from a fresh cache entry, else (None, None).

    The key is the one `simulate --preview --cached` stores under, so an entry made by a change of the
    profile, the styles, the plugin or the reset is stale here. Nothing is simulated or written."""
    pace = pace or _pace()
    try:
        profile = read_own_profile(pace, ctx.sd)
        overrides, _warnings = pace.load_style_overrides(ctx.sd)
        profile["plans"] = []
        tz = pace.load_zone(profile["timezone"])
        styles = select_styles(pace, ["all"], profile, overrides)
        reset, source = pick_reset(pace, ctx, profile, tz, None)
        key = cache_key(pace, profile, styles, reset, source, ["on-track"], [False, True])
    except (pace.PaceError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, None
    results = cached_results(ctx.sd, key)
    preview = preview_from(results) if results else None
    if preview is None:
        return None, None
    return preview, {"reset": pace.iso_ms(reset), "reset_local": pace.fmt_local(reset, tz), "reset_source": source}


def main(ctx, pace=None):
    pace = pace or _pace()
    args = ctx.args
    sd = ctx.sd
    if getattr(args, "preview", False):
        requested, week_args, queue_arg = ["all"], ["on-track"], "both"
    else:
        requested = getattr(args, "style", None) or ["all"]
        week_args = getattr(args, "week", None) or ["all"]
        queue_arg = getattr(args, "night_queue", None) or "both"
    weeks = [w for w in WEEKS if "all" in week_args or w in week_args]
    queues = {"no": [False], "yes": [True], "both": [False, True]}[queue_arg]

    if getattr(args, "defaults", False):
        profile = json.loads(json.dumps(pace.PROFILE_DEFAULTS))
        profile["timezone"] = "UTC"
        which = "defaults"
        overrides, warnings = {}, []
    else:
        profile = read_own_profile(pace, sd)
        which = "own"
        overrides, warnings = pace.load_style_overrides(sd)
    profile["plans"] = []
    tz = pace.load_zone(profile["timezone"])
    styles = select_styles(pace, requested, profile, overrides)
    reset, source = pick_reset(pace, ctx, profile, tz, getattr(args, "reset", None))
    key = cache_key(pace, profile, styles, reset, source, weeks, queues)

    results, hit = None, False
    use_cache = getattr(args, "cached", False)
    if use_cache:
        results = cached_results(sd, key)
        hit = results is not None
    if results is None:
        hook = load_hook()
        results = []
        for name, values in styles:
            styled = dict(json.loads(json.dumps(profile)), plans=[])
            for k, (value, _source) in values.items():
                styled[k] = json.loads(json.dumps(value))
            for shape in weeks:
                for queue in queues:
                    row = {"style": name, "week": shape, "night_queue": queue}
                    row.update(simulate_week(pace, hook, styled, tz, reset, shape, queue))
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
                   "profile": which, "timezone": profile["timezone"], "day_hours": list(profile["day_hours"]),
                   "night_hours": list(profile["night_hours"]),
                   "styles": {name: {k: {"value": value, "source": src} for k, (value, src) in values.items()}
                              for name, values in styles}},
        "results": results,
        "preview": preview_from(results) if getattr(args, "preview", False) else None,
        "warnings": warnings,
        "cache": {"key": key, "hit": hit},
    }
    day, night = profile["day_hours"], profile["night_hours"]
    header = (f"Reset {reset.astimezone(tz).strftime('%a')} {pace.fmt_local(reset, tz)} ({source}) | zone "
              f"{profile['timezone']} | day {day[0]}-{day[1]} h, night {night[0]}-{night[1]} h | "
              f"{'the defaults' if which == 'defaults' else 'your profile'} | {MODEL_NOTE}")
    lines = [header, ""] + table(results)
    if warnings:
        lines += [""] + [f"warning: {w}" for w in warnings]
    pace.emit(args, "\n".join(lines), payload)
    return pace.EXIT_OK
