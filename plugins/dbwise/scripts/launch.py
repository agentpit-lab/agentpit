"""Starts one engine's MCP server with the aliases from the dbwise config, passwords resolved.

Run by the plugin venv's Python after the bootstrap has built it. Nothing here writes to stdout,
which belongs to the MCP protocol, and no message carries a password.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from urllib.parse import quote

import config
import levels
import passwords

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5432
DEFAULT_SSLMODE = "require"

SERVERS = {"postgres": {"engine": "postgresql", "package": "postgres_mcp", "dir": "postgres"}}


class ResolveError(RuntimeError):
    """A password could not be obtained; the message says where and what to do, never the password."""


class Resolved:
    """DSNs by level for the levels that could be resolved, and the reason for each that could not."""

    def __init__(self):
        self.dsns = {}
        self.problems = {}


def warn(message: str) -> None:
    print(f"dbwise: {message}", file=sys.stderr, flush=True)


def resolve_password(source: dict) -> str:
    if not isinstance(source, dict) or len(source) != 1:
        raise ResolveError("the password source must hold exactly one of keyring, env or file")
    (kind, arg), = source.items()
    if kind == "keyring":
        try:
            backend = passwords.keychain()
        except passwords.PasswordError as error:
            raise ResolveError(str(error)) from error
        try:
            value = backend.get_password(arg.get("service"), arg.get("username"))
        except Exception as error:
            # Backend errors can quote the secret service's reply, so only the exception type is shown.
            raise ResolveError(f"keychain read failed for credential '{arg.get('username')}': "
                               f"{type(error).__name__}") from None
        if value is None:
            raise ResolveError(f"no keychain entry for credential '{arg.get('username')}' "
                               f"(service '{arg.get('service')}'); store its password with the dbwise command")
        return value
    if kind == "env":
        value = os.environ.get(arg)
        if not value:
            raise ResolveError(f"environment variable {arg} is not set or empty")
        return value
    if kind == "file":
        try:
            path = passwords.check_password_file(arg)
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except passwords.PasswordError as error:
            raise ResolveError(str(error)) from error
        except OSError as error:
            raise ResolveError(f"cannot read password file {arg}: {error.strerror or error}") from error
        if not value:
            raise ResolveError(f"password file {arg} is empty")
        return value
    raise ResolveError(f"unknown password source '{kind}' (allowed: keyring, env, file)")


def build_dsn(spec: dict, password: str) -> str:
    host = spec["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return (f"postgresql://{quote(spec['user'], safe='')}:{quote(password, safe='')}@{host}:"
            f"{spec.get('port', DEFAULT_PORT)}/{quote(spec['dbname'], safe='')}"
            f"?sslmode={spec.get('sslmode', DEFAULT_SSLMODE)}")


def vault_fields(data: dict, alias: dict, level: str) -> set:
    """The connection fields that a Vault mapping of the alias's server or of its credential at `level` supplies."""
    server = data["servers"][alias["server"]]
    credential = data["credentials"][levels.credential_for(alias, level)]
    return set(server.get("vault", {}).get("fields", {})) | set(credential.get("vault", {}).get("fields", {}))


def resolve_alias(entry: dict, mapped: dict | None = None) -> Resolved:
    """`mapped` holds, per level, the fields a Vault mapping supplies; a missing one then names the import."""
    resolved = Resolved()
    for level, spec in entry["connections"].items():
        missing = [field for field in ("host", "user") if not spec.get(field)]
        if missing:
            remedy = ("run the Vault import with the dbwise command"
                      if any(field in (mapped or {}).get(level, ()) for field in missing)
                      else "complete the server entry")
            resolved.problems[level] = f"the connection has no {' or '.join(missing)}; {remedy}"
            continue
        try:
            password = resolve_password(spec["password"])
        except ResolveError as error:
            resolved.problems[level] = str(error)
            continue
        resolved.dsns[level] = build_dsn(spec, password)
    return resolved


def run_server(engine: str, aliases: dict, os_name: str = os.name) -> None:
    server = SERVERS[engine]
    directory = str(PLUGIN_ROOT / "servers" / server["dir"])
    if directory not in sys.path:
        sys.path.insert(0, directory)
    module = importlib.import_module(server["package"])
    # The proactor loop, the Windows default, cannot serve the psycopg async pool.
    kwargs = {"loop_factory": asyncio.SelectorEventLoop} if os_name == "nt" else {}
    asyncio.run(module.run(aliases), **kwargs)


def selected(entries: dict, engine: str) -> dict:
    wanted = SERVERS[engine]["engine"]
    result = {name: entry for name, entry in entries.items() if entry["engine"] == wanted}
    names = [name.strip() for name in os.environ.get("DBWISE_ALIASES", "").split(",") if name.strip()]
    if not names:
        return result
    for name in names:
        if name not in result:
            warn(f"DBWISE_ALIASES names '{name}', which is not an alias of engine '{engine}'; ignored")
    return {name: entry for name, entry in result.items() if name in names}


def main(argv: list) -> int:
    if len(argv) != 1 or argv[0] not in SERVERS:
        sys.exit(f"dbwise: usage: launch.py <engine> (one of {', '.join(sorted(SERVERS))})")
    engine = argv[0]
    try:
        data = config.load()
    except config.ConfigMissing:
        data = {}
    except config.ConfigError as error:
        warn(str(error))
        return 1
    aliases = {}
    for name, entry in selected(config.entries(data), engine).items():
        resolved = resolve_alias(entry, {level: vault_fields(data, data["aliases"][name], level)
                                         for level in entry["connections"]})
        for level, reason in resolved.problems.items():
            warn(f"alias '{name}' ({level}): {reason}")
        aliases[name] = {"level": entry["level"], "role_check": entry["role_check"],
                         "databases": entry["databases"], "dsns": resolved.dsns, "problems": resolved.problems}
    run_server(engine, aliases)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
