"""Merges the sessions' limit readings from <state>/inbox/ into <state>/rate-limits.json.

Run by the hooks module's inbox watch (hooks/register.tsx), at each session start and every 15 seconds
while the inbox changed. The merge and its lock live in limits_file.py beside this script; this file
is the module's entry point.

Always exits 0 and prints nothing: the module reads any other exit code as an interpreter that cannot
run the script, and a failure goes to the debug log only. Standard library only.

After the merge it runs the plan step: with pace on and the session's account known, it fixes the current part
and publishes the week plan file when the file does not hold now. The merge never waits for limits.lock; the
plan step waits at most MERGE_LOCK_WAIT_SEC (3 s) for pace.lock, and the two locks are never held together.
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limits_file import (  # noqa: E402
    ACCOUNT_KEY, FUTURE_SLACK, SAME_WEEK_TOLERANCE_SEC, UTC, WEEK_PLAN_VERSION, held_partitions, merge_step,
    pace_enabled, paceline_dir, prune_session_accounts, read_json, require_python, safe_iso, trace_failure,
    week_plan_path, write_json,
)

# The form of an instant in a week plan file: what limits_file.iso_ms writes (a fraction and a "+HH:MM" offset
# are read as well). hooks/watch.ts holds the same rule; tests/fixtures/plan-holds.ts keeps the two together.
# The twin rule is guaranteed only for the forms iso_ms writes: the two parsers differ on a fraction of more than
# six digits and on a negative number of seconds, which no writer produces.
PLAN_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", re.ASCII)


def plan_instant(value):
    """safe_iso for an instant of the week plan file: a number of seconds or text of the form above, else None."""
    if isinstance(value, bool) or (isinstance(value, str) and not PLAN_INSTANT.fullmatch(value)):
        return None
    return safe_iso(value)


def reading_week_reset(record, key):
    """The weekly reset of the key's partition in the limits record (an instant), or None when it is unknown.
    hooks/watch.ts readingWeekReset is the same rule (tests/fixtures/reading-reset.ts keeps the two together).
    PACELINE_USAGE_FILE (test-only) makes pace read another source than rate-limits.json: the rule assumes it is unset."""
    partition = held_partitions(record).get(key)
    bucket = partition.get("seven_day") if isinstance(partition, dict) else None
    return plan_instant(bucket.get("resets_at")) if isinstance(bucket, dict) else None


def plan_holds(sd, key, now, reading_reset):
    """True when <state>/week-plan-<key>.json is a current-version object that a reader would accept now: its
    part has started and ends after now, its week ends after now, it was computed no more than FUTURE_SLACK
    after now, and its week is the reading's: reading_reset, the key's current weekly reset, is unknown, not
    after now, or within limits_file.SAME_WEEK_TOLERANCE_SEC of the file's week_resets_at (PitBoard's own test). This is
    the one place that states the rule; hooks/watch.ts holds the same.

    Reads the one small file with plain json: the hot path never imports pace.py."""
    plan = read_json(week_plan_path(sd, key))
    if not plan or isinstance(plan.get("version"), bool) or plan.get("version") != WEEK_PLAN_VERSION \
            or not isinstance(plan.get("part"), dict):
        return False
    part = plan["part"]
    computed, start, end = plan_instant(plan.get("computed_at")), plan_instant(part.get("start")), plan_instant(part.get("end"))
    week_end = plan_instant(plan.get("week_resets_at"))
    if None in (computed, start, end, week_end):
        return False
    off_schedule = (reading_reset is not None and reading_reset > now
                    and abs((reading_reset - week_end).total_seconds()) >= SAME_WEEK_TOLERANCE_SEC)
    if off_schedule:
        return False
    return computed <= now + FUTURE_SLACK and start <= now < end and week_end > now


# A plan step that left the file not holding (no weekly bucket for the account, no part holds now, a plan that
# cannot be built, a busy lock) is tried again after PLAN_RETRY_FAST_SEC for PLAN_RETRY_FAST_FOR_SEC, then every
# PLAN_RETRY_SLOW_SEC: hooks/watch.ts PLAN_FAST_MS, PLAN_FAST_FOR_MS and PLAN_SLOW_MS, the boundary trigger's steps.
# The attempt is kept in <state>/merge-retry-<key>.json ({"first", "last"}, epoch seconds), removed once the file holds.
PLAN_RETRY_FAST_SEC = 60
PLAN_RETRY_FAST_FOR_SEC = 600
PLAN_RETRY_SLOW_SEC = 300
# A marker whose `last` is older than the whole backoff (the fast phase and one slow step) belongs to a failure
# that stopped; a live backoff never waits that long, so only a failure that resumed after a gap is reset.
PLAN_RETRY_STALE_SEC = PLAN_RETRY_FAST_FOR_SEC + PLAN_RETRY_SLOW_SEC


def retry_wait(tried, now_sec):
    """Seconds until the plan step may run again after the failed attempt `tried` (0: now; no attempt: 0)."""
    if not isinstance(tried, dict):
        return 0.0
    first, last = tried.get("first"), tried.get("last")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (first, last)):
        return 0.0
    step = PLAN_RETRY_FAST_SEC if now_sec - first < PLAN_RETRY_FAST_FOR_SEC else PLAN_RETRY_SLOW_SEC
    return max(0.0, last + step - now_sec) if last <= now_sec + step else 0.0


def retry_first(tried, now_sec):
    """The `first` to keep in the marker of a failed attempt at `now_sec`: the earlier run's, while its `last`
    is not older than PLAN_RETRY_STALE_SEC, else now (a failure that resumes after a gap is a fresh one and
    starts on the fast step again)."""
    if isinstance(tried, dict):
        first, last = tried.get("first"), tried.get("last")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (first, last)) \
                and 0 <= now_sec - last <= PLAN_RETRY_STALE_SEC:
            return first
    return now_sec


def write_marker(path, first, last):
    """Keep the failed attempt; a write that fails is traced, never raised (the step then just runs again)."""
    try:
        write_json(path, {"first": first, "last": last})
    except OSError as error:
        trace_failure("week_plan.merge", path.parent, error=error, reason="the retry marker could not be written")


def plan_step(sd, now, record=None):
    """Fix the current part and publish the week plan file when it does not hold now (pace on, the session's
    account known). Takes pace.lock only, never together with limits.lock: merge_step has released that.
    record is rate-limits.json as the merge left it; None reads the file."""
    if not pace_enabled(sd):
        return
    key = os.environ.get("PACELINE_ACCOUNT_KEY")
    if not (isinstance(key, str) and ACCOUNT_KEY.fullmatch(key)):
        return
    if record is None:
        record = read_json(sd / "rate-limits.json")
    reading_reset = reading_week_reset(record, key)
    marker = sd / f"merge-retry-{key}.json"
    if plan_holds(sd, key, now, reading_reset):
        marker.unlink(missing_ok=True)
        return
    tried = read_json(marker)
    if retry_wait(tried, now.timestamp()) > 0:
        return
    # Imported here, under main()'s try, and only when the file needs it: pace.py costs about 130 ms to load.
    import pace
    pace.after_merge_plan(sd, now)
    if plan_holds(sd, key, now, reading_reset):
        marker.unlink(missing_ok=True)
    else:
        write_marker(marker, retry_first(tried, now.timestamp()), now.timestamp())


def run(now):
    """One merge step that never waits for limits.lock, then the plan step, which waits at most
    MERGE_LOCK_WAIT_SEC for pace.lock. Returns the merge's outcome line."""
    sd = paceline_dir()
    outcome, record = merge_step(sd, now)
    try:
        prune_session_accounts(sd)
        if outcome == "ok: written":
            # Imported here, under main()'s try: a snapshot module that fails to load is logged, never fatal.
            import five_burn
            five_burn.after_merge(sd, now, record)
    finally:
        # Whatever the burn snapshot does, the plan step runs; its own failures are traced inside it.
        plan_step(sd, now, record)
    return outcome


def main():
    # The hook's input names the session; nothing here needs it, but an unread pipe can block the writer.
    try:
        if not sys.stdin.isatty():
            sys.stdin.buffer.read()
    except (OSError, ValueError):
        pass
    too_old = require_python()
    if too_old:
        trace_failure("limits_merge.python", reason=too_old)
        return 0
    try:
        run(dt.datetime.now(UTC))
    except Exception as error:  # noqa: BLE001 - a hook must never fail the session
        trace_failure("limits_merge.crash", error=error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
