"""Builds the dependency environment on first use and hands over to the server or the CLI.

The launchers call this file with a bare system Python, so it imports the standard library only and
nothing from its own directory at module level. Nothing here writes to stdout: for an MCP server
stdout belongs to the protocol.
"""
import contextlib
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PYTHON_RANGE = "3.12 to 3.14"
UNUSED_DAYS = 14
LOCK_POLL_SECONDS = 0.2
# A stub probing the lock holds it for an instant; a build waits this long before it concludes another build runs.
BUILD_LOCK_GRACE_SECONDS = 2.0
CREATE_NO_WINDOW = 0x08000000
PIP_INSTALL_ARGS = ["install", "--disable-pip-version-check", "--require-hashes", "--only-binary", ":all:", "-r",
                    str(PLUGIN_ROOT / "requirements.txt")]


class BuildError(Exception):
    pass


def pip_args(requirements: Path) -> list:
    return PIP_INSTALL_ARGS[:-1] + [str(requirements)]


def venv_python(venv: Path, os_name: str = os.name) -> Path:
    return venv.joinpath("Scripts", "python.exe") if os_name == "nt" else venv.joinpath("bin", "python")


def default_home(environ) -> Path:
    configured = environ.get("DBWISE_HOME")
    return Path(configured) if configured else Path.home() / ".dbwise"


class Paths:
    """Every location the bootstrap touches. One venv per lock-file hash, so a plugin update that
    changes the lock builds beside the old environment and a running server keeps its files."""

    def __init__(self, root: Path, home: Path, os_name: str = os.name):
        self.root = Path(root)
        self.home = Path(home)
        self.os_name = os_name

    @property
    def requirements(self) -> Path:
        return self.root / "requirements.txt"

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.requirements.read_bytes()).hexdigest()

    @property
    def venvs(self) -> Path:
        return self.home / "venvs"

    @property
    def venv(self) -> Path:
        return self.venvs / self.digest[:12]

    @property
    def python(self) -> Path:
        return venv_python(self.venv, self.os_name)

    @property
    def pyvenv_cfg(self) -> Path:
        return self.venv / "pyvenv.cfg"

    @property
    def stamp(self) -> Path:
        return self.venv / "deps.sha256"

    @property
    def last_used(self) -> Path:
        return self.venv / "last-used"

    @property
    def log(self) -> Path:
        return self.home / "bootstrap.log"

    @property
    def lock(self) -> Path:
        return self.home / "bootstrap.lock"

    @property
    def launch(self) -> Path:
        return self.root / "scripts" / "launch.py"

    @property
    def cli(self) -> Path:
        return self.root / "scripts" / "dbwise.py"


def resolve_paths() -> Paths:
    return Paths(PLUGIN_ROOT, default_home(os.environ))


def status(message: str) -> None:
    print(f"dbwise: {message}", file=sys.stderr, flush=True)


def python_ok(version_info) -> bool:
    return (3, 12) <= tuple(version_info[:2]) < (3, 15)


def python_refusal(version_info, exe: str) -> str:
    version = ".".join(str(part) for part in version_info[:3])
    return f"dbwise needs Python {PYTHON_RANGE}; {exe} is Python {version}."


def arm64_refusal(os_name: str, build_platform: str):
    """`build_platform` is sysconfig.get_platform(): it names the interpreter's build, where
    platform.machine() names the host CPU and would refuse an x64 Python emulated on ARM64."""
    if os_name == "nt" and build_platform.lower() == "win-arm64":
        return ("dbwise does not run on native Windows ARM64 Python: psycopg-binary publishes no wheel "
                "for it. Install an x64 Python 3.12 to 3.14; Windows runs it by emulation.")
    return None


def base_alive(cfg: Path) -> bool:
    """False when the interpreter the venv was made from is gone (uninstalled or upgraded in place)."""
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return False
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    if "executable" in values:
        return Path(values["executable"]).exists()
    if "home" in values:
        return Path(values["home"]).is_dir()
    return True


def is_current(paths: Paths) -> bool:
    try:
        digest = paths.digest
        stamp = paths.stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stamp == digest and paths.python.exists() and base_alive(paths.pyvenv_cfg)


def touch_last_used(paths: Paths) -> None:
    with contextlib.suppress(OSError):
        paths.last_used.touch(exist_ok=True)


def run_logged(cmd, log: Path) -> int:
    """Runs a command with all its output appended to the log; returns the exit code."""
    flags = CREATE_NO_WINDOW if os.name == "nt" else 0
    with open(log, "ab") as out:
        out.write(("$ " + " ".join(str(c) for c in cmd) + "\n").encode("utf-8"))
        out.flush()
        return subprocess.call([str(c) for c in cmd], stdin=subprocess.DEVNULL, stdout=out,
                               stderr=subprocess.STDOUT, creationflags=flags)


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def create_venv(base, target: Path, log: Path, os_name: str = os.name) -> None:
    if run_logged([base, "-I", "-m", "venv", str(target)], log) == 0:
        return
    # Some distributions ship venv without ensurepip; build the venv bare and add pip afterwards.
    _remove(target)
    if (run_logged([base, "-I", "-m", "venv", "--without-pip", str(target)], log) == 0
            and run_logged([venv_python(target, os_name), "-I", "-m", "ensurepip", "--upgrade", "--default-pip"],
                           log) == 0):
        return
    _remove(target)
    raise BuildError(f"venv creation failed with {base}; see {log}")


