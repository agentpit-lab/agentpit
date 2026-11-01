"""Where stint keeps its files, and how it reads and writes them.

State is STINT_STATE_DIR, else <config dir>/state/stint; <config dir> is CLAUDE_CONFIG_DIR, else
~/.claude. PaceLine's directory (PACELINE_STATE_DIR, else <config dir>/state/paceline) is the legacy
one: its night-queue.json is read until Stint writes its own queue, and never written.
Stdlib only, Python 3.9+.
"""
import contextlib
import json
import os
import time
import uuid
from pathlib import Path

LOCK_WAIT_SEC = 10.0
# Longer than the 60 s timeout of a PaceLine call made while the lock is held.
LOCK_STALE_SEC = 300.0


class StateError(Exception):
    """A file Stint cannot read or take the lock of; the message names it."""


def config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def state_dir():
    """Stint's state directory."""
    return Path(os.environ.get("STINT_STATE_DIR") or config_dir() / "state" / "stint")


def legacy_state_dir():
    """PaceLine's state directory, where night-queue.json lived before Stint."""
    return Path(os.environ.get("PACELINE_STATE_DIR") or config_dir() / "state" / "paceline")


def read_json(path):
    """The JSON object in a file, or None for a missing, unreadable, non-JSON or non-object file."""
    with contextlib.suppress(OSError, ValueError, RecursionError):
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    return None


def read_object(path):
    """The JSON object in a file, None when the file is missing. A file that exists and is not a JSON
    object is an error that names it."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateError(f"cannot read {path}: {error}")
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise StateError(f"{path} is not valid JSON ({error}); fix or remove it")
    if not isinstance(parsed, dict):
        raise StateError(f"{path} does not hold a JSON object; fix or remove it")
    return parsed


def write_json(path, data):
    """Write data whole: UTF-8, LF, one trailing newline, by atomic replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        # Windows refuses the replace while a reader has the target open: retry briefly.
        for attempt in range(20):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink()


@contextlib.contextmanager
def locked(directory):
    """Hold <directory>/queue.lock (created exclusively, holding a token of its own). Waits up to
    LOCK_WAIT_SEC; a lock older than LOCK_STALE_SEC is taken over; release deletes only the lock that
    still holds this token."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "queue.lock"
    token = uuid.uuid4().hex
    deadline = time.monotonic() + LOCK_WAIT_SEC
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(handle, token.encode("ascii"))
            finally:
                os.close(handle)
            break
        except (FileExistsError, PermissionError):
            # PermissionError: Windows refuses a create while another process deletes or holds the file.
            if _take_over_stale(lock):
                continue
            if time.monotonic() >= deadline:
                raise StateError(f"queue is locked: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            if lock.read_text(encoding="ascii") == token:
                lock.unlink()


def _take_over_stale(lock):
    """True when the lock was older than LOCK_STALE_SEC and has been removed. The file is renamed to a
    name of its own first, so that of two processes taking over, only one removes it."""
    with contextlib.suppress(OSError):
        if time.time() - lock.stat().st_mtime <= LOCK_STALE_SEC:
            return False
        taken = lock.with_name(f"{lock.name}.{uuid.uuid4().hex}.stale")
        os.rename(str(lock), str(taken))
        with contextlib.suppress(OSError):
            taken.unlink()
        return True
    return False
