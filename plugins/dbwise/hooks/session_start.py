#!/usr/bin/env python3
"""SessionStart hook: adds the offline health check's problem lines to the session context.

Runs scripts/session_check.py and copies its lines to stdout, which Claude Code adds to the context:
one line per problem with the config, a password or the first build, nothing when all is well or
nothing is configured. The check opens no network connection. The hook builds nothing: it runs
alongside the MCP server's start, and the launcher's stub owns the first build. A failure is
appended to <config dir>/hook.log and the hook always exits 0.

The hook runs under whichever python3 or python is on PATH, so this file keeps to syntax old
interpreters parse. The check runs under the plugin venv's Python when that is ready, because it
reads the keychain through keyring, which only the venv has.
"""
import os
import subprocess
import sys
from pathlib import Path

CHECK_TIMEOUT = 20
PREFIX = "dbwise:"


def plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(root) if root else Path(__file__).resolve().parent.parent


def log_path():
    home = os.environ.get("DBWISE_HOME")
    return (Path(home) if home else Path.home() / ".dbwise") / "hook.log"


def append_log(text):
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "a", encoding="utf-8") as log:
            log.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def check_command(root, environ=None):
    """The command that runs the check: the ready venv's Python, else the hook's own."""
    script = Path(root) / "scripts" / "session_check.py"
    try:
        sys.path.insert(0, str(Path(root) / "scripts"))
        import bootstrap

        paths = bootstrap.Paths(Path(root), bootstrap.default_home(os.environ if environ is None else environ))
        if bootstrap.is_current(paths):
            return [str(paths.python), str(script)]
    except Exception:  # noqa: BLE001 - any trouble finding the venv falls back to the hook's Python
        pass
    return [sys.executable, str(script)]


def check_lines(command):
    """The check's problem lines; none when it fails or takes too long, and the failure is logged."""
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, encoding="utf-8", errors="replace", timeout=CHECK_TIMEOUT,
                              env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except (OSError, subprocess.SubprocessError) as e:
        append_log("dbwise: session check could not run: {}".format(type(e).__name__))
        return []
    if done.returncode != 0:
        append_log("dbwise: session check exited {}: {}".format(done.returncode, done.stderr.strip()[-500:]))
        return []
    return [line for line in done.stdout.splitlines() if line.startswith(PREFIX)]


def main():
    script = plugin_root() / "scripts" / "session_check.py"
    if not script.is_file():
        return 0
    for line in check_lines(check_command(plugin_root())):
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a hook failure must never block the session
        sys.exit(0)
