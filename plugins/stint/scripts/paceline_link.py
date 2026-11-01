"""Every call from stint into PaceLine.

PaceLine is found by STINT_PACELINE: "off" for none, a path for that program (a .py path runs with the
current Python), unset for `paceline` on PATH. A program that is a .cmd or .bat launcher is never run:
cmd.exe would parse the arguments (an item title may hold `&`), so Stint runs the paceline.py beside the
launcher's folder (<folder>/../scripts/paceline.py) with the current Python. Calls run with stdin from the
null device, the caller's environment and a 60 s timeout. Stdlib only, Python 3.9+.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_SEC = 60


def command():
    """The argv prefix that runs PaceLine, or None when there is none."""
    setting = os.environ.get("STINT_PACELINE")
    if setting is not None and setting.strip().lower() == "off":
        return None
    program = setting or shutil.which("paceline")
    if not program:
        return None
    lower = program.lower()
    if lower.endswith(".py"):
        return [sys.executable, program]
    if lower.endswith((".cmd", ".bat")):
        script = Path(program).resolve().parent.parent / "scripts" / "paceline.py"
        return [sys.executable, str(script)] if script.is_file() else None
    return [program]


def describe():
    """The command path as text, or None."""
    argv = command()
    return argv[-1] if argv else None


def call(*args):
    """(parsed JSON object or None, why). `why` names the failure when nothing parsed; the exit code does
    not matter, PaceLine answers wait and stop through it."""
    argv = command()
    if argv is None:
        return None, "PaceLine not found"
    try:
        proc = subprocess.Popen([*argv, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except OSError as error:
        return None, f"cannot run {argv[-1]}: {error}"
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        # Kill first and read nothing: a grandchild that holds the pipes would block a read for good.
        proc.kill()
        proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()
        return None, f"timed out after {TIMEOUT_SEC} s"
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed, None
    text = err or out
    if not text:
        return None, f"exit {proc.returncode}, no output"
    return None, text.splitlines()[0][:200]


def advice():
    """(advice dict, why): valid when it is an object with advice_version 1, an action of start, wait or
    stop and a list of reasons. Else (None, why)."""
    parsed, why = call("advice", "--json")
    if parsed is None:
        return None, why
    if parsed.get("advice_version") != 1:
        return None, f"advice_version {parsed.get('advice_version')!r}, not 1"
    if parsed.get("action") not in ("start", "wait", "stop") or not isinstance(parsed.get("reasons"), list):
        return None, "answer lacks an action or reasons"
    return parsed, None


def status():
    """The parsed `paceline status --json`, or None when it failed."""
    parsed, _ = call("status", "--json")
    return parsed


def week_pct(parsed):
    """weekly.effective_pct of a status, or None."""
    weekly = (parsed or {}).get("weekly")
    value = weekly.get("effective_pct") if isinstance(weekly, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def night_of(parsed):
    night = (parsed or {}).get("night")
    return night if isinstance(night, dict) else None


def hold_acquire(estimate, title, item_id, pid=None):
    """(hold id, why): `why` names the failure when no hold was taken."""
    # --label=<title>: a title may start with "-". --key gives the hold PaceLine's keyed lease (240 minutes).
    args = ["hold", "acquire", "--kind", "queue", "--estimate", f"{estimate:g}", f"--label={title}",
            "--item", item_id, "--key", f"stint-{item_id}"]
    if pid is not None:
        args += ["--pid", str(pid)]
    parsed, why = call(*args, "--json")
    hold = (parsed or {}).get("hold")
    if isinstance(hold, dict) and hold.get("id"):
        return str(hold["id"]), None
    return None, why or str((parsed or {}).get("error") or "no hold in the answer")


def hold_release(hold_id, actual=None):
    """None when released, else why not."""
    args = ["hold", "release", str(hold_id)]
    if actual is not None:
        args += ["--actual", f"{actual:.3f}"]
    parsed, why = call(*args, "--json")
    if parsed is None:
        return why
    if parsed.get("error"):
        return str(parsed["error"])
    return None
