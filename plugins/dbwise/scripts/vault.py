"""HashiCorp Vault / OpenBao KV v2 access for `dbwise import --from-vault`, through the `vault` CLI.

The address, namespace, mount and login method come from the team file's `vault` section; this
module has no defaults for any of them. Every call is a child process of the `vault` CLI, which owns
the token, the trust store and the namespace handling of the login the operator already completed.
A child's stdout holds secret values: it is captured, parsed and dropped, never printed or logged,
and an error shows at most the first 200 characters of the CLI's stderr.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_LOGIN = "oidc"
INSTALL_DOCS = "https://developer.hashicorp.com/vault/install"
STDERR_LIMIT = 200
READ_TIMEOUT = 30
LOGIN_TIMEOUT = 300

# What the CLI prints on stderr, matched case-insensitively. The wording is the CLI's, not a contract.
_MISSING_TOKEN = re.compile(r"missing client token", re.I)
_REFUSED = re.compile(r"\bcode: 40[13]\b|permission denied|invalid token|bad token|token (?:is )?expired", re.I)
_UNREACHABLE = re.compile(r"dial tcp|connection refused|no such host|i/o timeout|context deadline|"
                          r"connection reset|network is unreachable|tls:|x509:", re.I)
_NOT_FOUND = re.compile(r"no value found", re.I)


class VaultError(RuntimeError):
    """A Vault failure other than authentication; the message never holds a secret value."""


class VaultAuthError(VaultError):
    """No token, or Vault refused the token (HTTP 401 or 403)."""


class VaultCliMissing(VaultError):
    """The `vault` CLI is not on PATH."""


@dataclass(frozen=True)
class Settings:
    addr: str
    mount: str
    namespace: str | None = None
    login: str = DEFAULT_LOGIN

    @classmethod
    def from_config(cls, section: dict) -> "Settings":
        """Settings from a validated `vault` section; addr and mount are required there."""
        return cls(addr=section["addr"].rstrip("/"), mount=section["mount"].strip("/"),
                   namespace=section.get("namespace") or None, login=section.get("login") or DEFAULT_LOGIN)


def install_hint(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform.startswith("win"):
        command = "winget install --id HashiCorp.Vault -e"
    elif platform == "darwin":
        command = "brew tap hashicorp/tap && brew install hashicorp/tap/vault"
    else:
        command = f"follow the package instructions for your distribution at {INSTALL_DOCS}"
    return (f"The Vault CLI (`vault`) is not on PATH; dbwise reads from Vault and logs in through it. "
            f"Install it: {command} (or download it from {INSTALL_DOCS}), open a new terminal, then rerun.")


def find_on_path(name: str) -> str | None:
    """The first executable `name` in an absolute PATH directory, or None.

    shutil.which looks in the current directory first on Windows (Python before 3.12 always, later
    versions unless NoDefaultCurrentDirectoryInExePath is set, which an operator's terminal does not
    set) and treats an empty or "." PATH entry as the current directory on POSIX. A `vault` placed in
    a cloned repository would then run instead of the real CLI and receive the operator's token.
    """
    extensions = [""]
    if os.name == "nt":
        extensions = [ext.lower() for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if ext]
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or not os.path.isabs(directory):
            continue
        for extension in extensions:
            candidate = os.path.join(directory, name + extension)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def require_cli() -> str:
    """The absolute path of the `vault` CLI, or VaultCliMissing."""
    executable = find_on_path("vault")
    if not executable:
        raise VaultCliMissing(install_hint())
    if not os.path.isabs(executable):
        raise VaultCliMissing(f"the lookup of the `vault` CLI returned a relative path ({executable}); "
                              "dbwise runs only a vault found through an absolute PATH entry, because "
                              "the child receives your Vault token. Fix PATH, then rerun.")
    return executable


def login_args(settings: Settings, executable: str = "vault") -> list[str]:
    return [executable, "login", f"-method={settings.login}", "-no-print"]


def child_env(settings: Settings, base: dict | None = None) -> dict:
    """The environment of every child: the configured address and namespace win over the parent's."""
    env = dict(os.environ if base is None else base)
    env["VAULT_ADDR"] = settings.addr
    if settings.namespace:
        env["VAULT_NAMESPACE"] = settings.namespace
    else:
        env.pop("VAULT_NAMESPACE", None)
    return env


