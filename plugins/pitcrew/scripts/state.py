"""Where pitcrew keeps its settings, and how it reads them.

State is PITCREW_STATE_DIR, else <config dir>/state/pitcrew; <config dir> is CLAUDE_CONFIG_DIR, else
~/.claude. PaceLine's directory (PACELINE_STATE_DIR, else <config dir>/state/paceline) is the legacy
one: a settings file that PitCrew has not written yet is read from there, and never written.
Stdlib only, Python 3.9+.
"""
import contextlib
import json
import os
from pathlib import Path


def config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def state_dir():
    """PitCrew's state directory."""
    return Path(os.environ.get("PITCREW_STATE_DIR") or config_dir() / "state" / "pitcrew")


def legacy_state_dir():
    """PaceLine's state directory, where context-watch.json and cache-watch.json lived before PitCrew."""
    return Path(os.environ.get("PACELINE_STATE_DIR") or config_dir() / "state" / "paceline")


def read_json(path):
    """The JSON object in a file, or None for a missing, unreadable, non-JSON or non-object file."""
    with contextlib.suppress(OSError, ValueError, RecursionError):
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    return None


def settings_source(name):
    """(path, from_legacy): the file a settings object is read from. A file in PitCrew's directory is the
    only source, even when it is invalid; else PaceLine's file of the same name; else PitCrew's path."""
    own = state_dir() / name
    if own.is_file():
        return own, False
    legacy = legacy_state_dir() / name
    if legacy.is_file():
        return legacy, True
    return own, False
