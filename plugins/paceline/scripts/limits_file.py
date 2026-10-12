"""The limits file every reader shares: <state>/rate-limits.json, written from the sessions' readings.

paceline's function-hooks module drops one reading per session into <state>/inbox/ from the
`rateLimits` Claude Code hands to plugins; another plugin may drop readings there in the same format.
merge_step() merges every reading into rate-limits.json under <state>/limits.lock. It is the only
writer of that file; scripts/limits_merge.py runs it from the hooks, scripts/pace.py from its
commands. The file format, the inbox format and the merge rule are the contract in docs/reference.md,
sections "The limits file (for readers)" and "The inbox (for writers)".

<state> is $PACELINE_STATE_DIR, else <config>/state/paceline, where <config> is $CLAUDE_CONFIG_DIR,
else ~/.claude.

Standard library only; Python 3.9 or later.
"""
import contextlib
import datetime as dt
import json
import math
import os
import re
import sys
import time
import traceback
import uuid
from pathlib import Path

UTC = dt.timezone.utc
HISTORY_LIMIT_BYTES = 1024 * 1024
FUTURE_SLACK = dt.timedelta(minutes=5)
# A reading of the account's own usage, as /usage shows it; every other reading is "payload".
EXACT_SOURCE = "usage-endpoint"
# How long ordinary readings leave an exact one alone; pitboard shows scoped for as long after one.
EXACT_HOLD = dt.timedelta(minutes=15)
WINDOW_LENGTH = {"five_hour": dt.timedelta(hours=5), "seven_day": dt.timedelta(days=7)}
# Top-level fields of rate-limits.json that describe the record rather than hold a bucket.
RECORD_META = ("observed_at", "source", "session_id", "model", "scoped")
INBOX_DIR = "inbox"
INBOX_BUCKETS = ("five_hour", "seven_day")
INBOX_MAX_AGE = dt.timedelta(days=7)
LOCK_FILE = "limits.lock"
SWITCH_FILE = "pace.json"
SKIP_LOCK = f"skipped: another writer holds {LOCK_FILE}"


# A fraction of a second, before an offset or the end of the text.
_FRACTION = re.compile(r"\.(\d+)(?=[+-]\d{2}:\d{2}$|$)")


def fromisoformat(value):
    """datetime.fromisoformat for the forms the readings and the files hold, on Python 3.9 too.

    Before 3.11 it takes neither "Z" nor a fraction of other than 3 or 6 digits, and reset times can
    carry other counts ("02:59:59.7+00:00"), which 3.9 would refuse.
    """
    text = str(value).strip().replace("Z", "+00:00")
    return dt.datetime.fromisoformat(_FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text))


def parse_iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, UTC)
    d = fromisoformat(value)
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)


def safe_iso(value):
    with contextlib.suppress(ValueError, TypeError, OverflowError, OSError):
        return parse_iso(value)
    return None


def iso_ms(d):
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def round_minute(d):
    return (d + dt.timedelta(seconds=30)).replace(second=0, microsecond=0)


def wall_clock():
    """The real time. A writer may date its reading by another clock (pace's --now); what counts as
    "from the future" is always judged against this one, so a what-if run never sheds real readings."""
    return dt.datetime.now(UTC)


def config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def paceline_dir():
    """<state>: every file paceline keeps outside the plugin's data directory and the project."""
    return Path(os.environ.get("PACELINE_STATE_DIR") or config_dir() / "state" / "paceline")


# ---------------------------------------------------------------- the pace switch

def pace_enabled(sd=None):
    """Whether the pace hooks run: <state>/pace.json holds {"on": true}. Anything else reads as off."""
    switch = read_json((sd or paceline_dir()) / SWITCH_FILE)
    return bool(switch) and switch.get("on") is True


def set_pace(sd, on):
    sd.mkdir(parents=True, exist_ok=True)
    write_json(sd / SWITCH_FILE, {"on": bool(on)})


# ---------------------------------------------------------------- record

def is_bucket(value):
    return isinstance(value, dict) and isinstance(value.get("used_percentage"), (int, float)) \
        and not isinstance(value["used_percentage"], bool)