def login_command_text(settings: Settings) -> str:
    """The login as the operator would type it in PowerShell and in a POSIX shell."""
    namespace_ps = f"$env:VAULT_NAMESPACE='{settings.namespace}'; " if settings.namespace else ""
    namespace_sh = f"VAULT_NAMESPACE='{settings.namespace}' " if settings.namespace else ""
    args = " ".join(login_args(settings)[1:])
    return (f"  PowerShell: $env:VAULT_ADDR='{settings.addr}'; {namespace_ps}vault {args}\n"
            f"  Terminal or a Linux shell: VAULT_ADDR='{settings.addr}' {namespace_sh}vault {args}")


def _stderr_text(stderr: str | None) -> str:
    """The CLI's stderr on one line, from the first "Errors:" marker when there is one, cut to the limit.

    A raw API error opens with a long request preamble, and the reason follows the marker.
    """
    text = " ".join((stderr or "").split())
    marker = re.search(r"\berrors:\s*", text, re.I)
    if marker and text[marker.end():]:
        text = text[marker.end():]
    return text[:STDERR_LIMIT]


def _capture(settings: Settings, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Runs `vault <args>` with captured output. Raises VaultCliMissing, or VaultError on a timeout."""
    executable = require_cli()
    try:
        return subprocess.run([executable, *args], env=child_env(settings), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        raise VaultError(f"the vault CLI did not answer within {timeout:g} s (address {settings.addr})") from None
    except OSError as e:
        raise VaultError(f"cannot start {executable}: {e.strerror or type(e).__name__}") from None


def read_secret(settings: Settings, path: str, timeout: float = READ_TIMEOUT) -> dict:
    """The key-value pairs of the KV v2 secret `<mount>/<path>`, latest version.

    A raw read of `<mount>/data/<path>`: `vault kv get` is avoided because its mount lookup needs a
    permission that a narrow token may lack.
    """
    path = path.strip("/")
    shown = f"{settings.mount}/{path}"
    done = _capture(settings, ["read", "-format=json", f"{settings.mount}/data/{path}"], timeout)
    if done.returncode != 0:
        stderr = done.stderr or ""
        if _MISSING_TOKEN.search(stderr) or _REFUSED.search(stderr):
            raise VaultAuthError(f"Vault refused the token reading {shown}: it is missing or expired, or it "
                                 f"has no access to that path ({_stderr_text(stderr)})")
        if _NOT_FOUND.search(stderr):
            raise VaultError(f"no secret at {shown}")
        raise VaultError(f"vault read {shown} failed (exit {done.returncode}): {_stderr_text(stderr)}")
    try:
        data = json.loads(done.stdout)["data"]["data"]
    except (ValueError, KeyError, TypeError):
        data = None
    if not isinstance(data, dict):
        raise VaultError(f"{shown} is not a KV v2 secret (the output has no data.data object); "
                         "check that the mount is a KV version 2 engine")
    return data


def check_token(settings: Settings, timeout: float = 10) -> str:
    """"ok", "missing", "expired", "unreachable", "no-cli" or "error:exit<code>"; reads no secret.

    The lookup output holds the token itself, so it is not looked at beyond the exit code.
    """
    try:
        done = _capture(settings, ["token", "lookup", "-format=json"], timeout)
    except VaultCliMissing:
        return "no-cli"
    except VaultError:
        return "unreachable"
    if done.returncode == 0:
        return "ok"
    stderr = done.stderr or ""
    if _MISSING_TOKEN.search(stderr):
        return "missing"
    if _REFUSED.search(stderr):
        return "expired"
    if _UNREACHABLE.search(stderr):
        return "unreachable"
    return f"error:exit{done.returncode}"


def run_login(settings: Settings, interactive: bool = True) -> int:
    """Runs `vault login -method=<login> -no-print` against the configured Vault and returns its exit code.

    The child inherits stdout and stderr so the operator sees the browser SSO prompt or the URL to
    open; -no-print keeps the token out of that output. Without a terminal its stdin is the null
    device, so a method that would prompt fails instead of waiting on an unseen console, and nobody
    is there to cancel a sign-in that never completes, so it is stopped after LOGIN_TIMEOUT seconds.
    Raises VaultCliMissing when there is no CLI.
    """
    executable = require_cli()
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        # subprocess.run kills the child when the timeout expires.
        return subprocess.run(login_args(settings, executable), env=child_env(settings),
                              stdin=None if interactive else subprocess.DEVNULL,
                              timeout=None if interactive else LOGIN_TIMEOUT, check=False).returncode
    except subprocess.TimeoutExpired:
        raise VaultError(f"the vault login did not finish within {LOGIN_TIMEOUT} s and was stopped "
                         f"(address {settings.addr})") from None
    except OSError as e:
        raise VaultError(f"cannot start {executable}: {e}") from None
