"""The dbwise local config: ~/.dbwise/config.json (standard library only).

A team file has the same schema. Neither holds a password, only a reference to where one lives:

    {
      "dbwise": 1,
      "vault": {"addr": "...", "namespace": "...", "mount": "...", "login": "oidc"},
      "credentials": {"<name>": {"user": "...", "password": {"keyring": {}}, "vault": {...}}},
      "servers": {"<name>": {"engine": "postgresql", "host": "...", "port": 5432, "sslmode": "require",
                             "dbname": "postgres", "databases": ["..."], "role_check": "enforce",
                             "vault": {...}}},
      "aliases": {"<alias>": {"server": "<server name>", "level": "read",
                              "credential": "<name>" or {"read": "<name>", "diagnose": "<name>"}}}
    }

A credential is identified by its name and never by its user: the keyring entry of
{"keyring": {}} is service "dbwise", username <credential name>, so two credentials with the same
user keep two passwords. DBWISE_HOME overrides the directory (tests).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import engine_postgres
from errors import FieldError
from levels import DEFAULT_LEVEL, LEVELS, alias_level, credential_for

SCHEMA_VERSION = 1
KEYRING_SERVICE = "dbwise"
ENGINES = {"postgresql": engine_postgres}
ROLE_CHECKS = ("enforce", "warn")
DEFAULT_ROLE_CHECK = "enforce"
# An alias is the tool argument the model types, so its name stays a plain token.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

TOP_KEYS = {"dbwise", "vault", "credentials", "servers", "aliases"}
VAULT_KEYS = {"addr", "namespace", "mount", "login"}
CREDENTIAL_KEYS = {"user", "password", "vault"}
ALIAS_KEYS = {"server", "level", "credential"}


class ConfigError(ValueError):
    pass


class ConfigMissing(ConfigError):
    pass


def config_dir() -> Path:
    home = os.environ.get("DBWISE_HOME")
    return Path(home) if home else Path.home() / ".dbwise"


def config_path() -> Path:
    return config_dir() / "config.json"


def _fail(path: str, message: str) -> None:
    raise ConfigError(f"{path}: {message}")


def _find_literal_password(node, path: str) -> str | None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key == "password" and isinstance(value, str):
                return child
            if path.endswith(".vault") and key == "fields":
                # A Vault mapping names the secret key that holds the password, never the password.
                continue
            found = _find_literal_password(value, child)
            if found:
                return found
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found = _find_literal_password(value, f"{path}[{i}]")
            if found:
                return found
    return None


def _object(value, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be a JSON object")
    return value


def _string(value, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _keys(obj: dict, allowed: set, path: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        _fail(f"{path}.{unknown[0]}", f"unknown key (allowed: {', '.join(sorted(allowed))})")


def _name(name: str, path: str) -> None:
    if not NAME.fullmatch(name):
        _fail(path, f"name '{name}' must start with a letter or digit and hold only letters, digits, '.', '_' or '-'")


def _vault_mapping(value, path: str) -> set:
    obj = _object(value, path)
    _keys(obj, {"path", "fields"}, path)
    _string(obj.get("path"), f"{path}.path")
    fields = _object(obj.get("fields"), f"{path}.fields")
    for key, secret_key in fields.items():
        _string(secret_key, f"{path}.fields.{key}")
    return set(fields)


def _password_source(value, path: str) -> None:
    obj = _object(value, path)
    if len(obj) != 1:
        _fail(path, 'must hold exactly one of {"keyring": {}}, {"env": VAR} or {"file": path}')
    (kind, arg), = obj.items()
    if kind == "keyring":
        if arg != {}:
            _fail(f"{path}.keyring",
                  f"must be {{}}: the keychain entry is always service '{KEYRING_SERVICE}', username <credential name>")
    elif kind in ("env", "file"):
        _string(arg, f"{path}.{kind}")
    else:
        _fail(f"{path}.{kind}", "unknown password source (allowed: keyring, env, file)")


def _server(name: str, server) -> None:
    path = f"$.servers.{name}"
    _name(name, path)
    server = _object(server, path)
    engine = server.get("engine")
    if "engine" not in server:
        _fail(f"{path}.engine", f"required (allowed: {', '.join(ENGINES)})")
    if not isinstance(engine, str) or engine not in ENGINES:
        _fail(f"{path}.engine", f"unsupported engine {json.dumps(engine)} (allowed: {', '.join(ENGINES)})")
    if "role_check" in server and server["role_check"] not in ROLE_CHECKS:
        _fail(f"{path}.role_check", f"must be one of {', '.join(ROLE_CHECKS)}")
    mapped = _vault_mapping(server["vault"], f"{path}.vault") if "vault" in server else set()
    try:
        ENGINES[engine].validate_server(server, path, mapped)
    except FieldError as e:
        _fail(e.path, e.message)


def _alias(name: str, alias, servers: dict, credentials: dict) -> None:
    path = f"$.aliases.{name}"
    _name(name, path)
    alias = _object(alias, path)
    _keys(alias, ALIAS_KEYS, path)
    server = _string(alias.get("server"), f"{path}.server")
    if server not in servers:
        _fail(f"{path}.server", f"unknown server '{server}'")
    level = alias.get("level", DEFAULT_LEVEL)
    if level not in LEVELS:
        _fail(f"{path}.level", f"must be one of {', '.join(LEVELS)}")
    if "credential" not in alias:
        _fail(f"{path}.credential", "required: a credential name or a map from level to credential name")
    credential = alias["credential"]
    allowed = LEVELS[:LEVELS.index(level) + 1]
    if isinstance(credential, str):
        names = {"credential": _string(credential, f"{path}.credential")}
    else:
        credential = _object(credential, f"{path}.credential")
        for lvl in credential:
            if lvl not in LEVELS:
                _fail(f"{path}.credential.{lvl}", f"unknown level (allowed: {', '.join(LEVELS)})")
            if lvl not in allowed:
                _fail(f"{path}.credential.{lvl}", f"the alias is at level '{level}', which has no credential for '{lvl}'")
        for lvl in allowed:
            if lvl not in credential:
                _fail(f"{path}.credential",
                      f"names no credential for level '{lvl}'; a map must name every level from "
                      f"'{LEVELS[0]}' up to '{level}'")
        names = {f"credential.{lvl}": _string(credential[lvl], f"{path}.credential.{lvl}") for lvl in allowed}
    for where, cred_name in names.items():
        if cred_name not in credentials:
            _fail(f"{path}.{where}", f"unknown credential '{cred_name}'")


def validate(data) -> dict:
    """Returns `data` unchanged when it follows the schema; otherwise raises ConfigError naming the JSON path."""
    literal = _find_literal_password(data, "$")
    if literal:
        _fail(literal, "a literal password is not allowed; store it with `dbwise set-password` or reference "
                       'it as {"env": VAR} or {"file": path}')
    top = _object(data, "$")
    _keys(top, TOP_KEYS, "$")
    if top.get("dbwise") != SCHEMA_VERSION:
        _fail("$.dbwise", f"must be {SCHEMA_VERSION}")

    if "vault" in top:
        vault = _object(top["vault"], "$.vault")
        _keys(vault, VAULT_KEYS, "$.vault")
        for key in ("addr", "mount"):
            _string(vault.get(key), f"$.vault.{key}")
        for key in ("namespace", "login"):
            if key in vault:
                _string(vault[key], f"$.vault.{key}")

    credentials = _object(top.get("credentials", {}), "$.credentials")
    for name, cred in credentials.items():
        path = f"$.credentials.{name}"
        _name(name, path)
        cred = _object(cred, path)
        _keys(cred, CREDENTIAL_KEYS, path)
        mapped = _vault_mapping(cred["vault"], f"{path}.vault") if "vault" in cred else set()
        if "user" in cred or "user" not in mapped:
            _string(cred.get("user"), f"{path}.user")
        if "password" in cred:
            _password_source(cred["password"], f"{path}.password")

    servers = _object(top.get("servers", {}), "$.servers")
    for name, server in servers.items():
        _server(name, server)

    aliases = _object(top.get("aliases", {}), "$.aliases")
    for name, alias in aliases.items():
        _alias(name, alias, servers, credentials)
    return data


def load(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ConfigMissing(f"{path}: not found") from e
    except OSError as e:
        raise ConfigError(f"{path}: cannot read: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON: {e}") from e
    try:
        return validate(data)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e


def save(data: dict, path: Path | None = None) -> Path:
    """Validates and writes the config; the previous file is kept as config.json.bak."""
    validate(data)
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def password_source(name: str, credential: dict) -> dict:
    source = credential.get("password") or {"keyring": {}}
    if "keyring" in source:
        return {"keyring": {"service": KEYRING_SERVICE, "username": name}}
    return dict(source)


def role_check(server: dict) -> str:
    """The server's role gate mode; a forgotten field fails closed."""
    return server.get("role_check", DEFAULT_ROLE_CHECK)


def entries(data: dict) -> dict:
    """One entry per alias for the launcher and the server.

    Each entry holds the alias's engine, server name, level, role gate mode, discovery database,
    database list, and `connections`: one connection spec per level from `read` up to the alias's
    level, built with the credential that level uses.
    """
    credentials = data.get("credentials", {})
    servers = data.get("servers", {})
    result = {}
    for alias_name, alias in data.get("aliases", {}).items():
        server = servers[alias["server"]]
        engine = ENGINES[server["engine"]]
        level = alias_level(alias)
        connections = {}
        for lvl in LEVELS[:LEVELS.index(level) + 1]:
            credential_name = credential_for(alias, lvl)
            credential = credentials[credential_name]
            connections[lvl] = engine.connection_spec(
                server, credential.get("user"), password_source(credential_name, credential))
        result[alias_name] = {
            "engine": server["engine"],
            "server": alias["server"],
            "level": level,
            "role_check": role_check(server),
            "dbname": server["dbname"],
            "databases": list(server.get("databases", [])),
            "connections": connections,
        }
    return result
