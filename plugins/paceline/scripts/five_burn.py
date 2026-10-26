"""The five-hour burn rate: the measurement, the projection to the threshold, the shared zone rules
and the snapshot file other readers take the projection from.

The measurement and projection are pure functions over plain data. Standard library only;
scripts/limits_merge.py imports this module, so it must never import pace.py.
"""
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limits_file  # noqa: E402
import localzone  # noqa: E402

UTC = dt.timezone.utc
HOUR = dt.timedelta(hours=1)
DAY = dt.timedelta(days=1)


class ZoneUnavailable(ValueError):
    """The time zone cannot be loaded here."""


def _last_sunday(year, month):
    last = dt.date(year + (month == 12), month % 12 + 1, 1) - DAY
    return last - dt.timedelta(days=(last.weekday() - 6) % 7)


class EuropeanZone(dt.tzinfo):
    """Fixed-offset zone with EU summer time; stands in for zoneinfo when tzdata is absent."""

    def __init__(self, key, std_hours):
        self.key = key
        self.std = dt.timedelta(hours=std_hours)

    def __repr__(self):
        return f"EuropeanZone({self.key!r})"

    def _bounds(self, year):
        return (dt.datetime.combine(_last_sunday(year, 3), dt.time(1)),
                dt.datetime.combine(_last_sunday(year, 10), dt.time(1)))

    def _summer(self, d):
        wall = d.replace(tzinfo=None, fold=0)
        start, end = self._bounds(wall.year)
        gap = start + self.std
        overlap = end + self.std
        if gap <= wall < gap + HOUR:
            return d.fold == 1
        if overlap <= wall < overlap + HOUR:
            return d.fold == 0
        return gap + HOUR <= wall < overlap

    def utcoffset(self, d):
        return self.std + self.dst(d)

    def dst(self, d):
        return HOUR if d is not None and self._summer(d) else dt.timedelta(0)

    def tzname(self, d):
        return "summer" if self.dst(d) else "standard"

    def fromutc(self, d):
        naive = d.replace(tzinfo=None)
        start, end = self._bounds(naive.year)
        if start <= naive < end:
            return (naive + self.std + HOUR).replace(tzinfo=self)
        fold = 1 if end <= naive < end + HOUR else 0
        return (naive + self.std).replace(tzinfo=self, fold=fold)


CET_ZONES = {"Europe/Madrid", "Europe/Paris", "Europe/Berlin", "Europe/Rome", "Europe/Amsterdam",
             "Europe/Brussels", "Europe/Vienna", "Europe/Warsaw", "Europe/Prague", "Europe/Stockholm"}
EET_ZONES = {"Europe/Kyiv", "Europe/Kiev", "Europe/Helsinki", "Europe/Athens", "Europe/Riga"}


def load_zone(key, force_builtin=False):
    if not force_builtin:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(key)
        except Exception:
            pass
    if key in CET_ZONES:
        return EuropeanZone(key, 1)
    if key in EET_ZONES:
        return EuropeanZone(key, 2)
    if key in ("UTC", "Etc/UTC"):
        return UTC
    raise ZoneUnavailable(f"time zone {key} unavailable: install tzdata or use a CET/EET zone")


def detect_zone():
    """The machine's zone and whether load_zone can use it here: (None, False) when nothing is found."""
    try:
        name = localzone.detect()
    except Exception:  # noqa: BLE001 - a detector failure means nothing was detected
        return None, False
    if not name:
        return None, False
    try:
        load_zone(name)
    except Exception:  # noqa: BLE001 - zoneinfo can fail in more ways than ZoneUnavailable
        return name, False
    return name, True


TIMEZONE_MODES = ("machine", "fixed")


