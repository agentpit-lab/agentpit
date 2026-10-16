"""dbwise command line: manages ~/.dbwise/config.json and the keychain passwords it refers to.

    dbwise alias add <alias> <server> [--level read|diagnose|write] [--credential NAME | --credential LEVEL=NAME ...]
    dbwise alias rm <alias> | alias list
    dbwise server add <name> --host H --databases a,b [--engine] [--port] [--sslmode] [--dbname]
    dbwise server rm <name> | server list
    dbwise credential add <name> --user U [--password-env VAR | --password-file PATH]
    dbwise credential rm <name> | credential list
    dbwise import <team file> [--prune]
    dbwise import [<team file>] --from-vault [--prune]
    dbwise export [--aliases a,b] [--output PATH]
    dbwise set-password <credential> [--password-env VAR | --password-file PATH]
    dbwise doctor [--alias A]

Every command except `set-password` without a source option and the password step of `import`
is non-interactive and never handles a secret. Each write keeps the previous config as
config.json.bak. The MCP server reads the config at start-up only, so a change takes effect after
the server or the session restarts.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import config
import engine_postgres
import levels
import passwords
import vault
from errors import RELOAD

RESTART = f"Reconnect {RELOAD}"


class CliError(RuntimeError):
    pass


def empty_config() -> dict:
    return {"dbwise": config.SCHEMA_VERSION, "credentials": {}, "servers": {}, "aliases": {}}


def load_local(create: bool = False) -> dict:
    path = config.config_path()
    try:
        data = config.load(path)
    except config.ConfigMissing:
        if create:
            return empty_config()
        raise CliError(f"no config at {path}; run `dbwise import <team file>` or add a credential, "
                       "a server and an alias") from None
    except config.ConfigError as e:
        bak = path.with_name(path.name + ".bak")
        hint = (f"the previous version is {bak}; copy it over {path.name} to restore it"
                if bak.exists() else f"there is no {bak.name} to restore from")
        raise CliError(f"cannot use the config, nothing was changed: {e}. Fix the file, or {hint}.") from None
    for section in ("credentials", "servers", "aliases"):
        data.setdefault(section, {})
    return data


def save_local(data: dict) -> None:
    try:
        path = config.save(data)
    except config.ConfigError as e:
        raise CliError(f"the change would make the config invalid, nothing was written: {e}") from None
    print(f"saved {path}")


def split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def source_text(credential: dict) -> str:
    source = credential.get("password") or {"keyring": {}}
    if "env" in source:
        return f"env:{source['env']}"
    if "file" in source:
        return f"file:{source['file']}"
    return "keyring"


def is_keyring(credential: dict) -> bool:
    return "keyring" in (credential.get("password") or {"keyring": {}})


def vault_supplies_password(credential: dict) -> bool:
    return "password" in credential.get("vault", {}).get("fields", {})


def credentials_text(alias: dict) -> str:
    level = levels.alias_level(alias)
    return ", ".join(f"{lvl}={levels.credential_for(alias, lvl)}" for lvl in levels.LEVELS[:levels.LEVELS.index(level) + 1])


# alias

def credential_value(options: list | None, level: str):
    """The alias's `credential` field from the repeated --credential options: one NAME, or LEVEL=NAME per level.

    A map must name every level from the lowest up to the alias's level and none above it, so a call
    never falls back to a credential of another level.
    """
    if not options:
        raise CliError("name a credential: `--credential NAME` for every level up to the alias's level, or "
                       "`--credential LEVEL=NAME` once per level")
    mapped = [option for option in options if "=" in option]
    if not mapped:
        if len(options) > 1:
            raise CliError("only one credential NAME serves all levels; to use a credential per level, write "
                           "`--credential LEVEL=NAME` for each")
        name = options[0].strip()
        if not name:
            raise CliError("empty credential name in `--credential`")
        return name
    if len(mapped) != len(options):
        raise CliError("use either one `--credential NAME` or `--credential LEVEL=NAME` options, not both")
    result = {}
    for option in mapped:
        lvl, _, name = (part.strip() for part in option.partition("="))
        if lvl not in levels.LEVELS:
            raise CliError(f"unknown level '{lvl}' in `--credential {option}` (allowed: {', '.join(levels.LEVELS)})")
        if not name:
            raise CliError(f"empty credential name in `--credential {option}`")
        if lvl in result:
            raise CliError(f"`--credential` names level '{lvl}' twice")
        result[lvl] = name
    allowed = levels.LEVELS[:levels.LEVELS.index(level) + 1]
    for lvl in result:
        if lvl not in allowed:
            raise CliError(f"the alias is at level '{level}', which has no credential for '{lvl}'")
    for lvl in allowed:
        if lvl not in result:
            raise CliError(f"`--credential` names no credential for level '{lvl}'; a map must name every level "
                           f"from '{levels.LEVELS[0]}' up to '{level}'")
    return {lvl: result[lvl] for lvl in allowed}


def alias_add(args) -> int:
    data = load_local(create=True)
    if args.server not in data["servers"]:
        raise CliError(f"unknown server '{args.server}'; add it with `dbwise server add` first")
    credential = credential_value(args.credential, args.level)
    for name in sorted(passwords.credentials_of({"credential": credential})):
        if name not in data["credentials"]:
            raise CliError(f"unknown credential '{name}'; add it with `dbwise credential add` first")
    replaced = args.alias in data["aliases"]
    data["aliases"][args.alias] = {"server": args.server, "level": args.level, "credential": credential}
    save_local(data)
    print(f"alias {args.alias} -> {args.server} at level {args.level} {'replaced' if replaced else 'added'}. {RESTART}")
    return 0


def alias_rm(args) -> int:
    data = load_local()
    if args.alias not in data["aliases"]:
        raise CliError(f"unknown alias '{args.alias}'")
    del data["aliases"][args.alias]
    save_local(data)
    print(f"alias {args.alias} removed. {RESTART}")
    return 0


def alias_list(args) -> int:
    data = load_local()
    if not data["aliases"]:
        print("no aliases")
    for name, alias in sorted(data["aliases"].items()):
        server = data["servers"][alias["server"]]
        engine = config.ENGINES[server["engine"]]
        host = server.get("host", "host from Vault")
        databases = ", ".join(server.get("databases", [])) or "from Vault"
        print(f"{name} -> {alias['server']} ({host}:{server.get('port', engine.DEFAULT_PORT)}; "
              f"databases {databases}; level {levels.alias_level(alias)}; credentials {credentials_text(alias)})")
    return 0


# server

def server_add(args) -> int:
    data = load_local(create=True)
    server = {"engine": args.engine, "host": args.host, "port": args.port, "sslmode": args.sslmode,
              "dbname": args.dbname, "databases": split_list(args.databases)}
    previous = data["servers"].get(args.name)
    data["servers"][args.name] = server
    save_local(data)
    dropped = [text for key, text in (("vault", "its Vault mapping"), ("role_check", "its role_check setting"))
               if previous and key in previous]
    note = f" ({' and '.join(dropped)} dropped)" if dropped else ""
    print(f"server {args.name} {'replaced' if previous else 'added'}{note}. {RESTART}")
    return 0


def server_rm(args) -> int:
    data = load_local()
    if args.name not in data["servers"]:
        raise CliError(f"unknown server '{args.name}'")
    users = sorted(a for a, alias in data["aliases"].items() if alias["server"] == args.name)
    if users:
        raise CliError(f"server {args.name} is used by alias {', '.join(users)}; remove the alias first "
                       f"(`dbwise alias rm {users[0]}`)")
    del data["servers"][args.name]
    save_local(data)
    print(f"server {args.name} removed. {RESTART}")
    return 0


def server_list(args) -> int:
    data = load_local()
    if not data["servers"]:
        print("no servers")
    for name, server in sorted(data["servers"].items()):
        engine = config.ENGINES[server["engine"]]
        host = server.get("host", "(from Vault)")
        databases = ",".join(server.get("databases", [])) or "(from Vault)"
        vault_note = " vault" if "vault" in server else ""
        print(f"{name} engine={server['engine']} host={host} port={server.get('port', engine.DEFAULT_PORT)} "
              f"sslmode={server.get('sslmode', engine.DEFAULT_SSLMODE)} dbname={server['dbname']} "
              f"databases={databases} role_check={config.role_check(server)}{vault_note}")
    return 0


# credential

def explicit_source(args) -> dict | None:
    if args.password_env:
        return {"env": args.password_env}
    if args.password_file:
        try:
            return {"file": str(passwords.check_password_file(args.password_file))}
        except passwords.PasswordError as e:
            raise CliError(str(e)) from None
    return None


def credential_add(args) -> int:
    data = load_local(create=True)
    credential = {"user": args.user}
    source = explicit_source(args)
    if source:
        credential["password"] = source
    previous = data["credentials"].get(args.name)
    data["credentials"][args.name] = credential
    save_local(data)
    note = " (its Vault mapping was dropped)" if previous and "vault" in previous else ""
    print(f"credential {args.name} {'replaced' if previous else 'added'}{note}, password source "
          f"{source_text(credential)}. {RESTART}")
    if not source:
        print(passwords.terminal_instructions([args.name]))
    return 0


def credential_rm(args) -> int:
    data = load_local()
    if args.name not in data["credentials"]:
        raise CliError(f"unknown credential '{args.name}'")
    users = sorted(a for a, alias in data["aliases"].items() if args.name in passwords.credentials_of(alias))
    if users:
        raise CliError(f"credential {args.name} is used by alias {', '.join(users)}; point the alias to "
                       "another credential or remove it first")
    del data["credentials"][args.name]
    save_local(data)
    print(f"credential {args.name} removed; its keychain entry (service {config.KEYRING_SERVICE}, "
          f"username {args.name}) is left in place. {RESTART}")
    return 0


def credential_list(args) -> int:
    data = load_local()
    if not data["credentials"]:
        print("no credentials")
    for name, credential in sorted(data["credentials"].items()):
        user = credential.get("user", "(from Vault)")
        vault_note = " vault" if "vault" in credential else ""
        print(f"{name} user={user} password={source_text(credential)}{vault_note}")
    return 0


# passwords

def set_password(args) -> int:
    data = load_local()
    credential = data["credentials"].get(args.credential)
    if credential is None:
        raise CliError(f"unknown credential '{args.credential}'")
    source = explicit_source(args)
    if source:
        credential["password"] = source
        save_local(data)
        print(f"credential {args.credential} now reads its password from {source_text(credential)}. {RESTART}")
        return 0
    try:
        backend = passwords.keychain()
        passwords.ask_and_store(backend, args.credential, data)
    except passwords.PasswordError as e:
        raise CliError(str(e)) from None
    print(f"password for {args.credential} stored in the OS keychain and read back.")
    if not is_keyring(credential):
        previous = source_text(credential)
        credential.pop("password")
        save_local(data)
        print(f"credential {args.credential} now uses the keychain instead of {previous}.")
    print(RESTART)
    return 0


def password_step(data: dict, stdin=None, reader=None, vault_done: bool = False) -> int:
    """Asks the password of every keyring credential that has none stored, when a terminal allows it;
    otherwise lists them with the command to run. Returns how many are still missing."""
    vault_names = sorted(n for n, c in data["credentials"].items() if is_keyring(c) and vault_supplies_password(c))
    names = sorted(n for n, c in data["credentials"].items() if is_keyring(c) and not vault_supplies_password(c))
    announce = vault_names if not vault_done else []
    if not names and not announce:
        return 0
    try:
        backend = passwords.keychain()
    except passwords.PasswordError as e:
        backend, refusal = None, e
    for name in announce:
        if backend is None or not passwords.has_password(backend, name):
            print(f"credential {name}: password mapped to Vault; it is filled by "
                  "`dbwise import <team file> --from-vault`")
    if not names:
        return 0
    if backend is None:
        print(f"PASSWORDS_MISSING: {', '.join(names)}\n{refusal}")
        return len(names)
    missing = [n for n in names if not passwords.has_password(backend, n)]
    if not missing:
        return 0
    stdin = stdin if stdin is not None else sys.stdin
    if not passwords.is_terminal(stdin):
        print(f"PASSWORDS_MISSING: {', '.join(missing)}")
        print("No terminal on stdin, so no password was asked. " + passwords.terminal_instructions(missing))
        return len(missing)
    for i, name in enumerate(missing):
        try:
            passwords.ask_and_store(backend, name, data, stdin, reader)
        except passwords.PasswordError as e:
            left = missing[i:]
            print(f"PASSWORDS_MISSING: {', '.join(left)}")
            print(str(e).split("\n")[0] + "\n" + passwords.terminal_instructions(left))
            return len(left)
        print(f"password for {name} stored in the OS keychain and read back.")
    return 0


# team files

SECTIONS = ("credentials", "servers", "aliases")
# Token states in which a login can help; for any other state of a refused read the token is not blamed.
TOKEN_NEEDS_LOGIN = ("missing", "expired")
# The config fields a Vault mapping may fill, per section; "password" goes to the keychain only.
VAULT_FIELDS = {"credentials": ("user", "password"), "servers": ("host", "port", "databases")}


def load_team(args) -> tuple[dict, dict, str]:
    """(team, local config, label of the team's source). Without a file, --from-vault refreshes the
    local config from its own Vault mappings, so the local config plays the team file."""
    if args.file is None:
        if not args.from_vault:
            raise CliError("name a team file, or pass --from-vault to refresh from the Vault mappings "
                           "in the local config")
        data = load_local()
        return copy.deepcopy(data), data, f"the local config {config.config_path()}"
    path = Path(args.file)
    try:
        team = config.load(path)
    except config.ConfigMissing:
        raise CliError(f"team file {path} not found") from None
    except config.ConfigError as e:
        raise CliError(f"team file refused, nothing was changed: {e}") from None
    return team, load_local(create=True), str(path)


def with_vault_values(incoming: dict, previous: dict | None) -> dict:
    """The incoming entry, keeping the local values of its Vault-mapped fields.

    Vault is the authority for a mapped field and its last value lives only in the local config, so
    replacing an entry from a team file must not wipe a host or a user filled by an earlier
    `--from-vault` import. The password source is the operator's own choice on this machine, so an
    entry without one keeps the local one.
    """
    entry = copy.deepcopy(incoming)
    if previous:
        if "password" not in entry and "password" in previous:
            entry["password"] = copy.deepcopy(previous["password"])
        for field in entry.get("vault", {}).get("fields", {}):
            if field != "password" and field in previous:
                entry[field] = copy.deepcopy(previous[field])
    return entry


def merge_sections(team: dict, data: dict, prune: bool) -> list[str]:
    counts = []
    for section in SECTIONS:
        incoming = team.get(section, {})
        removed = 0
        if prune:
            for name in [n for n in data[section] if n not in incoming]:
                del data[section][name]
                removed += 1
        for name, entry in incoming.items():
            if isinstance(entry, dict):
                entry = with_vault_values(entry, data[section].get(name))
            data[section][name] = entry
        counts.append(f"{len(incoming)} {section}" + (f" ({removed} pruned)" if removed else ""))
    if "vault" in team:
        data["vault"] = copy.deepcopy(team["vault"])
    elif prune:
        data.pop("vault", None)
    return counts


def import_team(args) -> int:
    team, data, label = load_team(args)
    if args.from_vault:
        return import_from_vault(args, team, data, label)
    counts = merge_sections(team, data, args.prune)
    save_local(data)
    print(f"imported {label}: {', '.join(counts)}.")
    password_step(data)
    print(RESTART)
    return 0


# Vault import

def ask_yes(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
VAULT_NAME = re.compile(r"[A-Za-z0-9._/-]+")
VAULT_LOGIN = re.compile(r"[A-Za-z0-9._-]+")


def unsafe_text(value: str) -> bool:
    """Quotes would end the quoted value in the login command shown to the operator."""
    return any(c in "'\"" or ord(c) < 32 or ord(c) == 127 for c in value)


def check_vault_name(value: str, where: str) -> None:
    trimmed = value.strip("/")
    if (not VAULT_NAME.fullmatch(value) or not trimmed or trimmed.startswith("-")
            or ".." in trimmed.split("/")):
        raise CliError(f"{where}: must use only letters, digits, '.', '_', '-' and '/', must not start with "
                       "'-' and must not hold a '..' segment; nothing was changed")


def check_vault_team(team: dict) -> None:
    """Refuses a `vault` section that would send the operator's token to an unsafe address or pass an
    odd string to the CLI. The address receives the token, so it is checked before any call."""
    section = team["vault"]
    addr = section["addr"].strip()
    if unsafe_text(addr):
        raise CliError("$.vault.addr: must not contain quotes or control characters; nothing was changed")
    parts = urllib.parse.urlsplit(addr)
    try:
        hostname = parts.hostname
    except ValueError:
        hostname = None
    if not hostname or parts.scheme not in ("https", "http"):
        raise CliError("$.vault.addr: must be an https:// address; nothing was changed")
    if parts.scheme == "http" and hostname.lower() not in LOOPBACK_HOSTS:
        raise CliError("$.vault.addr: must be an https:// address (http:// is accepted only for localhost, "
                       "127.0.0.1 and [::1]), because the address receives your Vault token; nothing was changed")
    namespace = section.get("namespace")
    if namespace and unsafe_text(namespace):
        raise CliError("$.vault.namespace: must not contain quotes or control characters; nothing was changed")
    login = section.get("login")
    if login and not VAULT_LOGIN.fullmatch(login):
        raise CliError("$.vault.login: must use only letters, digits, '.', '_' and '-'; nothing was changed")
    check_vault_name(section["mount"], "$.vault.mount")
    for kind in VAULT_FIELDS:
        for name, entry in team.get(kind, {}).items():
            if "vault" in entry:
                check_vault_name(entry["vault"]["path"], f"$.{kind}.{name}.vault.path")


def check_vault_fields(team: dict) -> None:
    for section, allowed in VAULT_FIELDS.items():
        for name, entry in team.get(section, {}).items():
            for field in entry.get("vault", {}).get("fields", {}):
                if field not in allowed:
                    raise CliError(f"$.{section}.{name}.vault.fields.{field}: Vault can fill only "
                                   f"{', '.join(allowed)} here; nothing was changed")


def vault_paths(team: dict) -> list[str]:
    paths = []
    for section in VAULT_FIELDS:
        for entry in team.get(section, {}).values():
            path = entry.get("vault", {}).get("path")
            if path and path not in paths:
                paths.append(path)
    return paths


def read_paths(settings, paths: list[str]) -> tuple[dict, dict]:
    """Each distinct path is read once. A missing CLI, or a refusal while the token is missing, expired
    or cannot be checked, stops the whole read; any other failure, a path the valid token may not read
    included, is recorded for its path and the rest go on."""
    secrets, failures = {}, {}
    for path in paths:
        try:
            secrets[path] = vault.read_secret(settings, path)
        except vault.VaultCliMissing:
            raise
        except vault.VaultAuthError as e:
            state = vault.check_token(settings)
            if state == "ok":
                failures[path] = (f"permission denied reading {settings.mount}/{path}: the token is valid "
                                  "but has no access to that path")
                continue
            e.token_state = state
            if state not in TOKEN_NEEDS_LOGIN:
                e.args = (f"{e}; the token check answered '{state}', so the state of the token is unknown",)
            raise
        except vault.VaultError as e:
            failures[path] = str(e)
    return secrets, failures


def vault_login(settings, error: Exception, stdin) -> None:
    """Runs the Vault login once, or raises CliError with the command for the operator."""
    manual = f"Log in, then rerun this import:\n{vault.login_command_text(settings)}"
    if os.environ.get("VAULT_TOKEN", "").strip():
        advice = ("renew it" if getattr(error, "token_state", None) in TOKEN_NEEDS_LOGIN
                  else "check that it is valid")
        raise CliError(f"{error}. VAULT_TOKEN is set and takes precedence over the token a login "
                       f"writes; unset it or {advice}. Nothing was changed. {manual}")
    terminal = passwords.is_terminal(stdin)
    # An OIDC login reads nothing from stdin (the sign-in happens in the browser), so the agent can
    # start it; a method that prompts for input needs a terminal the operator sees.
    if not terminal and settings.login != vault.DEFAULT_LOGIN:
        raise CliError(f"{error}. The {settings.login} login asks for input and there is no terminal "
                       f"on stdin; nothing was changed. {manual}")
    hint = "; finish the sign-in in the browser" if settings.login == vault.DEFAULT_LOGIN else ""
    print(f"{error}. Running `vault login -method={settings.login} -no-print` against {settings.addr}{hint}.")
    try:
        code = vault.run_login(settings, interactive=terminal)
    except vault.VaultError as e:
        raise CliError(f"{e} Nothing was changed.") from None
    if code != 0:
        raise CliError(f"vault login exited with code {code}; nothing was changed. {manual}")


def read_vault(settings, paths: list[str], stdin) -> tuple[dict, dict]:
    try:
        return read_paths(settings, paths)
    except vault.VaultAuthError as e:
        vault_login(settings, e, stdin)
    try:
        return read_paths(settings, paths)
    except vault.VaultAuthError as e:
        raise CliError(f"{e}, also after the login; nothing was changed. Check your access to these "
                       f"paths, or log in again:\n{vault.login_command_text(settings)}") from None


def parse_databases(value) -> list[str]:
    """A database list from Vault: a comma-separated string or a JSON list, blanks and repeats removed."""
    items = value.split(",") if isinstance(value, str) else value if isinstance(value, list) else []
    result = []
    for item in items:
        name = str(item).strip()
        if name and name not in result:
            result.append(name)
    return result


def parse_port(value) -> int | None:
    """A port from 1 to 65535 given as a number or a string of ASCII digits, else None."""
    raw = str(value or "").strip()
    if not (raw.isascii() and raw.isdecimal()):
        return None
    try:
        port = int(raw)
    except ValueError:  # more digits than int() converts
        return None
    return port if 1 <= port <= 65535 else None


def present(secret: dict, key: str) -> bool:
    value = secret.get(key)
    return value is not None and str(value).strip() != ""


def import_from_vault(args, team: dict, data: dict, label: str, stdin=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    if "vault" not in team:
        raise CliError(f"{label} has no `vault` section (addr, mount, optional namespace and login), so "
                       "there is no Vault to read; nothing was changed. Run the import without --from-vault.")
    check_vault_team(team)
    check_vault_fields(team)
    try:
        vault.require_cli()
    except vault.VaultCliMissing as e:
        raise CliError(f"nothing was read from Vault or changed. {e}") from None
    try:
        backend = passwords.keychain()
    except passwords.PasswordError as e:
        raise CliError(f"nothing was read from Vault or changed, because Vault passwords go to the keychain. {e}") from None
    settings = vault.Settings.from_config(team["vault"])
    paths = vault_paths(team)
    where = f"{settings.addr}" + (f" (namespace {settings.namespace})" if settings.namespace else "")
    print(f"reading {len(paths)} secret(s) from Vault {where}, mount {settings.mount}")
    secrets, failures = read_vault(settings, paths, stdin) if paths else ({}, {})

    previous = copy.deepcopy(data)
    counts = merge_sections(team, data, args.prune)
    problems = [f"{message}; entries mapped to it keep their local values" for message in failures.values()]

    new_passwords = {}
    for name, credential in team.get("credentials", {}).items():
        mapping = credential.get("vault")
        if not mapping or mapping["path"] not in secrets:
            continue
        secret, fields = secrets[mapping["path"]], mapping["fields"]
        missing = [key for key in fields.values() if not present(secret, key)]
        if missing:
            problems.append(f"credential {name}: {settings.mount}/{mapping['path']} has no value for "
                            f"{', '.join(missing)}; the credential keeps its local values")
            continue
        if "user" in fields:
            data["credentials"][name]["user"] = str(secret[fields["user"]]).strip()
        if "password" in fields:
            new_passwords[name] = str(secret[fields["password"]])

    pending = {}
    for name, server in team.get("servers", {}).items():
        mapping = server.get("vault")
        if not mapping or mapping["path"] not in secrets:
            continue
        secret, fields, target = secrets[mapping["path"]], mapping["fields"], data["servers"][name]
        source = f"{settings.mount}/{mapping['path']}"
        if "host" in fields:
            if not present(secret, fields["host"]):
                gone = sorted(a for a, alias in data["aliases"].items() if alias["server"] == name)
                for alias in gone:
                    del data["aliases"][alias]
                del data["servers"][name]
                print(f"server {name} and alias {', '.join(gone) or '(none)'} removed: {source} has no value "
                      f"for {fields['host']}")
                continue
            target["host"] = str(secret[fields["host"]]).strip()
        if "port" in fields:
            port = parse_port(secret.get(fields["port"]))
            if port is None:
                problems.append(f"server {name}: {source} has no port number from 1 to 65535 under "
                                f"{fields['port']}; the port stays {target.get('port', engine_postgres.DEFAULT_PORT)}")
            else:
                target["port"] = port
        if "databases" in fields:
            listed = parse_databases(secret.get(fields["databases"]))
            before = previous["servers"].get(name, {}).get("databases") or []
            if not listed:
                if before:
                    target["databases"] = list(before)
            elif not before:
                target["databases"] = listed
            else:
                removals = [d for d in before if d not in listed]
                if removals and not args.prune:
                    target["databases"] = before + [d for d in listed if d not in before]
                    pending[name] = removals
                else:
                    target["databases"] = listed

    if pending and passwords.is_terminal(stdin):
        for name, removals in list(pending.items()):
            if ask_yes(f"remove {len(removals)} database(s) no longer in Vault from server {name} "
                       f"({', '.join(removals)})? [y/N] "):
                data["servers"][name]["databases"] = [d for d in data["servers"][name]["databases"]
                                                      if d not in removals]
                print(f"server {name}: removed {', '.join(removals)}")
            else:
                print(f"server {name}: kept {', '.join(removals)}")
            del pending[name]

    save_local(data)

    stored = []
    for name, password in new_passwords.items():
        credential = data["credentials"][name]
        if not is_keyring(credential):
            print(f"credential {name} reads its password from {source_text(credential)}; the Vault "
                  "password was not stored")
            continue
        try:
            passwords.store(backend, name, password)
        except passwords.PasswordError as e:
            problems.append(str(e))
            continue
        except Exception as e:  # keyring backends raise their own types; the message may echo input
            problems.append(f"the keychain refused the password of {name} ({type(e).__name__})")
            continue
        stored.append(name)

    print(f"imported {label} with Vault values: {', '.join(counts)}.")
    if stored:
        print(f"passwords of {', '.join(stored)} stored in the OS keychain (service {config.KEYRING_SERVICE}) "
              "and read back.")
    team_servers = set(team.get("servers", {}))
    for alias_name, alias in sorted(data["aliases"].items()):
        if alias["server"] in team_servers:
            count = len(data["servers"][alias["server"]].get("databases", []))
            print(f"{alias_name} -> {alias['server']} ({count} databases)")
    if pending:
        print("REMOVALS_PENDING: " + "; ".join(f"{name}: {', '.join(dbs)}" for name, dbs in pending.items()))
        print("These databases are in the local config but no longer in Vault, and were kept. Rerun in a "
              "terminal to confirm each removal, or add --prune to apply them.")
    password_step(data, stdin, vault_done=True)
    for problem in problems:
        print(f"problem: {problem}")
    print(RESTART)
    return 1 if problems else 0


def exportable(entry: dict) -> dict:
    """The entry as a team file may carry it: the Vault mapping stays, the values it filled and the
    operator's own password source go, since each reader fills them for themselves."""
    result = copy.deepcopy(entry)
    result.pop("password", None)
    for field in result.get("vault", {}).get("fields", {}):
        result.pop(field, None)
    return result


def export_team(args) -> int:
    data = load_local()
    if args.aliases:
        wanted = split_list(args.aliases)
        unknown = [a for a in wanted if a not in data["aliases"]]
        if unknown:
            raise CliError(f"unknown alias {', '.join(unknown)}")
        aliases = {a: data["aliases"][a] for a in wanted}
        servers = {s: data["servers"][s] for s in sorted({alias["server"] for alias in aliases.values()})}
        used = set().union(*(passwords.credentials_of(alias) for alias in aliases.values())) if aliases else set()
        credentials = {c: data["credentials"][c] for c in sorted(used)}
    else:
        aliases, servers, credentials = data["aliases"], data["servers"], data["credentials"]
    servers = {name: exportable(server) for name, server in servers.items()}
    credentials = {name: exportable(credential) for name, credential in credentials.items()}
    team = {"dbwise": config.SCHEMA_VERSION}
    if "vault" in data:
        team["vault"] = data["vault"]
    team.update({"credentials": credentials, "servers": servers, "aliases": aliases})
    try:
        config.validate(team)
    except config.ConfigError as e:
        raise CliError(f"the exported part would not be a valid team file: {e}") from None
    text = json.dumps(team, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out}: {len(aliases)} aliases, {len(servers)} servers, {len(credentials)} credentials")
    else:
        sys.stdout.write(text)
    return 0


def doctor_cmd(args) -> int:
    import doctor

    return doctor.run(args.alias)


def add_source_options(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--password-env", metavar="VAR",
                       help="read the password from this environment variable of the Claude Code process")
    group.add_argument("--password-file", metavar="PATH",
                       help="read the password from this file (mode 600 on macOS and Linux; under the "
                            "user profile on Windows)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dbwise", description="Manage the dbwise local config and passwords.")
    sub = parser.add_subparsers(dest="command", required=True)

    alias = sub.add_parser("alias", help="the names a tool call uses to pick a server and a level").add_subparsers(
        dest="action", required=True)
    p = alias.add_parser("add", help="add or replace an alias")
    p.add_argument("alias")
    p.add_argument("server")
    p.add_argument("--level", choices=levels.LEVELS, default=levels.DEFAULT_LEVEL,
                   help="the highest level the alias allows (default %(default)s)")
    p.add_argument("--credential", action="append", metavar="NAME|LEVEL=NAME",
                   help="a credential for every level up to the alias's level, or LEVEL=NAME once per level "
                        "from read up to the alias's level")
    p.set_defaults(func=alias_add)
    p = alias.add_parser("rm", help="remove an alias")
    p.add_argument("alias")
    p.set_defaults(func=alias_rm)
    alias.add_parser("list", help="list aliases with their level and credential per level").set_defaults(
        func=alias_list)

    server = sub.add_parser("server", help="database endpoints").add_subparsers(dest="action", required=True)
    p = server.add_parser("add", help="add or replace a server")
    p.add_argument("name")
    p.add_argument("--engine", choices=tuple(config.ENGINES), default="postgresql")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=engine_postgres.DEFAULT_PORT)
    p.add_argument("--sslmode", choices=engine_postgres.SSLMODES, default=engine_postgres.DEFAULT_SSLMODE)
    p.add_argument("--dbname", default="postgres", help="discovery database (default postgres)")
    p.add_argument("--databases", required=True, help="comma-separated databases the agent may use")
    p.set_defaults(func=server_add)
    p = server.add_parser("rm", help="remove a server no alias uses")
    p.add_argument("name")
    p.set_defaults(func=server_rm)
    server.add_parser("list", help="list servers").set_defaults(func=server_list)

    credential = sub.add_parser("credential", help="database logins").add_subparsers(dest="action", required=True)
    p = credential.add_parser("add", help="add or replace a credential (keychain password by default)")
    p.add_argument("name")
    p.add_argument("--user", required=True)
    add_source_options(p)
    p.set_defaults(func=credential_add)
    p = credential.add_parser("rm", help="remove a credential no alias uses")
    p.add_argument("name")
    p.set_defaults(func=credential_rm)
    credential.add_parser("list", help="list credentials and their password sources").set_defaults(
        func=credential_list)

    p = sub.add_parser("import", help="merge a team file into the local config, then ask missing passwords")
    p.add_argument("file", nargs="?", help="team file; with --from-vault and no file, the local config's "
                                           "own Vault mappings are refreshed")
    p.add_argument("--from-vault", action="store_true",
                   help="fill Vault-mapped users, passwords, hosts, ports and databases from the team's Vault")
    p.add_argument("--prune", action="store_true",
                   help="remove local entries the team file does not have, and databases Vault no longer lists")
    p.set_defaults(func=import_team)

    p = sub.add_parser("export", help="write the local config, or part of it, as a team file")
    p.add_argument("--aliases", help="comma-separated aliases to export with their servers and credentials")
    p.add_argument("--output", "-o", help="file to write (default: stdout)")
    p.set_defaults(func=export_team)

    p = sub.add_parser("set-password", help="type a credential's password into the OS keychain, or point "
                                            "the credential to an env or file source")
    p.add_argument("credential")
    add_source_options(p)
    p.set_defaults(func=set_password)

    p = sub.add_parser("doctor", help="check the keychain, the venv and every alias, and name the fix for each problem")
    p.add_argument("--alias", help="check only this alias")
    p.set_defaults(func=doctor_cmd)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as e:
        print(f"dbwise: {e}", file=sys.stderr)
        return 1
    except vault.VaultError as e:
        print(f"dbwise: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
