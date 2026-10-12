#!/usr/bin/env python3
"""Pace Claude subscription usage against the weekly and five-hour limits.

Stdlib only. See paceline's README.md for the CLI reference, exit codes and state file schemas.
"""
import argparse
import contextlib
import ctypes
import datetime as dt
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
from pathlib import Path

# limits_file.py sits beside this script: the state directory, the lock and the merge of
# rate-limits.json, shared with the hooks.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limits_file  # noqa: E402
import localzone  # noqa: E402
from limits_file import (  # noqa: E402
    FUTURE_SLACK, RECORD_META, bucket_at, is_bucket, parse_iso, safe_iso,
)

UTC = dt.timezone.utc
HOUR = dt.timedelta(hours=1)
DAY = dt.timedelta(days=1)
WEEK = dt.timedelta(days=7)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_SEVERAL = 3
EXIT_NONE = 4
EXIT_WAIT = 10
EXIT_STOP = 11

PROFILE_DEFAULTS = {
    "version": 1,
    "timezone": "UTC",
    "timezone_confirmed": False,
    "day_hours": [8, 22],
    "night_hours": [0, 8],
    "weekday_weight": 1.0,
    "weekend_weight": 1.0,
    "weekend_cap_pct": 20.0,
    "reserve_pct": 8.0,
    "reserve_decay_start": {"weekday": 4, "hour": 18},
    "five_hour_threshold_day": 80.0,
    "five_hour_threshold_night": 95.0,
    "five_hour_hard_stop": 95.0,
    "five_hour_to_week_pct": 10.0,
    "heavy_weight": 2.0,
    "night_base_pct": 5.0,
    "host_night_capacity_pct": None,
    "lease_ttl_min": 30,
    "agent_lease_ttl_min": 240,
    "subagent_estimate_pct": 1.0,
    "stale_threshold_min": 30,
    "usage_snapshot_max_age_sec": 300,
    "capacity_warn_ratio": 0.9,
    "auto_refresh_five_hour_pct": 85.0,
    "auto_refresh_cache_sec": 125,
    "min_free_commit_mb": 2048,
    "commit_reserve_mb": 2048,
    "heavy_job_mb": 6000,
    "max_heavy_runs": 3,
    "max_agents": 6,
    "models": ["opus", "sonnet", "haiku"],
    "routine_models": ["sonnet", "haiku"],
    "throttle_weekly_pct": 85.0,
    "scoped_exhausted_pct": 95.0,
    "leftover_suggest_pct": 10.0,
    "gate_fix_attempts": 3,
    "calibration_min_days": 14,
    "plans": [],
    "next_plan_id": 1,
}


class PaceError(Exception):
    pass


def migrate_profile(raw):
    """Add every key a stored profile lacks with its default. Returns True when anything changed."""
    changed = False
    for k, v in PROFILE_DEFAULTS.items():
        if k not in raw:
            raw[k] = json.loads(json.dumps(v))
            changed = True
    return changed


# ---------------------------------------------------------------- time zone

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
    raise PaceError(f"time zone {key} unavailable: install tzdata or use a CET/EET zone")


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
    except Exception:  # noqa: BLE001 - zoneinfo can fail in more ways than PaceError
        return name, False
    return name, True


def local_dt(day, hour, tz):
    if hour >= 24:
        day, hour = day + DAY, hour - 24
    return dt.datetime.combine(day, dt.time(hour)).replace(tzinfo=tz).astimezone(UTC)


def iso_ms(d):
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def round_minute(d):
    return (d + dt.timedelta(seconds=30)).replace(second=0, microsecond=0)


def parse_local(value, tz, now, end=False):
    """Owner-facing date or datetime in local time. A bare date as an end means the end of that day."""
    text = str(value).strip().lower()
    today = now.astimezone(tz).date()
    if text in ("today", "tomorrow"):
        day = today + (DAY if text == "tomorrow" else dt.timedelta(0))
        return local_dt(day + (DAY if end else dt.timedelta(0)), 0, tz)
    if len(text) == 10:
        day = dt.date.fromisoformat(text)
        return local_dt(day + (DAY if end else dt.timedelta(0)), 0, tz)
    d = limits_file.fromisoformat(value)
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.astimezone(UTC)


def fmt_local(d, tz):
    return d.astimezone(tz).isoformat(sep=" ", timespec="minutes")


# ---------------------------------------------------------------- files and locking

def state_dir(override=None):
    return Path(override) if override else limits_file.paceline_dir()


def config_dir():
    return limits_file.config_dir()


def read_json(path, default):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(text)
    except ValueError as error:
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


