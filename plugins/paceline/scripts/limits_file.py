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
import hashlib
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
RECORD_META = ("observed_at", "source", "session_id", "model", "scoped", "account", "accounts")
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


# ---------------------------------------------------------------- the account

ACCOUNT_PREFIX = "paceline-account-v1"
# The partition of readings whose account is not known.
UNKNOWN_ACCOUNT = "unknown"
ACCOUNT_KEY = re.compile(r"[0-9a-f]{16}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ACCOUNT_RETRY_SEC = 0.05


def account_file():
    """Claude Code's own state file, which names the signed-in account.

    Deliberately not under config_dir(): with CLAUDE_CONFIG_DIR unset the file is ~/.claude.json, and
    ~/.claude/.claude.json, which may exist, holds no account.
    """
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(config) / ".claude.json" if config else Path.home() / ".claude.json"


def _uuid_or_none(value):
    if not isinstance(value, str):
        return None
    value = value.lower()
    return value if _UUID.fullmatch(value) else None


def account_key_of(text):
    """(key, None) for the account the text of .claude.json names, else (None, reason).

    reason is "unreadable" when the text does not parse and "no-account" when it names no account.
    Only oauthAccount.accountUuid and oauthAccount.organizationUuid are touched; the key is one-way, so
    neither identifier leaves this function.
    """
    if not isinstance(text, str):
        return None, "unreadable"
    try:
        parsed = json.loads(text)
    except Exception:
        return None, "unreadable"
    oauth = parsed.get("oauthAccount") if isinstance(parsed, dict) else None
    oauth = oauth if isinstance(oauth, dict) else {}
    return account_key_of_ids(oauth.get("accountUuid"), oauth.get("organizationUuid"))


def account_key_of_ids(account_uuid, organization_uuid):
    """(key, None) for an account named by its identifiers, else (None, "no-account").

    The identifiers come from the state file or from the process environment. An organization that is
    not a UUID counts as empty.
    """
    account = _uuid_or_none(account_uuid)
    organization = _uuid_or_none(organization_uuid) or ""
    if account is None:
        return None, "no-account"
    digest = hashlib.sha256(f"{ACCOUNT_PREFIX}:{account}:{organization}".encode("utf-8")).hexdigest()
    return digest[:16], None


def environment_account():
    """The key of the account a Desktop host started this process with, else None.

    The host passes the two identifier variables; they belong to the process, while the state file
    names the account of the last terminal login. Both must be set.
    """
    account = os.environ.get("CLAUDE_CODE_ACCOUNT_UUID")
    organization = os.environ.get("CLAUDE_CODE_ORGANIZATION_UUID")
    if not account or not organization:
        return None
    return account_key_of_ids(account, organization)[0]


def read_account_text(path):
    return path.read_text(encoding="utf-8")


def resolve_account(path=None):
    """(key, None) for the account signed in now, else (None, reason): "no-file", "unreadable" or "no-account".

    Without a path the process's own account variables come first, then the state file.
    Claude Code rewrites the file often, so a read or parse failure is tried once more after
    ACCOUNT_RETRY_SEC. Never raises, and passes nothing from the file or an error on: an error message
    may quote the file.
    """
    if path is None:
        own = environment_account()
        if own is not None:
            return own, None
        path = account_file()
    result = (None, "unreadable")
    for attempt in range(2):
        try:
            if not path.is_file():
                return None, "no-file"
            result = account_key_of(read_account_text(path))
        except Exception:
            result = (None, "unreadable")
        if result[1] != "unreadable":
            return result
        if attempt == 0:
            time.sleep(ACCOUNT_RETRY_SEC)
    return result


def reading_account(reading):
    """The partition a reading belongs to: its account key, else UNKNOWN_ACCOUNT for a missing or malformed one."""
    account = reading.get("account")
    return account if isinstance(account, str) and ACCOUNT_KEY.fullmatch(account) else UNKNOWN_ACCOUNT


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


def remove_usage_watch_leftovers(sd):
    """Delete the shared usage-watch.state.json, a leftover gate of every account; the gate is now one file per account."""
    with contextlib.suppress(OSError):
        (sd / "usage-watch.state.json").unlink()


def read_inbox(sd):
    """The readings in <state>/inbox/; one older than INBOX_MAX_AGE is deleted.

    A file caught half written reads as nothing and is taken at the next merge. The shared
    usage-watch.json of earlier releases is a leftover, since each account now has its own inbox
    file, and is deleted unread.
    """
    out = []
    for path in sorted((sd / INBOX_DIR).glob("*.json")):
        if path.name == "usage-watch.json":
            with contextlib.suppress(OSError):
                path.unlink()
            continue
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

def held_partitions(previous):
    """The partitions of the limits file as it stands: {account key or UNKNOWN_ACCOUNT: record}.

    A file written before accounts existed is one record of unknown account. The top level of a file that
    has partitions is never read: an older merge may have left it mixed, and the next write rebuilds it.
    """
    if not previous:
        return {}
    accounts = previous.get("accounts")
    if isinstance(accounts, dict):
        return {key: value for key, value in accounts.items()
                if isinstance(value, dict) and (key == UNKNOWN_ACCOUNT or ACCOUNT_KEY.fullmatch(key))}
    legacy = {name: value for name, value in previous.items() if name not in ("account", "accounts")}
    return {UNKNOWN_ACCOUNT: legacy} if any(is_bucket(v) for v in legacy.values()) else {}


def partition_age(partition, now):
    """How long ago a partition's newest bucket was observed; None when none carries a time."""
    times = [t for t in (bucket_at(v, partition) for name, v in partition.items()
                         if name not in RECORD_META and is_bucket(v)) if t]
    return now - max(times) if times else None


def newest_first(partitions):
    """Partition keys, the one observed last first; the key orders equal times."""
    earliest = dt.datetime.min.replace(tzinfo=UTC)
    return sorted(partitions, key=lambda key: (safe_iso(partitions[key].get("observed_at")) or earliest, key),
                  reverse=True)


def with_accounts(partitions):
    """The limits file: the newest partition at the top level for readers that know nothing of accounts."""
    order = newest_first(partitions)
    top = dict(partitions[order[0]]) if order else {}
    top["account"] = None if not order or order[0] == UNKNOWN_ACCOUNT else order[0]
    top["accounts"] = {key: partitions[key] for key in sorted(partitions)}
    return top


def merge_step(sd, now, lock_wait=0.0):
    """One merge under limits.lock: read the inbox, merge, write rate-limits.json and its history.

    Each account's readings merge into a partition of their own (accounts[<key>]); a partition no
    reading has touched for INBOX_MAX_AGE is dropped. The top level mirrors the newest partition, and a
    history line is one partition that changed, with its account (None when unknown).

    Returns (outcome, record): outcome is a short line for a log ("ok: written", "ok: nothing new" or
    SKIP_LOCK), record is rate-limits.json as it stands afterwards (None when absent).
    """
    sd.mkdir(parents=True, exist_ok=True)
    with try_locked(sd, lock_wait) as held:
        if not held:
            trace("merge.skip", sd, reason="lock busy")
            return SKIP_LOCK, read_json(sd / "rate-limits.json")
        previous = read_json(sd / "rate-limits.json")
        before = held_partitions(previous)
        partitions = dict(before)
        remove_usage_watch_leftovers(sd)
        groups = {}
        for reading in read_inbox(sd):
            groups.setdefault(reading_account(reading), []).append(reading)
        for key, readings in groups.items():
            merged = merge_readings(partitions.get(key), readings, now)
            if merged is not None:
                partitions[key] = merged
        ages = {key: partition_age(part, now) for key, part in partitions.items()}
        # The newest partition outlives the limit, so readers keep showing its last figures as stale.
        keep = next((key for key in newest_first(partitions) if ages[key] is not None), None)
        for key in list(partitions):
            if ages[key] is None or (ages[key] > INBOX_MAX_AGE and key != keep):
                del partitions[key]
        if not partitions and "accounts" not in (previous or {}):
            trace("merge.readings", sd, written=False)
            return "ok: nothing new", previous
        record = with_accounts(partitions)
        if record == previous:
            trace("merge.readings", sd, written=False)
            return "ok: nothing new", previous
        write_json(sd / "rate-limits.json", record, compact=True)
        changed = [key for key in newest_first(partitions)[::-1]
                   if limit_values(partitions[key]) != limit_values(before.get(key))]
        for key in changed:
            append_history(sd, dict(partitions[key], account=None if key == UNKNOWN_ACCOUNT else key))
        trace("merge.readings", sd, written=True, history=bool(changed))
        return "ok: written", record


# ---------------------------------------------------------------- python floor

MIN_PYTHON = (3, 9)


def history_account_filter(records, key):
    """The history lines that belong to the account with this key.

    Lines written before accounts existed, or with a null account, are untagged: they count only when
    every tagged line is this account's, which is the case of a person who never switched. An unknown
    own key keeps every line.
    """
    if key is None:
        return records
    # Only a 0.2.5 merge puts an accounts map on a history line, and its buckets may mix accounts.
    tagged = [UNKNOWN_ACCOUNT if "accounts" in r else reading_account(r) for r in records]
    mixed = any(a not in (key, UNKNOWN_ACCOUNT) for a in tagged)
    return [r for r, a in zip(records, tagged) if a == key or (a == UNKNOWN_ACCOUNT and not mixed)]


def read_history(sd, account=None):
    """The history lines with a weekly reading, oldest first; account is the own key (None keeps every line)."""
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
    return history_account_filter(records, account)


HISTORY_FILES = ("rate-limits.history.jsonl", "rate-limits.history.jsonl.1")


def _lines_backward(path, block=64 * 1024):
    """The lines of a file as text, last first, reading it from the end in blocks."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        pos = handle.tell()
        rest = b""
        while pos > 0:
            step = min(block, pos)
            pos -= step
            handle.seek(pos)
            parts = (handle.read(step) + rest).split(b"\n")
            rest = parts[0]
            for raw in reversed(parts[1:]):
                yield raw.decode("utf-8", "replace").rstrip("\r")
        yield rest.decode("utf-8", "replace").rstrip("\r")


def read_history_tail(sd, account, is_base, window=None):
    """read_history(sd, account) cut to its newest lines, from the end back to a line that proves the base.

    The base is the line with the latest bucket time that `is_base` accepts. Bucket times need not rise in
    file order: an exact reading can win over a held bucket observed up to EXACT_HOLD after it, so a later
    line may carry an older bucket. The scan therefore goes on past the first accepted line until one is
    EXACT_HOLD older than the newest accepted time seen; no line before it can then beat that base. The
    older generation is opened only when the current one does not reach that line. When the tail holds a
    line of unknown account, whether it counts depends on the lines before it, so the whole history is
    read instead.

    `window` is the five-hour window (window_of) the caller reads. When given, the scan also stops at a line
    of the account whose five-hour bucket belongs to an earlier window and is dated more than EXACT_HOLD
    before that window began: bucket times run out of file order by at most EXACT_HOLD, so no earlier line
    can hold a reading of `window`. This ends the scan early while the window has no base yet.
    """
    records = []
    started = None if window is None else dt.datetime.fromtimestamp(window * 60, UTC) - WINDOW_LENGTH["five_hour"]
    best = None
    found = False
    for name in HISTORY_FILES:
        try:
            for line in _lines_backward(sd / name):
                with contextlib.suppress(ValueError, TypeError, AttributeError):
                    record = json.loads(line)
                    record["_at"] = parse_iso(record["observed_at"])
                    if not record.get("seven_day"):
                        continue
                    records.append(record)
                    if account is not None and ("accounts" in record or reading_account(record) == UNKNOWN_ACCOUNT):
                        return read_history(sd, account)
                    if started is not None and reading_account(record) == account:
                        reading = record.get("five_hour")
                        if is_bucket(reading) and window_of(reading) < window:
                            at = bucket_at(reading, record)
                            if at and at < started - EXACT_HOLD:
                                found = True
                                break
                    if reading_account(record) == account and is_base(record):
                        at = bucket_at(record["five_hour"], record)
                        if best is not None and at <= best - EXACT_HOLD:
                            found = True
                            break
                        best = at if best is None else max(best, at)
        except OSError:
            continue
        if found:
            break
    records.sort(key=lambda r: r["_at"])
    return history_account_filter(records, account)


STALE_EPOCH = dt.datetime(1970, 1, 1, tzinfo=UTC)


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


NO_PARTITION = "no-partition"


def pick_partition(limits, key, reason):
    """(partition, account) for the account with this key (None when unresolved, then reason says why).

    The account is {"state": "known" | "unknown", "key": the own key, "reason": a code or None}. Unknown
    means the figures cannot be told to be this account's: the own key is unresolved, or they come from
    the partition of untagged readings. Another account's partition is never returned.
    """
    def account(state, why):
        return {"state": state, "key": key, "reason": why}

    if not limits:
        return None, account("known" if key else "unknown", None if key else reason)
    if key is None:
        return limits, account("unknown", reason)
    held = held_partitions(limits)
    if key in held:
        return held[key], account("known", None)
    if UNKNOWN_ACCOUNT in held:
        return held[UNKNOWN_ACCOUNT], account("unknown", "untagged")
    if isinstance(limits.get("accounts"), dict):
        return None, account("known", NO_PARTITION)
    return limits, account("unknown", "untagged")


def dated_record(partition, now):
    """A partition dated by its oldest live bucket, never by mtime; None without a seven_day bucket."""
    if not partition or not is_bucket(partition.get("seven_day")):
        return None
    times = bucket_times(partition, now)
    at = min(times.values()) if times else safe_iso(partition.get("observed_at"))
    if not at:
        return None
    return dict(partition, _at=at, _bucket_at={name: iso_ms(t) for name, t in times.items()})


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
