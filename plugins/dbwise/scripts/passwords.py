"""Password handling for dbwise: the OS keychain, the terminal prompt, and env/file sources.

A password enters dbwise in one way only: typed by the operator into a terminal that hides input,
then stored in the OS keychain under service "dbwise", username <credential name>, and read back to
confirm. Every check below runs before the prompt, so a refused command has read and written
nothing. No function here returns, prints or logs a password.

keyring is imported inside the functions, so this module imports with the stdlib alone.
"""
from __future__ import annotations

import getpass
import io
import os
import sys
import warnings
from pathlib import Path

import config
import engine_postgres

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class PasswordError(RuntimeError):
    """A refusal or failure whose message tells the operator what to do next."""


def keyring_remedy(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform.startswith("linux"):
        os_step = ("install and unlock a Secret Service provider (GNOME Keyring or KWallet) in a D-Bus "
                   "session, then rerun")
    elif platform == "darwin":
        os_step = "unlock the login keychain in Keychain Access, then rerun"
    else:
        os_step = "check that Windows Credential Manager is available to your user, then rerun"
    return (f"No usable OS keychain. Either {os_step}; or give the credential an explicit source "
            "instead of the keychain: `dbwise credential add <name> --user <user> --password-env VAR` "
            "or `--password-file PATH`. If PYTHON_KEYRING_BACKEND is set, unset it.")


def keychain():
    """The keyring backend, or PasswordError when there is none that can store a password.

    keyring falls back to its fail or null backend (priority below 1) when the OS offers no
    keychain, for example a Linux host without a Secret Service; those backends raise or drop the
    value on write, so they are refused up front.
    """
    try:
        import keyring
    except ImportError as e:
        raise PasswordError(f"the keyring package is missing ({e}); run dbwise through bin/dbwise, which "
                            "builds the plugin venv") from e
    try:
        backend = keyring.get_keyring()
    except Exception as e:
        # The type name only: a backend's message can quote the keychain service's reply.
        raise PasswordError(f"the keychain backend cannot be loaded: {type(e).__name__}") from None
    priority = getattr(backend, "priority", 0)
    if priority < 1:
        raise PasswordError(f"{keyring_remedy()} (keyring backend: {type(backend).__module__}."
                            f"{type(backend).__name__}, priority {priority})")
    return backend


def has_password(backend, credential: str) -> bool:
    return backend.get_password(config.KEYRING_SERVICE, credential) is not None


def cli_commands(args: str, os_name: str = os.name, root: Path = PLUGIN_ROOT) -> list[str]:
    """The command lines that run `dbwise <args>` from a terminal outside Claude Code."""
    if os_name == "nt":
        cmd = Path(root, "bin", "dbwise.cmd")
        return [f'PowerShell: & "{cmd}" {args}', f'cmd: "{cmd}" {args}']
    return [f'Terminal (macOS) or a Linux shell: "{Path(root, "bin", "dbwise").as_posix()}" {args}']


def terminal_instructions(credentials: list[str], os_name: str = os.name, root: Path = PLUGIN_ROOT) -> str:
    lines = ["Run in a terminal where typed input can be hidden:"]
    for name in credentials:
        lines += [f"  {line}" for line in cli_commands(f"set-password {name}", os_name, root)]
    if os_name == "nt":
        lines.append("Git Bash cannot hide typed input on Windows, so use PowerShell or cmd.")
    return "\n".join(lines)


def windows_console(fd: int) -> bool:
    import ctypes
    import msvcrt

    mode = ctypes.c_uint()
    return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(fd), ctypes.byref(mode)))


def is_terminal(stdin, os_name: str = os.name) -> bool:
    """Whether a password can be typed on `stdin`.

    On Windows isatty() is also true for the NUL device, which is what the agent's Bash tool gives
    a child, and getpass there reads the console directly instead of stdin, so it would wait on a
    console nobody sees. Only a handle that answers GetConsoleMode is a console. An object without
    a file descriptor cannot be NUL, so its isatty() is trusted.
    """
    try:
        if stdin is None or not stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os_name != "nt":
        return True
    try:
        fd = stdin.fileno()
    except (AttributeError, ValueError, io.UnsupportedOperation):
        return True
    try:
        return windows_console(fd)
    except OSError:
        return False