if os.name == "nt":
    import msvcrt

    def _try_lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def locked(sd, timeout=20.0, name="pace.lock"):
    sd.mkdir(parents=True, exist_ok=True)
    fh = open(sd / name, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(fh)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise PaceError(f"{name} busy for {timeout:.0f} s; another paceline call is stuck")
                time.sleep(0.02)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                _unlock(fh)
    finally:
        fh.close()


class Store:
    """The four pace state files, read and written together under the lock."""

    FILES = {
        "profile": ("pace-profile.json", None),
        "registry": ("pace-registry.json", {"reservations": [], "completed": []}),
        "queue": ("night-queue.json", {"run": None, "next_id": 1, "items": []}),
        "pool": ("leftover-pool.json", {"approved_on": None, "approved_reset": None, "next_id": 1, "items": []}),
    }

    def __init__(self, sd):
        self.sd = sd
        self.data = {}
        self.dirty = set()
        for key, (name, default) in self.FILES.items():
            path = sd / name
            if key == "profile":
                raw = read_json(path, None)
                if raw is None:
                    raw = json.loads(json.dumps(PROFILE_DEFAULTS))
                    zone, usable = detect_zone()
                    if zone and usable:
                        raw["timezone"], raw["timezone_confirmed"] = zone, True
                    self.dirty.add(key)
                if migrate_profile(raw):
                    self.dirty.add(key)
                self.data[key] = raw
            else:
                value = read_json(path, None)
                if value is None:
                    value = json.loads(json.dumps(default))
                for k, v in default.items():
                    value.setdefault(k, json.loads(json.dumps(v)))
                self.data[key] = value

    def __getitem__(self, key):
        return self.data[key]

    def touch(self, *keys):
        self.dirty.update(keys)

    def save(self):
        for key in sorted(self.dirty):
            write_json(self.sd / self.FILES[key][0], self.data[key])
        self.dirty.clear()


# ---------------------------------------------------------------- host

MIB = 1024 * 1024
HOST_COMMAND_TIMEOUT_SEC = 2
# The state directory of the running command, so host calls deep in the code trace where it does.
TRACE_DIR = None


def run_command(argv, timeout=HOST_COMMAND_TIMEOUT_SEC):
    """stdout of a short host command, or None when it is missing, fails or times out."""
    started = time.monotonic()
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        limits_file.trace("host.command", TRACE_DIR, argv=argv, error=f"{type(error).__name__}: {error}")
        return None
    limits_file.trace("host.command", TRACE_DIR, argv=argv, exit_code=out.returncode,
                      ms=round((time.monotonic() - started) * 1000), stderr=out.stderr.strip()[:200])
    return out.stdout if out.returncode == 0 else None


def free_memory_mb():
    """Memory a new heavy process can still take, in MiB, or None when it can't be measured.

    Windows: free commit, the limit a runaway process exhausts first on that host. macOS has no
    commit limit; the kernel's share of available memory (kern.memorystatus_level, the figure its
    memory-pressure logic uses) times physical memory stands in. Linux: MemAvailable.
    """
    override = os.environ.get("PACELINE_FREE_COMMIT_MB")
    if override:
        return float(override)
    try:
        if os.name == "nt":
            return _free_commit_windows()
        if sys.platform == "darwin":
            return _free_memory_macos()
        return _free_memory_linux()
    except (OSError, ValueError, AttributeError):
        return None


def _free_commit_windows():
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.ullAvailPageFile / MIB


def _free_memory_macos():
    level = run_command(["sysctl", "-n", "kern.memorystatus_level"])
    total = run_command(["sysctl", "-n", "hw.memsize"])
    if level is None or total is None:
        return None
    return int(level.strip()) / 100 * int(total.strip()) / MIB


def _free_memory_linux(path="/proc/meminfo"):
    for line in Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return None


def _kernel32():
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    return k32


def process_image(pid):
    """Lower-case executable name of a live process, '' when it runs but the name is hidden, None when gone."""
    if pid is None:
        return None
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        return _posix_image(int(pid))
    from ctypes import wintypes
    k32 = _kernel32()
    handle = k32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return "" if ctypes.get_last_error() == 5 else None
    try:
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(handle)


def _posix_image(pid, proc="/proc"):
    # The same source as the process table on each OS, so an image recorded at acquire matches the
    # check later: /proc truncates names to 15 characters on Linux, and ps does too.
    with contextlib.suppress(OSError):
        return Path(proc, str(pid), "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
    name = (run_command(["ps", "-o", "comm=", "-p", str(pid)]) or "").strip()
    return os.path.basename(name).lower() if name else ""


def pid_alive(pid, image=None):
    name = process_image(pid)
    if name is None:
        return False
    return not image or not name or name == image


def process_table():
    """{pid: (parent pid, lower-case executable name)} for every process, or {} when it can't be read."""
    if os.name == "nt":
        return _process_table_windows()
    return _process_table_ps() or _process_table_proc()


def parse_ps(text):
    """Rows of `ps` output with pid, ppid and comm; a header and malformed lines are skipped."""
    table = {}
    for line in text.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            table[int(fields[0])] = (int(fields[1]), os.path.basename(fields[2].strip()).lower())
    return table


def _process_table_ps():
    # BusyBox ps rejects -A and lists every process without it; its -o takes no "=" header suppression.
    for argv in (["ps", "-A", "-o", "pid=,ppid=,comm="], ["ps", "-o", "pid,ppid,comm"]):
        out = run_command(argv)
        table = parse_ps(out) if out else {}
        if table:
            return table
    return {}


def _process_table_proc(root="/proc"):
    table = {}
    base = Path(root)
    if not base.is_dir():
        return table
    for entry in base.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # The name sits in parentheses and may hold spaces or parentheses; the fields after the last ")" are fixed.
        name, rest = stat[stat.find("(") + 1:stat.rfind(")")], stat[stat.rfind(")") + 1:].split()
        if len(rest) > 1 and rest[1].isdigit():
            table[int(entry.name)] = (int(rest[1]), name.lower())
    return table


def _process_table_windows():
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = _kernel32()
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    table = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return table


def claude_ancestors(table=None, start=None):
    """The chain from this process upward as (pid, parent pid, name), and the nearest claude ancestor's pid or None."""
    table = process_table() if table is None else table
    first = os.getpid() if start is None else start
    chain, pid = [], first
    for _ in range(32):
        info = table.get(pid)
        if not info:
            break
        chain.append((pid, info[0], info[1]))
        if pid != first and info[1].startswith("claude"):
            return chain, pid
        pid = info[0]
    return chain, None


def claude_pid():
    """The nearest ancestor whose executable starts with 'claude', else the parent process."""
    _, pid = claude_ancestors()
    return pid if pid is not None else os.getppid()


# ---------------------------------------------------------------- usage data

def read_history(sd):
    records = []
    for name in ("rate-limits.history.jsonl.1", "rate-limits.history.jsonl"):
        try:
            lines = (sd / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                record = json.loads(line)
                record["_at"] = parse_iso(record["observed_at"])
                if record.get("seven_day"):
                    records.append(record)
    records.sort(key=lambda r: r["_at"])
    return records


MERGE_LOCK_WAIT_SEC = 3
LOAD_LOCK_WAIT_SEC = 20
STALE_EPOCH = dt.datetime(1970, 1, 1, tzinfo=UTC)


def read_limits(sd):
    """rate-limits.json as it stands; None when absent or unreadable, which the next writer overwrites."""
    with contextlib.suppress(OSError, ValueError):
        parsed = json.loads((sd / "rate-limits.json").read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    return None


def bucket_times(record, now):
    """Each live bucket's time; a window that has reset reads 0, so it has none."""
    times = {}
    for name, bucket in record.items():
        if name in RECORD_META or not is_bucket(bucket):
            continue
        reset = safe_iso(bucket.get("resets_at"))
        if reset and reset <= now:
            continue
        at = bucket_at(bucket, record)
        if at and at > now + FUTURE_SLACK:
            # A reading from the future can't be real; it must not look fresh.
            at = STALE_EPOCH
        if at:
            times[name] = at
    return times


def current_record(sd, now):
    """rate-limits.json dated by its oldest live bucket, never by mtime; None without a seven_day bucket."""
    record = read_limits(sd)
    if not record or not is_bucket(record.get("seven_day")):
        return None
    times = bucket_times(record, now)
    at = min(times.values()) if times else safe_iso(record.get("observed_at"))
    if not at:
        return None
    return dict(record, _at=at, _bucket_at={name: iso_ms(t) for name, t in times.items()})


def record_source(record):
    return record.get("source") or "payload"


SKIP_LOCK_ERROR = f"another merge holds {limits_file.LOCK_FILE}"


def update_usage(sd, now, merge=True, lock_wait=0.0):
    """One merge step of limits_file. Returns (rate-limits.json record, merged, error)."""
    if not merge:
        return read_limits(sd), False, "merge disabled"
    if now > limits_file.wall_clock() + FUTURE_SLACK:
        return read_limits(sd), False, "merge disabled: --now is in the future"
    outcome, record = limits_file.merge_step(sd, now, lock_wait=lock_wait)
    if outcome == limits_file.SKIP_LOCK:
        return record, False, SKIP_LOCK_ERROR
    return record, True, None


def load_usage(sd, profile, now, allow_merge=True, max_age=None):
    """Latest usage record plus where it came from. Never raises on a busy lock or missing data."""
    history = read_history(sd)
    fake = os.environ.get("PACELINE_USAGE_FILE")
    if fake:
        record = json.loads(Path(fake).read_text(encoding="utf-8"))
        record["_at"] = parse_iso(record.get("observed_at")) or now
        return {"record": record, "source": "file", "error": None, "history": history}
    limit = profile["usage_snapshot_max_age_sec"] if max_age is None else max_age

    def fresh(record):
        return record is not None and (now - record["_at"]).total_seconds() < limit

    newest = current_record(sd, now)
    if fresh(newest):
        return {"record": newest, "source": record_source(newest), "error": None, "history": history}
    if not allow_merge or os.environ.get("PACELINE_NO_MERGE"):
        return {"record": newest, "source": "fallback", "error": "merge disabled", "history": history}
    # The record is older than the limit: take in whatever the sessions have handed over since.
    # A reading is never newer than the last turn of any session, so a merge without error leaves
    # no error to report even when the data stays old.
    _, merged, error = update_usage(sd, now, lock_wait=LOAD_LOCK_WAIT_SEC)
    history = read_history(sd)
    newest = current_record(sd, now)
    if fresh(newest):
        return {"record": newest, "source": record_source(newest), "error": None, "history": history}
    return {"record": newest, "source": "fallback", "error": None if merged else error, "history": history}


def scoped_match(scoped, model):
    if not model:
        return []
    m = model.lower()
    return [s for s in scoped or [] if isinstance(s.get("name"), str) and s["name"].lower() in m]


def effective_used(record, model):
    values = [float(record["seven_day"]["used_percentage"])]
    values += [float(s["used_percentage"]) for s in scoped_match(record.get("scoped"), model)]
    return max(values)


# ---------------------------------------------------------------- plans

def plan_weight(profile, plan):
    if plan["type"] == "away":
        return 0.0
    return float(plan.get("weight") or profile["heavy_weight"])


def plan_at(plans, t):
    """The plan record covering instant t; the later declaration wins an overlap."""
    best = None
    for plan in plans:
        if parse_iso(plan["start"]) <= t < parse_iso(plan["end"]):
            if best is None or (plan["declared_at"], plan["id"]) > (best["declared_at"], best["id"]):
                best = plan
    return best


def prune_plans(profile, now):
    before = len(profile["plans"])
    profile["plans"] = [p for p in profile["plans"] if parse_iso(p["end"]) > now]
    return before - len(profile["plans"])


def plan_view(plan, tz):
    return dict(plan, start_local=fmt_local(parse_iso(plan["start"]), tz),
                end_local=fmt_local(parse_iso(plan["end"]), tz))


# ---------------------------------------------------------------- budget model

class Slot:
    __slots__ = ("start", "hours", "local", "kind", "weight", "cap")

    def __init__(self, start, hours, local, kind, weight, cap):
        self.start, self.hours, self.local, self.kind, self.weight, self.cap = start, hours, local, kind, weight, cap

    @property
    def eff(self):
        return self.weight * self.hours


def classify(local, profile):
    h = local.hour
    n0, n1 = profile["night_hours"]
    d0, d1 = profile["day_hours"]
    if n0 <= h < n1:
        return "night"
    if d0 <= h < d1:
        return "weekend" if local.weekday() >= 5 else "work"
    return "off"


def build_slots(start, end, tz, profile, plans):
    per_hour = profile["five_hour_to_week_pct"] / 5.0
    night_share = profile["five_hour_threshold_night"] / 100.0
    slots = []
    t = start
    while t < end:
        boundary = t.replace(minute=0, second=0, microsecond=0) + HOUR
        stop = min(boundary, end)
        hours = (stop - t).total_seconds() / 3600.0
        local = t.astimezone(tz)
        kind = classify(local, profile)
        if kind == "work":
            weight = float(profile["weekday_weight"])
        elif kind == "weekend":
            weight = float(profile["weekend_weight"])
        else:
            weight = 0.0
        if kind in ("work", "weekend") and plans:
            plan = plan_at(plans, t)
            if plan:
                weight = 0.0 if plan["type"] == "away" else weight * plan_weight(profile, plan)
        cap = per_hour * hours * (night_share if kind == "night" else 1.0)
        slots.append(Slot(t, hours, local, kind, weight, cap))
        t = stop
    return slots


def waterfill(total, items):
    """Split total across items as weight * rate, each clipped at its cap. Returns (rate, allocations)."""
    alloc = [0.0] * len(items)
    active = [(i, w, c) for i, (w, c) in enumerate(items) if w > 0 and c > 0]
    if total <= 0 or not active:
        return 0.0, alloc
    if sum(c for _, _, c in active) <= total:
        for i, _, c in active:
            alloc[i] = c
        return math.inf, alloc
    active.sort(key=lambda x: x[2] / x[1])
    remaining = total
    wsum = sum(w for _, w, _ in active)
    k = 0
    while k < len(active):
        i, w, c = active[k]
        if w * remaining / wsum >= c:
            alloc[i] = c
            remaining -= c
            wsum -= w
            k += 1
        else:
            break
    rate = remaining / wsum
    for i, w, c in active[k:]:
        alloc[i] = w * rate
    return rate, alloc


def allocate(total, slots, weekend_cap_left):
    """Remaining-based split of total over day slots with the weekend cap. Returns rates and per-slot allocations."""
    day = [i for i, s in enumerate(slots) if s.kind in ("work", "weekend")]
    alloc = [0.0] * len(slots)
    rate, part = waterfill(total, [(slots[i].eff, slots[i].cap) for i in day])
    rates = {"work": rate, "weekend": rate}
    weekend = [i for i in day if slots[i].kind == "weekend"]
    if sum(part[day.index(i)] for i in weekend) > weekend_cap_left + 1e-9:
        wrate, wpart = waterfill(max(0.0, weekend_cap_left), [(slots[i].eff, slots[i].cap) for i in weekend])
        work = [i for i in day if slots[i].kind == "work"]
        drate, dpart = waterfill(total - sum(wpart), [(slots[i].eff, slots[i].cap) for i in work])
        for i, a in zip(weekend, wpart):
            alloc[i] = a
        for i, a in zip(work, dpart):
            alloc[i] = a
        rates = {"work": drate, "weekend": wrate}
    else:
        for i, a in zip(day, part):
            alloc[i] = a
    return rates, alloc


def reserve_at(t, reset, tz, profile):
    base = float(profile["reserve_pct"])
    decay = profile["reserve_decay_start"]
    reset_day = reset.astimezone(tz).date()
    start = None
    for back in range(8):
        day = reset_day - dt.timedelta(days=back)
        if day.weekday() == decay["weekday"]:
            candidate = local_dt(day, decay["hour"], tz)
            if candidate < reset:
                start = candidate
                break
    if t >= reset:
        return 0.0
    if start is None or t <= start:
        return base
    return base * (reset - t).total_seconds() / (reset - start).total_seconds()


def used_at(history, t, reset, model):
    """Effective weekly usage at instant t inside the window ending at reset; None when history has no point."""
    if t <= reset - WEEK:
        return 0.0
    best = None
    for record in history:
        if record["_at"] > t:
            break
        r = parse_iso(record["seven_day"].get("resets_at"))
        if r and abs((r - reset).total_seconds()) < 600:
            best = record
    return effective_used(best, model) if best else None


def weekend_day_spend(history, now, reset, tz, profile, model, effective, runs=None):
    """What the current weekend spent in its day hours; night spend is funded from surplus and not capped."""
    local = now.astimezone(tz)
    saturday = local.date() - dt.timedelta(days=local.weekday() - 5)
    start = max(local_dt(saturday, 0, tz), reset - WEEK)
    before = used_at(history, start, reset, model)
    spent = effective - (before if before is not None else 0.0)
    n0, n1 = profile["night_hours"]
    for day in (saturday, saturday + DAY):
        a = max(local_dt(day, n0, tz), start)
        b = min(local_dt(day, n1, tz), now)
        if b <= a:
            continue
        run = next((r for r in runs or [] if r.get("night_date") == day.isoformat()
                    and r.get("reset") in (None, iso_ms(reset))), None)
        if run is not None:
            last = effective if b == now else run.get("last_effective", run["used_at_start"])
            spent -= max(0.0, last - run["used_at_start"])
            continue
        u0 = used_at(history, a, reset, model)
        u1 = effective if b == now else used_at(history, b, reset, model)
        if u0 is not None and u1 is not None:
            spent -= max(0.0, u1 - u0)
    return max(0.0, spent)


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


def window_effective(record, now, model):
    """Effective weekly usage of the current window and its reset instant."""
    _, reset, rolled = roll_bucket(record["seven_day"], now, WEEK)
    return (0.0 if rolled else effective_used(record, model)), reset


def compute(now, profile, usage, model=None, commit_mb=None, outstanding=0.0, runs=None):
    """Every number the recommendation and the autonomous parameters are built from."""
    record = usage.get("record")
    if not record or not record.get("seven_day"):
        raise PaceError("no usage data yet: rate-limits.json has no weekly reading; it fills after the first "
                        "answer in a session with paceline loaded")
    tz = load_zone(profile["timezone"])
    history = usage.get("history", [])
    k = float(profile["five_hour_to_week_pct"])
    seven_used, reset, rolled = roll_bucket(record["seven_day"], now, WEEK)
    if reset is None:
        raise PaceError("usage record has no seven_day.resets_at")
    scoped = [] if rolled else [dict(s) for s in record.get("scoped") or []]
    matched = scoped_match(scoped, model)
    effective = max([seven_used] + [float(s["used_percentage"]) for s in matched])
    window_start = reset - WEEK
    remaining = max(0.0, 100.0 - effective)
    reserve = reserve_at(now, reset, tz, profile)
    spendable = max(0.0, remaining - reserve)

    age_min = (now - record["_at"]).total_seconds() / 60.0 if record.get("_at") else math.inf
    stale = age_min > profile["stale_threshold_min"]

    plans = profile.get("plans", [])
    slots = build_slots(now, reset, tz, profile, plans)
    local_now = now.astimezone(tz)

    weekend_spent = 0.0
    if local_now.weekday() >= 5:
        weekend_spent = weekend_day_spend(history, now, reset, tz, profile, model, effective, runs)
    weekend_cap_left = max(0.0, float(profile["weekend_cap_pct"]) - weekend_spent)
    rates, alloc = allocate(spendable, slots, weekend_cap_left)

    week_slots = build_slots(window_start, reset, tz, profile, [])
    normal_rates, _ = allocate(100.0 - float(profile["reserve_pct"]), week_slots, float(profile["weekend_cap_pct"]))
    expected = {"work": 0.0, "weekend": 0.0}
    for s in slots:
        if s.kind in expected and normal_rates[s.kind] != math.inf:
            expected[s.kind] += min(normal_rates[s.kind] * s.eff, s.cap)
        elif s.kind in expected:
            expected[s.kind] += s.cap if s.weight > 0 else 0.0
    expected_spend = expected["work"] + min(expected["weekend"], weekend_cap_left)
    surplus = max(0.0, spendable - expected_spend)

    day_share = profile["five_hour_threshold_day"] / 100.0
    day_capacity = sum(s.cap * day_share for s in slots if s.kind in ("work", "weekend") and s.weight > 0)
    unallocated = max(0.0, spendable - sum(alloc))
    capacity_warning = remaining >= profile["capacity_warn_ratio"] * day_capacity

    current = next((s for s in slots if s.kind in ("work", "weekend") and s.weight > 0), None)
    per_hour_cap = k / 5.0
    if current is None:
        budget_per_hour = 0.0
    else:
        rate = rates[current.kind]
        budget_per_hour = per_hour_cap if rate == math.inf else min(rate * current.weight, per_hour_cap)
    normal_kind = current.kind if current is not None else "work"
    normal_rate = normal_rates[normal_kind] if normal_rates[normal_kind] != math.inf else per_hour_cap
    in_day = bool(slots) and slots[0].kind in ("work", "weekend")
    current_plan = plan_at(plans, now)

    five_used, five_reset, _ = roll_bucket(record.get("five_hour"), now, None)
    in_night = classify(local_now, profile) == "night"
    five_threshold = float(profile["five_hour_threshold_night" if in_night else "five_hour_threshold_day"])

    night = night_info(now, reset, tz, profile, slots, alloc, spendable, surplus, commit_mb)

    heavy_runs = None
    if commit_mb is not None:
        heavy_runs = max(0, min(int(profile["max_heavy_runs"]),
                                int((commit_mb - profile["commit_reserve_mb"]) // profile["heavy_job_mb"])))

    forecast = forecast_unused(history, now, reset, remaining, model)
    hours_to_reset = (reset - now).total_seconds() / 3600.0
    elapsed_pct = 100.0 * (1 - hours_to_reset / 168.0)

    return {
        "now": iso_ms(now),
        "now_local": fmt_local(now, tz),
        "data": {"source": usage.get("source"), "error": usage.get("error"),
                 "observed_at": iso_ms(record["_at"]) if record.get("_at") else record.get("observed_at"),
                 "bucket_observed_at": record.get("_bucket_at"), "age_min": round(age_min, 1) if age_min != math.inf else None,
                 "stale": stale},
        "weekly": {"seven_day_pct": seven_used, "scoped": scoped, "model": model,
                   "matched_scoped": [s["name"] for s in matched], "effective_pct": effective,
                   "remaining_pct": remaining, "reset": iso_ms(reset), "reset_local": fmt_local(reset, tz),
                   "hours_to_reset": round(hours_to_reset, 2), "elapsed_pct": round(elapsed_pct, 1)},
        "reserve_pct": round(reserve, 3),
        "spendable_pct": round(spendable, 3),
        "budget_per_hour_pct": round(budget_per_hour, 3),
        "normal_per_hour_pct": round(normal_rate, 3),
        "normal_pct_kind": normal_kind,
        "rates": {k2: (None if v == math.inf else round(v, 4)) for k2, v in rates.items()},
        "weekend": {"cap_pct": profile["weekend_cap_pct"], "spent_pct": round(weekend_spent, 3),
                    "cap_left_pct": round(weekend_cap_left, 3)},
        "surplus_pct": round(surplus, 3),
        "expected_day_spend_pct": round(expected_spend, 3),
        "day_capacity_pct": round(day_capacity, 3),
        "unallocated_pct": round(unallocated, 3),
        "capacity_warning": capacity_warning,
        "in_day_hours": in_day,
        "current_plan": current_plan,
        "five_hour": {"used_pct": five_used, "reset": iso_ms(five_reset) if five_reset else None,
                      "reset_local": fmt_local(five_reset, tz) if five_reset else None,
                      "threshold_pct": five_threshold, "hard_stop_pct": profile["five_hour_hard_stop"]},
        "night": night,
        "host": {"free_commit_mb": None if commit_mb is None else round(commit_mb),
                 "min_free_commit_mb": profile["min_free_commit_mb"], "max_heavy_runs": heavy_runs},
        "outstanding_reservations_pct": round(outstanding, 3),
        "forecast_unused_pct": None if forecast is None else round(forecast, 1),
    }


def night_info(now, reset, tz, profile, slots, alloc, spendable, surplus, commit_mb):
    local = now.astimezone(tz)
    day_start = profile["day_hours"][0]
    if local.hour < profile["night_hours"][1]:
        night_date = local.date()
        start = max(now, local_dt(night_date, profile["night_hours"][0], tz))
    else:
        night_date = local.date() + DAY
        start = local_dt(night_date, profile["night_hours"][0], tz)
    end = local_dt(night_date, profile["night_hours"][1], tz)
    start, end = min(start, reset), min(end, reset)
    hours = max(0.0, (end - start).total_seconds() / 3600.0)
    window_capacity = hours * profile["five_hour_to_week_pct"] / 5.0 * profile["five_hour_threshold_night"] / 100.0
    host = profile.get("host_night_capacity_pct")
    host = math.inf if host is None else float(host)
    if commit_mb is not None and commit_mb < profile["min_free_commit_mb"]:
        host = 0.0
    last_night = local_dt(night_date, day_start, tz) >= reset
    next_day_share = 0.0
    if not last_night:
        next_day_share = sum(a for s, a in zip(slots, alloc)
                             if s.kind in ("work", "weekend") and s.local.date() == night_date)
    base = float(profile["night_base_pct"])
    allowance = max(0.0, min(base + surplus, window_capacity, host, spendable - next_day_share))
    return {"date": night_date.isoformat(), "start": iso_ms(start), "end": iso_ms(end), "hours": round(hours, 2),
            "last_before_reset": last_night, "base_pct": base, "window_capacity_pct": round(window_capacity, 3),
            "host_capacity_pct": None if host == math.inf else host, "next_day_share_pct": round(next_day_share, 3),
            "allowance_pct": round(allowance, 3)}


def forecast_unused(history, now, reset, remaining, model):
    """Unused weekly percentage at reset if the burn rate of the last 24 h continues."""
    points = [r for r in history if now - DAY <= r["_at"] <= now
              and (lambda x: x and abs((x - reset).total_seconds()) < 600)(parse_iso(r["seven_day"].get("resets_at")))]
    if len(points) < 2:
        return None
    span = (points[-1]["_at"] - points[0]["_at"]).total_seconds() / 3600.0
    if span < 3:
        return None
    burn = max(0.0, effective_used(points[-1], model) - effective_used(points[0], model)) / span
    return max(0.0, remaining - burn * (reset - now).total_seconds() / 3600.0)


# ---------------------------------------------------------------- recommendation

def recommend(calc, profile):
    reasons = []
    commit = calc["host"]["free_commit_mb"]
    five = calc["five_hour"]
    weekly = calc["weekly"]
    blocked = False
    if commit is not None and commit < profile["min_free_commit_mb"]:
        blocked = True
        reasons.append(f"free commit {commit / 1024:.1f} GB < {profile['min_free_commit_mb'] / 1024:.0f} GB")
    if five["used_pct"] >= five["hard_stop_pct"]:
        blocked = True
        reasons.append(f"5h {five['used_pct']:.0f}% >= {five['hard_stop_pct']:.0f}%")
    if weekly["effective_pct"] >= 100:
        blocked = True
        reasons.append("weekly limit exhausted")
    background_paused = not blocked and five["used_pct"] >= five["threshold_pct"]
    if background_paused:
        reasons.append(f"5h {five['used_pct']:.0f}% >= {five['threshold_pct']:.0f}%: no new background work "
                       f"until {five['reset_local'] or 'window reset'}")
    normal = calc["normal_per_hour_pct"] or 0.0
    ratio = calc["budget_per_hour_pct"] / normal if normal > 0 else 1.0
    throttle = ratio < 0.7 or (weekly["effective_pct"] >= profile["throttle_weekly_pct"] and weekly["hours_to_reset"] > 24)
    if blocked or background_paused:
        agents = 0
    else:
        agents = max(1, min(int(profile["max_agents"]), round(ratio * 2)))
    models = allowed_models(calc, profile, throttle)
    model = "none" if blocked else models[0] if models else "none"
    if throttle and not blocked:
        reasons.append("throttle: routine subagent work on " + "/".join(profile["routine_models"]))
    return {"blocked": blocked, "background_paused": background_paused, "pace_ratio": round(ratio, 2),
            "throttle": throttle, "max_parallel_agents": agents, "max_heavy_runs": calc["host"]["max_heavy_runs"],
            "model": model, "allowed_models": models, "reasons": reasons}


def allowed_models(calc, profile, throttle, need_pct=0.0):
    models = list(profile["routine_models"] if throttle else profile["models"])
    out = []
    for m in models:
        bucket = [s for s in calc["weekly"]["scoped"] if isinstance(s.get("name"), str) and s["name"].lower() in m]
        if bucket:
            used = max(float(s["used_percentage"]) for s in bucket)
            if used >= profile["scoped_exhausted_pct"] or 100 - used < need_pct:
                continue
        out.append(m)
    return out


KIND_LABEL = {"work": "weekday", "weekend": "weekend"}


def status_line(calc, rec, extras):
    w = calc["weekly"]
    scoped = ", ".join(f"{s['name']} {s['used_percentage']:.0f}%" for s in w["scoped"])
    normal_label = KIND_LABEL.get(calc["normal_pct_kind"], calc["normal_pct_kind"])
    normal = f"{normal_label} norm {calc['normal_per_hour_pct']:.2f}"
    parts = [f"7d {w['effective_pct']:.0f}%" + (f" ({scoped})" if scoped else "") + f" reset {w['reset_local']}",
             f"budget {calc['budget_per_hour_pct']:.2f}%/h" + (
                 f" (weekday {calc['rates']['work']:.2f}, {normal})"
                 if calc["rates"]["work"] is not None and abs(calc["rates"]["work"] - calc["budget_per_hour_pct"]) > 0.005
                 else f" ({normal})")]
    if calc["forecast_unused_pct"] is not None:
        parts.append(f"forecast unused {calc['forecast_unused_pct']:.0f}%")
    parts.append(f"agents <={rec['max_parallel_agents']} model {rec['model']}")
    parts.append(f"5h {calc['five_hour']['used_pct']:.0f}%/{calc['five_hour']['threshold_pct']:.0f}")
    parts.append(f"night {calc['night']['allowance_pct']:.1f}%")
    age = calc["data"]["age_min"]
    data = f"data {age:.0f} min old" if age is not None else "data age unknown"
    if calc["data"]["stale"]:
        data = "STALE " + data
    if calc["data"]["source"] == "fallback" and calc["data"]["error"]:
        data += f" ({calc['data']['error']})"
    parts.append(data)
    if calc["host"]["free_commit_mb"] is None:
        parts.append("host memory unknown")
    if calc.get("pace_on") is False:
        parts.append("pace hooks off (paceline on)")
    if calc["capacity_warning"]:
        parts.append(f"WARNING remainder {w['remaining_pct']:.0f}% near day capacity {calc['day_capacity_pct']:.0f}%")
    parts += rec["reasons"]
    parts += extras
    return " | ".join(parts)


# ---------------------------------------------------------------- reservations

def outstanding_pct(registry, effective):
    active = registry["reservations"]
    if not active:
        return 0.0
    consumed = max(0.0, effective - min(r["used_at_acquire"] for r in active))
    return max(0.0, sum(r["estimate_pct"] for r in active) - consumed)


def prune_reservations(registry, profile, now, alive=pid_alive):
    ttl = dt.timedelta(minutes=profile["lease_ttl_min"])
    # A keyed reservation is held by a hook pair that never renews it, and a subagent can outlive the lease.
    agent_ttl = dt.timedelta(minutes=profile["agent_lease_ttl_min"])
    kept, dropped = [], []
    for r in registry["reservations"]:
        if now - parse_iso(r["renewed_at"]) > (agent_ttl if r.get("key") else ttl):
            dropped.append((r, "lease expired"))
        elif r.get("pid") is not None and not alive(r["pid"], r.get("image")):
            dropped.append((r, "process gone"))
        else:
            kept.append(r)
    registry["reservations"] = kept
    for r, why in dropped:
        close_reservation(registry, r, now, None, None, why)
    return dropped


def close_reservation(registry, r, now, effective, actual, why):
    if actual is None and effective is not None:
        actual = max(0.0, effective - r["used_at_acquire"])
    others = [o for o in registry["reservations"] if o["id"] != r["id"]]
    entry = {k: r.get(k) for k in ("id", "kind", "label", "key", "session_id", "estimate_pct", "acquired_at", "item_id")}
    entry.update(released_at=iso_ms(now), actual_pct=None if actual is None else round(actual, 3),
                 overlapped=bool(others) or bool(r.get("overlapped")), reason=why)
    registry["completed"].append(entry)
    registry["completed"] = registry["completed"][-500:]
    return entry


def mark_overlap(registry):
    if len(registry["reservations"]) > 1:
        for r in registry["reservations"]:
            r["overlapped"] = True


# ---------------------------------------------------------------- queue

def item_view(item):
    return {k: item.get(k) for k in ("id", "title", "repo", "estimate_pct", "independent", "depends_on",
                                     "status", "reason", "attempts", "actual_pct")}


def defer_with_dependents(items, root_id, reason, now):
    deferred = []
    todo = [root_id]
    while todo:
        current = todo.pop()
        for item in items:
            if item["status"] in ("pending", "running") and (
                    item["id"] == current or current in item.get("depends_on", [])):
                item["status"] = "deferred"
                item["reason"] = reason if item["id"] == root_id else f"depends on deferred {current}"
                item["finished_at"] = iso_ms(now)
                deferred.append(item)
                todo.append(item["id"])
    return deferred


def pick_next(items, budget, running_ids):
    """First runnable pending item that fits the budget. Returns (item, reason_when_none)."""
    status = {i["id"]: i["status"] for i in items}
    pending = [i for i in items if i["status"] == "pending"]
    if not pending:
        if any(i["status"] == "running" for i in items):
            return None, "waiting"
        return None, "blocked" if any(i["status"] == "deferred" for i in items) else "empty"
    runnable, waiting = [], False
    for item in pending:
        deps = item.get("depends_on", [])
        if any(status.get(d) in ("deferred", None) for d in deps):
            continue
        if all(status.get(d) == "done" for d in deps):
            runnable.append(item)
        else:
            waiting = True
    if not runnable:
        return None, "waiting" if waiting else "blocked"
    running = [i for i in items if i["id"] in running_ids]
    exclusive = any(not i.get("independent") for i in running)
    for item in runnable:
        if running and (exclusive or not item.get("independent")):
            continue
        if item["estimate_pct"] <= budget + 1e-9:
            return item, None
    if running:
        return None, "waiting"
    return None, "budget"


# ---------------------------------------------------------------- calibration

def calibrate(history, store, now, profile, model):
    tz = load_zone(profile["timezone"])
    if not history:
        return {"days": 0, "proposals": [], "evidence": {},
                "note": f"not enough history: 0 days, need {profile['calibration_min_days']}"}
    days = (history[-1]["_at"] - history[0]["_at"]).total_seconds() / 86400.0
    result = {"days": round(days, 1), "proposals": [], "evidence": {}}
    if days < profile["calibration_min_days"]:
        result["note"] = f"not enough history: {days:.1f} days, need {profile['calibration_min_days']}"
        return result
    d5 = d7 = 0.0
    for a, b in zip(history, history[1:]):
        fa, fb = a.get("five_hour") or {}, b.get("five_hour") or {}
        if fa.get("resets_at") != fb.get("resets_at") or a["seven_day"].get("resets_at") != b["seven_day"].get("resets_at"):
            continue
        step5 = float(fb.get("used_percentage") or 0) - float(fa.get("used_percentage") or 0)
        step7 = effective_used(b, model) - effective_used(a, model)
        if step5 > 0 and step7 >= 0:
            d5 += step5
            d7 += step7
    if d5 >= 50:
        coefficient = 100.0 * d7 / d5
        result["evidence"]["five_hour_to_week_pct"] = round(coefficient, 2)
        if abs(coefficient - profile["five_hour_to_week_pct"]) > 1:
            result["proposals"].append({"parameter": "five_hour_to_week_pct", "current": profile["five_hour_to_week_pct"],
                                        "proposed": round(coefficient, 1),
                                        "evidence": f"{d7:.0f}% weekly over {d5:.0f}% of five-hour windows"})
    windows = {}
    for r in history:
        windows.setdefault(r["seven_day"].get("resets_at"), []).append(r)
    unused, weekend_share, nights = [], [], []
    for reset_iso, records in windows.items():
        reset = parse_iso(reset_iso)
        if not reset or reset > now:
            continue
        final = effective_used(records[-1], model)
        unused.append(100.0 - final)
        start = reset - WEEK
        first_monday = next((start + dt.timedelta(hours=h) for h in range(0, 168)
                             if (start + dt.timedelta(hours=h)).astimezone(tz).weekday() == 0
                             and (start + dt.timedelta(hours=h)).astimezone(tz).hour == 0), None)
        at_monday = used_at(records, first_monday, reset, model) if first_monday else None
        if at_monday is not None and final > 0:
            weekend_share.append(at_monday)
        for day_offset in range(8):
            day = start.astimezone(tz).date() + dt.timedelta(days=day_offset)
            n0, n1 = local_dt(day, profile["night_hours"][0], tz), local_dt(day, profile["night_hours"][1], tz)
            if n0 < start or n1 > reset:
                continue
            u0, u1 = used_at(records, n0, reset, model), used_at(records, n1, reset, model)
            if u0 is not None and u1 is not None:
                nights.append(max(0.0, u1 - u0))
    if unused:
        mean_unused = statistics.mean(unused)
        result["evidence"]["unused_at_reset_pct"] = [round(u, 1) for u in unused]
        reserve = profile["reserve_pct"]
        if mean_unused <= 2:
            result["proposals"].append({"parameter": "reserve_pct", "current": reserve, "proposed": reserve + 2,
                                        "evidence": f"weeks ended with {mean_unused:.1f}% unused on average"})
        elif mean_unused > profile["leftover_suggest_pct"] and reserve > 4:
            result["proposals"].append({"parameter": "reserve_pct", "current": reserve, "proposed": max(4, reserve - 2),
                                        "evidence": f"weeks ended with {mean_unused:.1f}% unused on average"})
    if weekend_share:
        cap = profile["weekend_cap_pct"]
        top = max(weekend_share)
        result["evidence"]["weekend_spend_pct"] = [round(w, 1) for w in weekend_share]
        if top < cap / 2:
            result["proposals"].append({"parameter": "weekend_cap_pct", "current": cap, "proposed": round(top + 3),
                                        "evidence": f"weekends never spent more than {top:.1f}%"})
        elif min(weekend_share) >= cap - 1 and unused and statistics.mean(unused) > profile["leftover_suggest_pct"]:
            result["proposals"].append({"parameter": "weekend_cap_pct", "current": cap, "proposed": cap + 5,
                                        "evidence": "every weekend hit the cap while weeks ended unused"})
    if nights:
        mean_night = statistics.mean(nights)
        result["evidence"]["night_spend_mean_pct"] = round(mean_night, 2)
        if abs(mean_night - profile["night_base_pct"]) > 1:
            result["proposals"].append({"parameter": "night_base_pct", "current": profile["night_base_pct"],
                                        "proposed": round(max(2.0, mean_night), 1),
                                        "evidence": f"mean night spend {mean_night:.1f}% over {len(nights)} nights"})
    clean = [c for c in store["registry"]["completed"]
             if c.get("actual_pct") is not None and c.get("estimate_pct") and not c.get("overlapped")]
    # Hook reservations carry the fixed profile estimate, so they calibrate that value, not the multiplier.
    agents = [c["actual_pct"] for c in clean if c.get("key") and c["kind"] == "subagent"]
    if len(agents) >= 3:
        median = statistics.median(agents)
        current = profile["subagent_estimate_pct"]
        result["evidence"]["subagent_actual_median_pct"] = round(median, 2)
        if current > 0 and abs(median / current - 1) > 0.2:
            result["proposals"].append({"parameter": "subagent_estimate_pct", "current": current,
                                        "proposed": round(max(0.1, median), 2),
                                        "evidence": f"median actual {median:.2f}% over {len(agents)} subagents"})
    ratios = [c["actual_pct"] / c["estimate_pct"] for c in clean if not c.get("key")]
    for key in ("queue", "pool"):
        ratios += [i["actual_pct"] / i["estimate_pct"] for i in store[key]["items"]
                   if i["status"] == "done" and i.get("actual_pct") is not None and i.get("estimate_pct")]
    if len(ratios) >= 3:
        median = statistics.median(ratios)
        result["evidence"]["estimate_ratio_median"] = round(median, 2)
        if abs(median - 1) > 0.2:
            result["proposals"].append({"parameter": "task_estimate_multiplier", "current": 1.0,
                                        "proposed": round(median, 2),
                                        "evidence": f"median actual/estimate {median:.2f} over {len(ratios)} tasks"})
    return result


# ---------------------------------------------------------------- CLI

class Context:
    def __init__(self, args):
        self.args = args
        self.sd = state_dir(args.state_dir)
        self.now = parse_iso(args.now or os.environ.get("PACELINE_NOW")) or dt.datetime.now(UTC)
        self.model = args.model or os.environ.get("PACELINE_MODEL") or os.environ.get("ANTHROPIC_MODEL")
        self.session = args.session or os.environ.get("CLAUDE_SESSION_ID")
        self.usage = None

    def get_usage(self, profile, allow_merge=True, max_age=None):
        if self.usage is None or max_age is not None:
            self.usage = load_usage(self.sd, profile, self.now, allow_merge and not self.args.no_merge, max_age)
        return self.usage

    def calc(self, store, allow_merge=True, max_age=None):
        usage = self.get_usage(store["profile"], allow_merge, max_age)
        record = usage.get("record")
        eff = window_effective(record, self.now, self.model)[0] if record and record.get("seven_day") else 0.0
        queue = store["queue"]
        runs = [r for r in [queue.get("run")] + queue.get("past_runs", []) if r]
        return compute(self.now, store["profile"], usage, self.model, free_memory_mb(),
                       outstanding_pct(store["registry"], eff), runs)


def emit(args, line, payload):
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(line)


def budget_suffix(calc):
    return f"budget {calc['budget_per_hour_pct']:.2f}%/h, night {calc['night']['allowance_pct']:.1f}%"


def housekeeping(store, now):
    if prune_plans(store["profile"], now):
        store.touch("profile")
    if prune_reservations(store["registry"], store["profile"], now):
        store.touch("registry")


def cmd_status(ctx):
    with locked(ctx.sd):
        store = Store(ctx.sd)
        housekeeping(store, ctx.now)
        store.save()
    line, calc = status_report(ctx, store)
    emit(ctx.args, line, calc)
    return EXIT_OK


def status_report(ctx, store):
    """The `paceline status` line and its full result."""
    calc = ctx.calc(store)
    rec = recommend(calc, store["profile"])
    tz = load_zone(store["profile"]["timezone"])
    extras = []
    tomorrow = plans_starting_tomorrow(store["profile"], ctx.now, tz)
    if tomorrow:
        extras.append(f"{len(tomorrow)} plan(s) start tomorrow, unconfirmed: " + ",".join(p["id"] for p in tomorrow))
    suggest = (ctx.now.astimezone(tz).weekday() in (3, 4) and calc["forecast_unused_pct"] is not None
               and calc["forecast_unused_pct"] > store["profile"]["leftover_suggest_pct"])
    if suggest:
        extras.append("forecast leaves >10% unused: propose leftover tasks")
    calc.update(recommendation=rec, unconfirmed_tomorrow=[p["id"] for p in tomorrow], suggest_leftover_tasks=suggest,
                active_reservations=len(store["registry"]["reservations"]), pace_on=limits_file.pace_enabled(ctx.sd))
    return status_line(calc, rec, extras), calc


def cmd_auto(ctx):
    with locked(ctx.sd):
        store = Store(ctx.sd)
        housekeeping(store, ctx.now)
        store.save()
    try:
        calc = ctx.calc(store)
        profile = store["profile"]
        age_sec = (calc["data"]["age_min"] or 0) * 60
        if (calc["five_hour"]["used_pct"] >= profile["auto_refresh_five_hour_pct"] and calc["data"]["source"] != "file"
                and age_sec >= profile["auto_refresh_cache_sec"]):
            # Near the five-hour ceiling, older data can hide a window that is already full: merge what
            # the sessions have handed over since.
            calc = ctx.calc(store, max_age=profile["auto_refresh_cache_sec"])
    except PaceError as error:
        result = {"mode": "autonomous", "action": "wait", "may_start_next_item": False, "max_parallel_agents": 0,
                  "max_heavy_runs": None, "allowed_models": [], "budget_for_next_item_pct": 0.0, "next_item": None,
                  "wait_until": None, "reasons": [str(error)], "run": None, "stale": True,
                  "weekly_effective_pct": None, "five_hour_used_pct": None}
        emit(ctx.args, f"auto: WAIT | {error}", result)
        return EXIT_WAIT
    profile = store["profile"]
    tz = load_zone(profile["timezone"])
    with locked(ctx.sd):
        store = Store(ctx.sd)
        result = autonomous(ctx.now, calc, store, profile, tz, ctx.args.start_run)
        store.save()
    line = (f"auto: {'START ' + result['next_item']['id'] if result['may_start_next_item'] else result['action'].upper()}"
            f" | budget {result['budget_for_next_item_pct']:.2f}% | agents <={result['max_parallel_agents']}"
            f" models {','.join(result['allowed_models']) or 'none'}")
    if result["run"] and result["run"]["reused"]:
        line += f" | run of {result['run']['night_date']} kept, {result['run']['remaining_pct']:.2f}% of" \
                f" {result['run']['allowance_pct']:.2f}% left"
    if result["reasons"]:
        line += " | " + "; ".join(result["reasons"])
    emit(ctx.args, line, result)
    return {"start": EXIT_OK, "wait": EXIT_WAIT, "stop": EXIT_STOP}[result["action"]]


def autonomous(now, calc, store, profile, tz, start_run=False):
    queue, pool = store["queue"], store["pool"]
    effective = calc["weekly"]["effective_pct"]
    night = calc["night"]
    local = now.astimezone(tz)
    run = queue.get("run")
    reused = bool(start_run and run and run.get("night_date") == night["date"])
    if start_run and not reused:
        if run:
            queue["past_runs"] = (queue.get("past_runs", []) + [run])[-14:]
        run = {"started_at": iso_ms(now), "night_date": night["date"], "reset": calc["weekly"]["reset"],
               "used_at_start": effective,
               "allowance_pct": night["allowance_pct"], "surplus_pct": calc["surplus_pct"],
               "last_before_reset": night["last_before_reset"], "pool_spent_pct": 0.0}
        queue["run"] = run
        store.touch("queue")
    if not run:
        return {"mode": "autonomous", "action": "stop", "may_start_next_item": False, "max_parallel_agents": 0,
                "max_heavy_runs": calc["host"]["max_heavy_runs"], "allowed_models": [],
                "budget_for_next_item_pct": 0.0, "next_item": None, "wait_until": None,
                "reasons": ["no queue run: start one with auto --start-run"], "run": None,
                "stale": calc["data"]["stale"], "weekly_effective_pct": effective,
                "five_hour_used_pct": calc["five_hour"]["used_pct"]}
    spent = max(0.0, effective - run["used_at_start"])
    if not (local.date().isoformat() > run["night_date"]
            or (local.date().isoformat() == run["night_date"] and local.hour >= profile["night_hours"][1])):
        run["last_effective"] = effective
        run["last_seen"] = iso_ms(now)
        store.touch("queue")
    budget = max(0.0, run["allowance_pct"] - spent - calc["outstanding_reservations_pct"])
    reasons, action, wait_until = [], "start", None
    commit = calc["host"]["free_commit_mb"]
    five = calc["five_hour"]
    stop = None
    if commit is not None and commit < profile["min_free_commit_mb"]:
        stop = f"free commit {commit / 1024:.1f} GB < {profile['min_free_commit_mb'] / 1024:.0f} GB"
    elif (local.date().isoformat() == run["night_date"] and local.hour >= profile["night_hours"][1]) \
            or local.date().isoformat() > run["night_date"]:
        stop = "night ended"
    running_ids = {i["id"] for i in queue["items"] + pool["items"] if i["status"] == "running"}
    item, why = pick_next(queue["items"], budget, running_ids)
    source = "queue"
    pool_ok = (run.get("last_before_reset") and pool.get("approved_reset") == calc["weekly"]["reset"])
    if item is None and pool_ok:
        pool_budget = min(budget, max(0.0, run["surplus_pct"] - run.get("pool_spent_pct", 0.0)))
        pitem, pwhy = pick_next(pool["items"], pool_budget, running_ids)
        if pitem is not None or why in ("empty", "blocked"):
            item, why, source = pitem, pwhy, "pool"
    if stop is None and item is None:
        if why == "empty":
            stop = "queue empty"
        elif why == "blocked":
            stop = "all remaining items need owner permission"
        elif why == "budget":
            stop = "night budget exhausted"
        else:
            action = "wait"
            reasons.append("waiting for running items or dependencies")
    if stop:
        action = "stop"
        reasons.insert(0, stop)
    if action == "start":
        if calc["data"]["stale"]:
            action = "wait"
            reasons.append(f"usage data stale ({calc['data']['age_min']} min)")
        if five["used_pct"] >= min(five["threshold_pct"], five["hard_stop_pct"]):
            action = "wait"
            wait_until = five["reset"]
            reasons.append(f"5h {five['used_pct']:.0f}% >= {five['threshold_pct']:.0f}%, wait for window reset")
    heavy = calc["host"]["max_heavy_runs"]
    agents = 0 if action == "stop" else max(1, min(int(profile["max_agents"]), heavy if heavy is not None else 3))
    throttle = effective >= profile["throttle_weekly_pct"] and calc["weekly"]["hours_to_reset"] > 24
    models = allowed_models(calc, profile, throttle, item["estimate_pct"] if item else 0.0)
    may = action == "start" and item is not None and bool(models)
    if action == "start" and not models:
        action = "wait"
        reasons.append("no model has weekly headroom")
    return {"mode": "autonomous", "action": action, "may_start_next_item": may,
            "max_parallel_agents": agents, "max_heavy_runs": heavy, "allowed_models": models,
            "budget_for_next_item_pct": round(budget, 3), "next_item": dict(item_view(item), source=source) if item else None,
            "wait_until": wait_until, "reasons": reasons,
            "run": {"night_date": run["night_date"], "allowance_pct": run["allowance_pct"], "spent_pct": round(spent, 3),
                    "last_before_reset": run["last_before_reset"], "started_at": run["started_at"],
                    "reused": reused, "remaining_pct": round(max(0.0, run["allowance_pct"] - spent), 3)},
            "stale": calc["data"]["stale"], "weekly_effective_pct": effective,
            "five_hour_used_pct": five["used_pct"]}


def plans_starting_tomorrow(profile, now, tz):
    today = now.astimezone(tz).date()
    return [p for p in profile["plans"]
            if parse_iso(p["start"]).astimezone(tz).date() == today + DAY and p.get("confirmed_on") != today.isoformat()]


def mutate(ctx, fn, need_calc=True):
    """Run fn(store) under the lock, save, then print one confirmation line with the new budget."""
    with locked(ctx.sd):
        store = Store(ctx.sd)
        housekeeping(store, ctx.now)
        line, payload, code = fn(store)
        store.save()
    if need_calc and code in (EXIT_OK,):
        try:
            calc = ctx.calc(store)
            line = f"{line}; {budget_suffix(calc)}"
            payload = dict(payload, budget_per_hour_pct=calc["budget_per_hour_pct"],
                           night_allowance_pct=calc["night"]["allowance_pct"])
        except PaceError as error:
            line = f"{line}; budget unavailable ({error})"
    emit(ctx.args, line, payload)
    return code


def find_plan(profile, plan_id):
    for p in profile["plans"]:
        if p["id"].lower() == plan_id.lower():
            return p
    raise PaceError(f"no plan {plan_id}")


def cmd_plan(ctx):
    a = ctx.args

    def zone(store):
        return load_zone(store["profile"]["timezone"])

    if a.plan_cmd == "add":
        def fn(store):
            profile = store["profile"]
            tz = zone(store)
            start, end = parse_local(a.start, tz, ctx.now), parse_local(a.end, tz, ctx.now, end=True)
            if end <= start:
                raise PaceError("plan end must be after its start")
            if end <= ctx.now:
                raise PaceError("plan lies in the past; past absences need no declaration")
            weight = a.weight if a.weight is not None else (0.0 if a.type == "away" else profile["heavy_weight"])
            plan = {"id": f"P{profile['next_plan_id']}", "type": a.type, "start": start.astimezone(tz).isoformat(),
                    "end": end.astimezone(tz).isoformat(),
                    "phrase": a.phrase or "", "declared_at": iso_ms(ctx.now), "weight": weight, "confirmed_on": None}
            profile["next_plan_id"] += 1
            profile["plans"].append(plan)
            store.touch("profile")
            return (f"plan {plan['id']} {a.type} {fmt_local(start, tz)} .. {fmt_local(end, tz)} added",
                    {"plan": plan_view(plan, tz)}, EXIT_OK)
        return mutate(ctx, fn)
    if a.plan_cmd in ("list", "find", "tomorrow"):
        with locked(ctx.sd):
            store = Store(ctx.sd)
            housekeeping(store, ctx.now)
            store.save()
        tz = zone(store)
        plans = store["profile"]["plans"]
        if a.plan_cmd == "tomorrow":
            plans = plans_starting_tomorrow(store["profile"], ctx.now, tz)
        elif a.plan_cmd == "find":
            if a.text:
                plans = [p for p in plans if a.text.lower() in (p.get("phrase") or "").lower()]
            if a.type:
                plans = [p for p in plans if p["type"] == a.type]
            if a.on:
                day0 = parse_local(a.on, tz, ctx.now)
                day1 = parse_local(a.on, tz, ctx.now, end=True)
                plans = [p for p in plans if parse_iso(p["start"]) < day1 and parse_iso(p["end"]) > day0]
        views = [plan_view(p, tz) for p in sorted(plans, key=lambda p: p["start"])]
        lines = [f"{p['id']} {p['type']} x{p['weight']} {p['start_local']} .. {p['end_local']} \"{p['phrase']}\""
                 f" declared {p['declared_at'][:16]}" + (f" confirmed {p['confirmed_on']}" if p.get("confirmed_on") else "")
                 for p in views]
        text = "\n".join(lines) if lines else "no plans"
        emit(a, text, {"plans": views, "count": len(views)})
        if a.plan_cmd == "find":
            return EXIT_OK if len(views) == 1 else EXIT_SEVERAL if views else EXIT_NONE
        return EXIT_OK
    if a.plan_cmd == "modify":
        def fn(store):
            tz = zone(store)
            plan = find_plan(store["profile"], a.id)
            start = parse_local(a.start, tz, ctx.now) if a.start else parse_iso(plan["start"])
            end = parse_local(a.end, tz, ctx.now, end=True) if a.end else parse_iso(plan["end"])
            if end <= start:
                raise PaceError("plan end must be after its start")
            plan["start"], plan["end"] = start.astimezone(tz).isoformat(), end.astimezone(tz).isoformat()
            if a.weight is not None:
                plan["weight"] = a.weight
            if a.phrase:
                plan["phrase"] = a.phrase
            plan["declared_at"] = iso_ms(ctx.now)
            plan["confirmed_on"] = None
            store.touch("profile")
            removed = prune_plans(store["profile"], ctx.now)
            note = " (now in the past, removed)" if removed else ""
            return (f"plan {plan['id']} now {fmt_local(start, tz)} .. {fmt_local(end, tz)}{note}",
                    {"plan": plan_view(plan, tz)}, EXIT_OK)
        return mutate(ctx, fn)
    if a.plan_cmd == "cancel":
        def fn(store):
            plan = find_plan(store["profile"], a.id)
            store["profile"]["plans"].remove(plan)
            store.touch("profile")
            return f"plan {plan['id']} cancelled", {"cancelled": plan}, EXIT_OK
        return mutate(ctx, fn)
    if a.plan_cmd == "confirm":
        def fn(store):
            tz = zone(store)
            plan = find_plan(store["profile"], a.id)
            plan["confirmed_on"] = ctx.now.astimezone(tz).date().isoformat()
            store.touch("profile")
            return f"plan {plan['id']} confirmed for {plan['confirmed_on']}", {"plan": plan_view(plan, tz)}, EXIT_OK
        return mutate(ctx, fn, need_calc=False)
    raise PaceError("unknown plan command")


def read_profile(sd):
    profile = {}
    with contextlib.suppress(PaceError):
        profile = read_json(sd / "pace-profile.json", {}) or {}
    migrate_profile(profile)
    return profile


def current_effective(ctx, allow_merge=True):
    usage = ctx.get_usage(read_profile(ctx.sd), allow_merge=allow_merge)
    record = usage.get("record")
    if not record or not record.get("seven_day"):
        return None
    return window_effective(record, ctx.now, ctx.model)[0]


def cmd_reserve(ctx):
    a = ctx.args
    if a.res_cmd == "acquire":
        if a.estimate is None and a.kind != "subagent":
            raise PaceError(f"--estimate is required for kind {a.kind}")
        effective = current_effective(ctx)

        def fn(store):
            reg = store["registry"]
            if a.key:
                held = next((r for r in reg["reservations"]
                             if r.get("key") == a.key and r.get("session_id") == ctx.session), None)
                if held:
                    held["renewed_at"] = iso_ms(ctx.now)
                    store.touch("registry")
                    return f"already reserved {held['id']} for key {a.key}", {"reservation": held}, EXIT_OK
            estimate = a.estimate if a.estimate is not None else float(store["profile"]["subagent_estimate_pct"])
            pid = a.pid if a.pid is not None else claude_pid()
            image = process_image(pid) or None
            r = {"id": f"R-{uuid.uuid4().hex[:8]}", "kind": a.kind, "label": a.label or "", "key": a.key,
                 "session_id": ctx.session, "pid": pid, "image": image, "estimate_pct": estimate,
                 "acquired_at": iso_ms(ctx.now), "renewed_at": iso_ms(ctx.now),
                 "used_at_acquire": effective if effective is not None else 0.0, "item_id": a.item}
            reg["reservations"].append(r)
            mark_overlap(reg)
            store.touch("registry")
            total = outstanding_pct(reg, effective or 0.0)
            return (f"reserved {r['id']} {estimate:.2f}% ({a.kind}, pid {pid}), outstanding {total:.2f}%",
                    {"reservation": r, "outstanding_pct": total}, EXIT_OK)
        return mutate(ctx, fn)
    if a.res_cmd == "renew":
        def fn(store):
            for r in store["registry"]["reservations"]:
                if r["id"] == a.id:
                    r["renewed_at"] = iso_ms(ctx.now)
                    store.touch("registry")
                    return f"renewed {r['id']} until {iso_ms(ctx.now + dt.timedelta(minutes=store['profile']['lease_ttl_min']))}", {"reservation": r}, EXIT_OK
            return f"no active reservation {a.id} (expired or released)", {"reservation": None}, EXIT_NONE
        return mutate(ctx, fn, need_calc=False)
    if a.res_cmd in ("release", "release-session"):
        if a.res_cmd == "release" and bool(a.id) == bool(a.key):
            raise PaceError("release needs a reservation id or --key, not both")
        effective = current_effective(ctx, allow_merge=a.res_cmd == "release")

        def fn(store):
            reg = store["registry"]
            if a.res_cmd == "release" and a.key:
                targets = [r for r in reg["reservations"]
                           if r.get("key") == a.key and r.get("session_id") == ctx.session]
            elif a.res_cmd == "release":
                targets = [r for r in reg["reservations"] if r["id"] == a.id]
            else:
                sid = a.target_session or ctx.session
                if not sid:
                    raise PaceError("release-session needs --session")
                targets = [r for r in reg["reservations"] if r.get("session_id") == sid]
            if not targets:
                return "nothing to release", {"released": []}, EXIT_NONE if a.res_cmd == "release" else EXIT_OK
            entries = []
            for r in targets:
                entries.append(close_reservation(reg, r, ctx.now, effective,
                                                 getattr(a, "actual", None), "released"))
                reg["reservations"].remove(r)
            store.touch("registry")
            summary = ", ".join(f"{e['id']} est {e['estimate_pct']:.2f}% act "
                                f"{'?' if e['actual_pct'] is None else format(e['actual_pct'], '.2f')}%" for e in entries)
            return f"released {summary}", {"released": entries}, EXIT_OK
        return mutate(ctx, fn, need_calc=a.res_cmd == "release")
    if a.res_cmd == "prune":
        def fn(store):
            dropped = prune_reservations(store["registry"], store["profile"], ctx.now)
            store.touch("registry")
            return (f"pruned {len(dropped)} reservation(s)",
                    {"pruned": [{"id": r["id"], "reason": why} for r, why in dropped]}, EXIT_OK)
        with locked(ctx.sd):
            store = Store(ctx.sd)
            prune_plans(store["profile"], ctx.now)
            line, payload, code = fn(store)
            store.save()
        emit(a, line, payload)
        return code
    if a.res_cmd == "list":
        with locked(ctx.sd):
            store = Store(ctx.sd)
            housekeeping(store, ctx.now)
            store.save()
        rs = store["registry"]["reservations"]
        text = "\n".join(f"{r['id']} {r['kind']} {r['estimate_pct']:.2f}% pid {r['pid']} session {r['session_id']} "
                         f"renewed {r['renewed_at']} {r['label']}" for r in rs) or "no active reservations"
        emit(a, text, {"reservations": rs})
        return EXIT_OK
    if a.res_cmd == "reconcile":
        with locked(ctx.sd):
            store = Store(ctx.sd)
        done = [c for c in store["registry"]["completed"] if c.get("actual_pct") is not None and c.get("estimate_pct")]
        by_kind = {}
        for c in done:
            by_kind.setdefault(c["kind"], []).append(c)
        summary = {}
        for kind, rows in by_kind.items():
            clean = [r for r in rows if not r.get("overlapped")] or rows
            ratios = [r["actual_pct"] / r["estimate_pct"] for r in clean]
            summary[kind] = {"count": len(rows), "clean": len([r for r in rows if not r.get("overlapped")]),
                             "estimate_sum_pct": round(sum(r["estimate_pct"] for r in rows), 3),
                             "actual_sum_pct": round(sum(r["actual_pct"] for r in rows), 3),
                             "median_ratio": round(statistics.median(ratios), 2)}
        text = "\n".join(f"{k}: {v['count']} done, est {v['estimate_sum_pct']:.2f}% act {v['actual_sum_pct']:.2f}%,"
                         f" median actual/estimate {v['median_ratio']}" for k, v in summary.items()) or "no completed reservations"
        emit(a, text, {"by_kind": summary, "completed": done[-50:]})
        return EXIT_OK
    raise PaceError("unknown reserve command")


def cmd_queue(ctx):
    a = ctx.args
    key = "pool" if a.pool else "queue"
    prefix = "L" if a.pool else "Q"
    if a.q_cmd == "add":
        def fn(store):
            data = store[key]
            deps = [d.strip() for d in (a.depends or "").split(",") if d.strip()]
            known = {i["id"] for i in data["items"]}
            missing = [d for d in deps if d not in known]
            if missing:
                raise PaceError(f"unknown dependencies: {', '.join(missing)}")
            item = {"id": f"{prefix}{data['next_id']}", "title": a.title, "repo": a.repo, "estimate_pct": a.estimate,
                    "independent": a.independent, "depends_on": deps, "status": "pending", "reason": None,
                    "attempts": 0, "added_at": iso_ms(ctx.now), "started_at": None, "finished_at": None,
                    "actual_pct": None, "reservation_id": None, "used_at_start": None}
            data["next_id"] += 1
            data["items"].append(item)
            store.touch(key)
            pending = sum(i["estimate_pct"] for i in data["items"] if i["status"] == "pending")
            return (f"{key} {item['id']} added ({a.estimate:.2f}%), pending total {pending:.2f}%",
                    {"item": item, "pending_total_pct": pending}, EXIT_OK)
        return mutate(ctx, fn)
    if a.q_cmd in ("list", "report"):
        with locked(ctx.sd):
            store = Store(ctx.sd)
        data = store[key]
        items = data["items"]
        if a.q_cmd == "report":
            since = parse_iso(data.get("run", {}).get("started_at")) if key == "queue" and data.get("run") else None
            touched = [i for i in items if since is None or (i.get("finished_at") and parse_iso(i["finished_at"]) >= since)
                       or i["status"] in ("running", "pending")]
            report = {"run": data.get("run"),
                      "done": [item_view(i) for i in touched if i["status"] == "done"],
                      "deferred": [item_view(i) for i in touched if i["status"] == "deferred"],
                      "running": [item_view(i) for i in touched if i["status"] == "running"],
                      "pending": [item_view(i) for i in touched if i["status"] == "pending"]}
            report["estimate_sum_pct"] = round(sum(i["estimate_pct"] for i in report["done"]), 3)
            report["actual_sum_pct"] = round(sum(i["actual_pct"] or 0 for i in report["done"]), 3)
            text = (f"done {len(report['done'])} (est {report['estimate_sum_pct']:.2f}% act {report['actual_sum_pct']:.2f}%),"
                    f" deferred {len(report['deferred'])}, running {len(report['running'])}, pending {len(report['pending'])}")
            emit(a, text, report)
            return EXIT_OK
        text = "\n".join(f"{i['id']} [{i['status']}] {i['estimate_pct']:.2f}% {'indep ' if i['independent'] else ''}"
                         f"deps={','.join(i['depends_on']) or '-'} {i['title']} ({i['repo']})"
                         + (f" reason: {i['reason']}" if i.get("reason") else "") for i in items) or f"{key} empty"
        payload = {"items": items}
        if a.pool:
            payload.update(approved_on=data.get("approved_on"), approved_reset=data.get("approved_reset"))
        emit(a, text, payload)
        return EXIT_OK
    if a.q_cmd == "approve":
        if not a.pool:
            raise PaceError("approve applies to the leftover pool: use --pool")

        def fn(store):
            record = ctx.get_usage(store["profile"]).get("record")
            reset = window_effective(record, ctx.now, ctx.model)[1] if record and record.get("seven_day") else None
            tz = load_zone(store["profile"]["timezone"])
            store["pool"]["approved_on"] = ctx.now.astimezone(tz).date().isoformat()
            store["pool"]["approved_reset"] = iso_ms(reset) if reset else None
            store.touch("pool")
            n = sum(1 for i in store["pool"]["items"] if i["status"] == "pending")
            return (f"leftover pool approved on {store['pool']['approved_on']} for the window ending "
                    f"{store['pool']['approved_reset']}, {n} pending item(s)", {"pool": store["pool"]}, EXIT_OK)
        return mutate(ctx, fn, need_calc=False)

    def find_item(data, item_id):
        for i in data["items"]:
            if i["id"].lower() == item_id.lower():
                return i
        raise PaceError(f"no {key} item {item_id}")

    needs_effective = a.q_cmd in ("start", "done", "fail", "defer")
    effective = None
    if needs_effective:
        effective = current_effective(ctx)

    def release_item(store, item, actual, why):
        reg = store["registry"]
        for r in list(reg["reservations"]):
            if r["id"] == item.get("reservation_id"):
                close_reservation(reg, r, ctx.now, effective, actual, why)
                reg["reservations"].remove(r)
                store.touch("registry")
        item["reservation_id"] = None

    if a.q_cmd == "start":
        def fn(store):
            data = store[key]
            item = find_item(data, a.id)
            if item["status"] != "pending":
                raise PaceError(f"{item['id']} is {item['status']}, not pending")
            pid = a.pid if a.pid is not None else claude_pid()
            r = {"id": f"R-{uuid.uuid4().hex[:8]}", "kind": "queue", "label": item["title"], "session_id": ctx.session,
                 "pid": pid, "image": process_image(pid) or None, "estimate_pct": item["estimate_pct"],
                 "acquired_at": iso_ms(ctx.now), "renewed_at": iso_ms(ctx.now),
                 "used_at_acquire": effective or 0.0, "item_id": item["id"]}
            store["registry"]["reservations"].append(r)
            mark_overlap(store["registry"])
            item.update(status="running", started_at=iso_ms(ctx.now), reservation_id=r["id"], used_at_start=effective)
            if key == "pool" and store["queue"].get("run"):
                store["queue"]["run"]["pool_spent_pct"] = store["queue"]["run"].get("pool_spent_pct", 0.0) + item["estimate_pct"]
                store.touch("queue")
            store.touch(key, "registry")
            return f"{item['id']} started, reservation {r['id']} {item['estimate_pct']:.2f}%", {"item": item, "reservation": r}, EXIT_OK
        return mutate(ctx, fn)
    if a.q_cmd == "done":
        def fn(store):
            data = store[key]
            item = find_item(data, a.id)
            actual = a.actual
            if actual is None and effective is not None and item.get("used_at_start") is not None:
                actual = max(0.0, effective - item["used_at_start"])
            release_item(store, item, actual, "done")
            item.update(status="done", finished_at=iso_ms(ctx.now), actual_pct=None if actual is None else round(actual, 3),
                        reason=None)
            store.touch(key)
            act = "?" if actual is None else f"{actual:.2f}"
            return f"{item['id']} done, estimate {item['estimate_pct']:.2f}% actual {act}%", {"item": item}, EXIT_OK
        return mutate(ctx, fn)
    if a.q_cmd in ("defer", "fail"):
        def fn(store):
            data = store[key]
            item = find_item(data, a.id)
            if a.q_cmd == "fail":
                item["attempts"] = item.get("attempts", 0) + 1
                limit = store["profile"]["gate_fix_attempts"]
                if item["attempts"] < limit:
                    store.touch(key)
                    return (f"{item['id']} failed attempt {item['attempts']}/{limit}, keep fixing",
                            {"item": item, "deferred": []}, EXIT_OK)
                reason = a.reason or f"gates failed after {limit} fix attempts"
            else:
                reason = a.reason or "needs owner permission"
            release_item(store, item, None, "deferred")
            deferred = defer_with_dependents(data["items"], item["id"], reason, ctx.now)
            for d in deferred:
                if d is not item and d.get("reservation_id"):
                    release_item(store, d, None, "deferred")
            store.touch(key)
            ids = ",".join(d["id"] for d in deferred)
            return f"deferred {ids}: {reason}", {"deferred": [item_view(d) for d in deferred]}, EXIT_OK
        return mutate(ctx, fn)
    if a.q_cmd == "retry":
        def fn(store):
            item = find_item(store[key], a.id)
            item.update(status="pending", reason=None, attempts=0, finished_at=None)
            store.touch(key)
            return f"{item['id']} back to pending", {"item": item}, EXIT_OK
        return mutate(ctx, fn, need_calc=False)
    if a.q_cmd == "remove":
        def fn(store):
            data = store[key]
            item = find_item(data, a.id)
            dependents = [i["id"] for i in data["items"] if item["id"] in i.get("depends_on", [])
                          and i["status"] in ("pending", "running")]
            if dependents:
                raise PaceError(f"{item['id']} has dependents {','.join(dependents)}; defer or remove them first")
            data["items"].remove(item)
            store.touch(key)
            return f"{item['id']} removed", {"removed": item}, EXIT_OK
        return mutate(ctx, fn, need_calc=False)
    if a.q_cmd == "clear-done":
        def fn(store):
            data = store[key]
            before = len(data["items"])
            data["items"] = [i for i in data["items"] if i["status"] != "done"]
            store.touch(key)
            return f"cleared {before - len(data['items'])} done item(s)", {}, EXIT_OK
        return mutate(ctx, fn, need_calc=False)
    raise PaceError("unknown queue command")


def cmd_calibrate(ctx):
    with locked(ctx.sd):
        store = Store(ctx.sd)
        store.save()
    history = read_history(ctx.sd)
    result = calibrate(history, store, ctx.now, store["profile"], ctx.model)
    if result.get("note"):
        text = result["note"]
    elif not result["proposals"]:
        text = f"{result['days']} days of history: no corrections proposed"
    else:
        text = "; ".join(f"{p['parameter']} {p['current']} -> {p['proposed']} ({p['evidence']})" for p in result["proposals"])
    emit(ctx.args, text, result)
    return EXIT_OK


PROFILE_INTERNAL_KEYS = ("version", "plans", "next_plan_id", "timezone_confirmed")
PROFILE_PERCENT_KEYS = {k for k in PROFILE_DEFAULTS if k.endswith("_pct")} | {
    "five_hour_threshold_day", "five_hour_threshold_night", "five_hour_hard_stop"}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_whole(value):
    return _is_number(value) and float(value).is_integer()


def parse_profile_value(text):
    """A `profile set` value: JSON when it parses, else the text itself."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def check_profile_value(key, value):
    """The value `profile set` stores for key, or PaceError with the reason it is refused."""
    if key not in PROFILE_DEFAULTS:
        raise PaceError(f"unknown key {key}; `paceline profile` lists the keys")
    if key in PROFILE_INTERNAL_KEYS:
        raise PaceError(f"{key} is set by paceline itself")
    default = PROFILE_DEFAULTS[key]
    if key == "timezone":
        if not isinstance(value, str) or not value.strip():
            raise PaceError("timezone must be the name of a time zone, for example Europe/Madrid")
        load_zone(value)
        return value
    if key in ("day_hours", "night_hours"):
        if (isinstance(value, list) and len(value) == 2 and all(_is_whole(h) for h in value)
                and 0 <= value[0] < value[1] <= 24):
            return [int(h) for h in value]
        raise PaceError(f"{key} must be two whole hours [start, end] with 0 <= start < end <= 24")
    if key == "reserve_decay_start":
        if (isinstance(value, dict) and set(value) == {"weekday", "hour"}
                and _is_whole(value["weekday"]) and 0 <= value["weekday"] <= 6
                and _is_whole(value["hour"]) and 0 <= value["hour"] <= 23):
            return {"weekday": int(value["weekday"]), "hour": int(value["hour"])}
        raise PaceError('reserve_decay_start must be {"weekday": W, "hour": H} with a weekday 0-6 and an hour '
                        "0-23 (Monday is 0)")
    if key in ("models", "routine_models"):
        if isinstance(value, list) and value and all(isinstance(m, str) and m.strip() for m in value):
            return value
        raise PaceError(f'{key} must be a list of model names, for example ["opus", "sonnet"]')
    if value is None and default is None:
        return None
    if not _is_number(value):
        raise PaceError(f"{key} must be a number" + (" or null" if default is None else ""))
    if isinstance(default, int) and not _is_whole(value):
        raise PaceError(f"{key} must be a whole number")
    if key in PROFILE_PERCENT_KEYS and not 0 <= value <= 100:
        raise PaceError(f"{key} must lie in 0-100")
    if value < 0:
        raise PaceError(f"{key} must not be negative")
    return int(value) if isinstance(default, int) else value


def timezone_hint(profile, detected, usable):
    """What to tell the owner while the profile counts in UTC and this machine is in another zone."""
    if profile.get("timezone") != "UTC" or profile.get("timezone_confirmed") or not detected or detected == "UTC":
        return None
    command = f"`paceline profile set timezone {detected}`"
    if usable:
        return (f"This machine is in {detected}, but the pace profile counts in UTC. Run {command} to count in "
                "this machine's zone, or `paceline profile set timezone UTC` to keep UTC.")
    return (f"This machine is in {detected}, but the pace profile counts in UTC, and Python here has no time zone "
            f"data for {detected}. Run `python -m pip install tzdata` first, then {command}.")


def profile_check(ctx, profile):
    detected, usable = detect_zone()
    confirmed = bool(profile.get("timezone_confirmed"))
    hint = timezone_hint(profile, detected, usable)
    result = {"timezone": profile.get("timezone"), "confirmed": confirmed, "detected": detected,
              "usable": usable, "hint": hint}
    if hint:
        line = hint
    else:
        line = (f"timezone {profile.get('timezone')} ({'confirmed' if confirmed else 'not confirmed'}); detected "
                + (f"{detected}" + ("" if usable else " (no time zone data for it here)") if detected else "nothing"))
    emit(ctx.args, line, result)
    return EXIT_OK


def profile_set(ctx):
    key, value = ctx.args.key, parse_profile_value(ctx.args.value)
    with locked(ctx.sd):
        store = Store(ctx.sd)
        try:
            new = check_profile_value(key, value)
        except PaceError as error:
            print(f"paceline: {error}; nothing changed", file=sys.stderr)
            return EXIT_USAGE
        profile = store["profile"]
        old = profile[key]
        profile[key] = new
        if key == "timezone":
            profile["timezone_confirmed"] = True
        store.touch("profile")
        store.save()
    change = f"{key}: {json.dumps(old, ensure_ascii=False)} -> {json.dumps(new, ensure_ascii=False)}"
    payload = {"key": key, "old": old, "new": new, "status_line": None, "status": None}
    try:
        line, calc = status_report(ctx, store)
        payload.update(status_line=line, status=calc)
    except PaceError as error:
        line = f"status unavailable: {error}"
    emit(ctx.args, f"{change}\n{line}", payload)
    return EXIT_OK


def cmd_profile(ctx):
    action = getattr(ctx.args, "profile_cmd", None) or "show"
    if action == "set":
        return profile_set(ctx)
    with locked(ctx.sd):
        store = Store(ctx.sd)
        store.save()
    if action == "check":
        return profile_check(ctx, store["profile"])
    profile = {k: v for k, v in store["profile"].items() if k != "plans"}
    emit(ctx.args, json.dumps(profile, ensure_ascii=False), profile)
    return EXIT_OK


def cmd_merge(ctx):
    """One merge step on demand. Silent unless --verbose; a busy lock still exits 0."""
    verbose = ctx.args.verbose or ctx.args.json
    try:
        allowed = not (ctx.args.no_merge or os.environ.get("PACELINE_NO_MERGE"))
        record, merged, error = update_usage(ctx.sd, ctx.now, allowed, lock_wait=MERGE_LOCK_WAIT_SEC)
        if verbose:
            result = {"merged": merged, "error": None if merged else error,
                      "observed_at": record and record.get("observed_at"), "source": record and record.get("source")}
            emit(ctx.args, f"merge: {'ok' if merged else error} | rate-limits.json "
                           f"{result['observed_at'] or 'not written'} {result['source'] or ''}".rstrip(), result)
        return EXIT_OK
    except Exception as error:  # a merge run by a scheduler must never end in a traceback
        if verbose:
            print(f"paceline merge: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR


SWITCH_ON_NOTE = ("the hooks now add a pace: line before every Agent and Workflow launch (an ask prompt at a hard "
                  "limit), hold a reservation for each subagent, and remind at session start of unconfirmed plans "
                  "and leftover tasks")
SWITCH_OFF_NOTE = "the hooks add nothing; every paceline command still runs"


def cmd_switch(ctx):
    on = ctx.args.cmd == "on"
    limits_file.set_pace(ctx.sd, on)
    line = f"pace is {'on' if on else 'off'}: {SWITCH_ON_NOTE if on else SWITCH_OFF_NOTE}"
    emit(ctx.args, line, {"pace": "on" if on else "off", "switch": str(ctx.sd / limits_file.SWITCH_FILE)})
    return EXIT_OK


# ---------------------------------------------------------------- doctor

DOCTOR_LOG_LINES = 20
BUNDLE_HISTORY_LINES = 50
BUNDLE_RESERVATIONS = 20
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def probe(name, fn, *args):
    """Run one doctor probe; a probe that raises reports `fail` with the error instead of ending the report."""
    try:
        status, evidence = fn(*args)
    except Exception as error:  # noqa: BLE001 - one broken probe must not hide the others
        status, evidence = "fail", {"error": f"{type(error).__name__}: {error}",
                                    "traceback": traceback.format_exc(limit=5)}
    return {"name": name, "status": status, "evidence": limits_file.redact(evidence)}


def probe_runtime():
    floor = limits_file.require_python()
    evidence = {"os": platform.platform(), "python": sys.version.split()[0], "executable": sys.executable,
                "python3_on_path": shutil.which("python3"), "python_on_path": shutil.which("python"),
                "paceline_on_path": shutil.which("paceline"), "floor": floor}
    if floor:
        return "fail", evidence
    # paceline is on PATH only in a session's Bash tool, so its absence elsewhere is expected; the hooks
    # need python3 or python on PATH everywhere.
    return ("ok" if evidence["python3_on_path"] or evidence["python_on_path"] else "warn"), evidence


def probe_paths(sd):
    config = config_dir()
    exists = sd.is_dir()
    # A state directory that doesn't exist yet is made on the first write; what matters is whether it can be.
    nearest = next((d for d in [sd, *sd.parents] if d.is_dir()), None)
    writable = nearest is not None and os.access(nearest, os.W_OK)
    evidence = {"config_dir": str(config), "state_dir": str(sd), "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR"),
                "PACELINE_STATE_DIR": os.environ.get("PACELINE_STATE_DIR"), "state_dir_exists": exists,
                "writable": writable, "checked_at": str(nearest) if nearest else None}
    if not writable:
        return "fail", evidence
    return ("ok" if exists else "warn"), evidence


def probe_switch(sd):
    switch = sd / limits_file.SWITCH_FILE
    on = limits_file.pace_enabled(sd)
    evidence = {"pace": "on" if on else "off", "switch_file": str(switch), "exists": switch.exists()}
    if not on:
        evidence["hint"] = "pace is off: the hooks add nothing to a session. `paceline on` turns them on."
    return "ok", evidence


def probe_inbox(sd, profile, now, live):
    inbox = sd / limits_file.INBOX_DIR
    files = sorted(inbox.glob("*.json")) if inbox.is_dir() else []
    times = [t for t in (safe_iso((limits_file.read_json(f) or {}).get("observed_at")) for f in files) if t]
    newest = max(times, default=None)
    age = round((now - newest).total_seconds() / 60, 1) if newest else None
    evidence = {"inbox": str(inbox), "files": len(files), "newest_observed_at": iso_ms(newest) if newest else None,
                "age_min": age}
    # Opening the lock creates it, so a state directory not made yet is left alone without --live.
    evidence["lock_free"] = None
    if sd.is_dir():
        with limits_file.try_locked(sd) as held:
            evidence["lock_free"] = held
    if live:
        _, merged, error = update_usage(sd, now, lock_wait=MERGE_LOCK_WAIT_SEC)
        evidence["live"] = {"merged": merged, "error": error}
    if age is None or age > profile["stale_threshold_min"]:
        evidence["hint"] = ("No recent reading in the inbox: it fills after each answer in a session with paceline "
                            "loaded, so it stays empty while no session runs.")
        return "warn", evidence
    return "ok", evidence


def probe_limits(sd, now):
    record = read_limits(sd)
    if record is None:
        return "fail", {"rate_limits_json": "missing or unreadable"}
    buckets = {}
    oldest = None
    for name, bucket in record.items():
        if name in RECORD_META or not is_bucket(bucket):
            continue
        at = bucket_at(bucket, record)
        age = round((now - at).total_seconds() / 60, 1) if at else None
        reason = limits_file.implausible({name: dict(bucket, observed_at=bucket.get("observed_at") or record.get("observed_at"))},
                                         limits_file.wall_clock())
        buckets[name] = {"used_percentage": bucket["used_percentage"], "resets_at": bucket.get("resets_at"),
                         "source": bucket.get("source") or record.get("source"), "age_min": age, "implausible": reason}
        if age is not None:
            oldest = age if oldest is None else max(oldest, age)
    history = 0
    with contextlib.suppress(OSError):
        history = len((sd / "rate-limits.history.jsonl").read_text(encoding="utf-8").splitlines())
    evidence = {"buckets": buckets, "scoped": record.get("scoped"), "history_lines": history}
    stale = oldest is None or oldest > 30 or any(b["implausible"] for b in buckets.values())
    return ("warn" if stale else "ok"), evidence


def probe_process():
    table = process_table()
    chain, pid = claude_ancestors(table)
    evidence = {"table_size": len(table), "chain": [{"pid": p, "ppid": pp, "name": n} for p, pp, n in chain],
                "claude_pid": pid, "fallback_pid": os.getppid()}
    if os.name != "nt":
        raw = run_command(["ps", "-A", "-o", "pid=,ppid=,comm="]) or run_command(["ps", "-o", "pid,ppid,comm"])
        evidence["ps_head"] = (raw or "").splitlines()[:5] if raw else None
        evidence["proc_available"] = Path("/proc/self/stat").exists()
    return ("ok" if pid is not None else "warn"), evidence


def probe_memory():
    evidence = {"free_memory_mb": free_memory_mb(), "override": os.environ.get("PACELINE_FREE_COMMIT_MB")}
    if os.name == "nt":
        evidence["source"] = "GlobalMemoryStatusEx().ullAvailPageFile"
    elif sys.platform == "darwin":
        evidence["source"] = "sysctl kern.memorystatus_level x hw.memsize"
        evidence["kern.memorystatus_level"] = run_command(["sysctl", "-n", "kern.memorystatus_level"])
        evidence["hw.memsize"] = run_command(["sysctl", "-n", "hw.memsize"])
    else:
        evidence["source"] = "/proc/meminfo MemAvailable"
        with contextlib.suppress(OSError):
            evidence["meminfo"] = [line for line in Path("/proc/meminfo").read_text(encoding="ascii", errors="replace")
                                   .splitlines() if line.startswith(("MemTotal", "MemAvailable"))]
    return ("ok" if evidence["free_memory_mb"] is not None else "warn"), evidence


def probe_timezone(profile, now):
    tz = load_zone(profile["timezone"])
    return "ok", {"profile_timezone": profile["timezone"], "zone": type(tz).__name__, "now_local": fmt_local(now, tz)}


def probe_hooks():
    evidence = {"plugin_hooks": None}
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        evidence["plugin_hooks"] = sorted(json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"])
    return "ok", evidence


def probe_debug_log(sd):
    log = sd / limits_file.DEBUG_LOG
    lines = []
    with contextlib.suppress(OSError):
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-DOCTOR_LOG_LINES:]
    evidence = {"tracing": limits_file.debug_enabled(sd), "flag_file": str(sd / limits_file.DEBUG_FLAG),
                "log": str(log), "last_lines": lines}
    failures = [line for line in lines if '"failure":true' in line]
    return ("warn" if failures else "ok"), evidence


def doctor_report(ctx, profile, live):
    sd = ctx.sd
    # The live merge runs before the limits probe, so that probe shows what the merge wrote.
    inbox = probe("inbox", probe_inbox, sd, profile, ctx.now, live)
    probes = [
        probe("runtime", probe_runtime),
        probe("paths", probe_paths, sd),
        probe("switch", probe_switch, sd),
        inbox,
        probe("limits", probe_limits, sd, ctx.now),
        probe("process", probe_process),
        probe("memory", probe_memory),
        probe("timezone", probe_timezone, profile, ctx.now),
        probe("hooks", probe_hooks),
        probe("debug_log", probe_debug_log, sd),
    ]
    return {"generated_at": iso_ms(limits_file.wall_clock()), "paceline_version": plugin_version(), "probes": probes}


def plugin_version():
    with contextlib.suppress(OSError, ValueError):
        return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    return None


def write_bundle(path, report, sd):
    """One zip for the maintainer: the report, the debug log and the state a budget is replayed from."""
    def text(name):
        with contextlib.suppress(OSError):
            return (sd / name).read_text(encoding="utf-8", errors="replace")
        return None

    registry = {}
    with contextlib.suppress(PaceError):
        registry = read_json(sd / "pace-registry.json", {}) or {}
    members = {
        "doctor.json": json.dumps(report, indent=2, ensure_ascii=False, default=str),
        "debug.log": text(limits_file.DEBUG_LOG),
        "debug.log.1": text(limits_file.DEBUG_LOG + ".1"),
        "pace-profile.json": text("pace-profile.json"),
        "pace.json": text(limits_file.SWITCH_FILE),
        "rate-limits.json": text("rate-limits.json"),
        "rate-limits.history.tail.jsonl": "\n".join(((text("rate-limits.history.jsonl") or "").splitlines())
                                                    [-BUNDLE_HISTORY_LINES:]),
        "pace-registry.json": json.dumps({
            "reservations": registry.get("reservations", [])[-BUNDLE_RESERVATIONS:],
            "reservation_count": len(registry.get("reservations", [])),
            "completed_count": len(registry.get("completed", [])),
            "completed_last": registry.get("completed", [])[-BUNDLE_RESERVATIONS:]}, indent=2, default=str),
    }
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            if body is not None:
                zf.writestr(name, limits_file.redact(body))
    return path


def cmd_doctor(ctx):
    try:
        profile = read_profile(ctx.sd)
    except PaceError:
        profile = json.loads(json.dumps(PROFILE_DEFAULTS))
    report = doctor_report(ctx, profile, ctx.args.live)
    bundle = None
    if ctx.args.bundle:
        bundle = write_bundle(ctx.args.bundle, report, ctx.sd)
        report["bundle"] = str(bundle)
    lines = [f"{p['status']:<4}  {p['name']}" for p in report["probes"]]
    lines += [f"{p['name']}: {p['evidence']['hint']}" for p in report["probes"] if p["evidence"].get("hint")]
    worst = [p["name"] for p in report["probes"] if p["status"] != "ok"]
    lines.append(f"doctor: {len(report['probes']) - len(worst)} ok, {len(worst)} to look at"
                 + (f" ({', '.join(worst)})" if worst else "") + (f"; bundle {bundle}" if bundle else "")
                 + ("" if ctx.args.json else "; --json shows the evidence"))
    emit(ctx.args, "\n".join(lines), report)
    return EXIT_OK


def build_parser():
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", help="print the full result as JSON")
    common.add_argument("--model", help="current model id; selects a model-scoped weekly bucket when the limits file has one")
    common.add_argument("--session", help="Claude session id (default: $CLAUDE_SESSION_ID)")
    common.add_argument("--no-merge", action="store_true", help="do not merge the inbox readings into rate-limits.json first")
    common.add_argument("--now", help="override the clock (ISO 8601), for what-if and tests")
    common.add_argument("--state-dir", help="state directory (default: $PACELINE_STATE_DIR, else ~/.claude/state/paceline)")

    parser = argparse.ArgumentParser(
        prog="paceline", parents=[common],
        description="Subscription usage pacing, plus the cache-watch, context-watch and usage-watch groups.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("on", parents=[common], help="turn the pace hooks on")
    sub.add_parser("off", parents=[common], help="turn the pace hooks off (every command still runs)")
    sub.add_parser("status", parents=[common], help="interactive one-line recommendation")
    sub.add_parser("auto", parents=[common], help="autonomous parameters for the next queue item").add_argument(
        "--start-run", action="store_true", help="begin a new night run: fix tonight's allowance and baseline")
    sub.add_parser("calibrate", parents=[common], help="propose parameter corrections from history")
    prof = sub.add_parser("profile", parents=[common],
                          help="print, change or check the profile (creates it with the detected time zone)")
    prsub = prof.add_subparsers(dest="profile_cmd")
    prsub.add_parser("show", parents=[common], help="print the profile (the default)")
    p = prsub.add_parser("set", parents=[common], help="change one key, with checks")
    p.add_argument("key")
    p.add_argument("value", help="JSON when it parses, else text, for example Europe/Madrid or '[8, 24]'")
    prsub.add_parser("check", parents=[common], help="the time zone against this machine's, with a hint")
    doctor = sub.add_parser("doctor", parents=[common], help="diagnose pace on this machine; read-only without --live")
    doctor.add_argument("--live", action="store_true", help="also run one merge under the normal lock")
    doctor.add_argument("--bundle", metavar="PATH", help="write the report, the debug log and the state to a zip")
    sub.add_parser("merge", parents=[common], help="merge the sessions' readings from the inbox into rate-limits.json"
                   ).add_argument("--verbose", action="store_true", help="print one result line (default: silent)")

    plan = sub.add_parser("plan", parents=[common], help="declared future plan changes")
    psub = plan.add_subparsers(dest="plan_cmd", required=True)
    p = psub.add_parser("add", parents=[common])
    p.add_argument("--type", choices=["away", "heavy"], required=True)
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--weight", type=float)
    p.add_argument("--phrase", default="")
    psub.add_parser("list", parents=[common])
    p = psub.add_parser("find", parents=[common])
    p.add_argument("--text")
    p.add_argument("--on")
    p.add_argument("--type", choices=["away", "heavy"])
    p = psub.add_parser("modify", parents=[common])
    p.add_argument("id")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--weight", type=float)
    p.add_argument("--phrase")
    for name in ("cancel", "confirm"):
        psub.add_parser(name, parents=[common]).add_argument("id")
    psub.add_parser("tomorrow", parents=[common])

    res = sub.add_parser("reserve", parents=[common], help="reservation registry")
    rsub = res.add_subparsers(dest="res_cmd", required=True)
    r = rsub.add_parser("acquire", parents=[common])
    r.add_argument("--kind", choices=["subagent", "workflow", "gate", "queue"], required=True)
    r.add_argument("--estimate", type=float,
                   help="estimated cost, %% of the weekly limit (subagent default: profile subagent_estimate_pct)")
    r.add_argument("--label", default="")
    r.add_argument("--key", help="caller's own id, such as a hook's agent_id: one active reservation per key and session")
    r.add_argument("--pid", type=int, help="owning Claude process (default: nearest claude* ancestor)")
    r.add_argument("--item", help="queue item id this reservation belongs to")
    rsub.add_parser("renew", parents=[common]).add_argument("id")
    r = rsub.add_parser("release", parents=[common])
    r.add_argument("id", nargs="?")
    r.add_argument("--key", help="release the reservation acquired with this --key in this session")
    r.add_argument("--actual", type=float, help="actual cost, %% of the weekly limit (default: measured delta)")
    r = rsub.add_parser("release-session", parents=[common])
    r.add_argument("--target-session", help="session whose reservations to release (default: --session)")
    for name in ("prune", "list", "reconcile"):
        rsub.add_parser(name, parents=[common])

    q = sub.add_parser("queue", parents=[common], help="night queue and leftover pool (--pool)")
    q.add_argument("--pool", action="store_true", default=argparse.SUPPRESS, help="operate on the leftover pool")
    qsub = q.add_subparsers(dest="q_cmd", required=True)
    qp = argparse.ArgumentParser(add_help=False, parents=[common])
    qp.add_argument("--pool", action="store_true", default=argparse.SUPPRESS, help="operate on the leftover pool")
    x = qsub.add_parser("add", parents=[qp])
    x.add_argument("--title", required=True)
    x.add_argument("--repo", required=True)
    x.add_argument("--estimate", type=float, required=True)
    x.add_argument("--independent", action="store_true")
    x.add_argument("--depends", help="comma-separated item ids")
    for name in ("list", "report", "approve", "clear-done"):
        qsub.add_parser(name, parents=[qp])
    x = qsub.add_parser("start", parents=[qp])
    x.add_argument("id")
    x.add_argument("--pid", type=int)
    x = qsub.add_parser("done", parents=[qp])
    x.add_argument("id")
    x.add_argument("--actual", type=float)
    for name in ("defer", "fail"):
        x = qsub.add_parser(name, parents=[qp])
        x.add_argument("id")
        x.add_argument("--reason")
    for name in ("retry", "remove"):
        qsub.add_parser(name, parents=[qp]).add_argument("id")

    # paceline.py hands these two groups to their own modules before this parser runs; they are listed
    # here so that --help shows them.
    for name, about in GROUPS.items():
        sub.add_parser(name, help=about, add_help=False).add_argument("rest", nargs=argparse.REMAINDER)
    return parser


GROUPS = {"cache-watch": "keep an idle session's prompt cache alive: on|off|status|config",
          "context-watch": "context against the auto-compaction point: config|status",
          "usage-watch": "read the account's exact usage through Claude Code: on|off|status"}


def cmd_group(ctx):
    if ctx.args.cmd == "cache-watch":
        import cache_watch
        return cache_watch.main(ctx.args.rest)
    if ctx.args.cmd == "usage-watch":
        import usage_watch
        return usage_watch.main(ctx.args.rest)
    import context_watch
    return context_watch.main(ctx.args.rest)


COMMON_DEFAULTS = {"json": False, "model": None, "session": None, "no_merge": False, "now": None,
                   "state_dir": None, "pool": False}


def main(argv=None):
    global TRACE_DIR
    too_old = limits_file.require_python()
    if too_old:
        print(f"paceline: {too_old}", file=sys.stderr)
        limits_file.trace_failure("pace.python", reason=too_old)
        return EXIT_ERROR
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    for key, value in COMMON_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    ctx = Context(args)
    TRACE_DIR = ctx.sd
    handlers = {"status": cmd_status, "auto": cmd_auto, "plan": cmd_plan, "reserve": cmd_reserve,
                "queue": cmd_queue, "calibrate": cmd_calibrate, "profile": cmd_profile,
                "merge": cmd_merge, "doctor": cmd_doctor, "on": cmd_switch, "off": cmd_switch,
                "cache-watch": cmd_group, "context-watch": cmd_group, "usage-watch": cmd_group}
    started, code = time.monotonic(), EXIT_ERROR
    try:
        code = handlers[args.cmd](ctx)
        return code
    except PaceError as error:
        print(f"paceline: {error}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:
        limits_file.trace_failure("pace.crash", ctx.sd, error=error, argv=sys.argv[1:] if argv is None else argv)
        raise
    finally:
        limits_file.trace("pace.command", ctx.sd, argv=sys.argv[1:] if argv is None else argv, exit_code=code,
                          ms=round((time.monotonic() - started) * 1000))


if __name__ == "__main__":
    sys.exit(main())
