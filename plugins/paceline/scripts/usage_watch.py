"""usage-watch: turn on or off paceline's reader of the account's exact usage, and show its state.

    paceline usage-watch on|off|status

The reader itself is the plugin's function-hooks module (hooks/register.ts, hooks/usage.ts): it asks
Claude Code for the figures /usage shows and writes them to the inbox. This script only writes the
switch, <state>/usage-watch.json, and reads the module's gate of the account it resolves,
<state>/usage-watch.state-<key>.json (usage-watch.state-unknown.json with no account). It sends no
request. Stdlib only, Python 3.9+.
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import limits_file  # noqa: E402

SWITCH_FILE = "usage-watch.json"


def is_on(sd):
    switch = limits_file.read_json(sd / SWITCH_FILE)
    return bool(switch) and switch.get("on") is True


def when(value):
    """An epoch-millisecond time as ISO 8601 UTC to the second, or "never"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "never"
    at = dt.datetime.fromtimestamp(value / 1000, limits_file.UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_switch(sd, on):
    sd.mkdir(parents=True, exist_ok=True)
    limits_file.write_json(sd / SWITCH_FILE, {"on": on})
    print(f"usage-watch: {'on' if on else 'off'}")
    return 0


def cmd_status(sd):
    key = limits_file.resolve_account()[0] or limits_file.UNKNOWN_ACCOUNT
    state = limits_file.read_json(sd / f"usage-watch.state-{key}.json") or {}
    error = state.get("last_error")
    print(f"usage-watch: {'on' if is_on(sd) else 'off'}")
    print(f"account: {key}")
    print(f"last attempt: {when(state.get('last_attempt'))}")
    print(f"last success: {when(state.get('last_success'))}")
    print(f"last error: {error if isinstance(error, str) and error else 'none'}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="paceline usage-watch", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("on", "off", "status"))
    args = parser.parse_args(argv)
    sd = limits_file.paceline_dir()
    if args.command == "status":
        return cmd_status(sd)
    return cmd_switch(sd, args.command == "on")


if __name__ == "__main__":
    sys.exit(main())
