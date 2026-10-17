#!/usr/bin/env python3
"""paceline: one command for pace, cache-watch, context-watch and usage-watch.

    paceline <pace command> ...      status, auto, on, off, plan, reserve, queue, merge, doctor, ...
    paceline cache-watch ...         on, off, status, config
    paceline context-watch ...       config, status
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
    if argv[:1] == ["cache-watch"]:
        import cache_watch
        return cache_watch.main(argv[1:])
    if argv[:1] == ["context-watch"]:
        import context_watch
        return context_watch.main(argv[1:])
    if argv[:1] == ["usage-watch"]:
        import usage_watch
        return usage_watch.main(argv[1:])
    if argv[:1] == ["away"]:
        import away
        return away.main(argv[1:])
    import pace
    return pace.main(argv)


if __name__ == "__main__":
    sys.exit(main())