def window_of(bucket):
    """A bucket's window: its reset in whole minutes, since a reported reset drifts by a fraction of a second."""
    reset = safe_iso(bucket.get("resets_at"))
    return math.floor(reset.timestamp() / 60 + 0.5) if reset else -math.inf


def bucket_at(bucket, record=None):
    """When a bucket's reading was true; a bucket without its own time takes the record's."""
    return safe_iso(bucket.get("observed_at") or (record or {}).get("observed_at"))


def from_future(name, bucket, record, now):
    """For a held bucket: observed too far ahead, or a window ending later than its length allows."""
    at = bucket_at(bucket, record)
    reset = safe_iso(bucket.get("resets_at"))
    return bool((at and at > now + FUTURE_SLACK)
                or (name in WINDOW_LENGTH and reset and reset > now + WINDOW_LENGTH[name] + FUTURE_SLACK))


def reading_source(reading):
    return EXACT_SOURCE if reading.get("source") == EXACT_SOURCE else "payload"


def reading_scoped(reading):
    """The valid model-scoped buckets an exact reading carries, resets rounded like the buckets'."""
    out = []
    for entry in reading.get("scoped") if isinstance(reading.get("scoped"), list) else []:
        if not (is_bucket(entry) and isinstance(entry.get("name"), str)):
            continue
        clean = {"name": entry["name"], "used_percentage": entry["used_percentage"]}
        reset = safe_iso(entry.get("resets_at"))
        if reset:
            clean["resets_at"] = iso_ms(round_minute(reset))
        out.append(clean)
    return out


def inbox_buckets(readings, clock):
    """(name, bucket, reading) for each plausible bucket of the inbox readings, dated by its reading."""
    for reading in readings:
        at = safe_iso(reading.get("observed_at"))
        for name in INBOX_BUCKETS:
            src = reading.get(name)
            if not at or not is_bucket(src):
                continue
            reset = safe_iso(src.get("resets_at"))
            bucket = {"used_percentage": src["used_percentage"],
                      "resets_at": iso_ms(round_minute(reset)) if reset else None,
                      "observed_at": iso_ms(at), "source": reading_source(reading)}
            if implausible({name: bucket}, clock) is None:
                yield name, bucket, reading


def reading_wins(new, held, record, now):
    """The merge rule. An exact reading wins over a held exact bucket observed no later, and over a held
    ordinary bucket observed less than EXACT_HOLD after it, unless its own window has passed and the held
    one's has not: inbox files are read again at every merge, so an old exact reading must not undo much
    newer ones. Otherwise a passed window loses to any reading, a held exact reading stands for
    EXACT_HOLD, the later window wins, within one window the higher reading wins, and a tie keeps the
    later one."""
    if not is_bucket(held):
        return True
    held_at = bucket_at(held, record)
    if new["source"] == EXACT_SOURCE:
        if held_at is None:
            return True
        if held.get("source", (record or {}).get("source")) == EXACT_SOURCE:
            return bucket_at(new) >= held_at
        # An ordinary reading lags the account by one request, so seconds or minutes do not outrank an
        # exact one.
        new_reset = safe_iso(new.get("resets_at"))
        held_reset = safe_iso(held.get("resets_at"))
        if new_reset and new_reset <= now and not (held_reset and held_reset <= now):
            return False
        return bucket_at(new) >= held_at - EXACT_HOLD
    reset = safe_iso(held.get("resets_at"))
    if reset and reset <= now:
        return True
    if held.get("source", (record or {}).get("source")) == EXACT_SOURCE and held_at and now - held_at <= EXACT_HOLD:
        return False
    if window_of(new) != window_of(held):
        return window_of(new) > window_of(held)
    if new["used_percentage"] != held["used_percentage"]:
        return new["used_percentage"] > held["used_percentage"]
    return held_at is None or bucket_at(new) > held_at


