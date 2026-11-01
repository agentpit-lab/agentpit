#!/usr/bin/env python3
"""pitcrew: one command for context-watch and cache-watch.

    pitcrew context-watch ...        config, status
    pitcrew cache-watch ...          on, off, status, config

bin/pitcrew and bin/pitcrew.cmd run this script. Stdlib only, Python 3.9+.
"""
import argparse
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

USAGE = """usage: pitcrew <group> ...

groups:
  pitcrew context-watch config|status    warnings before auto-compaction, snapshots at each compaction
  pitcrew cache-watch on|off|status|config    keep an idle session's one-hour prompt cache alive
"""


def build_parser():
    """The command tree as an argparse parser, for the documentation's facts; main() parses by its own."""
    parser = argparse.ArgumentParser(prog="pitcrew", description="context-watch and cache-watch for Claude Code.")
    groups = parser.add_subparsers(dest="group", required=True)

    context = groups.add_parser("context-watch", help="warnings before auto-compaction, snapshots at each compaction")
    commands = context.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="show or change context-watch's settings")
    switch = config.add_mutually_exclusive_group()
    switch.add_argument("--on", dest="on", action="store_const", const=True, help="turn context-watch on")
    switch.add_argument("--off", dest="on", action="store_const", const=False, help="turn context-watch off")
    config.add_argument("--line", choices=("on", "off"),
                        help="add (on) or remove (off) the context line on every prompt; off by default")
    config.add_argument("--levels", help="warning levels, ascending whole percents 50-99, e.g. 85,95")
    config.add_argument("--days", type=int, help="days to keep snapshots, 1-90")
    status = commands.add_parser("status", help="show the settings, the snapshot folder and the settings file")
    status.add_argument("--project", help="the project whose snapshots are counted; default the current directory")

    cache = groups.add_parser("cache-watch", help="keep an idle session's one-hour prompt cache alive")
    commands = cache.add_subparsers(dest="command", required=True)
    on = commands.add_parser("on", help="watch one session")
    on.add_argument("--session", required=True, help="the session id")
    on.add_argument("--data-dir", required=True, help="the plugin's data directory (CLAUDE_PLUGIN_DATA)")
    on.add_argument("--lead", type=int, help="minutes before the cache expires to wake, 1-30")
    on.add_argument("--max", type=int, help="wakes in a row without a reply before watching stops, 1-20")
    off = commands.add_parser("off", help="stop watching one session")
    off.add_argument("--session", required=True, help="the session id")
    off.add_argument("--data-dir", required=True, help="the plugin's data directory (CLAUDE_PLUGIN_DATA)")
    status = commands.add_parser("status", help="list the watched sessions")
    status.add_argument("--data-dir", required=True, help="the plugin's data directory (CLAUDE_PLUGIN_DATA)")
    config = commands.add_parser("config", help="show or change the defaults for every session")
    config.add_argument("--lead", type=int, help="default minutes before the cache expires to wake, 1-30")
    config.add_argument("--max", type=int, help="default wakes without a reply, 1-20")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # Every group prints the same text on a Windows console as elsewhere, whatever its code page.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")
    if argv[:1] in (["-h"], ["--help"]):
        sys.stdout.write(USAGE)
        return 0
    if argv[:1] == ["context-watch"]:
        import context_watch
        return context_watch.main(argv[1:])
    if argv[:1] == ["cache-watch"]:
        import cache_watch
        return cache_watch.main(argv[1:])
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