def require_terminal(credential: str, stdin=None) -> None:
    if not is_terminal(stdin if stdin is not None else sys.stdin):
        raise PasswordError("a password can only be typed in an interactive terminal, and this one "
                            "has no terminal on stdin (for example the agent's Bash tool).\n"
                            + terminal_instructions([credential]))


def credentials_of(alias: dict) -> set:
    credential = alias.get("credential")
    return {credential} if isinstance(credential, str) else set((credential or {}).values())


def prompt_text(name: str, data: dict) -> str:
    credential = data.get("credentials", {}).get(name, {})
    user = credential.get("user", "(user from Vault)")
    servers = data.get("servers", {})
    used = []
    for alias_name, alias in sorted(data.get("aliases", {}).items()):
        if name not in credentials_of(alias):
            continue
        server = servers.get(alias.get("server"), {})
        host = server.get("host")
        where = f"{host}:{server.get('port', engine_postgres.DEFAULT_PORT)}" if host else "host from Vault"
        used.append(f"{alias_name} at {where}")
    used_by = "aliases " + ", ".join(used) if used else "no alias uses it yet"
    return f"Password for {name} (user {user}; {used_by}): "


def read_hidden(prompt: str, reader=None) -> str:
    """getpass with its no-echo fallback turned into a refusal.

    getpass falls back to reading a visible line, with only a GetPassWarning, when it cannot turn
    echo off (Git Bash under mintty is the usual case); the warning becomes an error here, raised
    before anything is read.
    """
    reader = reader or getpass.getpass
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return reader(prompt)
        except getpass.GetPassWarning as e:
            raise PasswordError("this terminal cannot hide typed input, so no password was read.") from e


def store(backend, credential: str, password: str) -> None:
    backend.set_password(config.KEYRING_SERVICE, credential, password)
    if backend.get_password(config.KEYRING_SERVICE, credential) != password:
        raise PasswordError(f"the keychain returned a different value for {credential} after the write; "
                            "check the keychain and rerun set-password")


def ask_and_store(backend, name: str, data: dict, stdin=None, reader=None) -> None:
    """Prompts for the credential's password in the terminal and stores it in the keychain.

    The caller has already checked the keychain; the terminal is checked here, before the prompt.
    """
    require_terminal(name, stdin)
    try:
        password = read_hidden(prompt_text(name, data), reader)
    except PasswordError as e:
        raise PasswordError(f"{e}\n{terminal_instructions([name])}") from None
    except (EOFError, KeyboardInterrupt):
        raise PasswordError(f"no password entered for {name}; nothing was stored") from None
    if not password:
        raise PasswordError(f"empty password for {name}; nothing was stored")
    store(backend, name, password)


def file_mode_problem(mode: int) -> str | None:
    if mode & 0o077:
        return f"is accessible to group or others (mode {mode & 0o777:o}); run chmod 600 on it"
    return None


def check_password_file(path: str, os_name: str = os.name) -> Path:
    """The absolute path of a usable password file, or PasswordError.

    The launcher reads the file without checking who else can read it, so dbwise refuses a
    file readable by group or others on macOS and Linux. Windows has no mode bits to check; there
    the file must sit under the user profile, whose ACL keeps other users out by default.
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if not p.is_file():
        raise PasswordError(f"password file {p} not found")
    if os_name == "nt":
        home = Path.home().resolve()
        if not p.is_relative_to(home):
            raise PasswordError(f"password file {p} is outside your user profile {home}; "
                                "move it under the profile")
    else:
        problem = file_mode_problem(p.stat().st_mode)
        if problem:
            raise PasswordError(f"password file {p} {problem}")
    return p
