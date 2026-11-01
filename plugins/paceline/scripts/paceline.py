#!/usr/bin/env python3
"""paceline: one command for pace, usage-watch and away.

    paceline <pace command> ...      status, advice, on, off, plan, reserve, hold, merge, doctor, ...
    paceline cache-watch ...         moved to the PitCrew plugin (prints where, exits 2)
    paceline context-watch ...       moved to the PitCrew plugin (prints where, exits 2)
    paceline usage-watch ...         on, off, status
    paceline away ...                on, off, status

bin/paceline and bin/paceline.cmd run this script. Stdlib only, Python 3.9+.
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # Every group prints the same text on a Windows console as elsewhere, whatever its code page.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")
    import pace
    if argv[:1] and argv[0] in pace.MOVED_GROUPS:
        return pace.moved_group(argv[0])
    if argv[:1] and argv[0] in pace.MOVED_COMMANDS:
        return pace.moved_command(argv[0])
    if argv[:1] == ["usage-watch"]:
        import usage_watch
        return usage_watch.main(argv[1:])
    if argv[:1] == ["away"]:
        import away
        return away.main(argv[1:])
    return pace.main(argv)


if __name__ == "__main__":
    sys.exit(main())
