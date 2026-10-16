"""The local health check the SessionStart hook adds to the session context.

    python session_check.py    prints one line per problem, nothing when all is well; exits 0

Checks only what can be seen without a network: the config is readable and valid; every alias has a
host and a user at each level; every credential an alias uses has its password where its source says
(a keychain entry, a set environment variable, or a file dbwise accepts); and the plugin's environment
is built. With no config, or a config without aliases, there is nothing to check and it prints nothing.
Each line names the item and the command that fixes it. `dbwise doctor` covers the network side.

Run with the plugin venv's interpreter when it is ready, since the keychain is read through keyring.
No line contains a password.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import bootstrap
import config
import levels
import passwords
from errors import RELOAD

PREFIX = "dbwise:"
VENV_NOT_READY = (f"{PREFIX} the `postgres` server offers only `dbwise_status` until the first build ends. "
                  f"When it finishes, reconnect {RELOAD}")


def keyring_installed() -> bool:
    try:
        return importlib.util.find_spec("keyring") is not None
    except ValueError:
        # A module already loaded without a spec, such as a test double.
        return True


def fix_password(name: str, credential: dict) -> str:
    if "password" in credential.get("vault", {}).get("fields", {}):
        return "run `dbwise import --from-vault`"
    return f"run `dbwise set-password {name}` in a terminal"


def credential_users(data: dict) -> dict:
    """Credential name -> the aliases that use it at any level."""
    used: dict[str, list[str]] = {}
    for alias_name, alias in sorted(data["aliases"].items()):
        for level in levels.LEVELS[:levels.LEVELS.index(levels.alias_level(alias)) + 1]:
            aliases = used.setdefault(levels.credential_for(alias, level), [])
            if alias_name not in aliases:
                aliases.append(alias_name)
    return used


def missing_fields(data: dict) -> list[str]:
    lines = []
    for alias_name, entry in sorted(config.entries(data).items()):
        for level, spec in entry["connections"].items():
            missing = [field for field in ("host", "user") if not spec.get(field)]
            if missing:
                lines.append(f"{PREFIX} alias {alias_name} has no {' or '.join(missing)} yet (it comes from "
                             "Vault); run `dbwise import --from-vault`.")
                break
    return lines


def password_lines(data: dict, environ) -> list[str]:
    lines = []
    backend, backend_error = None, None
    keychain_names = []
    for name, used_by in sorted(credential_users(data).items()):
        credential = data["credentials"][name]
        source = credential.get("password") or {"keyring": {}}
        where = f"aliases {', '.join(used_by)}"
        if "keyring" in source:
            if not keyring_installed():
                # Only a base interpreter lacks keyring, and the first build is reported separately.
                continue
            keychain_names.append(name)
            if backend is None and backend_error is None:
                try:
                    backend = passwords.keychain()
                except passwords.PasswordError as e:
                    backend_error = str(e)
            if backend_error is not None:
                continue
            try:
                stored = passwords.has_password(backend, name)
            except Exception as e:  # noqa: BLE001 - keyring backends raise their own types
                lines.append(f"{PREFIX} the keychain could not be read for credential {name} ({where}): "
                             f"{type(e).__name__}; run /dbwise:doctor.")
                continue
            if not stored:
                lines.append(f"{PREFIX} no password in the OS keychain for credential {name} ({where}); "
                             f"{fix_password(name, credential)}.")
        elif "env" in source:
            if not environ.get(source["env"]):
                lines.append(f"{PREFIX} credential {name} ({where}) reads its password from the environment "
                             f"variable {source['env']}, which is not set; set it before starting Claude Code, "
                             f"or move the password to the keychain with `dbwise set-password {name}`.")
        else:
            try:
                passwords.check_password_file(source["file"])
            except passwords.PasswordError as e:
                lines.append(f"{PREFIX} credential {name} ({where}): {e}; the server cannot use it.")
    if backend_error is not None:
        lines.append(f"{PREFIX} credentials {', '.join(keychain_names)} use the keychain, but there is none: "
                     f"{backend_error}")
    return lines


def problems(environ=os.environ) -> list[str]:
    path = config.config_path()
    try:
        data = config.load(path)
    except config.ConfigMissing:
        return []
    except config.ConfigError as e:
        bak = path.with_name(path.name + ".bak")
        restore = f"or copy {bak} over it" if bak.exists() else f"(there is no {bak.name})"
        return [f"{PREFIX} the config cannot be used, so the postgres server does not start and no database tool is offered: {e}. Fix the file "
                f"{restore}, then reconnect {RELOAD}"]
    if not data.get("aliases"):
        return []
    lines = missing_fields(data) + password_lines(data, environ)
    try:
        ready = bootstrap.is_current(bootstrap.resolve_paths())
    except OSError:
        ready = False
    if not ready:
        lines.append(VENV_NOT_READY)
    return lines


def main() -> int:
    try:
        for line in problems():
            print(line)
    except Exception as e:  # noqa: BLE001 - a check failure must stay one line, never a traceback
        print(f"{PREFIX} the session check failed ({type(e).__name__}); run /dbwise:doctor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
