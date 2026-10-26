"""Merges the sessions' limit readings from <state>/inbox/ into <state>/rate-limits.json.

Run by the hooks module's inbox watch (hooks/register.tsx), at each session start and every 15 seconds
while the inbox changed. The merge and its lock live in limits_file.py beside this script; this file
is the module's entry point.

Always exits 0 and prints nothing: the module reads any other exit code as an interpreter that cannot
run the script, and a failure goes to the debug log only. Standard library only.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limits_file import UTC, merge_step, paceline_dir, require_python, trace_failure  # noqa: E402


def run(now):
    """One merge step without waiting for the lock. Returns its outcome line."""
    sd = paceline_dir()
    outcome, record = merge_step(sd, now)
    if outcome == "ok: written":
        # Imported here, under main()'s try: a snapshot module that fails to load is logged, never fatal.
        import five_burn
        five_burn.after_merge(sd, now, record)
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
