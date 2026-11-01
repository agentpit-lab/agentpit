#!/usr/bin/env python3
"""Pace Claude subscription usage against the weekly and five-hour limits.

Stdlib only. See paceline's docs/reference.md for the CLI reference, exit codes and state file schemas.
"""
import argparse
import contextlib
import ctypes
import dataclasses
import datetime as dt
import json
import math
import os
import platform
import re
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
import advice  # noqa: E402
import away  # noqa: E402
import five_burn  # noqa: E402
import limits_file  # noqa: E402
import localzone  # noqa: E402
import week  # noqa: E402
from five_burn import (  # noqa: E402,F401
    CET_ZONES, EET_ZONES, STALE_THRESHOLD_MIN, EuropeanZone, _last_sunday, detect_zone, effective_zone,
    fmt_local,
)
from limits_file import (  # noqa: E402,F401
    FUTURE_SLACK, NO_PARTITION, RECORD_META, STALE_EPOCH, bucket_at, bucket_times, dated_record, history_account_filter,
    WEEK_PLAN_VERSION, is_bucket, parse_iso, pick_partition, read_history, safe_iso, week_plan_path,
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

MEMORY_GUARD_KEYS = ("min_free_commit_mb", "commit_reserve_mb", "heavy_job_mb", "max_heavy_runs")

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

# Values that tune the implementation rather than how the operator works (intent 0047): fixed here, not in
# the profile. pages/paceline-budget/port.js copies the ones it reads; the golden test keeps them equal.
LEASE_TTL_MIN = 30  # the lease of a hold without a key
ADVICE_REFRESH_FIVE_HOUR_PCT = 85.0  # from this five-hour usage, advice merges the inbox sooner:
ADVICE_REFRESH_CACHE_SEC = 125  # when the data is older than this
AGENT_LEASE_TTL_MIN = 240  # the lease of a keyed hold (a gate, a hook-held subagent)
USAGE_SNAPSHOT_MAX_AGE_SEC = 300  # a command merges the inbox first when the data is older
CAPACITY_WARN_RATIO = 0.9  # status warns when the week's rest reaches this share of the working hours' capacity
CALIBRATION_MIN_DAYS = 14  # the history calibrate needs
RESERVE_LOWER_UNUSED_PCT = 10.0  # calibrate proposes a lower reserve when the weeks left more than this unused

# Keys earlier versions kept in the profile; a stored profile that still has them loses them when read.
RETIRED_PROFILE_KEYS = (
    "heavy_weight", "night_base_pct", "host_night_capacity_pct", "lease_ttl_min", "agent_lease_ttl_min",
    "stale_threshold_min", "usage_snapshot_max_age_sec", "capacity_warn_ratio", "auto_refresh_five_hour_pct",
    "auto_refresh_cache_sec", "gate_fix_attempts", "calibration_min_days", "plans", "next_plan_id",
    # The five keys of the hour-slot model (retired by the week plan).
    "day_hours", "night_hours", "weekday_weight", "weekend_weight", "weekend_cap_pct",
    # The three reserve keys: the weekly reserve is kept per account in the plan file (RESERVE_DEFAULTS).
    "reserve_pct", "reserve_decay_start", "reserve_release_min_hours",
    # The advice layer (model choice, the throttle, the leftover suggestion).
    "models", "routine_models", "throttle_weekly_pct", "scoped_exhausted_pct", "leftover_suggest_pct",
    # The three five-hour keys, replaced by the one five_hour_stop_pct (no conversion of their values).
    "five_hour_threshold_day", "five_hour_threshold_night", "five_hour_hard_stop")


class PaceError(Exception):
    pass


class NoWeekError(PaceError):
    """A command that needs the week found no weekly reading (the account is unknown, or has no data yet)."""


# What a stored profile without the key reads as, where that differs from the default of a new profile: a
# stored zone may be a deliberate one, so an old profile keeps counting in it.
MIGRATION_VALUES = {"timezone_mode": "fixed"}


def migrate_profile(raw):
    """Add every key a stored profile lacks (MIGRATION_VALUES first, else its default) and drop the retired keys.
    Returns True when anything changed."""
    changed = False
    for k in RETIRED_PROFILE_KEYS:
        if k in raw:
            del raw[k]
            changed = True
    for k, v in PROFILE_DEFAULTS.items():
        if k not in raw:
            raw[k] = json.loads(json.dumps(MIGRATION_VALUES.get(k, v)))
            changed = True
    return changed


def load_stored_profile(path):
    """The profile at `path` with every key present: (profile, changed).

    One reader for every caller. No file yet is a copy of PROFILE_DEFAULTS (timezone_mode "machine", spec
    R6); a stored file lacking a key gets MIGRATION_VALUES (an old profile is "fixed").
    `changed` is True when the file is absent or a key was added. A file that is not valid JSON is a PaceError."""
    raw = read_json(path, None)
    if raw is None:
        return json.loads(json.dumps(PROFILE_DEFAULTS)), True
    if not isinstance(raw, dict):
        raise PaceError(f"{path.name} must hold one JSON object; left untouched, fix it by hand")
    return raw, migrate_profile(raw)


# ---------------------------------------------------------------- time zone

def load_zone(key, force_builtin=False):
    try:
        return five_burn.load_zone(key, force_builtin)
    except five_burn.ZoneUnavailable as error:
        raise PaceError(str(error)) from None


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
                    raise PaceError(f"{name} busy for {timeout:g} s; another paceline call is stuck")
                time.sleep(0.02)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                _unlock(fh)
    finally:
        fh.close()


class Store:
    """The two pace state files, read and written together under the lock."""

    FILES = {
        "profile": ("pace-profile.json", None),
        "holds": ("pace-holds.json", {"holds": [], "completed": []}),
    }

    def __init__(self, sd):
        self.sd = sd
        self.data = {}
        self.dirty = set()
        for key, (name, default) in self.FILES.items():
            path = sd / name
            if key == "profile":
                raw, changed = load_stored_profile(path)
                if changed:
                    self.dirty.add(key)
                # In machine mode the zone is resolved on every load; a new profile takes it the same way.
                _, zone, followed = effective_zone(raw, detect_zone)
                if followed and (raw.get("timezone") != zone or not raw.get("timezone_confirmed")):
                    raw["timezone"], raw["timezone_confirmed"] = zone, True
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


def floor_gb(mb):
    """A memory floor in GB with one decimal, dropped when whole: 2048 gives 2, 1500 gives 1.5."""
    return f"{mb / 1024:.1f}".removesuffix(".0")


def host_view(profile, commit_mb):
    """The `host` object of every status answer: the one place the memory guard's figures are made.

    `commit_mb` is the free commit as `memory_mb` measured it (None when nothing was measured). The floor and
    the heavy-run count exist only with the guard on; a key the profile lacks reads as its default, and a
    figure that cannot be computed leaves the count None, never the floor."""
    guard = profile.get("memory_guard") is True

    def figure(key):
        return profile.get(key, PROFILE_DEFAULTS[key])
    heavy = None
    if guard and commit_mb is not None:
        try:
            heavy = max(0, min(int(figure("max_heavy_runs")),
                               int((commit_mb - figure("commit_reserve_mb")) // figure("heavy_job_mb"))))
        except (TypeError, ValueError, ArithmeticError):
            heavy = None
    return {"guard": guard, "free_commit_mb": None if commit_mb is None else round(commit_mb),
            "min_free_commit_mb": figure("min_free_commit_mb") if guard else None, "max_heavy_runs": heavy}


def guard_profile(sd):
    """The profile the memory guard reads when `status` could not answer, and the read error to log (or None).

    The stored profile; the defaults when no file is stored (the guard off); and, when the file exists but
    cannot be read or parsed, the defaults with the guard ON: the memory floor is the one stop that must not
    lapse because a hand edit broke the file."""
    try:
        return load_stored_profile(sd / "pace-profile.json")[0], None
    except Exception as error:  # noqa: BLE001 - whatever stops the read (JSON, encoding, access) is a failed read
        return dict(PROFILE_DEFAULTS, memory_guard=True), error


def memory_mb(profile, report):
    """Free memory in MiB where it is wanted, else None: the only caller of free_memory_mb besides doctor.

    Measured while the memory guard is on, or when `report` is true (a person reads the figure).
    """
    if profile.get("memory_guard") is True or report:
        return free_memory_mb()
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


def env_claude_pid():
    """The pid Claude Code exports as CLAUDE_PID when it names a live claude* process, else None."""
    try:
        pid = int(os.environ.get("CLAUDE_PID", ""))
    except ValueError:
        return None
    if not 0 < pid < 2 ** 31:  # os.kill reads 0 and -1 as groups and overflows past a C int
        return None
    return pid if (process_image(pid) or "").startswith("claude") else None


def claude_pid():
    """The Claude Code process that owns a hold, or None when there is none to find.

    The nearest ancestor whose executable starts with 'claude'; else the live claude* process CLAUDE_PID
    names, since a shell launcher that has already exited breaks the chain (Git Bash on Windows). Never a
    short-lived parent: a hold without an owner ends with its lease."""
    _, pid = claude_ancestors()
    return pid if pid is not None else env_claude_pid()


# ---------------------------------------------------------------- usage data

def own_account(session=None, sd=None):
    """(key, reason) for the account of the session, as limits_file.resolve_account reports it."""
    return limits_file.resolve_account(session=session, sd=sd)


class UsageError(PaceError):
    """A refused argument or value: exit 2, nothing changed."""


class AccountChoiceError(PaceError):
    """--account names no single partition; the message lists the candidates."""


ACCOUNT_PREFIX_MIN = 4
# A reading older than this is stale; the doctor and the other-account notice apply the same bound.
FRESH_READING_MIN = 30


def partition_keys(limits):
    """The account keys of the partitions of a limits file, in file order; untagged readings have none."""
    return [k for k in limits_file.held_partitions(limits) if k != limits_file.UNKNOWN_ACCOUNT]


def select_account(prefix, limits):
    """The one partition key that starts with the prefix; AccountChoiceError when none or several do."""
    keys = partition_keys(limits)
    listed = ", ".join(keys) if keys else "none yet"
    wanted = prefix.lower()
    if not re.fullmatch(f"[0-9a-f]{{{ACCOUNT_PREFIX_MIN},16}}", wanted):
        raise AccountChoiceError(f"--account takes {ACCOUNT_PREFIX_MIN} to 16 hex digits of an account key; "
                                 f"candidates: {listed}")
    found = [k for k in keys if k.startswith(wanted)]
    if len(found) == 1:
        return found[0]
    why = "matches no account" if not found else "matches several accounts"
    raise AccountChoiceError(f"--account {prefix} {why}; candidates: {', '.join(found) if found else listed}")


def other_active_accounts(limits, key, now):
    """The keys of the other partitions that hold a reading under FRESH_READING_MIN minutes old."""
    limit = dt.timedelta(minutes=FRESH_READING_MIN)
    held = limits_file.held_partitions(limits)
    others = []
    for other in partition_keys(limits):
        record = dated_record(held[other], now) if other != key else None
        if record is not None and dt.timedelta(0) <= now - record["_at"] < limit:
            others.append(other)
    return others


MERGE_LOCK_WAIT_SEC = 3
LOAD_LOCK_WAIT_SEC = 20


def read_limits(sd):
    """rate-limits.json as it stands; None when absent or unreadable, which the next writer overwrites."""
    with contextlib.suppress(OSError, ValueError):
        parsed = json.loads((sd / "rate-limits.json").read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    return None


def current_record(sd, now, key=None, reason="no-file"):
    """(record, account): the signed-in account's partition of rate-limits.json, dated; record is None without one."""
    partition, account = pick_partition(read_limits(sd), key, reason)
    return dated_record(partition, now), account


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


def usage_limits(sd):
    """The limits file the readings come from: PACELINE_USAGE_FILE when set, else rate-limits.json."""
    fake = os.environ.get("PACELINE_USAGE_FILE")
    if fake:
        return json.loads(Path(fake).read_text(encoding="utf-8"))
    return read_limits(sd)


def load_usage(sd, profile, now, allow_merge=True, max_age=None, account_prefix=None, session=None):
    """Latest usage record plus where it came from. Never raises on a busy lock or missing data.

    account_prefix picks another account's partition by the start of its key. Without it, a key that
    came from the shared state file (no env key, no session entry) also names the other partitions that
    hold a fresh reading, under "other_accounts".
    """
    if account_prefix is not None:
        key, reason, source = select_account(account_prefix, usage_limits(sd)), None, None
    else:
        key, reason, source = limits_file.account_source(None, session, sd)
    result = read_usage(sd, profile, now, key, reason, allow_merge, max_age)
    if key and account_prefix is None and source == "state-file":
        others = other_active_accounts(usage_limits(sd), key, now)
        if others:
            result["other_accounts"] = others
    return result


def read_usage(sd, profile, now, key, reason, allow_merge, max_age):
    """load_usage for a resolved key: the partition's newest record, merging the inbox when it is old."""
    history = read_history(sd, key)
    fake = os.environ.get("PACELINE_USAGE_FILE")
    if fake:
        partition, account = pick_partition(json.loads(Path(fake).read_text(encoding="utf-8")), key, reason)
        record = dict(partition) if partition is not None else None
        if record is not None:
            record["_at"] = parse_iso(record.get("observed_at")) or now
        return {"record": record, "source": "file", "error": None, "history": history, "account": account}
    limit = USAGE_SNAPSHOT_MAX_AGE_SEC if max_age is None else max_age

    def fresh(record):
        return record is not None and (now - record["_at"]).total_seconds() < limit

    newest, account = current_record(sd, now, key, reason)
    if fresh(newest):
        return {"record": newest, "source": record_source(newest), "error": None, "history": history,
                "account": account}
    if not allow_merge or os.environ.get("PACELINE_NO_MERGE"):
        return {"record": newest, "source": "fallback", "error": "merge disabled", "history": history,
                "account": account}
    # The record is older than the limit: take in whatever the sessions have handed over since.
    # A reading is never newer than the last turn of any session, so a merge without error leaves
    # no error to report even when the data stays old.
    _, merged, error = update_usage(sd, now, lock_wait=LOAD_LOCK_WAIT_SEC)
    history = read_history(sd, key)
    newest, account = current_record(sd, now, key, reason)
    if fresh(newest):
        return {"record": newest, "source": record_source(newest), "error": None, "history": history,
                "account": account}
    return {"record": newest, "source": "fallback", "error": None if merged else error, "history": history,
            "account": account}


def scoped_match(scoped, model):
    if not model:
        return []
    m = model.lower()
    return [s for s in scoped or [] if isinstance(s.get("name"), str) and s["name"].lower() in m]


def effective_used(record, model):
    values = [float(record["seven_day"]["used_percentage"])]
    values += [float(s["used_percentage"]) for s in scoped_match(record.get("scoped"), model)]
    return max(values)


# ---------------------------------------------------------------- budget model

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


def window_effective(record, now, model):
    """Effective weekly usage of the current window and its reset instant."""
    _, reset, rolled = roll_bucket(record["seven_day"], now, WEEK)
    return (0.0 if rolled else effective_used(record, model)), reset


def compute(now, profile, usage, model=None, commit_mb=None, outstanding=0.0, *, plan=None, entry=None):
    """Every number the recommendation and the advice are built from.

    Pure: it reads no file and writes none. `plan` is the account's normalised plan dict (None reads as the
    default plan); `entry` is the current part's fixed entry, derived in memory from the history when None."""
    record = usage.get("record")
    account = usage.get("account")
    if (account or {}).get("reason") == NO_PARTITION or not record or not record.get("seven_day"):
        raise no_week_error(usage)
    tz = load_zone(profile["timezone"])
    history = usage.get("history", [])
    k = float(profile["five_hour_to_week_pct"])
    seven_used, reset, rolled = roll_bucket(record["seven_day"], now, WEEK)
    if reset is None:
        raise PaceError("usage record has no seven_day.resets_at")
    scoped = [] if rolled else [dict(s) for s in record.get("scoped") or []]
    matched = scoped_match(scoped, model)
    effective = max([seven_used] + [float(s["used_percentage"]) for s in matched])
    remaining = max(0.0, 100.0 - effective)
    # The reserve has a section of its own, before the plan's guarded block and outside it: a failure here
    # gives the error form and a state that keeps nothing, and never touches the hard-limit figures below.
    state, reserve, reserve_exception = reserve_section(now, plan, seven_used, reset, tz)
    spendable = max(0.0, remaining - state.kept(now))

    age_min = (now - record["_at"]).total_seconds() / 60.0 if record.get("_at") else math.inf
    stale = age_min > STALE_THRESHOLD_MIN

    host = host_view(profile, commit_mb)
    guarded = commit_mb if host["guard"] else None
    host_pct = 0.0 if guarded is not None and guarded < host["min_free_commit_mb"] else None

    # Everything that depends on the plan runs in ONE guarded block. Whatever the plan holds, a failure
    # here gives the unplanned result (budget 0, nominal rate, night allowance 0); the memory, five-hour and
    # weekly figures around it never depend on the plan.
    plan_error = plan_exception = None
    try:
        figures = plan_figures(now, profile, usage, plan, entry, tz, seven_used, reset, host_pct, state)
    except Exception as error:  # noqa: BLE001 - a bad plan must never take the hard limits down
        plan_error = f"the week plan could not be built ({type(error).__name__})"
        plan_exception = error
        figures = unplanned_figures(now, profile, tz, seven_used, reset, host_pct, plan, state.pct)
    entry, kind_now, e_start, e_end = figures["entry"], figures["kind_now"], figures["start"], figures["end"]
    share, spent, part_hours = figures["share"], figures["spent"], figures["part_hours"]
    hours_left = figures["hours_left"]
    budget_per_hour, normal_rate = figures["budget"], figures["normal"]
    stored, day_capacity, night = figures["stored"], figures["day_capacity"], figures["night"]
    capacity_warning = remaining >= CAPACITY_WARN_RATIO * day_capacity

    five_used, five_reset, _ = roll_bucket(record.get("five_hour"), now, None)
    five_threshold = float(profile["five_hour_stop_pct"])

    burn = None
    if five_reset is not None:
        measured = five_burn.measure(five_burn.samples(history, record["five_hour"]), five_used, now)
        if measured is not None:
            projected = five_burn.project(measured["rate"], five_used, five_threshold, now, five_reset)
            burn = five_burn.burn_view(measured, projected, stale, tz)

    forecast = forecast_unused(history, now, reset, remaining, model)
    hours_to_reset = (reset - now).total_seconds() / 3600.0
    elapsed_pct = 100.0 * (1 - hours_to_reset / 168.0)

    return {
        "now": iso_ms(now),
        "now_local": fmt_local(now, tz),
        "data": {"source": usage.get("source"), "error": usage.get("error"),
                 "observed_at": iso_ms(record["_at"]) if record.get("_at") else record.get("observed_at"),
                 "bucket_observed_at": record.get("_bucket_at"), "age_min": round(age_min, 1) if age_min != math.inf else None,
                 "stale": stale, **({"account": account} if account else {}),
                 **({"other_accounts": usage["other_accounts"]} if usage.get("other_accounts") else {})},
        "weekly": {"seven_day_pct": seven_used, "scoped": scoped, "model": model,
                   "matched_scoped": [s["name"] for s in matched], "effective_pct": effective,
                   "remaining_pct": remaining, "reset": iso_ms(reset), "reset_local": fmt_local(reset, tz),
                   "hours_to_reset": round(hours_to_reset, 2), "elapsed_pct": round(elapsed_pct, 1)},
        "reserve": reserve,
        "spendable_pct": round(spendable, 3),
        "five_hour_to_week_pct": k,
        "budget_per_hour_pct": round(budget_per_hour, 3),
        "normal_per_hour_pct": round(normal_rate, 3),
        "normal_pct_kind": entry["kind"],
        "budget_part": {"kind": entry["kind"], "day": entry["day"], "start": iso_ms(e_start), "end": iso_ms(e_end),
                        "share_pct": _r3(share), "spent_pct": round(spent, 3), "part_hours": round(part_hours, 3),
                        "hours_left": round(hours_left, 3), "unplanned": share is None, "stored": stored,
                        "next_planned": figures["next_planned"]},
        "day_capacity_pct": round(day_capacity, 3),
        "capacity_warning": capacity_warning,
        "in_day_hours": kind_now == "work",
        "five_hour": {"used_pct": five_used, "reset": iso_ms(five_reset) if five_reset else None,
                      "reset_local": fmt_local(five_reset, tz) if five_reset else None,
                      "threshold_pct": five_threshold,
                      "burn": burn},
        "night": night,
        "host": host,
        "outstanding_holds_pct": round(outstanding, 3),
        "forecast_unused_pct": None if forecast is None else round(forecast, 1),
        **({"plan_error": plan_error, "_plan_exception": plan_exception} if plan_error else {}),
        **({"_reserve_exception": reserve_exception} if reserve_exception is not None else {}),
    }


def plan_figures(now, profile, usage, plan, entry, tz, seven_used, reset, host_pct, state=None):
    """The plan-dependent figures of compute: the current part, the budget, the reference rate, the night
    and the day capacity. Raises on a plan that cannot be used; compute guards the call. `state` is the
    reserve state of the week (the plan's own when None)."""
    k = float(profile["five_hour_to_week_pct"])
    if plan is None:
        plan = new_plan()
    if state is None:
        state = reserve_state(plan, reset, tz)
    work_hours = plan["work_hours"]
    local_now = now.astimezone(tz)
    # The kind of the moment is the one rule the merge shares: the stored part of this week that holds now,
    # else the plan's own working hours (a work_hours change applies from the end of the running part).
    kind_now = week.kind_now(plan, now, tz, reset)
    given = entry is not None
    if entry is None:
        entry, _new = part_entry(now, profile, usage, plan, tz, (seven_used, reset), state)
    if entry is None:
        # No part of the week holds `now`: nothing is planned there, and the budget is 0.
        entry = {"day": local_now.date().isoformat(), "kind": kind_now, "start": iso_ms(now), "end": iso_ms(reset),
                 "share_pct": None, "unplanned": True, "used_at_start": seven_used,
                 "weights": {"part": 0.0, "total": 0.0}, "mark": 0.0, "full_hours": (reset - now) / HOUR}
    # An entry passed in counts as stored; a derived one is stored when it is the plan file's own.
    stored = given or any(e is entry for e in plan["fixed"])
    e_start, e_end = parse_iso(entry["start"]), parse_iso(entry["end"])
    share = entry["share_pct"]
    spent = max(0.0, seven_used - float(entry["used_at_start"]))
    part_hours = (e_end - e_start) / HOUR
    hours_left = max((e_end - now) / HOUR, 1e-9)
    per_hour_cap = k / 5.0
    if share is None:
        budget_per_hour = 0.0
    else:
        budget_per_hour = min(max(0.0, share - spent) / hours_left, per_hour_cap)
    rate = (share / part_hours) if share and part_hours > 0 else 0.0
    # A part with no share has no rate to compare with; the nominal hourly rate keeps its ratio at 0.
    normal_rate = min(rate, per_hour_cap) if rate > 0 else (100.0 - state.pct) / 168.0

    later = week.parts(e_end, reset, tz, work_hours, plan)
    left_now = week.left(seven_used, state.kept(now))
    _rest, later_shares = week.spread(left_now, share, spent, later)

    # The current part counts only when it carries weight, like the later parts.
    weight_now = float((entry.get("weights") or {}).get("part", 1.0))
    stop = float(profile["five_hour_stop_pct"])
    day_capacity = 0.0
    if weight_now > 0:
        day_capacity += hours_left * per_hour_cap * stop / 100.0
    day_capacity += sum(part.hours * per_hour_cap * stop / 100.0
                        for part in later if part.weight > 0)
    night = night_info(now, reset, tz, profile, entry, later, later_shares, spent, host_pct)
    # The first later part that carries weight: where the next planned work starts (None when there is none).
    following = next((part for part in later if part.weight > 0), None)
    next_planned = None if following is None else {
        "start": iso_ms(following.start), "start_local": fmt_local(following.start, tz),
        "day": following.day.isoformat(), "kind": following.kind}
    return {"entry": entry, "kind_now": kind_now, "start": e_start, "end": e_end, "share": share, "spent": spent,
            "part_hours": part_hours, "hours_left": hours_left, "budget": budget_per_hour, "normal": normal_rate,
            "stored": stored, "day_capacity": day_capacity, "night": night, "next_planned": next_planned}


def unplanned_figures(now, profile, tz, seven_used, reset, host_pct, plan=None,
                      reserve_pct=None):
    """The synthetic result when the plan cannot be used: one unplanned part up to the reset. The kind of
    the moment comes from the plan's own working hours, the default
    hours only when those cannot be used. `reserve_pct` is the nominal size of the reserve (the state's);
    nothing is read from the plan for it (None: the default size)."""
    if reserve_pct is None:
        reserve_pct = RESERVE_DEFAULTS["pct"]
    k = float(profile["five_hour_to_week_pct"])
    local_now = now.astimezone(tz)
    try:
        kind_now = week.kind_now(plan, now, tz, reset)
    except Exception:  # noqa: BLE001 - the plan is what failed; the default hours still give a kind
        kind_now = week.kind_at(local_now, week.DEFAULT_WORK_HOURS)
    entry = {"day": local_now.date().isoformat(), "kind": kind_now, "start": iso_ms(now), "end": iso_ms(reset),
             "share_pct": None, "unplanned": True, "used_at_start": seven_used,
             "weights": {"part": 0.0, "total": 0.0}, "mark": 0.0, "full_hours": (reset - now) / HOUR}
    hours_left = max((reset - now) / HOUR, 1e-9)
    return {"entry": entry, "kind_now": kind_now, "start": now, "end": reset, "share": None, "spent": 0.0,
            "part_hours": hours_left, "hours_left": hours_left, "budget": 0.0,
            "normal": (100.0 - reserve_pct) / 168.0, "stored": False,
            "day_capacity": hours_left * k / 5.0 * float(profile["five_hour_stop_pct"]) / 100.0,
            "night": empty_night(entry["day"], reset, host_pct), "next_planned": None}


def empty_night(day, reset, host_pct):
    return {"date": day, "start": iso_ms(reset), "end": iso_ms(reset), "hours": 0.0, "current": False,
            "mark": None, "weight": 0.0, "share_pct": None, "fixed": False, "window_capacity_pct": 0.0,
            "host_capacity_pct": host_pct, "last_before_reset": True, "allowance_pct": 0.0}


def night_info(now, reset, tz, profile, entry, later, later_shares, spent, host_pct):
    """Tonight's off-hours part (T of the week plan) as the night run's allowance."""
    k = float(profile["five_hour_to_week_pct"])
    tonight = None  # (part, share, current)
    if entry["kind"] == "off":
        tonight = (entry_part(entry), entry["share_pct"], True)
    else:
        for part, part_share in zip(later, later_shares):
            if part.kind == "off" and part.day.isoformat() == entry["day"]:
                tonight = (part, part_share, False)
                break
    host = math.inf if host_pct is None else host_pct
    if tonight is None:
        return empty_night(entry["day"], reset, host_pct)
    part, share, current = tonight
    start = max(now, part.start)
    hours = max(0.0, (part.end - start) / HOUR)
    window_capacity = hours * k / 5.0 * float(profile["five_hour_stop_pct"]) / 100.0
    if current:
        basis = max(0.0, share - spent) if share is not None else 0.0
    else:
        basis = share or 0.0
    allowance = max(0.0, min(basis, window_capacity, host))
    return {"date": part.day.isoformat(), "start": iso_ms(start), "end": iso_ms(part.end), "hours": round(hours, 2),
            "current": current, "mark": part.mark, "weight": part.weight, "share_pct": _r3(share), "fixed": current,
            "window_capacity_pct": round(window_capacity, 3), "host_capacity_pct": host_pct,
            "last_before_reset": part.end >= reset, "allowance_pct": round(allowance, 3)}


def forecast_unused(history, now, reset, remaining, model):
    """Unused weekly percentage at reset if the burn rate of the last 24 h continues."""
    points = [r for r in history if now - DAY <= r["_at"] <= now
              and limits_file.same_week(parse_iso(r["seven_day"].get("resets_at")), reset)]
    if len(points) < 2:
        return None
    span = (points[-1]["_at"] - points[0]["_at"]).total_seconds() / 3600.0
    if span < 3:
        return None
    burn = max(0.0, effective_used(points[-1], model) - effective_used(points[0], model)) / span
    return max(0.0, remaining - burn * (reset - now).total_seconds() / 3600.0)


# ---------------------------------------------------------------- recommendation

def recommend(calc):
    """The safety stops recommend knows: memory (guard on), the five-hour stop, the weekly limit used up."""
    reasons = []
    commit = calc["host"]["free_commit_mb"]
    floor = calc["host"]["min_free_commit_mb"]
    five = calc["five_hour"]
    weekly = calc["weekly"]
    blocked = False
    if commit is not None and floor is not None and commit < floor:
        blocked = True
        reasons.append(f"free commit {commit / 1024:.1f} GB < {floor_gb(floor)} GB")
    if five["used_pct"] >= five["threshold_pct"]:
        blocked = True
        reasons.append(f"5h {five['used_pct']:.0f}% >= {five['threshold_pct']:.0f}%")
    if weekly["effective_pct"] >= 100:
        blocked = True
        reasons.append("weekly limit exhausted")
    return {"blocked": blocked, "reasons": reasons}


KIND_LABEL = {"work": "working-hours", "off": "off-hours"}


def status_line(calc, rec):
    w = calc["weekly"]
    normal_label = KIND_LABEL.get(calc["normal_pct_kind"], calc["normal_pct_kind"])
    normal = f"{normal_label} norm {calc['normal_per_hour_pct']:.2f}"
    parts = [f"7d {w['seven_day_pct']:.0f}% reset {w['reset_local']}",
             f"budget {calc['budget_per_hour_pct']:.2f}%/h ({normal})"]
    if calc["forecast_unused_pct"] is not None:
        parts.append(f"forecast unused {calc['forecast_unused_pct']:.0f}%")
    reserve = calc.get("reserve")
    # Once released, or at 0 %, the plan already says it: the part shows only while the stop acts or is unknown.
    if reserve and (reserve.get("error") or (reserve["pct"] > 0 and not reserve["released"])):
        parts.append(reserve["text"])
    parts.append(f"5h {calc['five_hour']['used_pct']:.0f}%/{calc['five_hour']['threshold_pct']:.0f}")
    parts.append(f"night {calc['night']['allowance_pct']:.1f}%")
    age = calc["data"]["age_min"]
    data = f"data {age:.0f} min old" if age is not None else "data age unknown"
    if calc["data"]["stale"]:
        data = "STALE " + data
    if calc["data"]["source"] == "fallback" and calc["data"]["error"]:
        data += f" ({calc['data']['error']})"
    parts.append(data)
    account = calc["data"].get("account") or {}
    if account.get("state") == "unknown":
        parts.append(f"account unknown ({account.get('reason')})")
    if calc["host"]["guard"] and calc["host"]["free_commit_mb"] is None:
        parts.append("host memory unknown")
    if calc.get("pace_on") is False:
        parts.append("pace hooks off (paceline on)")
    if calc["capacity_warning"]:
        parts.append(f"WARNING remainder {w['remaining_pct']:.0f}% near day capacity {calc['day_capacity_pct']:.0f}%")
    parts += rec["reasons"]
    for other in calc["data"].get("other_accounts") or []:
        parts.append(f"other account {other[:8]} active; --account {other[:8]}")
    return " | ".join(parts)


# ---------------------------------------------------------------- holds

# A launch hold covers the gap between an Agent or Workflow gate and the SubagentStart that
# replaces it. It carries no estimate, never reaches the history and ends on its own after this long.
LAUNCH_KIND = "launch"
LAUNCH_TTL = dt.timedelta(seconds=60)


def outstanding_pct(registry, effective):
    active = [r for r in registry["holds"] if r["kind"] != LAUNCH_KIND]
    if not active:
        return 0.0
    consumed = max(0.0, effective - min(r["used_at_acquire"] for r in active))
    return max(0.0, sum(r["estimate_pct"] for r in active) - consumed)


def prune_holds(registry, profile, now, alive=pid_alive):
    ttl = dt.timedelta(minutes=LEASE_TTL_MIN)
    # A keyed hold is held by a hook pair that never renews it, and a subagent can outlive the lease.
    agent_ttl = dt.timedelta(minutes=AGENT_LEASE_TTL_MIN)
    kept, dropped = [], []
    for r in registry["holds"]:
        if r["kind"] == LAUNCH_KIND:
            span = LAUNCH_TTL
        else:
            span = agent_ttl if r.get("key") else ttl
        if now - parse_iso(r["renewed_at"]) > span:
            dropped.append((r, "lease expired"))
        elif r.get("pid") is not None and not alive(r["pid"], r.get("image")):
            dropped.append((r, "process gone"))
        else:
            kept.append(r)
    registry["holds"] = kept
    for r, why in dropped:
        close_hold(registry, r, now, None, None, why)
    return dropped


def close_hold(registry, r, now, effective, actual, why):
    entry = {k: r.get(k) for k in ("id", "kind", "label", "key", "session_id", "estimate_pct", "acquired_at", "item_id")}
    if r["kind"] == LAUNCH_KIND:
        return dict(entry, released_at=iso_ms(now), actual_pct=None, overlapped=False, reason=why)
    if actual is None and effective is not None:
        actual = max(0.0, effective - r["used_at_acquire"])
    others = [o for o in registry["holds"] if o["id"] != r["id"] and o["kind"] != LAUNCH_KIND]
    entry.update(released_at=iso_ms(now), actual_pct=None if actual is None else round(actual, 3),
                 overlapped=bool(others) or bool(r.get("overlapped")), reason=why)
    registry["completed"].append(entry)
    registry["completed"] = registry["completed"][-500:]
    return entry


ENDED_DIR = "ended-sessions"
ENDED_MAX_AGE = limits_file.SESSION_ACCOUNT_MAX_AGE  # one retention for an ended session's markers and entry


def sweep_ended_sessions(store, now, effective=None):
    """Close the holds of each session a session-end hook marked as ended, then drop its marker.

    A hold acquired after the end belongs to the same session resumed under its id, and stays.
    The session's session-accounts entry goes with its marker, unless it was written after the end. Returns the closed entries.
    """
    folder = store.sd / ENDED_DIR
    try:
        markers = sorted(folder.glob("*.json"))
    except OSError:
        markers = []
    registry, closed, ended_sessions = store["holds"], [], []
    for path in markers:
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            session, ended = marker["session_id"], parse_iso(marker["ended_at"])
        except (OSError, ValueError, TypeError, KeyError):
            session = ended = None
        if isinstance(session, str) and session and ended:
            ended_sessions.append((session, ended))
        if session and ended and now - ended <= ENDED_MAX_AGE:
            targets = [r for r in registry["holds"]
                       if r.get("session_id") == session and parse_iso(r["acquired_at"]) <= ended]
            for r in targets:
                closed.append(close_hold(registry, r, now, effective, None, "session ended"))
                registry["holds"].remove(r)
        with contextlib.suppress(OSError):
            path.unlink()
    limits_file.prune_session_accounts(store.sd, ended_sessions, ENDED_MAX_AGE)
    if closed:
        store.touch("holds")
    return closed


def end_session(sd, session, now, lock_timeout=0.5):
    """The session-end hook's release: sweep the end markers with the measured usage, waiting briefly for the lock.

    Raises PaceError when the lock stays busy; the marker then waits for the next pace call.
    """
    effective = None
    usage = load_usage(sd, read_profile(sd), now, allow_merge=False, session=session)
    record = usage.get("record")
    if record and record.get("seven_day"):
        model = os.environ.get("PACELINE_MODEL") or os.environ.get("ANTHROPIC_MODEL")
        effective = window_effective(record, now, model)[0]
    with locked(sd, timeout=lock_timeout):
        store = Store(sd)
        closed = sweep_ended_sessions(store, now, effective)
        store.save()
    return [c for c in closed if c["session_id"] == session]


def mark_overlap(registry):
    held = [r for r in registry["holds"] if r["kind"] != LAUNCH_KIND]
    if len(held) > 1:
        for r in held:
            r["overlapped"] = True


# ---------------------------------------------------------------- calibration

def calibrate(history, store, now, profile, model, plan=None):
    """Corrections the history supports. `plan` is the account's plan dict: its working hours and its
    reserve (size and release day) are what the proposals read; None reads as the default plan."""
    tz = load_zone(profile["timezone"])
    plan = new_plan() if plan is None else plan
    work_hours = plan["work_hours"]
    if not history:
        return {"days": 0, "proposals": [], "evidence": {},
                "note": f"not enough history: 0 days, need {CALIBRATION_MIN_DAYS}"}
    days = (history[-1]["_at"] - history[0]["_at"]).total_seconds() / 86400.0
    result = {"days": round(days, 1), "proposals": [], "evidence": {}}
    if days < CALIBRATION_MIN_DAYS:
        result["note"] = f"not enough history: {days:.1f} days, need {CALIBRATION_MIN_DAYS}"
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
    unused, nights, at_release = [], [], []
    for reset_iso, records in windows.items():
        reset = parse_iso(reset_iso)
        if not reset or reset > now:
            continue
        final = effective_used(records[-1], model)
        unused.append(100.0 - final)
        release = reserve_state(plan, reset, tz).release_at  # None at 0 % or when it could not be computed
        used_release = None if release is None else used_at(records, release, reset, model)
        if used_release is not None:
            at_release.append(100.0 - used_release)
        # The off-hours parts that lie wholly inside the window.
        for part in week.parts(reset - WEEK, reset, tz, work_hours, None):
            if part.kind != "off" or part.hours < part.full_hours - 1e-9:
                continue
            u0, u1 = used_at(records, part.start, reset, model), used_at(records, part.end, reset, model)
            if u0 is not None and u1 is not None:
                nights.append(max(0.0, u1 - u0))
    if unused:
        mean_unused = statistics.mean(unused)
        result["evidence"]["unused_at_reset_pct"] = [round(u, 1) for u in unused]
        # The size is the account's own, whatever the release instants turned out to be (R7).
        reserve = _reserve_of(plan.get("reserve") if plan else None)["pct"]
        # An on-track week ends near 1 % unused, so the raise looks at what was left
        # when the reserve was released, not at the reset; a window without a reading by then is skipped.
        left_at_release = statistics.mean(at_release) if at_release else None
        if left_at_release is not None:
            result["evidence"]["left_at_release_pct"] = [round(u, 1) for u in at_release]
        if reserve == 0:
            pass  # a reserve that is off gets no proposal
        elif left_at_release is not None and left_at_release < reserve:
            if reserve < RESERVE_PCT_MAX:
                result["proposals"].append({"parameter": "reserve", "current": reserve,
                                            "proposed": min(RESERVE_PCT_MAX, reserve + 2),
                                            "evidence": f"weeks reached the release of the reserve with "
                                                        f"{left_at_release:.1f}% left on average, below the "
                                                        f"{reserve:g}% reserve"})
        elif mean_unused > RESERVE_LOWER_UNUSED_PCT and reserve > 4:
            result["proposals"].append({"parameter": "reserve", "current": reserve, "proposed": max(4, reserve - 2),
                                        "evidence": f"weeks ended with {mean_unused:.1f}% unused on average"})
    if nights:
        mean_night = statistics.mean(nights)
        result["evidence"]["night_spend_mean_pct"] = round(mean_night, 2)
    clean = [c for c in store["holds"]["completed"]
             if c.get("actual_pct") is not None and c.get("estimate_pct") and not c.get("overlapped")]
    # Hook holds carry the fixed profile estimate, so they calibrate that value, not the multiplier.
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
    if len(ratios) >= 3:
        median = statistics.median(ratios)
        result["evidence"]["estimate_ratio_median"] = round(median, 2)
        if abs(median - 1) > 0.2:
            result["proposals"].append({"parameter": "task_estimate_multiplier", "current": 1.0,
                                        "proposed": round(median, 2),
                                        "evidence": f"median actual/estimate {median:.2f} over {len(ratios)} tasks"})
    return result


# ---------------------------------------------------------------- CLI

NOT_GIVEN = object()  # a plan argument that was not passed (None means "no readable plan")


class Context:
    def __init__(self, args):
        self.args = args
        self.sd = state_dir(args.state_dir)
        self.now = parse_iso(args.now or os.environ.get("PACELINE_NOW")) or dt.datetime.now(UTC)
        self.model = args.model or os.environ.get("PACELINE_MODEL") or os.environ.get("ANTHROPIC_MODEL")
        self.session = args.session or limits_file.own_session()
        self.usage = None

    def get_usage(self, profile, allow_merge=True, max_age=None):
        if self.usage is None or max_age is not None:
            self.usage = load_usage(self.sd, profile, self.now, allow_merge and not self.args.no_merge, max_age,
                                    getattr(self.args, "account", None), self.session)
        return self.usage

    def plan_of(self, usage):
        """The plan of the account the usage belongs to, read without the lock; None when it cannot be read
        (a damaged or newer file): the budget then runs on the default plan and the file is left alone."""
        key = (usage.get("account") or {}).get("key")
        try:
            return load_plan(self.sd, key)
        except PaceError:
            return None
        except Exception as error:  # noqa: BLE001 - whatever the plan file holds, the hard limits go on
            limits_file.trace_failure("plan.load", self.sd, error=error)
            return None

    def calc(self, store, allow_merge=True, max_age=None, plan=NOT_GIVEN, entry=None, report=False):
        usage = self.get_usage(store["profile"], allow_merge, max_age)
        record = usage.get("record")
        eff = window_effective(record, self.now, self.model)[0] if record and record.get("seven_day") else 0.0
        if plan is NOT_GIVEN:
            try:
                plan = self.plan_of(usage)
            except Exception as error:  # noqa: BLE001 - whatever the plan load does, the hard limits go on
                limits_file.trace_failure("plan.load", self.sd, error=error)
                plan = None
        result = compute(self.now, store["profile"], usage, self.model, memory_mb(store["profile"], report),
                         outstanding_pct(store["holds"], eff), plan=plan, entry=entry)
        fallback = result.pop("_plan_exception", None)  # internal: the fallback's exception, never published
        if result.get("plan_error"):
            limits_file.trace_failure("plan.compute", self.sd, error=fallback, reason=result["plan_error"])
        failed = result.pop("_reserve_exception", None)  # internal: the reserve's exception, never published
        if result.get("reserve", {}).get("error"):
            limits_file.trace_failure("reserve.compute", self.sd, error=failed, reason=result["reserve"]["error"])
        if getattr(self.args, "account", None) is None:
            self.publish_burn(result)
        return result

    def publish_burn(self, result):
        """Write the five-hour burn snapshot of a run that reads the live clock.

        compute() stays pure; Context.calc() calls this for every command that computes (status, auto and
        the mutating commands). A run dated by --now or PACELINE_NOW outside the future slack of the wall
        clock is a what-if and must not replace the live file with an old or future computed_at."""
        account = result["data"].get("account") or {}
        if not (account.get("state") == "known" and account.get("key") and result["five_hour"]["reset"]
                and abs(self.now - limits_file.wall_clock()) <= FUTURE_SLACK and limits_file.pace_enabled(self.sd)):
            return
        try:
            five_burn.publish(self.sd, five_burn.snapshot(self.now, account["key"], result["five_hour"]))
        except OSError as error:
            limits_file.trace_failure("five_burn.snapshot", self.sd, error=error)


def emit(args, line, payload):
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(line)


def budget_suffix(calc):
    return f"budget {calc['budget_per_hour_pct']:.2f}%/h, night {calc['night']['allowance_pct']:.1f}%"


def housekeeping(store, now):
    if prune_holds(store["holds"], store["profile"], now):
        store.touch("holds")
    sweep_ended_sessions(store, now)


def cmd_status(ctx):
    launch_key = getattr(ctx.args, "launch_key", None)
    # The usage is loaded, with its merge, before the lock is taken: the report below runs under it.
    ctx.get_usage(read_profile(ctx.sd))
    with locked(ctx.sd):
        store = Store(ctx.sd)
        housekeeping(store, ctx.now)
        try:
            # The report counts the holds held before this launch, and the launch is recorded in
            # the same lock, so concurrent gates take turns: each sees the launches of those before it.
            line, calc = status_report(ctx, store, launch=bool(launch_key), report=not launch_key)
        except NoWeekError as error:
            store.save()
            if not ctx.args.json:
                raise
            # No weekly record, whatever the reason: the gate still needs the host's free memory, so the
            # JSON answer is partial instead of an error (the figures of the week are absent, data is stale).
            emit(ctx.args, "", with_advice(ctx, store, no_week_status(ctx, store, error, report=not launch_key)))
            return EXIT_OK
        except PaceError:
            store.save()
            raise
        if launch_key:
            calc["launch_hold"] = hold_launch(store, ctx, launch_key)
        store.save()
    emit(ctx.args, line, with_advice(ctx, store, calc))
    return EXIT_OK


def with_advice(ctx, store, status):
    """The status dict with the unattended-work advice built from it, last, so it reads the finished answer.

    A failure leaves the key out and goes to the debug log: the hook then builds the advice from the status
    itself (pace_pre_agent.advice_of), so no stop depends on this build."""
    try:
        return dict(status, advice=advice.build(status, int(store["profile"]["max_agents"])))
    except Exception as error:  # noqa: BLE001 - the status answer never fails for the advice
        limits_file.trace_failure("status.advice", ctx.sd, error=error)
        return status


def get_advice(ctx):
    """(the advice, the profile) of this moment, from the status path without a launch key.

    The current part is fixed like `status` does it, under pace.lock; without the week's figures the advice
    is built from the no-week answer."""
    ctx.get_usage(read_profile(ctx.sd))
    for refresh in (False, True):
        with locked(ctx.sd):
            store = Store(ctx.sd)
            housekeeping(store, ctx.now)
            try:
                _line, calc = status_report(ctx, store, launch=False, report=True)
            except NoWeekError as error:
                calc = no_week_status(ctx, store, error, report=True)
            except PaceError:
                store.save()
                raise
            store.save()
        five_hour, data = calc.get("five_hour") or {}, calc.get("data") or {}
        age_sec = (data.get("age_min") or 0) * 60
        if refresh or not ((five_hour.get("used_pct") or 0) >= ADVICE_REFRESH_FIVE_HOUR_PCT
                           and data.get("source") != "file" and age_sec >= ADVICE_REFRESH_CACHE_SEC):
            break
        # Near the five-hour ceiling, older data can hide a window that is already full: merge what
        # the sessions have handed over since, once.
        ctx.get_usage(store["profile"], max_age=ADVICE_REFRESH_CACHE_SEC)
    profile = store["profile"]
    return advice.build(calc, int(profile["max_agents"])), profile


def advice_path(sd, key):
    return sd / f"advice-{key}.json"


def write_advice(sd, data):
    """Write the advice file of its account: UTF-8, LF line ends (write_text would give CRLF on Windows), a
    temporary file replaced into place so a reader never sees half of it."""
    path = advice_path(sd, data["account"])
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        for attempt in range(40):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows refuses the replace while a reader holds the file open.
                if attempt == 39:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def cmd_advice(ctx):
    given, profile = get_advice(ctx)
    key = given["account"]
    if key:
        # The file lets a reader without a process see the last answer (its `computed_at` says how old).
        try:
            ctx.sd.mkdir(parents=True, exist_ok=True)
            write_advice(ctx.sd, given)
        except OSError as error:
            limits_file.trace_failure("advice.write", ctx.sd, error=error)
    tz = load_zone(profile["timezone"])
    emit(ctx.args, advice.line(given, lambda text: fmt_local(parse_iso(text), tz)), given)
    return {"start": EXIT_OK, "wait": EXIT_WAIT, "stop": EXIT_STOP}[given["action"]]


def _valid_reset(bucket):
    """True when a bucket's resets_at is absent or a time that parses."""
    raw = bucket.get("resets_at")
    return raw in (None, "") or limits_file.safe_iso(raw) is not None


def stop_five_hour(record, now):
    """(used percent, reset) of a record's five-hour bucket for the stops, None when it is damaged."""
    bucket = record.get("five_hour")
    if not limits_file.is_bucket(bucket) or not _valid_reset(bucket):
        return None
    used, reset, _ = roll_bucket(bucket, now, None)
    return used, reset


def stop_weekly(record, now, model, sd, key):
    """(effective percent, plain week percent, week reset) of a record for the stops: the plain week and each
    scoped entry that is well formed; a damaged scoped entry is skipped and traced. None when the plain week
    is damaged."""
    week_bucket = record["seven_day"]
    if not _valid_reset(week_bucket):
        limits_file.trace_failure("restrictive_stops.figure", sd, figure="seven_day", account=key)
        return None
    used, reset, rolled = roll_bucket(week_bucket, now, WEEK)
    if rolled:
        return 0.0, used, reset
    scoped = record.get("scoped")
    if scoped is None:
        return used, used, reset
    if not isinstance(scoped, list):
        limits_file.trace_failure("restrictive_stops.figure", sd, figure="scoped", account=key)
        return used, used, reset
    good = [s for s in scoped if isinstance(s, dict) and limits_file.is_bucket(s)]
    if len(good) != len(scoped):
        limits_file.trace_failure("restrictive_stops.figure", sd, figure="scoped entry", account=key)
    effective = max([used] + [float(s["used_percentage"]) for s in scoped_match(good, model)])
    return effective, used, reset


def restrictive_stops(sd, now, profile, model=None):
    """The figures the 5h and weekly hard stops act on while the session's account is unknown.

    The highest fresh five-hour and weekly used percent among the accounts of rate-limits.json (a reading
    under FRESH_READING_MIN minutes old), so a session that cannot name its account is stopped as the most
    loaded account would be. None when no account holds a fresh reading: there is nothing to stop on. The
    plan gate and the budget never use these figures. The one place that makes this choice: the status
    answer carries it and the pre-agent hook only reads it."""
    limits = usage_limits(sd)
    held = limits_file.held_partitions(limits) if limits else {}
    limit = dt.timedelta(minutes=FRESH_READING_MIN)
    five, weekly, keys = None, None, []
    plain, week_reset = 0.0, None
    for key in sorted(held):
        record = dated_record(held[key], now)
        if record is None or not dt.timedelta(0) <= now - record["_at"] < limit:
            continue
        # Each figure is read on its own: a damaged one is skipped and traced, the valid figures of the
        # same account and of the others still count.
        reading = stop_five_hour(record, now)
        if reading is None and record.get("five_hour") is not None:
            limits_file.trace_failure("restrictive_stops.figure", sd, figure="five_hour", account=key)
        week_figures = stop_weekly(record, now, model, sd, key)
        if reading is None and week_figures is None:
            continue
        keys.append(key)
        if reading is not None and (five is None or reading[0] > five[0]):
            five = (reading[0], reading[1], key)
        if week_figures is not None:
            effective, plain_used, week_end = week_figures
            plain = max(plain, plain_used)
            if weekly is None or effective > weekly:
                weekly, week_reset = effective, week_end
    if five is None and weekly is None:
        return None
    tz = load_zone(profile["timezone"])
    stops = {"weekly": {"effective_pct": weekly, "seven_day_pct": plain,
                        "reset_local": fmt_local(week_reset, tz) if week_reset else None},
             "accounts": keys}
    if five is not None:
        stops["five_hour"] = {"used_pct": five[0], "threshold_pct": float(profile["five_hour_stop_pct"]),
                              "reset": iso_ms(five[1]) if five[1] else None,
                              "reset_local": fmt_local(five[1], tz) if five[1] else None}
    return stops


def no_week_status(ctx, store, error, report=False):
    """The `status --json` answer when the account has no weekly record: the host's free memory measured
    where a person reads it or the memory guard is on, the data marked stale with the account's reason, and none
    of the figures of the week, the five hours or the plan. While the account is unknown (pending, desktop,
    refused, logged out) it also carries `stops`, the most restrictive fresh figures of any account
    (restrictive_stops): the pre-agent hook then applies the memory floor (with the guard on) and those hard
    stops, and nothing else."""
    profile = store["profile"]
    usage = ctx.get_usage(profile)
    host = host_view(profile, memory_mb(profile, report))
    account = usage.get("account")
    unknown = (account or {}).get("state") == "unknown" and account.get("reason") in limits_file.NO_RECORD_REASONS
    stops = restrictive_stops(ctx.sd, ctx.now, profile, ctx.model) if unknown else None
    return {"now": iso_ms(ctx.now),
            "data": {"source": usage.get("source"), "error": usage.get("error"), "observed_at": None,
                     "age_min": None, "stale": True, **({"account": account} if account else {})},
            "host": host,
            **({"stops": stops} if stops else {}),
            "no_usage": str(error), "pace_on": limits_file.pace_enabled(ctx.sd), "away": away.read(ctx.sd)}


def hold_launch(store, ctx, key):
    """Record a launch hold for the key, or renew the one already held by this session."""
    reg = store["holds"]
    held = next((r for r in reg["holds"] if r["kind"] == LAUNCH_KIND and r.get("key") == key
                 and r.get("session_id") == ctx.session), None)
    if held:
        held["renewed_at"] = iso_ms(ctx.now)
    else:
        held = {"id": f"H-{uuid.uuid4().hex[:8]}", "kind": LAUNCH_KIND, "label": "", "key": key,
                "session_id": ctx.session, "pid": None, "image": None, "estimate_pct": 0.0,
                "acquired_at": iso_ms(ctx.now), "renewed_at": iso_ms(ctx.now), "used_at_acquire": 0.0, "item_id": None}
        reg["holds"].append(held)
    store.touch("holds")
    return held


def status_report(ctx, store, launch=False, report=False):
    """The `paceline status` line and its full result. Runs with pace.lock held: it fixes the current part.

    `launch` is the --launch-key path, the only one that may set a part's `prompted` flag. `report` is true only
    for `paceline status` without a launch key, where a person reads the host's free memory; it is false for every
    other caller, so the memory is measured only with the memory guard on."""
    # The current part is fixed first: the budget is computed from the same entry the plan view shows.
    access, plan_failure = open_plan(ctx, store)
    if access is None:
        calc = ctx.calc(store, plan=None, report=report)
    else:
        # A failed fix or save leaves access.entry None: compute then derives the part from the plan's own marks.
        calc = ctx.calc(store, plan=access.plan, entry=access.entry, report=report)
    rec = recommend(calc)
    calc.update(recommendation=rec,
                active_holds=len(store["holds"]["holds"]), pace_on=limits_file.pace_enabled(ctx.sd),
                away=away.read(ctx.sd))
    if calc.get("plan_error"):
        # compute fell back to the unplanned result: no plan object and no plan gate, whatever the access says.
        calc["plan"] = None
    else:
        plan_object, plan_error = plan_status(ctx, calc, launch, access, plan_failure)
        calc["plan"] = plan_object
        if plan_error:
            calc["plan_error"] = plan_error
    return status_line(calc, rec), calc


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


# ---------------------------------------------------------------- the week plan

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


class PlanAccess:
    """One command's use of the plan file of the account in use. Every use sits inside locked(ctx.sd)."""

    def __init__(self, ctx, store, writes=False):
        self.ctx, self.profile = ctx, store["profile"]
        self.usage = ctx.get_usage(self.profile)
        account = self.usage.get("account") or {}
        key, reason = account.get("key"), account.get("reason")
        self.week = week_of(self.usage, ctx.now)
        if self.week is None and not key:
            key, reason = own_account(ctx.session, ctx.sd)
        if writes and not key and reason in limits_file.NO_RECORD_REASONS:
            raise PaceError(f"the session's account is not known yet ({reason}); nothing changed")
        self.key = key or None
        self.tz = load_zone(self.profile["timezone"])
        try:
            self.plan = load_plan(ctx.sd, self.key)
        except PaceError as error:
            # A plan PaceLine cannot read is no plan it keeps: no reader keeps trusting the last file.
            drop_week_plan(ctx, self.key, error)
            raise
        self.entry = None
        self.skipped = False  # a write was skipped because the clock is ahead of the wall clock
        # The split of the current part (split_current): the plan's mark for it when it was fixed, whether the
        # command named the part itself, and what save() did.
        self.mark_at_fix = None
        self.asked_current = False
        self.split = None
        self.split_refused = None

    def need_week(self):
        if self.week is None:
            raise no_week_error(self.usage)

    def fix(self):
        """Fix the current part on the loaded plan (fix_part); a no-op without a weekly record."""
        if self.week is not None and self.entry is None:
            try:
                self.entry = fix_part(self.ctx.sd, self.key, self.ctx.now, self.profile, self.usage, self.plan,
                                      self.tz, self.week)
            except Exception as error:
                # A plan that cannot be fixed is no plan PaceLine keeps (as one that cannot be built); a failed
                # save (OSError) keeps the file, since the stored plan did not change.
                drop_week_plan(self.ctx, self.key, error)
                raise
            self.mark_at_fix = current_mark(self)
            publish_week_plan(self)
        return self.entry

    def need_entry(self):
        self.need_week()
        if self.fix() is None:
            raise PaceError("no part of the week holds the current time; nothing changed")
        return self.entry

    def save(self):
        """Store the plan. A change that gave the current part another mark splits it here, so every command
        that writes the plan gets the rule (split_current); the result is in self.split or self.split_refused."""
        if self.ctx.now > limits_file.wall_clock() + FUTURE_SLACK:
            self.skipped = True
        self.split = split_current(self) or self.split
        save_plan(self.ctx.sd, self.key, self.plan, self.ctx.now, self.week[1] if self.week else None, self.tz)
        publish_week_plan(self)

    def stored(self, entry):
        """True when the entry is in the file's list (a what-if run's entry is not)."""
        return any(e is entry for e in self.plan["fixed"])

    def view(self):
        """The section of `plan --json`, or None without a week or a current part."""
        if self.week is None or self.fix() is None:
            return None
        return build_plan_view(self)


def week_plan_target(ctx, key):
    """The week plan file of the account `key` when this run may write or remove it, else None: pace is on
    (read now, inside pace.lock), the key is known, the clock is live and `--account` was not given."""
    if not (isinstance(key, str) and limits_file.ACCOUNT_KEY.fullmatch(key)):
        return None
    if getattr(ctx.args, "account", None) is not None:
        return None
    if abs(ctx.now - limits_file.wall_clock()) > FUTURE_SLACK or not limits_file.pace_enabled(ctx.sd):
        return None
    return week_plan_path(ctx.sd, key)


def unlink_week_plan(sd, path):
    """Remove one week plan file, retrying while Windows refuses it to a reader that holds it open (as
    limits_file.write_json does); a final failure is traced, never raised."""
    try:
        for attempt in range(40):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
    except OSError as error:
        limits_file.trace_failure("week_plan.publish", sd, error=error, reason="the file could not be removed")


def remove_week_plan(ctx, key):
    """Remove the account's week plan file under the writer's own conditions; never raises."""
    path = week_plan_target(ctx, key)
    if path is not None:
        unlink_week_plan(ctx.sd, path)


def drop_week_plan(ctx, key, error):
    """The one rule for removing the week plan file after `error`: a plan that cannot be fixed, read or built is
    no plan PaceLine keeps, but an OSError (a failed save or write of a plan that did not change) removes nothing.
    Never raises."""
    if not isinstance(error, OSError):
        remove_week_plan(ctx, key)


def spread_later(now, plan, entry, used_now, reset, tz, state):
    """(left now, spent in the current part, the parts after it, what is left to spread, their shares): the
    figures of the plan view and of the week plan file, from one place so the two cannot drift."""
    left_now = week.left(used_now, state.kept(now))
    spent = max(0.0, used_now - float(entry["used_at_start"]))
    later = week.parts(parse_iso(entry["end"]), reset, tz, plan["work_hours"], plan)
    rest, shares = week.spread(left_now, entry["share_pct"], spent, later)
    return left_now, spent, later, rest, shares


def _inform_until_text(value):
    """The stored informing end as the file spells every instant (spec R1); null when it cannot be read."""
    moment = safe_iso(value)
    return iso_ms(moment) if moment is not None else None


def week_plan_data(access):
    """The version-1 week plan file of a stored current entry (spec R1). Raises when plan_figures or build_plan_view does."""
    ctx, profile, plan, entry = access.ctx, access.profile, access.plan, access.entry
    now, (used_now, reset) = ctx.now, access.week
    build_plan_view(access)  # the writer refuses what `status` refuses, so the two never alternate
    state = reserve_section(now, plan, used_now, reset, access.tz)[0]
    plan_figures(now, profile, access.usage, plan, entry, access.tz, used_now, reset, None, state)
    _left, _spent, later, _rest, shares = spread_later(now, plan, entry, used_now, reset, access.tz, state)
    reserve = _reserve_of(plan.get("reserve"))
    return {
        "version": WEEK_PLAN_VERSION, "computed_at": iso_ms(now), "account": access.key, "timezone": profile["timezone"],
        "work_hours": plan["work_hours"], "mode": plan["mode"], "inform_until": _inform_until_text(plan["inform_until"]),
        "week_resets_at": entry["week_reset"], "five_hour_to_week_pct": float(profile["five_hour_to_week_pct"]),
        "reserve": {"pct": reserve["pct"], "name": reserve["name"], "release": reserve["release"],
                    "release_at": iso_ms(state.release_at) if state.release_at is not None else None,
                    "error": state.error},
        "part": {"day": entry["day"], "kind": entry["kind"], "start": entry["start"], "end": entry["end"],
                 "share_pct": entry["share_pct"], "unplanned": bool(entry["unplanned"]),
                 "used_at_start": entry["used_at_start"], "estimated": bool(entry["estimated"]),
                 "fixed_at": entry["fixed_at"], "weight": entry["weights"]["part"]},
        "later": [{"day": p.day.isoformat(), "kind": p.kind, "start": iso_ms(p.start), "end": iso_ms(p.end),
                   "weight": round(p.weight, 6), "share_pct": _r3(share)} for p, share in zip(later, shares)]}


def publish_week_plan(access):
    """Write the account's week plan file for readers, or remove it when the plan cannot be built.

    The caller holds pace.lock. Writes only for a stored entry of a live run with pace on and an account
    known to own the figures (as publish_burn); an OSError is traced and never fails the command."""
    ctx, entry = access.ctx, access.entry
    try:
        path = week_plan_target(ctx, access.key)
        if path is None or access.week is None or entry is None or not access.stored(entry):
            return
        if (access.usage.get("account") or {}).get("state") != "known":
            return
        try:
            data = week_plan_data(access)
        except Exception as error:  # noqa: BLE001 - the same plans compute() falls back from
            limits_file.trace_failure("week_plan.publish", ctx.sd, error=error, reason="the plan cannot be built")
            drop_week_plan(ctx, access.key, error)
            return
        ctx.sd.mkdir(parents=True, exist_ok=True)
        limits_file.write_json(path, data)
    except (OSError, TypeError, ValueError) as error:
        limits_file.trace_failure("week_plan.publish", ctx.sd, error=error)


def keep_plan_current(ctx, store):
    """Fix the current part for a caller that only keeps the plan up to date: a missing week or a damaged
    plan file is not its error."""
    try:
        PlanAccess(ctx, store).fix()
    except PaceError:
        pass
    except Exception as error:  # noqa: BLE001 - whatever the plan file or its write does, the caller goes on
        limits_file.trace_failure("plan.keep_current", ctx.sd, error=error)


def after_merge_plan(sd, now):
    """The merge's plan step (limits_merge.py calls it when the account's week plan file does not hold now):
    fix the current part and publish the file, waiting at most MERGE_LOCK_WAIT_SEC for pace.lock.

    The caller holds no lock; the usage is read as it stands, with no merge, so the two locks are never
    held together. A busy lock is traced and left to the boundary trigger; nothing is raised."""
    try:
        with locked(sd, timeout=MERGE_LOCK_WAIT_SEC):
            # The arguments of a `status` run with the parser's own defaults, so a new option is covered here too.
            args = with_defaults(build_parser().parse_args(
                ["--now", iso_ms(now), "--no-merge", "--state-dir", str(sd), "status"]))
            PlanAccess(Context(args), {"profile": read_profile(sd)}).fix()
    except PaceError as error:
        limits_file.trace("week_plan.merge", sd, reason=str(error))
    except Exception as error:  # noqa: BLE001 - whatever the plan or its write does, the merge goes on
        limits_file.trace_failure("week_plan.merge", sd, error=error)


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


def open_plan(ctx, store):
    """(the PlanAccess with its current part fixed, None) or (None, the reason there is none). Runs with
    pace.lock held. A plan file that cannot be read or written never takes status down. When the plan
    loaded but fixing or saving its current part failed, the access is still returned (with entry None) beside
    the reason, so the budget runs on the account's own marks and work hours, not on the default plan."""
    access = None
    try:
        access = PlanAccess(ctx, store)
        access.fix()
        return access, None
    except PaceError as error:
        return access, str(error)
    except Exception as error:  # noqa: BLE001 - a bad plan file or a failed write must not take status down
        limits_file.trace_failure("plan.status", ctx.sd, error=error)
        return access, f"the week plan could not be built ({type(error).__name__})"


def plan_status(ctx, calc, launch, access, failure):
    """(the `plan` object of status --json or None, the reason it is None). Runs with pace.lock held.

    Only the --launch-key path (`launch`) may set the part's `prompted` flag, and then only for the first
    over-share launch of a part in limit mode on fresh data."""
    if access is None or failure:
        return None, failure
    try:
        view = access.view()
        if view is None:
            return None, None
        part, first = view["current"], False
        if (launch and view["effective_mode"] == "limit" and part["over"] is True and not part["unplanned"]
                and not calc["data"]["stale"] and not access.entry["prompted"] and access.stored(access.entry)):
            access.entry["prompted"] = True
            try:
                access.save()
                first = True
            except OSError as error:  # the gate still applies; the next launch tries to save the flag again
                access.entry["prompted"] = False
                limits_file.trace_failure("plan.status", ctx.sd, error=error, step="prompted")
        return {"mode": view["mode"], "effective_mode": view["effective_mode"], "inform_until": view["inform_until"],
                "part": {k: part[k] for k in PLAN_STATUS_PART_KEYS}, "first_prompt": first}, None
    except PaceError as error:
        return None, str(error)
    except Exception as error:  # noqa: BLE001 - a bad plan file or a failed write must not take status down
        limits_file.trace_failure("plan.status", ctx.sd, error=error)
        drop_week_plan(ctx, access.key, error)  # compute's fallback and the file must not disagree
        return None, f"the week plan could not be built ({type(error).__name__})"


# ---------------------------------------------------------------- plan commands

def parse_marks(work, off):
    """{"work": V, "off": V} for the values given; a refused value is a UsageError naming the rule."""
    marks = {}
    for kind, value in (("work", work), ("off", off)):
        if value is None:
            continue
        try:
            marks[kind] = week.parse_mark(value)
        except ValueError as error:
            raise UsageError(str(error)) from None
    return marks


def resolve_day(access, text):
    """The local date `text` names (today, tomorrow, mon..sun, YYYY-MM-DD); it must own a part of the week."""
    word = str(text).strip().lower()
    today = access.ctx.now.astimezone(access.tz).date()
    if word == "today":
        day = today
    elif word == "tomorrow":
        day = today + DAY
    elif word in week.WEEKDAYS:
        day = today + dt.timedelta(days=(week.WEEKDAYS.index(word) - today.weekday()) % 7)
    else:
        try:
            if not ISO_DAY.fullmatch(word):
                raise ValueError(word)
            day = dt.date.fromisoformat(word)
        except ValueError:
            raise UsageError(f"{text} is not a weekday (mon..sun), today, tomorrow or a date YYYY-MM-DD") from None
    reset = access.week[1]
    owners = sorted({p.day for p in week_parts_for_days(access)})
    if day not in owners:
        raise UsageError(f"{day} owns no part of this week ({owners[0]} .. {owners[-1]}); nothing changed")
    return day


def week_parts_for_days(access):
    """The anchored parts, then the parts of the whole week under the current hours for what they leave
    out, so a work_hours change never leaves a date of the week without a part."""
    reset = access.week[1]
    anchored = anchored_parts(access.plan, access.ctx.now, reset, access.tz)
    extra = [p for p in week.parts(reset - WEEK, reset, access.tz, access.plan["work_hours"], access.plan)
             if not any((q.day, q.kind) == (p.day, p.kind) for q in anchored)]
    return anchored + extra


def _no_effect(access, day, kinds):
    """For each kind set on `day`: why it changes nothing in this week's plan, as (kind, reason)."""
    reset, now = access.week[1], access.ctx.now
    full = week_parts_for_days(access)
    started = [parse_iso(e["start"]) for e in this_week_entries(access.plan, reset) if parse_iso(e["start"]) <= now]
    anchor_start = max(started) if started else reset - WEEK
    out = []
    for kind in kinds:
        part = next((p for p in full if p.day == day and p.kind == kind), None)
        if part is None:
            out.append((kind, "outside-week"))
        elif access.entry and access.entry["day"] == day.isoformat() and access.entry["kind"] == kind:
            out.append((kind, "current"))
        elif part.end <= max(now, anchor_start):
            out.append((kind, "ended"))
        elif part.start <= now:
            out.append((kind, "current"))
    return out


NO_EFFECT_TEXT = {"current": "is the current part, not split now", "ended": "has ended",
                  "outside-week": "lies outside this week", "unchanged": "already has that mark"}


def current_mark(access):
    """The plan's mark now for the part of the stored current entry, or None without a stored current entry."""
    entry = access.entry
    if entry is None or not access.stored(entry):
        return None
    return week.marks_for(dt.date.fromisoformat(entry["day"]), access.plan)[entry["kind"]]


def split_current(access):
    """Split the current part at now when the plan now gives it another mark; None when nothing was split.

    PlanAccess.save() calls it before the plan is stored. `access.mark_at_fix` is the plan's mark for the part
    when it was fixed; `access.asked_current` is True when the command named the current part itself (a part
    whose recorded mark differs from the plan's is then split even if this change left the plan's mark as it was).
    The stored part ends now and keeps its share and spending; the rest becomes a new current part that starts
    now: used_at_start is the week's usage now, and its share is what the part-start rule gives from the week's
    remainder (week.fix_share) by the new weight. Later parts follow from it as usual. While PaceLine's away
    marker is on nothing is split: access.split_refused says so and the mark stays stored for later parts."""
    entry, plan, tz = access.entry, access.plan, access.tz
    before = access.mark_at_fix
    if before is None or access.week is None or entry is None or not access.stored(entry):
        return None
    new = current_mark(access)
    recorded = entry_part(entry)
    # An entry stored before marks were recorded has none: the plan's mark when it was fixed stands in.
    recorded_mark = recorded.mark if _is_number(entry.get("mark")) else before
    if abs(new - recorded_mark) < 1e-9 or (not access.asked_current and abs(new - before) < 1e-9):
        return None
    if away.read(access.ctx.sd)["on"]:
        access.split_refused = {"kind": entry["kind"], "day": entry["day"], "mark": new}
        return None
    now = parse_iso(iso_ms(access.ctx.now))
    used_now, reset = access.week
    start, end = parse_iso(entry["start"]), parse_iso(entry["end"])
    state = reserve_state(plan, reset, tz)
    hours = (end - now) / HOUR
    cut = week.Part(recorded.day, recorded.kind, now, end, recorded.full_hours, hours, new,
                    new * hours / recorded.full_hours)
    chain = [cut] + week.parts(end, reset, tz, plan["work_hours"], plan)
    left_start = week.left(used_now, state.kept(now))
    share = week.fix_share(left_start, chain)
    # A reading older than the staleness bound may lack points spent since: the share is then an estimate.
    seen = (access.usage.get("record") or {}).get("_at")
    old = seen is None or (now - seen).total_seconds() / 60.0 > STALE_THRESHOLD_MIN
    fresh = {"day": entry["day"], "kind": entry["kind"], "start": iso_ms(now), "end": entry["end"],
             "week_reset": entry["week_reset"], "share_pct": share, "unplanned": share is None,
             "used_at_start": used_now, "estimated": old, "left_at_start": left_start,
             "weights": {"part": cut.weight, "total": sum(p.weight for p in chain)}, "prompted": False,
             "fixed_at": iso_ms(now), "mark": new, "full_hours": recorded.full_hours}
    previous_share = entry["share_pct"]
    if now <= start:
        # The part began this very millisecond: nothing of it is left to keep, so the new entry replaces it.
        plan["fixed"] = [e for e in plan["fixed"] if e is not entry]
    else:
        entry["end"] = iso_ms(now)
    plan["fixed"].append(fresh)
    access.entry = fresh
    return {"kind": entry["kind"], "day": entry["day"], "at": iso_ms(now), "mark": new, "share_pct": _r3(share),
            "previous_share_pct": _r3(previous_share), "estimated": old}


def split_line(split):
    """The command's message for a split: the change applies from now."""
    share = split["share_pct"]
    rest = f"{share:.1f} % of the week" if share is not None else "no share"
    text = (f"{split['day']} {KIND_GLYPH[split['kind']]} is the current part: the change applies from now "
            f"(the part is split here, the rest has {rest})")
    return text + "; the last reading of the week is old, so the share is an estimate" if split["estimated"] else text


def refused_line(refused):
    return (f"{refused['day']} {KIND_GLYPH[refused['kind']]} is the current part and PaceLine's away marker is on: "
            f"the part is not split, the mark is stored for later parts (the operator's next message clears the "
            f"marker; `paceline away off` does too)")


def _after_split(access, silent):
    """The no-effect list once the plan is saved: the current part's own entry is gone when it was split, and
    reads "unchanged" when its mark was already the one set (it stays "current" when the split was refused)."""
    out = []
    for kind, why in silent:
        if why == "current" and access.entry is not None and access.stored(access.entry) \
                and kind == access.entry["kind"] and access.split_refused is None:
            if access.split is not None:
                continue
            why = "unchanged"
        out.append((kind, why))
    return out


def _split_lines(access):
    """The lines a command adds after it saved the plan: the split, or why the current part was not split."""
    text = ""
    if access.split:
        text += "\n" + split_line(access.split)
    if access.split_refused:
        text += "\n" + refused_line(access.split_refused)
    return text


def _marks_line(label, marks):
    return f"{label}: " + ", ".join(f"{KIND_GLYPH[k]} {mark_label(v)}" for k, v in marks.items())


NOT_WRITTEN_TEXT = "clock is ahead of the wall clock: nothing written"


def plan_command(ctx, writes, body, check=None):
    """Run body(access) under one pace.lock: load the usage first (its merge stays outside the lock), then
    the store and the plan. The order is fixed: check(access) validates, access.fix() fixes the current part,
    body(access) changes the plan and returns (text, payload). A writing command's payload says `written`."""
    ctx.get_usage(read_profile(ctx.sd))
    with locked(ctx.sd):
        store = Store(ctx.sd)
        access = PlanAccess(ctx, store, writes=writes)
        if check is not None:
            check(access)
        access.fix()
        text, payload = body(access)
        store.save()
    if writes:
        payload = dict(payload, written=not access.skipped)
        if access.skipped:
            text += "\n" + NOT_WRITTEN_TEXT
    emit(ctx.args, text, payload)
    return EXIT_OK


def cmd_plan(ctx):
    sub = getattr(ctx.args, "plan_cmd", None)
    if sub is None:
        return plan_show(ctx)
    return {"set": plan_set, "reset": plan_reset, "template": plan_template, "offer": plan_offer}[sub](ctx)


def plan_show(ctx):
    def body(access):
        access.need_week()
        view = access.view()
        if view is None:
            raise PaceError("no part of the week holds the current time")
        return plan_text(view, access.profile), view
    return plan_command(ctx, False, body)


def plan_set(ctx):
    a = ctx.args
    if a.work is None and a.off_hours is None:
        raise UsageError("give --work, --off-hours or both")
    marks = parse_marks(a.work, a.off_hours)

    picked = []

    def check(access):
        access.need_week()
        picked.append(resolve_day(access, a.day))

    def body(access):
        day = picked[0]
        access.need_entry()
        silent = _no_effect(access, day, list(marks))
        access.asked_current = access.entry["day"] == day.isoformat() and access.entry["kind"] in marks
        stored = access.plan["days"].setdefault(day.isoformat(), {})
        stored.update(marks)
        access.save()
        split = access.split
        silent = _after_split(access, silent)
        lines = [_marks_line(day.isoformat(), marks)]
        lines += [f"{day} {KIND_GLYPH[kind]} {NO_EFFECT_TEXT[why]}: stored, this week's plan does not change"
                  for kind, why in silent]
        if split:
            lines.append(split_line(split))
        if access.split_refused:
            lines.append(refused_line(access.split_refused))
        payload = {"date": day.isoformat(), "entry": dict(stored), "split": split,
                   "split_refused": access.split_refused,
                   "no_effect": [{"kind": kind, "reason": why} for kind, why in silent], "plan": access.view()}
        return "\n".join(lines), payload
    return plan_command(ctx, True, body, check)


def plan_reset(ctx):
    a = ctx.args
    picked = []

    def check(access):
        if getattr(a, "day", None):
            access.need_week()
            picked.append(resolve_day(access, a.day).isoformat())

    def body(access):
        asked = not picked or (access.entry is not None and picked[0] == access.entry["day"])
        if picked:
            day = picked[0]
            removed = [day] if access.plan["days"].pop(day, None) is not None else []
        else:
            removed = sorted(access.plan["days"])
            access.plan["days"] = {}
        access.asked_current = asked and bool(removed)
        access.save()
        text = ("reset " + ", ".join(removed)) if removed else "nothing to reset"
        text += _split_lines(access)
        return text, {"removed": removed, "split": access.split, "split_refused": access.split_refused,
                      "plan": access.view()}
    return plan_command(ctx, True, body, check)


def template_marks(plan, name):
    """The effective marks of a weekday: the template's value, else the default."""
    stored = plan["template"].get(name) or {}
    out = {}
    for kind in PLAN_KINDS:
        out[kind] = week.DEFAULT_MARKS[kind]
        if kind in stored:
            with contextlib.suppress(ValueError):
                out[kind] = week.parse_mark(stored[kind])
    return out


def plan_template(ctx):
    a = ctx.args
    sub = getattr(a, "tpl_cmd", None) or "show"
    name = getattr(a, "weekday", None)
    if name is not None:
        name = name.strip().lower()
        if name not in week.WEEKDAYS:
            raise UsageError(f"{a.weekday} is not a weekday (mon..sun)")
    marks = {}
    if sub == "set":
        if a.work is None and a.off_hours is None:
            raise UsageError("give --work, --off-hours or both")
        marks = parse_marks(a.work, a.off_hours)

    def body(access):
        plan = access.plan
        if sub == "show":
            lines = []
            for weekday in week.WEEKDAYS:
                have = plan["template"].get(weekday) or {}
                effective = template_marks(plan, weekday)
                cells = [f"{KIND_GLYPH[k]} {mark_label(effective[k])} ({'template' if k in have else 'default'})"
                         for k in PLAN_KINDS]
                lines.append(f"{weekday}  " + "  ".join(cells))
            return "\n".join(lines), {"template": plan["template"],
                                      "effective": {w: template_marks(plan, w) for w in week.WEEKDAYS}}
        if sub == "set":
            stored = plan["template"].setdefault(name, {})
            stored.update(marks)
            access.save()
            text = _marks_line(name, marks) + _split_lines(access)
            return text, {"weekday": name, "entry": dict(stored), "split": access.split,
                          "split_refused": access.split_refused, "plan": access.view()}
        removed = [name] if name and plan["template"].pop(name, None) is not None else []
        if name is None:
            removed = [w for w in week.WEEKDAYS if w in plan["template"]]
            plan["template"] = {}
        access.save()
        text = ("reset " + ", ".join(removed)) if removed else "nothing to reset"
        return text + _split_lines(access), {"removed": removed, "split": access.split,
                                             "split_refused": access.split_refused, "plan": access.view()}
    return plan_command(ctx, sub != "show", body)


# ---------------------------------------------------------------- the off-hours offer

# The prompt hook waits for this command: a busy lock answers nothing after a moment, never after seconds.
OFFER_LOCK_WAIT_SEC = 0.5
OFFER_ANSWERS = "light, normal or heavy"


def offer_path(sd, key):
    """<state>/plan-offer-<key>.json: the part of the week the operator was last offered planning for, and how it
    ended ("offered" or "refused"). A key that is not 16 lower-case hex digits names plan-offer-unknown.json."""
    valid = isinstance(key, str) and limits_file.ACCOUNT_KEY.fullmatch(key)
    return sd / f"plan-offer-{key if valid else limits_file.UNKNOWN_ACCOUNT}.json"


def offer_part(entry):
    """What names a part for the offer: its date, kind and week. A split of the part keeps all three, so the
    rest of a part that was split by a plan change is the same part and is not offered again."""
    return {"day": entry["day"], "kind": entry["kind"], "week_reset": entry["week_reset"]}


def offer_marked(plan, entry):
    """True when the operator set the part's mark: a dated mark of its day or a template mark of its weekday."""
    day = dt.date.fromisoformat(entry["day"])
    kind = entry["kind"]
    dated = plan["days"].get(entry["day"]) or {}
    weekday = plan["template"].get(week.WEEKDAYS[day.weekday()]) or {}
    return dated.get(kind) is not None or weekday.get(kind) is not None


def offer_unplanned(entry, plan):
    """True for an off part at the default off mark that carries no share or no weight: the operator works
    where nothing was planned. A part whose mark the operator set (offer_marked) is theirs and is not offered."""
    return (entry["kind"] == "off" and (entry["share_pct"] is None or float(entry["weights"]["part"]) <= 0)
            and not offer_marked(plan, entry))


def offer_text(entry):
    return (f"PaceLine: the operator is working in off-hours that this week's plan gives no share. Ask them once "
            f"whether to plan it: {OFFER_ANSWERS}. If they answer, run `paceline plan set {entry['day']} "
            f"--off-hours <answer> < /dev/null`; if they say no, run `paceline plan offer --refuse < /dev/null`.")


def plan_offer(ctx):
    """`paceline plan offer`: print the one-line offer when it is due and record it as made, else print nothing.

    Due: pace is on, the current part is an unplanned off part at the default mark (offer_unplanned), and the part was neither
    offered nor refused yet. With --refuse: record that the operator said no, which holds until the part ends.
    One state file per account, written under pace.lock like the plan."""
    ctx.get_usage(read_profile(ctx.sd))
    refuse = bool(ctx.args.refuse)
    text, state = None, None
    with locked(ctx.sd, timeout=OFFER_LOCK_WAIT_SEC):
        if not limits_file.pace_enabled(ctx.sd):
            raise PaceError("pace is off; nothing recorded" if refuse else "pace is off")
        access = PlanAccess(ctx, {"profile": read_profile(ctx.sd)}, writes=True)
        access.need_week()
        entry = access.fix()
        if entry is None or not access.stored(entry) or access.key is None:
            raise PaceError("no stored current part to offer for")
        path = offer_path(ctx.sd, access.key)
        try:
            known = read_json(path, None)
        except PaceError:
            known = None
        part = offer_part(entry)
        seen = isinstance(known, dict) and all(known.get(k) == v for k, v in part.items())
        if refuse:
            state = "refused"
        elif not seen and offer_unplanned(entry, access.plan):
            state, text = "offered", offer_text(entry)
        if state and ctx.now <= limits_file.wall_clock() + FUTURE_SLACK:
            ctx.sd.mkdir(parents=True, exist_ok=True)
            write_json(path, {**part, "state": state, "at": iso_ms(ctx.now)})
    if refuse:
        line = f"off-hours offer: refused for {part['day']} {KIND_GLYPH[part['kind']]}, not repeated until it ends"
    else:
        line = text or ""
    if ctx.args.json:
        print(json.dumps({"offer": text, "state": state, "part": part}, indent=2, ensure_ascii=False))
    elif line:
        print(line)
    return EXIT_OK


def cmd_mode(ctx):
    a = ctx.args
    value, until_flag = a.value, a.until_part_end
    if until_flag and value != "inform":
        raise UsageError("--until-part-end goes with `mode inform`")

    def check(access):
        if until_flag:
            access.need_week()
            if access.plan["mode"] == "inform":
                raise UsageError("already in inform mode")

    def body(access):
        plan = access.plan
        if until_flag:
            entry = access.need_entry()
            begins = [parse_iso(e["start"]) for e in this_week_entries(plan, access.week[1])
                      if parse_iso(e["start"]) > parse_iso(entry["start"])]
            plan["inform_until"] = iso_ms(min([parse_iso(entry["end"])] + begins))
            access.save()
        elif value is not None:
            plan["mode"], plan["inform_until"] = value, None
            access.save()
        mode, until = effective_mode(plan, ctx.now)
        text = f"mode: {plan['mode']}"
        if until:
            text += f"\ninform until {fmt_local(parse_iso(until), access.tz)}, limit again after it"
        return text, {"mode": plan["mode"], "effective_mode": mode, "inform_until": until}
    return plan_command(ctx, value is not None, body, check)


RESERVE_RELEASE_RULE = ("the release is a weekday kept from week to week: mon..sun, or last for the last day "
                        "before the reset")
RESERVE_PCT_RULE = f"the reserve is a whole number of percent, 0-{RESERVE_PCT_MAX} (0: off)"
RESERVE_NAME_RULE = f"the reserve name is 1-{RESERVE_NAME_MAX} printable characters (an empty text clears it)"
RESERVE_RELEASE_DEFAULT_TEXT = "the last day before the reset"
RESERVE_UNKNOWN_TEXT = "release instant unknown: no usage data yet"


def reserve_request(args):
    """(pct or None, release or None, whether --release was given, name or None, whether --name was given) of
    a `reserve` command line, validated; a value that breaks a rule is a UsageError."""
    pct, release, name = args.pct, args.release, args.name
    if pct is not None:
        if not re.fullmatch(r"[0-9]{1,6}", str(pct).strip()) or not 0 <= int(pct) <= RESERVE_PCT_MAX:
            raise UsageError(f"{pct}: {RESERVE_PCT_RULE}")
        pct = int(pct)
    if release is not None:
        word = release.strip().lower()
        if word != "last" and word not in week.WEEKDAYS:
            raise UsageError(f"{release}: {RESERVE_RELEASE_RULE}")
        release = None if word == "last" else word
    if name is not None:
        name = name.strip()
        if name != "" and not reserve_name_ok(name):
            raise UsageError(f"{name!r}: {RESERVE_NAME_RULE}")
    return pct, release, args.release is not None, (name or None), name is not None


def reserve_stored(reserve):
    """The `reserve` object of a week that is not known: the stored values, no release instant."""
    return {"pct": reserve["pct"], "name": reserve["name"], "release": reserve["release"], "release_at": None}


def reserve_stored_text(reserve):
    if reserve["pct"] == 0:
        return RESERVE_TEXT_OFF
    label = reserve_label(reserve["pct"], reserve["name"])
    return f"{label}, opens on {reserve['release'] or RESERVE_RELEASE_DEFAULT_TEXT}\n{RESERVE_UNKNOWN_TEXT}"


def reserve_view(access, reserve, trace=True):
    """(the `reserve` object, its text) of a stored reserve dict, with the week when it is known."""
    if access.week is None:
        return reserve_stored(reserve), reserve_stored_text(reserve)
    saved = access.plan["reserve"]
    access.plan["reserve"] = reserve
    try:
        _state, obj, error = reserve_section(access.ctx.now, access.plan, access.week[0], access.week[1], access.tz)
    finally:
        access.plan["reserve"] = saved
    if error is not None and trace:
        limits_file.trace_failure("reserve.command", getattr(access.ctx, "sd", None), error=error, reason=obj["error"])
    return obj, obj["text"]


def cmd_reserve(ctx):
    """`paceline reserve [PCT] [--release DAY] [--name TEXT]`: show or set the weekly reserve of the account."""
    a = ctx.args
    pct, release, release_given, name, name_given = reserve_request(a)
    writes = pct is not None or release_given or name_given

    def body(access):
        old = dict(_reserve_of(access.plan.get("reserve")))
        if not writes:
            obj, text = reserve_view(access, old)
            return text, obj
        new = dict(old)
        changes = []
        if pct is not None:
            new["pct"] = pct
            changes.append(("reserve_pct", old["pct"], pct))
        if release_given:
            new["release"] = release
            changes.append(("reserve_release", old["release"], release))
        if name_given:
            new["name"] = name
            changes.append(("reserve_name", old["name"], name))
        old_obj, _old_text = reserve_view(access, old, trace=False)
        access.plan["reserve"] = new
        access.save()
        obj, text = reserve_view(access, new)
        shown = lambda v: "none" if v is None else str(v)  # noqa: E731
        lines = []
        for key, before, after in changes:
            if key == "reserve_pct":
                lines.append(f"reserve: {before} % -> {after} %")
            elif key == "reserve_release":
                lines.append(f"release: {before or 'last'} -> {after or 'last'}")
            else:
                lines.append(f"name: {shown(before)} -> {shown(after)}")
        lines.append(text)
        if obj.get("release_at") is not None:  # the week is known, the reserve is on and did not fail
            if obj["released"]:
                if release_given:
                    lines.append(f"released for this week; kept again from the reset {obj['reset_text']}")
            else:
                if old_obj.get("released"):
                    lines.append("held again: no release this week, kept to the reset " + obj["reset_text"]
                                 if obj["kept_to_reset"]
                                 else f"held again: the release moved past now, it opens {obj['release_text']}")
                if obj["over"]:
                    lines.append(f"week used {obj['used_pct']:g} % is past {obj['stop_at_pct']:g} %: "
                                 "launches ask from now")
        first = changes[0]
        payload = {"key": first[0], "old": first[1], "new": first[2],
                   "changes": [{"key": k, "old": b, "new": n} for k, b, n in changes], "reserve": obj}
        return "\n".join(lines), payload
    return plan_command(ctx, writes, body)


def profile_set_work_hours(ctx):
    """`profile set work_hours`: a key of the plan file, not of the profile."""
    value = parse_profile_value(ctx.args.value)
    try:
        new = week.format_work_hours(value if isinstance(value, str) else str(ctx.args.value))
    except ValueError as error:
        print(f"paceline: {error}; nothing changed", file=sys.stderr)
        return EXIT_USAGE
    ctx.get_usage(read_profile(ctx.sd))
    with locked(ctx.sd):
        store = Store(ctx.sd)
        access = PlanAccess(ctx, store, writes=True)
        access.fix()
        old = access.plan["work_hours"]
        access.plan["work_hours"] = new
        access.save()
        store.save()
        report = profile_status(ctx, store)
    change = f"work_hours: {json.dumps(old)} -> {json.dumps(new)}"
    applies = access.entry["end"] if access.entry else None
    if applies:
        change += NL + f"the new hours apply from {fmt_local(parse_iso(applies), access.tz)}, the end of the current part"
    payload = {"key": "work_hours", "old": old, "new": new, "applies_from": applies, "status_line": report[1],
               "status": report[2], "written": not access.skipped}
    if access.skipped:
        change += NL + NOT_WRITTEN_TEXT
    emit(ctx.args, f"{change}\n{report[0]}", payload)
    return EXIT_OK


def read_profile(sd):
    try:
        return load_stored_profile(sd / "pace-profile.json")[0]
    except PaceError:
        profile = {}
        migrate_profile(profile)
        return profile


def current_effective(ctx, allow_merge=True):
    usage = ctx.get_usage(read_profile(ctx.sd), allow_merge=allow_merge)
    record = usage.get("record")
    if not record or not record.get("seven_day"):
        return None
    return window_effective(record, ctx.now, ctx.model)[0]


def take_over_launch(reg, session):
    """Drop the oldest launch hold of the session: the agent that just started replaces it.

    Without this the agent counts twice until the launch ends on its own. A Workflow gate holds one
    launch for many starts, so its first start consumes it. No history entry is written.
    """
    if not session:
        return None
    mine = [r for r in reg["holds"] if r["kind"] == LAUNCH_KIND and r.get("session_id") == session]
    if not mine:
        return None
    oldest = min(mine, key=lambda r: parse_iso(r["acquired_at"]))
    reg["holds"].remove(oldest)
    return oldest


def cmd_hold(ctx):
    a = ctx.args
    if a.hold_cmd == "acquire":
        if a.estimate is None and a.kind != "subagent":
            raise PaceError(f"--estimate is required for kind {a.kind}")
        effective = current_effective(ctx)

        def fn(store):
            reg = store["holds"]
            if a.key:
                held = next((r for r in reg["holds"]
                             if r.get("key") == a.key and r.get("session_id") == ctx.session), None)
                if held:
                    held["renewed_at"] = iso_ms(ctx.now)
                    store.touch("holds")
                    return f"already held {held['id']} for key {a.key}", {"hold": held}, EXIT_OK
            estimate = a.estimate if a.estimate is not None else float(store["profile"]["subagent_estimate_pct"])
            pid = a.pid if a.pid is not None else claude_pid()
            image = process_image(pid) or None
            r = {"id": f"H-{uuid.uuid4().hex[:8]}", "kind": a.kind, "label": a.label or "", "key": a.key,
                 "session_id": ctx.session, "pid": pid, "image": image, "estimate_pct": estimate,
                 "acquired_at": iso_ms(ctx.now), "renewed_at": iso_ms(ctx.now),
                 "used_at_acquire": effective if effective is not None else 0.0, "item_id": a.item}
            if a.kind == "subagent":
                take_over_launch(reg, ctx.session)
            reg["holds"].append(r)
            mark_overlap(reg)
            store.touch("holds")
            total = outstanding_pct(reg, effective or 0.0)
            return (f"held {r['id']} {estimate:.2f}% ({a.kind}, pid {pid}), outstanding {total:.2f}%",
                    {"hold": r, "outstanding_pct": total}, EXIT_OK)
        return mutate(ctx, fn)
    if a.hold_cmd == "renew":
        def fn(store):
            for r in store["holds"]["holds"]:
                if r["id"] == a.id:
                    r["renewed_at"] = iso_ms(ctx.now)
                    store.touch("holds")
                    return f"renewed {r['id']} until {iso_ms(ctx.now + dt.timedelta(minutes=LEASE_TTL_MIN))}", {"hold": r}, EXIT_OK
            return f"no active hold {a.id} (expired or released)", {"hold": None}, EXIT_NONE
        return mutate(ctx, fn, need_calc=False)
    if a.hold_cmd in ("release", "release-session"):
        if a.hold_cmd == "release" and bool(a.id) == bool(a.key):
            raise PaceError("release needs a hold id or --key, not both")
        effective = current_effective(ctx, allow_merge=a.hold_cmd == "release")

        def fn(store):
            reg = store["holds"]
            if a.hold_cmd == "release" and a.key:
                targets = [r for r in reg["holds"]
                           if r.get("key") == a.key and r.get("session_id") == ctx.session]
            elif a.hold_cmd == "release":
                targets = [r for r in reg["holds"] if r["id"] == a.id]
            else:
                sid = a.target_session or ctx.session
                if not sid:
                    raise PaceError("release-session needs --session")
                targets = [r for r in reg["holds"] if r.get("session_id") == sid]
            if not targets:
                return "nothing to release", {"released": []}, EXIT_NONE if a.hold_cmd == "release" else EXIT_OK
            entries = []
            for r in targets:
                entries.append(close_hold(reg, r, ctx.now, effective,
                                                 getattr(a, "actual", None), "released"))
                reg["holds"].remove(r)
            store.touch("holds")
            summary = ", ".join(f"{e['id']} est {e['estimate_pct']:.2f}% act "
                                f"{'?' if e['actual_pct'] is None else format(e['actual_pct'], '.2f')}%" for e in entries)
            return f"released {summary}", {"released": entries}, EXIT_OK
        return mutate(ctx, fn, need_calc=a.hold_cmd == "release")
    if a.hold_cmd == "prune":
        def fn(store):
            dropped = prune_holds(store["holds"], store["profile"], ctx.now)
            store.touch("holds")
            return (f"pruned {len(dropped)} hold(s)",
                    {"pruned": [{"id": r["id"], "reason": why} for r, why in dropped]}, EXIT_OK)
        with locked(ctx.sd):
            store = Store(ctx.sd)
            line, payload, code = fn(store)
            store.save()
        emit(a, line, payload)
        return code
    if a.hold_cmd == "list":
        with locked(ctx.sd):
            store = Store(ctx.sd)
            housekeeping(store, ctx.now)
            store.save()
        rs = store["holds"]["holds"]
        text = "\n".join(f"{r['id']} {r['kind']} {r['estimate_pct']:.2f}% pid {r['pid']} session {r['session_id']} "
                         f"renewed {r['renewed_at']} {r['label']}" for r in rs) or "no active holds"
        emit(a, text, {"holds": rs})
        return EXIT_OK
    if a.hold_cmd == "reconcile":
        with locked(ctx.sd):
            store = Store(ctx.sd)
        done = [c for c in store["holds"]["completed"] if c.get("actual_pct") is not None and c.get("estimate_pct")]
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
                         f" median actual/estimate {v['median_ratio']}" for k, v in summary.items()) or "no completed holds"
        emit(a, text, {"by_kind": summary, "completed": done[-50:]})
        return EXIT_OK
    raise PaceError("unknown hold command")


def cmd_calibrate(ctx):
    with locked(ctx.sd):
        store = Store(ctx.sd)
        store.save()
    key = own_account(ctx.session, ctx.sd)[0]
    history = read_history(ctx.sd, key)
    try:
        plan = load_plan(ctx.sd, key)
    except PaceError:
        plan = None
    result = calibrate(history, store, ctx.now, store["profile"], ctx.model, plan)
    if result.get("note"):
        text = result["note"]
    elif not result["proposals"]:
        text = f"{result['days']} days of history: no corrections proposed"
    else:
        text = "; ".join(f"{p['parameter']} {p['current']} -> {p['proposed']} ({p['evidence']})"
                         + (f": paceline reserve {p['proposed']}" if p["parameter"] == "reserve" else "")
                         for p in result["proposals"])
    emit(ctx.args, text, result)
    return EXIT_OK


PROFILE_INTERNAL_KEYS = ("version", "timezone_confirmed")
PROFILE_PERCENT_KEYS = {k for k in PROFILE_DEFAULTS if k.endswith("_pct")}


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
    if key == "timezone_mode":
        if value in five_burn.TIMEZONE_MODES:
            return value
        raise PaceError('timezone_mode must be "machine" or "fixed"')
    if key == "timezone":
        if not isinstance(value, str) or not value.strip():
            raise PaceError("timezone must be the name of a time zone, for example Europe/Madrid")
        load_zone(value)
        return value
    if key == "memory_guard":
        if isinstance(value, bool):
            return value
        raise PaceError("memory_guard must be true or false")
    if not _is_number(value):
        raise PaceError(f"{key} must be a number")
    if isinstance(default, int) and not _is_whole(value):
        raise PaceError(f"{key} must be a whole number")
    if key in PROFILE_PERCENT_KEYS and not 0 <= value <= 100:
        raise PaceError(f"{key} must lie in 0-100")
    if value < 0:
        raise PaceError(f"{key} must not be negative")
    return int(value) if isinstance(default, int) else value


def profile_mode(profile):
    """The time zone mode of a profile, "machine" or "fixed" (an invalid or missing value reads as fixed)."""
    return effective_zone(profile, lambda: (None, False))[0]


def timezone_hint(profile, detected, usable):
    """What to tell the owner while the profile counts in UTC and this machine is in another zone."""
    if profile.get("timezone") != "UTC" or profile.get("timezone_confirmed") or not detected or detected == "UTC":
        return None
    machine = "`paceline profile set timezone_mode machine`"
    if profile_mode(profile) == "machine":
        # Detection is tried on every load, so this holds only while the detected zone has no data here.
        return (f"This machine is in {detected}, but Python here has no time zone data for it, so the pace profile "
                "counts in UTC. Run `python -m pip install tzdata`; the profile then follows this machine.")
    command = f"`paceline profile set timezone {detected}`"
    if usable:
        return (f"This machine is in {detected}, but the pace profile counts in UTC. Run {command} to count in "
                f"this machine's zone, {machine} to follow this machine as it changes, or "
                "`paceline profile set timezone UTC` to keep UTC.")
    return (f"This machine is in {detected}, but the pace profile counts in UTC, and Python here has no time zone "
            f"data for {detected}. Run `python -m pip install tzdata` first, then {command} or {machine}.")


def profile_check(ctx, profile):
    detected, usable = detect_zone()
    confirmed = bool(profile.get("timezone_confirmed"))
    hint = timezone_hint(profile, detected, usable)
    mode = profile_mode(profile)
    result = {"timezone": profile.get("timezone"), "confirmed": confirmed, "detected": detected,
              "usable": usable, "hint": hint, "mode": mode,
              "effective": effective_zone(profile, lambda: (detected, usable))[1]}
    if hint:
        line = hint
    elif mode == "machine":
        line = (f"timezone follows this machine: {profile.get('timezone')}" if usable
                else f"timezone follows this machine, but Python here has no time zone data for {detected} "
                   f"(install tzdata); using {profile.get('timezone')}" if detected
                   else f"timezone follows this machine, but no zone was detected; using {profile.get('timezone')}")
    else:
        line = (f"timezone {profile.get('timezone')} ({'confirmed' if confirmed else 'not confirmed'}); detected "
                + (f"{detected}" + ("" if usable else " (no time zone data for it here)") if detected else "nothing"))
    emit(ctx.args, line, result)
    return EXIT_OK


NL = "\n"


def profile_set(ctx):
    if ctx.args.key == "work_hours":
        return profile_set_work_hours(ctx)
    key, value = ctx.args.key, parse_profile_value(ctx.args.value)
    ctx.get_usage(read_profile(ctx.sd))
    with locked(ctx.sd):
        store = Store(ctx.sd)
        try:
            new = check_profile_value(key, value)
        except PaceError as error:
            print(f"paceline: {error}; nothing changed", file=sys.stderr)
            return EXIT_USAGE
        keep_plan_current(ctx, store)  # the current part is fixed under the old zone and reserve
        profile = store["profile"]
        old = profile[key]
        old_zone, old_mode = profile["timezone"], profile_mode(profile)
        profile[key] = new
        if key == "timezone":
            profile["timezone_confirmed"] = True
            profile["timezone_mode"] = "fixed"
        elif key == "timezone_mode":
            if new == "machine":
                _, zone, followed = effective_zone(profile, detect_zone)
                if followed:
                    profile["timezone"], profile["timezone_confirmed"] = zone, True
            else:
                profile["timezone_confirmed"] = True
        store.touch("profile")
        store.save()
        report = profile_status(ctx, store)
    change = f"{key}: {json.dumps(old, ensure_ascii=False)} -> {json.dumps(new, ensure_ascii=False)}"
    if key == "timezone" and old_mode != "fixed":
        change += NL + 'timezone_mode: "machine" -> "fixed" (a zone you set no longer follows this machine)'
    elif key == "timezone_mode":
        if profile["timezone"] != old_zone:
            change += NL + f"timezone: {json.dumps(old_zone)} -> {json.dumps(profile['timezone'])}"
        elif new == "machine" and old_mode != "machine":
            change += NL + f"timezone stays {json.dumps(old_zone)}: no usable zone was detected on this machine"
    payload = {"key": key, "old": old, "new": new, "status_line": report[1], "status": report[2]}
    emit(ctx.args, f"{change}\n{report[0]}", payload)
    return EXIT_OK


def profile_status(ctx, store):
    """(text, status line or None, status or None) of the status report a profile command ends with; the
    caller holds pace.lock."""
    try:
        line, calc = status_report(ctx, store, report=False)
        return line, line, calc
    except PaceError as error:
        return f"status unavailable: {error}", None, None


def cmd_profile(ctx):
    action = getattr(ctx.args, "profile_cmd", None) or "show"
    if action == "set":
        return profile_set(ctx)
    with locked(ctx.sd):
        store = Store(ctx.sd)
        store.save()
    if action == "check":
        return profile_check(ctx, store["profile"])
    profile = dict(store["profile"])
    emit(ctx.args, json.dumps(profile, ensure_ascii=False), profile)
    return EXIT_OK


WORK_HOURS_RANGE = ("HH:MM-HH:MM in steps of 15 min, within one day, at least 1 h of working hours and "
                    "1 h of off-hours")


def _settings_plan_key(ctx):
    """(account key, None), or (None, the reason text) while the session's account is not known."""
    key, reason = own_account(ctx.session, ctx.sd)
    if key or reason not in limits_file.NO_RECORD_REASONS:
        return key or None, None
    return None, f"the session's account is not known yet ({reason})"


def settings_report(ctx):
    """Everything `paceline settings` shows, as the JSON object of the spec's shared design."""
    import usage_watch
    # Only what the view shows is read: the profile (in memory, nothing is saved), the usage-watch switch, the
    # switches. A damaged hold registry file cannot reach it.
    with locked(ctx.sd):
        profile, _changed = load_stored_profile(ctx.sd / "pace-profile.json")
        stored_zone = profile.get("timezone")
        mode, zone_name, followed = effective_zone(profile, detect_zone)
        profile["timezone"] = zone_name
        if followed:
            profile["timezone_confirmed"] = True
        marker = away.read(ctx.sd)
        switches = {"pace": limits_file.pace_enabled(ctx.sd), "usage_watch": usage_watch.is_on(ctx.sd),
                    "away": {"on": marker["on"], "set_at": marker["set_at"] if isinstance(marker["set_at"], str) else None}}
        plan_key, plan_note = _settings_plan_key(ctx)
        plan_file = None if plan_note else str(plan_path(ctx.sd, plan_key))  # no file while the account is unknown
        plan_hours, plan_reserve = None, dict(RESERVE_DEFAULTS)
        if plan_note is None:
            try:
                loaded = load_plan(ctx.sd, plan_key)
                plan_hours, plan_reserve = loaded["work_hours"], loaded["reserve"]
            except PaceError as error:
                plan_note = str(error)
            except Exception as error:  # noqa: BLE001 - settings never fails because of the plan file
                limits_file.trace_failure("settings.plan", ctx.sd, error=error)
                plan_note = "the week plan could not be read"
    detected, usable = detect_zone()
    timezone = {"mode": profile_mode(profile), "stored": stored_zone if isinstance(stored_zone, str) else "UTC",
                "effective": profile["timezone"], "detected": detected, "usable": usable,
                "hint": timezone_hint(profile, detected, usable)}
    work_hours = {"value": plan_hours, "default": week.DEFAULT_WORK_HOURS, "range": WORK_HOURS_RANGE}
    if plan_hours is None:
        work_hours["note"] = plan_note
    reserve_rows = {
        "reserve_pct": {"value": plan_reserve["pct"], "default": RESERVE_DEFAULTS["pct"], "range": RESERVE_PCT_RANGE},
        "reserve_release": {"value": plan_reserve["release"], "default": RESERVE_DEFAULTS["release"],
                            "range": RESERVE_RELEASE_RANGE},
        "reserve_name": {"value": plan_reserve["name"], "default": RESERVE_DEFAULTS["name"],
                         "range": RESERVE_NAME_RANGE}}
    if plan_hours is None:
        for row in reserve_rows.values():
            row["value"], row["note"] = None, plan_note
    return {
        "profile": {k: {"value": profile[k], "default": json.loads(json.dumps(PROFILE_DEFAULTS[k]))}
                    for k in PROFILE_DEFAULTS if k not in PROFILE_INTERNAL_KEYS},
        "plan": {"work_hours": work_hours, **reserve_rows},
        "switches": switches,
        "timezone": timezone,
        "moved": dict(MOVED_GROUPS),
        "files": {"profile": str(ctx.sd / "pace-profile.json"), "plan": plan_file},
    }


def settings_text(report):
    def shown(value):
        return json.dumps(value, ensure_ascii=False)

    def rows(entries, marked=None, notes=()):
        width = max(len(k) for k in entries)
        out = []
        for k, e in entries.items():
            changed = e["value"] != e["default"] if marked is None else marked(k, e)
            tail = ""
            if changed and k == "timezone":
                tail = "  (default follows this machine)"
            elif changed:
                tail = "  (default " + shown(e["default"]) + ")"
            if k in notes:
                tail += "  (used while memory_guard is on)"
            out.append(f"  {'* ' if changed else '  '}{k:<{width}}  {shown(e['value'])}{tail}")
        return out

    def profile_marked(k, e):
        # The zone row is marked while the mode is fixed (the default mode follows the machine); the mode row never.
        if k == "timezone":
            return report["timezone"]["mode"] != "machine"
        return k != "timezone_mode" and e["value"] != e["default"]

    sw = report["switches"]
    onoff = lambda on: "on" if on else "off"  # noqa: E731
    zone = report["timezone"]
    lines = [f"PaceLine settings. Switches: pace {onoff(sw['pace'])}, usage-watch {onoff(sw['usage_watch'])}, "
             f"away {onoff(sw['away']['on'])}",
             "A * marks a value that differs from its default.", ""]
    lines.append(f"Time zone: {zone['mode']} mode, stored {zone['stored']}, in use {zone['effective']}, "
                 f"detected {zone['detected'] or 'nothing'}{'' if zone['usable'] else ' (not usable here)'}")
    if zone["hint"]:
        lines.append(f"  {zone['hint']}")
    hours = report["plan"]["work_hours"]
    if hours["value"] is None:
        plan_rows = [f"    work_hours  null  ({hours['note']})"]
        plan_rows += [f"    {k}  {shown(v['value'])}  ({v['note']})" for k, v in report["plan"].items()
                      if k != "work_hours" and "note" in v]
    else:
        plan_rows = rows(report["plan"])
    lines += ["", f"Plan ({report['files']['plan']}):" if report["files"]["plan"] else "Plan:", *plan_rows]
    profile_rows = {}
    for k, e in report["profile"].items():
        if k in MEMORY_GUARD_KEYS:
            continue
        profile_rows[k] = e
        if k == "memory_guard":
            profile_rows.update({m: report["profile"][m] for m in MEMORY_GUARD_KEYS if m in report["profile"]})
    lines += ["", f"Profile ({report['files']['profile']}):",
              *rows(profile_rows, profile_marked, notes=MEMORY_GUARD_KEYS), "", MOVED_LINE]
    return NL.join(lines)


def cmd_settings(ctx):
    report = settings_report(ctx)
    emit(ctx.args, settings_text(report), report)
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


SWITCH_ON_NOTE = ("the hooks now ask before an Agent or Workflow launch at a safety stop, in limit mode also over "
                  "the part's share and give the model a pace: line, and place a hold for each subagent")
SWITCH_OFF_NOTE = "the hooks add nothing; every paceline command still runs"


def remove_all_week_plans(sd):
    """Remove every week-plan-*.json and plan-offer-*.json (the off-hours offer's record) of the state
    directory; a failure is traced by unlink_week_plan."""
    for path in [*sd.glob("week-plan-*.json"), *sd.glob("plan-offer-*.json")]:
        unlink_week_plan(sd, path)


def cmd_switch(ctx):
    on = ctx.args.cmd == "on"
    if on:
        # The usage loads first, its merge outside the lock (as plan_command); the switch and the plan file
        # change together inside it.
        profile = read_profile(ctx.sd)
        ctx.get_usage(profile)
        with locked(ctx.sd):
            limits_file.set_pace(ctx.sd, True)
            keep_plan_current(ctx, {"profile": profile})
    else:
        # The kill switch loads no usage and waits for nothing: the switch first, outside the lock, so a
        # publisher that takes the lock afterwards reads "off" and writes nothing; then the files go inside
        # the lock, which catches a publisher that read "on" before the switch flipped. A busy lock removes
        # them anyway.
        limits_file.set_pace(ctx.sd, False)
        try:
            with locked(ctx.sd):
                remove_all_week_plans(ctx.sd)
        except PaceError as error:
            limits_file.trace_failure("week_plan.publish", ctx.sd, error=error, reason="pace.lock busy on off")
            remove_all_week_plans(ctx.sd)
    line = f"pace is {'on' if on else 'off'}: {SWITCH_ON_NOTE if on else SWITCH_OFF_NOTE}"
    emit(ctx.args, line, {"pace": "on" if on else "off", "switch": str(ctx.sd / limits_file.SWITCH_FILE)})
    return EXIT_OK


# ---------------------------------------------------------------- doctor

DOCTOR_LOG_LINES = 20
BUNDLE_HISTORY_LINES = 50
BUNDLE_HOLDS = 20
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
    if age is None or age > STALE_THRESHOLD_MIN:
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
    stale = oldest is None or oldest > FRESH_READING_MIN or any(b["implausible"] for b in buckets.values())
    return ("warn" if stale else "ok"), evidence


def probe_account(session=None, sd=None):
    evidence = limits_file.account_report(session, sd)
    return ("ok" if evidence["state"] == "known" else "warn"), evidence


def probe_process():
    table = process_table()
    chain, pid = claude_ancestors(table)
    evidence = {"table_size": len(table), "chain": [{"pid": p, "ppid": pp, "name": n} for p, pp, n in chain],
                "claude_pid": pid, "claude_pid_env": os.environ.get("CLAUDE_PID")}
    owner = pid if pid is not None else env_claude_pid()
    evidence.update(owner_pid=owner, owner_source="chain" if pid is not None else "env" if owner is not None else "none")
    if os.name != "nt":
        raw = run_command(["ps", "-A", "-o", "pid=,ppid=,comm="]) or run_command(["ps", "-o", "pid,ppid,comm"])
        evidence["ps_head"] = (raw or "").splitlines()[:5] if raw else None
        evidence["proc_available"] = Path("/proc/self/stat").exists()
    return ("ok" if owner is not None else "warn"), evidence


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
    mode, effective, _ = effective_zone(profile, detect_zone)
    tz = load_zone(effective)
    return "ok", {"profile_timezone": profile["timezone"], "mode": mode,
                  "effective": effective, "zone": type(tz).__name__, "now_local": fmt_local(now, tz)}


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
        probe("account", probe_account, ctx.session, sd),
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
        registry = read_json(sd / "pace-holds.json", {}) or {}
    members = {
        "doctor.json": json.dumps(report, indent=2, ensure_ascii=False, default=str),
        "debug.log": text(limits_file.DEBUG_LOG),
        "debug.log.1": text(limits_file.DEBUG_LOG + ".1"),
        "pace-profile.json": text("pace-profile.json"),
        "pace.json": text(limits_file.SWITCH_FILE),
        "rate-limits.json": text("rate-limits.json"),
        "rate-limits.history.tail.jsonl": "\n".join(((text("rate-limits.history.jsonl") or "").splitlines())
                                                    [-BUNDLE_HISTORY_LINES:]),
        "pace-holds.json": json.dumps({
            "holds": registry.get("holds", [])[-BUNDLE_HOLDS:],
            "hold_count": len(registry.get("holds", [])),
            "completed_count": len(registry.get("completed", [])),
            "completed_last": registry.get("completed", [])[-BUNDLE_HOLDS:]}, indent=2, default=str),
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


def iso_instant_arg(text):
    """argparse type for --reset: an ISO 8601 instant, else a usage error (exit 2)."""
    try:
        ok = parse_iso(text) is not None
    except (ValueError, TypeError, OverflowError):
        ok = False
    if not ok:
        raise argparse.ArgumentTypeError(f"{text!r} is not an ISO 8601 instant")
    return text


def build_parser():
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", help="print the full result as JSON")
    common.add_argument("--model", help="current model id; selects a model-scoped weekly bucket when the limits file has one")
    common.add_argument("--session", help="Claude session id (default: $CLAUDE_SESSION_ID, else $CLAUDE_CODE_SESSION_ID)")
    common.add_argument("--no-merge", action="store_true", help="do not merge the inbox readings into rate-limits.json first")
    common.add_argument("--now", help="override the clock (ISO 8601), for what-if and tests")
    common.add_argument("--state-dir", help="state directory (default: $PACELINE_STATE_DIR, else $CLAUDE_CONFIG_DIR/state/paceline, else ~/.claude/state/paceline)")

    parser = argparse.ArgumentParser(
        prog="paceline", parents=[common],
        description="Subscription usage pacing, plus the usage-watch and away groups.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("on", parents=[common], help="turn the pace hooks on")
    sub.add_parser("off", parents=[common], help="turn the pace hooks off (every command still runs)")
    status = sub.add_parser("status", parents=[common], help="interactive one-line recommendation")
    status.add_argument("--launch-key", help="also take a short-lived launch hold under this key, counted after the report")
    status.add_argument("--account", metavar="KEY", help="report this account's partition instead of the signed-in one: "
                        "the first 4 or more hex digits of a key in rate-limits.json")
    sub.add_parser("advice", parents=[common], help="may unattended work start: start, wait or stop (exit 0, 10 or 11), "
                   "with the budget and agents; writes advice-<account>.json")
    sub.add_parser("calibrate", parents=[common], help="propose parameter corrections from history")
    sim = sub.add_parser("simulate", parents=[common],
                         help="replay a week through the budget with your profile (a model: one reading per hour, "
                              "no plans or holds)")
    sim.add_argument("--week", action="append", choices=("on-track", "heavy-start", "light-start", "away-thu-fri", "all"),
                     help="a week shape to replay; repeatable (default: all)")
    sim.add_argument("--night-queue", choices=("no", "yes", "both"), help="with, without or both (default: both)")
    sim.add_argument("--reset", metavar="ISO", type=iso_instant_arg, help="the weekly reset the window ends at (default: the account's next, "
                     "else the next Monday 00:00 in the profile's zone)")
    sim.add_argument("--defaults", action="store_true", help="use the built-in defaults in UTC instead of your profile")
    sim.add_argument("--cached", action="store_true", help="answer from pace-simulate.json when its key matches, else store")
    sub.add_parser("settings", parents=[common], help="every setting and switch with its value, default and source; "
                   "read-only")
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

    plan = sub.add_parser("plan", parents=[common], help="the week plan: marks per part, shares, template (no "
                          "subcommand: show the week)")
    psub = plan.add_subparsers(dest="plan_cmd")
    p = psub.add_parser("set", parents=[common], help="mark a day's working hours and off-hours")
    p.add_argument("day", help="today, tomorrow, mon..sun (the next such day) or YYYY-MM-DD")
    p.add_argument("--work", metavar="V", help="mark for the working hours: off, light, normal, heavy or 0-2 in steps of 0.25")
    p.add_argument("--off-hours", dest="off_hours", metavar="V", help="mark for the off-hours after them")
    p = psub.add_parser("reset", parents=[common], help="remove a day's marks, or every day's")
    p.add_argument("day", nargs="?")
    p = psub.add_parser("offer", parents=[common], help="the off-hours offer a prompt hook makes: print its line when "
                        "the current off part is unplanned (default mark, no share) and was not offered yet; "
                        "--refuse records a no")
    p.add_argument("--refuse", action="store_true", help="the operator said no: no offer again until the part ends")
    tpl = psub.add_parser("template", parents=[common], help="the weekday marks every week starts from")
    tsub = tpl.add_subparsers(dest="tpl_cmd")
    tsub.add_parser("show", parents=[common], help="the template (the default)")
    p = tsub.add_parser("set", parents=[common], help="mark a weekday")
    p.add_argument("weekday", metavar="WEEKDAY", help="mon..sun")
    p.add_argument("--work", metavar="V")
    p.add_argument("--off-hours", dest="off_hours", metavar="V")
    p = tsub.add_parser("reset", parents=[common], help="remove a weekday's marks, or the whole template")
    p.add_argument("weekday", metavar="WEEKDAY", nargs="?")

    mode = sub.add_parser("mode", parents=[common], help="inform (no plan prompts) or limit (a launch past the part's "
                          "share asks); bare: show it")
    mode.add_argument("value", nargs="?", choices=PLAN_MODES, help="inform or limit")
    mode.add_argument("--until-part-end", action="store_true", help="with inform, in limit mode: leave limit until "
                      "the current part ends")

    res = sub.add_parser("reserve", parents=[common], help="the weekly reserve: show it, or set its size, release "
                         "weekday and name")
    res.add_argument("pct", nargs="?", metavar="PCT", help="size in whole percent of the week, 0-50 (0: off)")
    res.add_argument("--release", metavar="DAY", help="the weekday it opens on: mon..sun, or last for the last day "
                     "before the reset")
    res.add_argument("--name", metavar="TEXT", help="a name of 1-32 printable characters; an empty text clears it")

    hold = sub.add_parser("hold", parents=[common], help="hold registry")
    rsub = hold.add_subparsers(dest="hold_cmd", required=True)
    r = rsub.add_parser("acquire", parents=[common])
    r.add_argument("--kind", choices=["subagent", "workflow", "gate", "queue"], required=True)
    r.add_argument("--estimate", type=float,
                   help="estimated cost, %% of the weekly limit (subagent default: profile subagent_estimate_pct)")
    r.add_argument("--label", default="")
    r.add_argument("--key", help="caller's own id, such as a hook's agent_id: one active hold per key and session")
    r.add_argument("--pid", type=int, help="owning Claude process (default: nearest claude* ancestor)")
    r.add_argument("--item", help="Stint item id this hold belongs to")
    rsub.add_parser("renew", parents=[common]).add_argument("id")
    r = rsub.add_parser("release", parents=[common])
    r.add_argument("id", nargs="?")
    r.add_argument("--key", help="release the hold acquired with this --key in this session")
    r.add_argument("--actual", type=float, help="actual cost, %% of the weekly limit (default: measured delta)")
    r = rsub.add_parser("release-session", parents=[common])
    r.add_argument("--target-session", help="session whose holds to release (default: --session)")
    for name in ("prune", "list", "reconcile"):
        rsub.add_parser(name, parents=[common])

    # paceline.py hands these groups to their own modules before this parser runs; they are listed
    # here so that --help shows them.
    for name, about in {**GROUPS, **{name: "moved to the Stint plugin" for name in MOVED_COMMANDS}}.items():
        sub.add_parser(name, help=about, add_help=False).add_argument("rest", nargs=argparse.REMAINDER)
    return parser


# The groups that now belong to the PitCrew plugin, and the plugin that owns each.
MOVED_GROUPS = {"context-watch": "pitcrew", "cache-watch": "pitcrew"}
MOVED_LINE = "context-watch and cache-watch moved to the PitCrew plugin: /pitcrew:context-watch, /pitcrew:cache-watch."

# The commands that now belong to the Stint plugin, with the line each prints.
MOVED_COMMANDS = {
    "queue": "paceline queue moved to the Stint plugin: run stint add|list|report|start|done|fail|defer|retry|remove|"
             "clear-done, or /stint:queue",
    "auto": "paceline auto moved to the Stint plugin: run stint next, or /stint:run"}

MOVED_ABOUT = "moved to the PitCrew plugin"
GROUPS = {"away": "tell the launch gate that you are away, so it denies a launch it would ask about: on|off|status",
          **{group: MOVED_ABOUT for group in MOVED_GROUPS},
          "usage-watch": "read the account's exact usage through Claude Code: on|off|status"}


def cmd_simulate(ctx):
    import simulate
    return simulate.main(ctx, sys.modules[__name__])


def moved_group(group):
    """One line on stderr, nothing on stdout, exit status 2: the two groups that moved to the PitCrew plugin."""
    print(f"paceline {group} moved to the PitCrew plugin: run pitcrew {group} ..., or /pitcrew:{group}",
          file=sys.stderr)
    return EXIT_USAGE


def moved_command(command):
    """One line on stderr, nothing on stdout, exit status 2: the two commands that moved to the Stint plugin."""
    print(MOVED_COMMANDS[command], file=sys.stderr)
    return EXIT_USAGE


def cmd_group(ctx):
    if ctx.args.cmd == "away":
        import away
        return away.main(ctx.args.rest)
    if ctx.args.cmd in MOVED_GROUPS:
        return moved_group(ctx.args.cmd)
    import usage_watch
    return usage_watch.main(ctx.args.rest)


COMMON_DEFAULTS = {"json": False, "model": None, "session": None, "no_merge": False, "now": None,
                   "state_dir": None}


def with_defaults(args):
    """The parsed arguments with every common option set (the parser leaves an option the command line did not
    give unset)."""
    for key, value in COMMON_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    return args


OLD_HOLD_COMMANDS = ("acquire", "renew", "release", "release-session", "prune", "list", "reconcile")


def value_options(parser):
    """Every option string of the parser and its subcommands that takes a value."""
    found, stack = set(), [parser]
    while stack:
        for action in stack.pop()._actions:
            if isinstance(action, argparse._SubParsersAction):
                stack.extend(action.choices.values())
            elif action.option_strings and action.nargs != 0:
                found.update(action.option_strings)
    return found


def first_command(tokens, takes_value):
    """The first non-option token, or None. Skips an option that takes a value (in `takes_value`, or an
    abbreviation argparse would accept for one) together with its value; an `--option=value` token carries
    its own."""
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif token.startswith("--") and "=" not in token and any(o.startswith(token) for o in takes_value):
            skip = True
        elif not token.startswith("-"):
            return token
    return None


def old_hold_command(tokens, takes_value):
    """The old `reserve <subcommand>` name in the tokens, or None. Skips an option that takes a value (in
    `takes_value`, or an abbreviation argparse would accept for one) together with its value; an
    `--option=value` token carries its own."""
    names, skip = [], False
    for token in tokens:
        if skip:
            skip = False
        elif token.startswith("--") and "=" not in token and any(o.startswith(token) for o in takes_value):
            skip = True
        elif not token.startswith("-"):
            names.append(token)
            if len(names) == 2:
                break
    return names[1] if len(names) == 2 and names[0] == "reserve" and names[1] in OLD_HOLD_COMMANDS else None


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
    tokens = sys.argv[1:] if argv is None else argv
    first = first_command(tokens, value_options(parser))
    if first in MOVED_GROUPS:
        return moved_group(first)
    if first in MOVED_COMMANDS:
        return moved_command(first)
    old = old_hold_command(tokens, value_options(parser))
    if old:
        print(f"paceline: reserve {old} is now paceline hold {old} (reserve sets the weekly reserve)", file=sys.stderr)
        return EXIT_USAGE
    args = with_defaults(parser.parse_args(argv))
    ctx = Context(args)
    TRACE_DIR = ctx.sd
    handlers = {"status": cmd_status, "advice": cmd_advice, "plan": cmd_plan, "hold": cmd_hold, "reserve": cmd_reserve,
                "calibrate": cmd_calibrate, "simulate": cmd_simulate, "profile": cmd_profile,
                "settings": cmd_settings, "mode": cmd_mode,
                "merge": cmd_merge, "doctor": cmd_doctor, "on": cmd_switch, "off": cmd_switch,
                "cache-watch": cmd_group, "context-watch": cmd_group, "usage-watch": cmd_group}
    started, code = time.monotonic(), EXIT_ERROR
    try:
        code = handlers[args.cmd](ctx)
        return code
    except (AccountChoiceError, UsageError) as error:
        print(f"paceline: {error}", file=sys.stderr)
        code = EXIT_USAGE
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