def merge_readings(previous, readings, now):
    """Merge the inbox readings into the record by the merge rule. None when nothing changes.

    A held bucket from the future is shed. scoped comes with each exact weekly reading that wins, and is
    dropped when seven_day moves to another window without one.
    """
    previous = previous or {}
    clock = wall_clock()
    stale = [name for name, v in previous.items()
             if name not in RECORD_META and is_bucket(v) and from_future(name, v, previous, clock)]
    record = {name: v for name, v in previous.items() if name not in stale}
    if "seven_day" in stale:
        record.pop("scoped", None)
    for name, value in list(record.items()):
        if name not in RECORD_META and is_bucket(value):
            # A bucket written without its own time and source keeps the record's, not the next writer's.
            missing = {key: previous[key] for key in ("observed_at", "source") if key not in value and previous.get(key)}
            if missing:
                record[name] = dict(value, **missing)
    held_seven = record.get("seven_day")
    newest = None
    exact_scoped = None
    # In the order they were observed, so file names cannot decide a merge; an undated reading goes last
    # and is skipped by inbox_buckets.
    far = dt.datetime.max.replace(tzinfo=UTC)
    readings = sorted(readings, key=lambda r: safe_iso(r.get("observed_at")) or far)
    for name, bucket, reading in inbox_buckets(readings, clock):
        if reading_wins(bucket, record.get(name), record, now):
            record[name] = bucket
            if name == "seven_day":
                exact_scoped = reading_scoped(reading) if bucket["source"] == EXACT_SOURCE else None
            if newest is None or bucket_at(bucket) > bucket_at(newest[0]):
                newest = (bucket, reading)
    if newest is None:
        return None
    seven = record.get("seven_day")
    if is_bucket(held_seven) and is_bucket(seven) and window_of(seven) != window_of(held_seven):
        record.pop("scoped", None)
    if exact_scoped is not None:
        record.pop("scoped", None)
        if exact_scoped:
            record["scoped"] = exact_scoped
    times = [t for t in (bucket_at(v, record) for name, v in record.items()
                         if name not in RECORD_META and is_bucket(v)) if t]
    rest = {name: v for name, v in record.items() if name not in ("observed_at", "source", "session_id", "model")}
    # scoped goes last, as the contract shows.
    if "scoped" in rest:
        rest["scoped"] = rest.pop("scoped")
    record = {"observed_at": iso_ms(max(times) if times else now), "source": newest[0]["source"],
              "session_id": newest[1].get("session_id"), "model": newest[1].get("model"), **rest}
    return None if record == previous else record


def limit_values(record):
    """What a history line records a change of: readings, windows and scoped, not times or writers."""
    if not record:
        return None
    return sorted(([k, [v["used_percentage"], window_of(v)] if is_bucket(v) else v]
                   for k, v in record.items() if k not in RECORD_META or k == "scoped"), key=lambda kv: kv[0])


def implausible(incoming, clock):
    """Why a reading can't be real, or None: a reading from the future or a reset beyond its window."""
    for name, bucket in incoming.items():
        at = safe_iso(bucket.get("observed_at"))
        if at is None or at > clock + FUTURE_SLACK:
            return f"{name} observed_at {bucket.get('observed_at')} is in the future"
        reset = safe_iso(bucket.get("resets_at"))
        if name in WINDOW_LENGTH and reset and reset > clock + WINDOW_LENGTH[name] + FUTURE_SLACK:
            return f"{name} resets_at {bucket.get('resets_at')} is beyond the window length"
    return None


# ---------------------------------------------------------------- files

def read_json(path):
    with contextlib.suppress(OSError, ValueError):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    return None