def sweep(paths: Paths) -> None:
    """Called under the build lock, so any staging or retired tree belongs to a builder that is gone."""
    try:
        entries = list(paths.venvs.iterdir())
    except OSError:
        return
    cutoff = time.time() - UNUSED_DAYS * 24 * 3600
    for entry in entries:
        name = entry.name
        try:
            if ".building." in name or ".old." in name:
                shutil.rmtree(entry)
            elif re.fullmatch("[0-9a-f]{12}", name) and name != paths.venv.name:
                marker = entry / "last-used"
                used = marker.stat().st_mtime if marker.exists() else entry.stat().st_mtime
                if used < cutoff:
                    shutil.rmtree(entry)
        except OSError:
            continue


def fresh_build(paths: Paths, base_exe) -> None:
    paths.venvs.mkdir(parents=True, exist_ok=True)
    sweep(paths)
    name = paths.venv.name
    staging = paths.venvs / f"{name}.building.{os.getpid()}"
    _remove(staging)
    create_venv(base_exe, staging, paths.log, paths.os_name)
    code = run_logged([venv_python(staging, paths.os_name), "-I", "-m", "pip", *pip_args(paths.requirements)], paths.log)
    if code != 0:
        _remove(staging)
        raise BuildError(f"dependency install failed (exit {code}) with Python "
                         f"{platform.python_version()} on {sys.platform}; see {paths.log}")
    # Console scripts inside the tree keep the staging path in their shebang; the server is started
    # as python -m or by file, never through them, so the rename-only publish is safe.
    retired = None
    try:
        if paths.venv.exists():
            retired = paths.venvs / f"{name}.old.{os.getpid()}"
            _remove(retired)
            os.replace(paths.venv, retired)
        os.replace(staging, paths.venv)
    except OSError as error:
        if retired is not None and not paths.venv.exists():
            with contextlib.suppress(OSError):
                os.replace(retired, paths.venv)
        _remove(staging)
        raise BuildError(f"cannot publish the new environment: {error}") from error


def build(paths: Paths, base) -> None:
    fresh_build(paths, base)
    touch_last_used(paths)


@contextlib.contextmanager
def build_lock(path: Path, os_name: str = os.name, wait: bool = True, grace: float = 0.0):
    """An OS-level lock on a file: it dies with its holder, so a crashed build never leaves it stuck.

    With wait=False the lock is tried again for `grace` seconds before it counts as held elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    held = False
    deadline = time.monotonic() + grace
    try:
        while True:
            try:
                _lock(handle, os_name)
                held = True
                break
            except OSError:
                if not wait and time.monotonic() >= deadline:
                    break
                time.sleep(LOCK_POLL_SECONDS)
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                _unlock(handle, os_name)
        handle.close()


def _lock(handle, os_name: str) -> None:
    if os_name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle, os_name: str) -> None:
    if os_name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lock_is_free(path: Path, grace: float = 0.0) -> bool:
    if not path.exists():
        return True
    try:
        with build_lock(path, wait=False, grace=grace) as held:
            return held
    except OSError:
        return False


def ensure(paths: Paths, base_exe=None, wait: bool = True) -> int:
    """Makes the venv current. With wait=False a build that is already running elsewhere counts as success."""
    refusal = arm64_refusal(paths.os_name, sysconfig.get_platform())
    if refusal:
        status(refusal)
        return 1
    try:
        paths.digest
    except OSError as error:
        status(f"cannot read the lock file {paths.requirements}: {error}")
        return 1
    if is_current(paths):
        return 0
    try:
        paths.home.mkdir(parents=True, exist_ok=True)
        with build_lock(paths.lock, paths.os_name, wait, BUILD_LOCK_GRACE_SECONDS) as held:
            if not held:
                status("another dbwise build is already running")
                return 0
            if is_current(paths):
                return 0
            with open(paths.log, "wb"):
                pass
            status(f"installing dependencies into {paths.venv}")
            build(paths, base_exe or sys.executable)
            paths.stamp.write_text(paths.digest, encoding="utf-8", newline="\n")
            status("dependencies installed")
            return 0
    except (BuildError, OSError) as error:
        status(str(error))
        return 1


def run_mcp(paths: Paths, args: list) -> int:
    if is_current(paths):
        touch_last_used(paths)
        command = [str(paths.python), str(paths.launch), *args]
        if paths.os_name == "nt":
            return subprocess.call(command)
        os.execv(command[0], command)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.modules.setdefault("bootstrap", sys.modules[__name__])
    import stub_server
    return stub_server.main(paths)


def run_cli(paths: Paths, args: list) -> int:
    code = ensure(paths, wait=True)
    if code:
        return code
    touch_last_used(paths)
    return subprocess.call([str(paths.python), str(paths.cli), *args])


def main(argv: list) -> int:
    mode, rest = (argv[0], argv[1:]) if argv else (None, [])
    if mode not in ("--build", "--run-mcp", "--run-cli"):
        status("usage: bootstrap.py --build | --run-mcp <engine> [args] | --run-cli [args]")
        return 2
    if mode == "--run-mcp" and not rest:
        status("--run-mcp needs an engine name, for example postgres")
        return 2
    if not python_ok(sys.version_info):
        status(python_refusal(sys.version_info, sys.executable))
        return 1
    paths = resolve_paths()
    if mode == "--run-mcp":
        return run_mcp(paths, rest)
    if mode == "--run-cli":
        return run_cli(paths, rest)
    # An inherited log handle keeps its own offset, so the build reports through an append handle instead.
    previous = sys.stderr
    stream = None
    try:
        paths.home.mkdir(parents=True, exist_ok=True)
        stream = open(paths.log, "a", encoding="utf-8", buffering=1)
        sys.stderr = stream
    except OSError:
        pass
    try:
        return ensure(paths, wait=False)
    finally:
        sys.stderr = previous
        if stream is not None:
            stream.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
