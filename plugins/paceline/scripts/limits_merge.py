"""Merges the sessions' limit readings from <state>/inbox/ into <state>/rate-limits.json.

Run by the plugin's SessionStart and Stop hooks, asynchronously. The merge and its lock live in
limits_file.py beside this script; this file is the hooks' entry point.

Always exits 0 and prints nothing: a hook must never fail the session, so a failure goes to the
debug log only. Standard library only.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limits_file import UTC, merge_step, paceline_dir, require_python, trace_failure  # noqa: E402


def run(now):
    """One merge step without waiting for the lock. Returns its outcome line."""
    return merge_step(paceline_dir(), now)[0]


def main():
    # The hook's input names the session; nothing here needs it, but an unread pipe can block the writer.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
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