def write_json(path, data, compact=False):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    body = json.dumps(data, separators=(",", ":")) if compact else json.dumps(data, indent=2) + "\n"
    tmp.write_text(body, encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows refuses the replace while a reader holds the file open.
            if attempt == 39:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def read_inbox(sd):
    """The readings in <state>/inbox/; one older than INBOX_MAX_AGE is deleted.

    A file caught half written reads as nothing and is taken at the next merge.
    """
    out = []
    for path in sorted((sd / INBOX_DIR).glob("*.json")):
        with contextlib.suppress(OSError):
            if wall_clock().timestamp() - path.stat().st_mtime > INBOX_MAX_AGE.total_seconds():
                path.unlink()
                continue
        reading = read_json(path)
        if reading:
            out.append(reading)
    return out


def append_history(sd, record):
    history = sd / "rate-limits.history.jsonl"
    with contextlib.suppress(OSError):
        if history.stat().st_size > HISTORY_LIMIT_BYTES:
            os.replace(history, sd / "rate-limits.history.jsonl.1")
    with open(history, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


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
def try_locked(sd, wait=0.0):
    """Yields True holding limits.lock, False when another writer still holds it after wait seconds."""
    fh = open(sd / LOCK_FILE, "a+b")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _try_lock(fh)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.02)
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                _unlock(fh)
    finally:
        fh.close()


# ---------------------------------------------------------------- the merge

def merge_step(sd, now, lock_wait=0.0):
    """One merge under limits.lock: read the inbox, merge, write rate-limits.json and its history.

    Returns (outcome, record): outcome is a short line for a log ("ok: written", "ok: nothing new" or
    SKIP_LOCK), record is rate-limits.json as it stands afterwards (None when absent).
    """
    sd.mkdir(parents=True, exist_ok=True)
    with try_locked(sd, lock_wait) as held:
        if not held:
            trace("merge.skip", sd, reason="lock busy")
            return SKIP_LOCK, read_json(sd / "rate-limits.json")
        previous = read_json(sd / "rate-limits.json")
        merged = merge_readings(previous, read_inbox(sd), now)
        if merged is None:
            trace("merge.readings", sd, written=False)
            return "ok: nothing new", previous
        write_json(sd / "rate-limits.json", merged, compact=True)
        changed = limit_values(merged) != limit_values(previous)
        if changed:
            append_history(sd, merged)
        trace("merge.readings", sd, written=True, history=changed)
        return "ok: written", merged


# ---------------------------------------------------------------- python floor

MIN_PYTHON = (3, 9)


def require_python(minimum=MIN_PYTHON, version=None):
    """None when the interpreter is new enough, else the one line that says why it is not."""
    found = tuple(version or sys.version_info[:3])
    if found[:2] >= tuple(minimum):
        return None
    return (f"Python {'.'.join(map(str, found))} at {sys.executable} is older than the "
            f"{'.'.join(map(str, minimum))} this script needs")


# ---------------------------------------------------------------- debug trace

DEBUG_FLAG = "debug.on"
DEBUG_LOG = "debug.log"
DEBUG_LIMIT_BYTES = 1024 * 1024


def debug_enabled(sd=None):
    """Tracing is on for one command with PACELINE_DEBUG=1, and for every process while the flag file exists.

    A file rather than only a variable: a hook does not see a variable set in the user's shell.
    """
    if os.environ.get("PACELINE_DEBUG") == "1":
        return True
    with contextlib.suppress(OSError):
        return ((sd or paceline_dir()) / DEBUG_FLAG).exists()
    return False


def redact(value):
    """A value safe for the log: the home directory is written as ~."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    home = str(Path.home())
    if len(home) > 1:
        value = value.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return value


def trace(event, sd=None, **fields):
    """Append one JSON line to the debug log when tracing is on. Never raises."""
    if debug_enabled(sd):
        _write_trace(event, sd, fields)


def trace_failure(event, sd=None, error=None, **fields):
    """Append one line whether tracing is on or not: a failure a hook would otherwise swallow."""
    if error is not None:
        fields["error"] = f"{type(error).__name__}: {error}"
        fields["traceback"] = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    _write_trace(event, sd, dict(fields, failure=True))


def _write_trace(event, sd, fields):
    with contextlib.suppress(Exception):
        sd = sd or paceline_dir()
        sd.mkdir(parents=True, exist_ok=True)
        log = sd / DEBUG_LOG
        with contextlib.suppress(OSError):
            if log.stat().st_size > DEBUG_LIMIT_BYTES:
                os.replace(log, sd / (DEBUG_LOG + ".1"))
        line = {"at": iso_ms(dt.datetime.now(UTC)), "pid": os.getpid(),
                "script": Path(sys.argv[0]).name if sys.argv and sys.argv[0] else None, "event": event}
        line.update(fields)
        with open(log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(redact(line), separators=(",", ":"), default=str) + "\n")
