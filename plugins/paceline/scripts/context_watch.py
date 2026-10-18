#!/usr/bin/env python3
"""context-watch: settings, status and snapshot cleanup for paceline's context-watch module.

The module (hooks/register.ts) shows the agent its context against the auto-compaction point, warns
at set levels and writes a snapshot of the session at each compaction. This script owns what the
module cannot do: the settings command, and removing old snapshots at session start.

CLI (through bin/paceline):
    paceline context-watch config [--on | --off] [--line on|off] [--levels 85,95] [--days N]
    paceline context-watch status [--project DIR]
Hook (through hooks/hooks.json):
    context_watch.py hook session-start

Settings live in <state>/context-watch.json, where the module reads them too; <state> is
PACELINE_STATE_DIR, else <config dir>/state/paceline, and <config dir> is CLAUDE_CONFIG_DIR or
~/.claude. Stdlib only, Python 3.9+. The hook never fails the session: any error exits 0.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limits_file  # noqa: E402

DEFAULTS = {"on": True, "line": True, "levels": [85, 95], "snapshot_days": 14}
LEVEL_RANGE = (50, 99)
DAYS_RANGE = (1, 90)
DAY_S = 86400
SNAPSHOT_DIR = (".claude", "paceline")

# Replaced by tests.
now = time.time


def settings_path():
    return limits_file.paceline_dir() / "context-watch.json"


def _valid_levels(value):
    return (
        isinstance(value, list)
        and value
        and all(isinstance(v, int) and LEVEL_RANGE[0] <= v <= LEVEL_RANGE[1] for v in value)
        and value == sorted(set(value))
    )


def load_settings():
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if not isinstance(stored, dict):
        stored = {}
    settings = dict(DEFAULTS)
    for key in ("on", "line"):
        if isinstance(stored.get(key), bool):
            settings[key] = stored[key]
    if _valid_levels(stored.get("levels")):
        settings["levels"] = stored["levels"]
    days = stored.get("snapshot_days")
    if isinstance(days, int) and not isinstance(days, bool) and DAYS_RANGE[0] <= days <= DAYS_RANGE[1]:
        settings["snapshot_days"] = days
    return settings


def save_settings(settings):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(settings, f, indent=1)
    os.replace(tmp, path)


def snapshot_dir(project):
    return Path(project).joinpath(*SNAPSHOT_DIR)


def remove_old_snapshots(project, days):
    folder = snapshot_dir(project)
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.glob("*.md"):
        try:
            if now() - path.stat().st_mtime > days * DAY_S:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _describe(settings):
    return (
        f"context-watch {'on' if settings['on'] else 'off'} · line {'on' if settings['line'] else 'off'}"
        f" · warn at {', '.join(f'{v}%' for v in settings['levels'])} of the auto-compact point"
        f" · snapshots kept {settings['snapshot_days']} days"
    )


def _parse_levels(text):
    try:
        values = [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        values = []
    if not _valid_levels(values):
        raise SystemExit(
            f"paceline context-watch: levels must be ascending whole percents {LEVEL_RANGE[0]}-{LEVEL_RANGE[1]}, e.g. 85,95"
        )
    return values


def cmd_config(args):
    settings = load_settings()
    changed = False
    if args.on is not None:
        settings["on"] = args.on
        changed = True
    if args.line is not None:
        settings["line"] = args.line == "on"
        changed = True
    if args.levels is not None:
        settings["levels"] = _parse_levels(args.levels)
        changed = True
    if args.days is not None:
        lo, hi = DAYS_RANGE
        if not lo <= args.days <= hi:
            raise SystemExit(f"paceline context-watch: days must be {lo}-{hi}")
        settings["snapshot_days"] = args.days
        changed = True
    if changed:
        save_settings(settings)
    print(_describe(settings))
    return 0


def cmd_status(args):
    settings = load_settings()
    print(_describe(settings))
    folder = snapshot_dir(args.project or os.getcwd())
    count = len(list(folder.glob("*.md"))) if folder.is_dir() else 0
    print(f"snapshots: {folder} ({count} file{'' if count == 1 else 's'})")
    print(f"settings: {settings_path()}")
    return 0


def hook_session_start(payload):
    project = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not project:
        return 0
    settings = load_settings()
    if settings["on"]:
        remove_old_snapshots(project, settings["snapshot_days"])
    return 0


def run_hook(name):
    try:
        if name != "session-start":
            return 0
        try:
            # Claude Code writes UTF-8; sys.stdin would decode it with the locale's code page.
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        return hook_session_start(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001 - a hook must never fail the session
        return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:
        return run_hook(argv[1] if len(argv) > 1 else "")
    parser = argparse.ArgumentParser(prog="paceline context-watch", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    config = sub.add_parser("config")
    switch = config.add_mutually_exclusive_group()
    switch.add_argument("--on", dest="on", action="store_const", const=True)
    switch.add_argument("--off", dest="on", action="store_const", const=False)
    config.add_argument("--line", choices=("on", "off"))
    config.add_argument("--levels")
    config.add_argument("--days", type=int)
    status = sub.add_parser("status")
    status.add_argument("--project")
    args = parser.parse_args(argv)
    return {"config": cmd_config, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
