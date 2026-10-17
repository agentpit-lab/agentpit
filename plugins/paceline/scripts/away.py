#!/usr/bin/env python3
"""away: tell paceline that the owner is not at the keyboard, so a launch that would ask for confirmation
(a hard limit, or the five-hour burn over the agent cap) is denied instead.

    paceline away on [--session ID]
    paceline away off
    paceline away status
    away.py hook prompt          (UserPromptSubmit; the payload arrives on stdin)

One marker, <state>/away.json, is shared by every session. The owner's next message in any session
clears it. A cache-watch wake and the /paceline:away command itself are not such messages. Stdlib
only, Python 3.9+.
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import limits_file  # noqa: E402
from cache_watch import WAKE_MARK  # noqa: E402

MARKER_FILE = "away.json"
# The command that sets the marker: Claude Code submits it as a prompt before the model runs it.
COMMAND_NAMES = ("/paceline:away", "/away")
# The same command after Claude Code expands it into tags before the model sees it.
EXPANDED_COMMAND = re.compile(r"<command-name>\s*(?:" + "|".join(map(re.escape, COMMAND_NAMES)) + r")\s*</command-name>", re.I)


def read(sd=None):
    """The marker as {"on", "set_at", "session"}; "on" is false when no marker is stored."""
    sd = sd or limits_file.paceline_dir()
    marker = limits_file.read_json(sd / MARKER_FILE)
    if not marker or marker.get("on") is not True:
        return {"on": False, "set_at": None, "session": None}
    return {"on": True, "set_at": marker.get("set_at"), "session": marker.get("session")}


def set_on(sd, session=None):
    sd.mkdir(parents=True, exist_ok=True)
    limits_file.write_json(sd / MARKER_FILE, {
        "on": True, "set_at": dt.datetime.now(limits_file.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "session": session})


def clear(sd):
    with contextlib.suppress(OSError):
        (sd / MARKER_FILE).unlink()


def is_genuine(prompt):
    """A prompt the owner wrote: not a cache-watch wake, not the command that sets the marker."""
    text = str(prompt or "").strip()
    if not text or WAKE_MARK in text:
        return False
    if EXPANDED_COMMAND.search(text):
        return False
    first = text.split(None, 1)[0].lower()
    return first not in COMMAND_NAMES


def hook_prompt(payload):
    sd = limits_file.paceline_dir()
    if read(sd)["on"] and is_genuine(payload.get("prompt")):
        clear(sd)
    return 0


def describe(state):
    if not state["on"]:
        return "away: off"
    line = f"away: on since {state['set_at'] or 'an unknown time'}"
    return line + (f" (set by session {state['session']})" if state["session"] else "")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:2] == ["hook", "prompt"]:
        try:
            return hook_prompt(json.loads(sys.stdin.read() or "{}"))
        except Exception:  # noqa: BLE001 - a hook must never fail the session
            return 0
    parser = argparse.ArgumentParser(prog="paceline away", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("on", "off", "status"))
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    sd = limits_file.paceline_dir()
    if args.command == "on":
        set_on(sd, args.session)
        print(describe(read(sd)))
        print("A launch that would ask for confirmation (a hard limit, or the five-hour burn over the agent cap) "
              "is now denied; your next message in any session clears this.")
    elif args.command == "off":
        clear(sd)
        print(describe(read(sd)))
    else:
        print(describe(read(sd)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