def effective_zone(stored, detect=None):
    """The mode and the zone to use for a stored profile: (mode, zone, followed).

    `stored` is the profile as read (None when there is none yet). The mode is the stored `timezone_mode`
    when it is one of the two strings; else "fixed" for a stored profile (an old one, or a hand-edited
    invalid value) and "machine" when there is no profile yet. In machine mode a usable detected zone is
    the one to use (`followed` is then True), else the stored zone stays in use. Nothing is written here.
    """
    stored = stored if isinstance(stored, dict) else None
    mode = stored.get("timezone_mode") if stored is not None else None
    if mode not in TIMEZONE_MODES:
        mode = "fixed" if stored is not None else "machine"
    zone = stored.get("timezone") if stored is not None else None
    if not isinstance(zone, str) or not zone.strip():
        zone = "UTC"
    if mode == "machine":
        name, usable = (detect or detect_zone)()
        if name and usable:
            return mode, name, True
    return mode, zone, False


def fmt_local(d, tz):
    return d.astimezone(tz).isoformat(sep=" ", timespec="minutes")


def classify(local, profile):
    h = local.hour
    n0, n1 = profile["night_hours"]
    d0, d1 = profile["day_hours"]
    if n0 <= h < n1:
        return "night"
    if d0 <= h < d1:
        return "weekend" if local.weekday() >= 5 else "work"
    return "off"


# The same lookback and minimum span as pitboard's burnRate().
LOOKBACK = dt.timedelta(minutes=90)
MIN_SPAN = dt.timedelta(minutes=10)


def samples(history, bucket):
    """(at, used_pct) of every history line that holds a reading of the window `bucket` belongs to, oldest first."""
    window = limits_file.window_of(bucket)
    out = []
    for line in history:
        reading = line.get("five_hour")
        if not limits_file.is_bucket(reading) or limits_file.window_of(reading) != window:
            continue
        at = limits_file.bucket_at(reading, line)
        if at:
            out.append((at, float(reading["used_percentage"])))
    out.sort(key=lambda sample: sample[0])
    return out


def measure(points, used, now):
    """The burn rate in percentage points per hour over the last LOOKBACK; None without a base far enough back."""
    points = [p for p in points if p[0] <= now]
    if not points:
        return None
    held = [p for p in points if p[0] <= now - LOOKBACK]
    base_at, base_pct = (now - LOOKBACK, held[-1][1]) if held else points[0]
    span = now - base_at
    if span < MIN_SPAN:
        return None
    hours = span.total_seconds() / 3600.0
    return {"rate": max(0.0, (used - base_pct) / hours), "base_at": base_at,
            "span_min": round(span.total_seconds() / 60.0, 1)}


def project(rate, used, threshold, now, reset):
    """The budget that keeps the window under the threshold until its reset, and when the rate reaches it."""
    hours_left = (reset - now).total_seconds() / 3600.0
    budget = max(0.0, (threshold - used) / hours_left) if hours_left > 0 else 0.0
    reaches = now + dt.timedelta(hours=(threshold - used) / rate) if rate > 0 and used < threshold else None
    return {"threshold": threshold, "budget": budget, "share": budget / rate if rate > 0 else None,
            "threshold_at": reaches,
            "limited": reaches is not None and reaches < reset}


def burn_view(measured, projected, stale, tz):
    """The `five_hour.burn` dict of the status output."""
    reaches = projected["threshold_at"]
    return {"rate_per_hour_pct": round(measured["rate"], 3),
            "budget_per_hour_pct": round(projected["budget"], 3),
            "budget_share": projected["share"],
            "threshold_pct": projected["threshold"],
            "threshold_at": None if reaches is None else limits_file.iso_ms(reaches),
            "threshold_at_local": None if reaches is None else fmt_local(reaches, tz),
            "base_at": limits_file.iso_ms(measured["base_at"]),
            "span_min": measured["span_min"],
            "limited": bool(projected["limited"]) and not stale}


def cap(agents, burn):
    """The agent count the burn lets through: the budget's share of the rate, rounded down."""
    share = burn.get("budget_share")
    if share is None:
        if burn["rate_per_hour_pct"] <= 0:
            return agents
        share = burn["budget_per_hour_pct"] / burn["rate_per_hour_pct"]
    return min(agents, math.floor(agents * share))


def reason(burn, reset_local):
    return (f"5h burn {burn['rate_per_hour_pct']:.1f}%/h vs budget {burn['budget_per_hour_pct']:.1f}%/h: "
            f"{burn['threshold_pct']:.0f}% at {burn['threshold_at_local']}, before reset {reset_local}")


# The profile keys the merge needs, with the defaults of pace.PROFILE_DEFAULTS (a test keeps them equal).
PROFILE_KEYS = {"timezone": "UTC", "timezone_mode": "machine", "day_hours": [8, 22], "night_hours": [0, 8],
                "five_hour_threshold_day": 80.0, "five_hour_threshold_night": 95.0}
# Limit data older than this many minutes is stale (pace.py reads it from here).
STALE_THRESHOLD_MIN = 30


def snapshot(now, account, five):
    """The snapshot of one account's window; `five` is a `five_hour` dict of the status output."""
    burn = five.get("burn")
    if burn:
        budget = burn["budget_per_hour_pct"]
    else:
        budget = round(project(0.0, five["used_pct"], five["threshold_pct"], now,
                               limits_file.parse_iso(five["reset"]))["budget"], 3)
        burn = {}
    return {"computed_at": limits_file.iso_ms(now), "account": account, "used_pct": five["used_pct"],
            "resets_at": five["reset"], "threshold_pct": five["threshold_pct"], "budget_per_hour_pct": budget,
            "rate_per_hour_pct": burn.get("rate_per_hour_pct"), "threshold_at": burn.get("threshold_at"),
            "threshold_at_local": burn.get("threshold_at_local"), "base_at": burn.get("base_at"),
            "span_min": burn.get("span_min"), "limited": bool(burn.get("limited"))}


def publish(sd, data):
    """Write the snapshot to the file of its account, `five-hour-burn-<key>.json`.

    A file per account lets two accounts in use at once each keep their own snapshot without a
    read-modify-write of a shared one. A snapshot with no account is not written. A leftover
    `five-hour-burn.json` of an earlier release is removed.
    """
    account = data.get("account")
    if not account:
        return
    sd.mkdir(parents=True, exist_ok=True)
    limits_file.write_json(sd / f"five-hour-burn-{account}.json", data)
    try:
        (sd / "five-hour-burn.json").unlink()
    except OSError:
        pass


def after_merge(sd, now, record):
    """Write the snapshot of the signed-in account after a merge that wrote `record` (rate-limits.json)."""
    if not limits_file.pace_enabled(sd):
        return
    # The file carries computed_at in milliseconds, so a reader can rerun the computation from it.
    now = now.replace(microsecond=now.microsecond // 1000 * 1000)
    key, _ = limits_file.resolve_account()
    if not key:
        return
    partition, account = limits_file.pick_partition(record, key, None)
    if partition is None or account["state"] != "known":
        return
    held = partition.get("five_hour")
    dated = limits_file.dated_record(partition, now)
    reset = limits_file.safe_iso(held.get("resets_at")) if limits_file.is_bucket(held) else None
    if dated is None or reset is None or reset <= now:
        return
    stored = limits_file.read_json(sd / "pace-profile.json")
    profile = {name: (stored or {}).get(name, default) for name, default in PROFILE_KEYS.items()}
    # The mode comes from effective_zone, never from the default above: an old profile reads as fixed.
    tz = load_zone(effective_zone(stored)[1])
    night = classify(now.astimezone(tz), profile) == "night"
    threshold = float(profile["five_hour_threshold_night" if night else "five_hour_threshold_day"])
    used = float(held["used_percentage"])
    stale = (now - dated["_at"]).total_seconds() / 60.0 > STALE_THRESHOLD_MIN
    window = limits_file.window_of(held)

    def is_base(record):
        reading = record.get("five_hour")
        if not limits_file.is_bucket(reading) or limits_file.window_of(reading) != window:
            return False
        at = limits_file.bucket_at(reading, record)
        return bool(at) and at <= now - LOOKBACK

    history = limits_file.read_history_tail(sd, key, is_base, window)
    measured = measure(samples(history, held), used, now)
    burn = None
    if measured is not None:
        burn = burn_view(measured, project(measured["rate"], used, threshold, now, reset), stale, tz)
    publish(sd, snapshot(now, key, {"used_pct": used, "reset": limits_file.iso_ms(reset),
                                    "threshold_pct": threshold, "burn": burn}))
