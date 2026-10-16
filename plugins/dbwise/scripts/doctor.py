"""dbwise doctor: why an alias does not work, and the command that fixes it.

    dbwise doctor [--alias A]

The MCP server records an alias it cannot open as unreachable and says why only through its own tools.
The doctor checks, in order:

  1. the OS keychain backend, the server package and the plugin's virtual environment;
  2. per alias: the host, then per credential level the user and the password source (keychain entry,
     environment variable, or a file whose permissions dbwise accepts);
  3. TCP reachability of host:port, then `SELECT 1` on the server's discovery database with each
     credential, an authentication failure (SQLSTATE 28P01 / 28000) told apart from a network one;
  4. each exposed database: OK, NOT FOUND or NO ACCESS;
  5. for an alias at level read or diagnose, per credential and database: the verdict of the role gate
     the server runs when it opens a pool (every reason, as warnings under `role_check: warn`), then
     audit findings that refuse nothing: owned objects, CREATE privileges, write grants, EXECUTE held by
     PUBLIC on functions a read-only transaction does not stop, and for diagnose the monitoring roles
     and extensions. An alias at level write has no role check.

It ends with one `VERDICT:` line, `ready` or the list of problems, and exits 0 only when ready. It prints
no password and no DSN; a driver message goes through `mask` first. Vault is contacted only to report
the token state after an authentication failure of a Vault-mapped credential.

psycopg and the server package are imported inside the functions, so this module imports with the
standard library alone.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import bootstrap
import config
import engine_postgres
import launch
import levels
import passwords
import vault
from errors import RELOAD

CONNECT_TIMEOUT = 5
STATEMENT_TIMEOUT = "30s"
AUTH_SQLSTATES = ("28P01", "28000")
MONITORING_ROLES = ("pg_monitor", "pg_read_all_stats")
INDENT = "  "


@dataclass
class Inspection:
    """What one database says about the role connected to it: the gate row, how long the gate took,
    and the rows of the audit queries. `errors` names an audit query that failed, with the error type."""
    gate: dict
    gate_ms: float
    owned: list = field(default_factory=list)
    create: dict = field(default_factory=lambda: {"database_create": False, "schemas": []})
    grants: list = field(default_factory=list)
    public_execute: list = field(default_factory=list)
    extensions: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)


def mask(text: str | None, password: str | None = None) -> str:
    """The text with any password removed: the userinfo of every URL, then the literal value in both
    forms, since a driver message is not guaranteed to hold the password URL-encoded."""
    if not text:
        return text or ""
    text = re.sub(r"(://[^/:@\s]*:)[^@/\s]*@", r"\1****@", text)
    if password:
        text = text.replace(password, "****").replace(quote(password, safe=""), "****")
    return text


def first_line(text: str, limit: int = 300) -> str:
    return (text.splitlines() or [""])[0][:limit]


def reachable(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def run_select1(dsn: str) -> tuple[bool, str | None, str]:
    """(True, None, "") on success, (False, sqlstate, message) on a psycopg.OperationalError.

    Any other exception propagates, so each caller maps it to its own catch-all bucket.
    """
    import psycopg

    try:
        with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True, None, ""
    except psycopg.OperationalError as e:
        return False, getattr(e, "sqlstate", None), str(e).strip()


def try_connect(dsn: str) -> tuple[bool, str, str]:
    """(ok, kind, detail) with kind one of ok, auth, network, other."""
    try:
        ok, sqlstate, message = run_select1(dsn)
    except Exception as e:  # noqa: BLE001 - any driver failure is reported, never raised
        return False, "other", str(e).strip()
    if ok:
        return True, "ok", ""
    if sqlstate in AUTH_SQLSTATES or "password authentication failed" in message.lower():
        return False, "auth", message
    return False, "network", message


def probe_database(dsn: str) -> tuple[str, str]:
    """(status, detail): OK, NOT FOUND, NO ACCESS, NETWORK or ERROR.

    The server skips a database that does not exist at start-up and fails a database without a
    CONNECT grant only on the first query, so both are checked here one database at a time.
    """
    try:
        ok, sqlstate, message = run_select1(dsn)
    except Exception as e:  # noqa: BLE001
        return "ERROR", str(e).strip()
    if ok:
        return "OK", ""
    low = message.lower()
    if sqlstate == "3D000" or "does not exist" in low:
        return "NOT FOUND", message
    if (sqlstate in ("42501",) + AUTH_SQLSTATES or "permission denied" in low
            or "authentication failed" in low):
        return "NO ACCESS", message
    return "NETWORK", message


def inspect_database(dsn: str) -> Inspection:
    """Runs the role gate and the audit queries as the credential in `dsn`, in read-only transactions.

    A failure of the gate query propagates. A failing audit query is recorded and the others still run.
    """
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT, row_factory=dict_row) as conn:
        conn.read_only = True

        def fetch(sql: str) -> tuple[list, float]:
            with conn.cursor() as cur:
                try:
                    cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    started = time.perf_counter()
                    cur.execute(sql)
                    rows = cur.fetchall()
                    return rows, (time.perf_counter() - started) * 1000
                finally:
                    conn.rollback()

        rows, gate_ms = fetch(engine_postgres.ROLE_GATE_SQL)
        if not rows:
            raise RuntimeError("the role gate query returned no row")
        found = Inspection(gate=dict(rows[0]), gate_ms=gate_ms)
        audits = (
            ("owned objects", engine_postgres.AUDIT_OWNED_SQL, "owned", lambda r: [dict(x) for x in r]),
            ("CREATE privileges", engine_postgres.AUDIT_CREATE_SQL, "create", lambda r: dict(r[0]) if r else None),
            ("write grants", engine_postgres.AUDIT_WRITE_GRANTS_SQL, "grants", lambda r: [dict(x) for x in r]),
            ("PUBLIC EXECUTE", engine_postgres.AUDIT_PUBLIC_EXECUTE_SQL, "public_execute",
             lambda r: [x["function"] for x in r]),
            ("extensions", engine_postgres.AUDIT_EXTENSIONS_SQL, "extensions", lambda r: [x["extname"] for x in r]),
        )
        for label, sql, attribute, convert in audits:
            try:
                value = convert(fetch(sql)[0])
            except Exception as e:  # noqa: BLE001 - one audit query must not hide the others
                found.errors[label] = type(e).__name__
                continue
            if value is not None:
                setattr(found, attribute, value)
        return found


# environment

def keychain_state() -> tuple[object | None, str]:
    try:
        backend = passwords.keychain()
    except passwords.PasswordError as e:
        return None, f"keychain: NONE. {e}"
    return backend, (f"keychain: {type(backend).__module__}.{type(backend).__name__} "
                     f"(priority {getattr(backend, 'priority', '?')})")


def server_package_problem() -> str | None:
    try:
        import psycopg  # noqa: F401

        directory = str(launch.PLUGIN_ROOT / "servers" / launch.SERVERS["postgres"]["dir"])
        if directory not in sys.path:
            sys.path.insert(0, directory)
        import postgres_mcp.server  # noqa: F401
    except Exception as e:  # noqa: BLE001 - a broken venv raises more than ImportError
        return f"{type(e).__name__}: {first_line(str(e))}"
    return None


def venv_state(paths: bootstrap.Paths) -> str:
    if not paths.python.exists():
        return "not built yet; the first MCP start builds it in the background (about a minute)"
    if not bootstrap.base_alive(paths.pyvenv_cfg):
        return ("its base Python is gone (Python was updated or removed); the next MCP start rebuilds it in "
                f"the background, then reconnect {RELOAD}")
    if bootstrap.is_current(paths):
        return "current"
    return f"still being built, or its last build failed; see {paths.log}"


def environment_lines() -> tuple[list[str], list[str], object | None]:
    lines, problems = [], []
    backend, line = keychain_state()
    lines.append(line)
    in_venv = sys.prefix != sys.base_prefix
    lines.append(f"python: {sys.executable} ({sys.version.split()[0]}{', venv' if in_venv else ''})")
    problem = server_package_problem()
    if problem:
        lines.append(f"{INDENT}server package: IMPORT FAILED ({problem}); delete {sys.prefix} and rerun dbwise")
        problems.append(f"the venv cannot import the server package ({problem})")
    else:
        lines.append(f"{INDENT}server package: imports")
    try:
        paths = bootstrap.resolve_paths()
        lines.append(f"MCP venv {paths.venv}: {venv_state(paths)}")
    except OSError as e:
        lines.append(f"MCP venv: cannot read {bootstrap.PLUGIN_ROOT / 'requirements.txt'} ({type(e).__name__})")
    return lines, problems, backend


def load_config() -> tuple[dict | None, str | None]:
    path = config.config_path()
    try:
        return config.load(path), None
    except config.ConfigMissing:
        return None, f"no config at {path}; run /dbwise:config or `dbwise import <team file>`"
    except config.ConfigError as e:
        bak = path.with_name(path.name + ".bak")
        restore = f"copy {bak} over it" if bak.exists() else f"there is no {bak.name}"
        return None, f"config unusable: {e}; fix it or {restore}"


# credentials

def source_label(credential: dict) -> str:
    source = credential.get("password") or {"keyring": {}}
    if "env" in source:
        return f"env {source['env']}"
    if "file" in source:
        return f"file {source['file']}"
    return "keychain"


def vault_supplies(credential: dict, field_name: str) -> bool:
    return field_name in credential.get("vault", {}).get("fields", {})


def password_for(name: str, credential: dict, backend) -> tuple[str | None, str | None, str | None]:
    """(password, None, None) or (None, short problem, message with the fix); never prints the password."""
    source = credential.get("password") or {"keyring": {}}
    if "keyring" in source:
        if backend is None:
            return None, "NO KEYCHAIN", "NO KEYCHAIN: see the keychain line above"
        try:
            value = backend.get_password(config.KEYRING_SERVICE, name)
        except Exception as e:  # noqa: BLE001 - keyring backends raise their own types
            return None, "KEYCHAIN ERROR", f"KEYCHAIN ERROR reading {name} ({type(e).__name__})"
        if not value:
            fix = ("`dbwise import --from-vault`" if vault_supplies(credential, "password")
                   else f"`dbwise set-password {name}` in a terminal")
            return None, "NO PASSWORD", f"NO PASSWORD in the keychain for credential {name}: run {fix}"
        return value, None, None
    if "env" in source:
        value = os.environ.get(source["env"])
        if not value:
            return None, "ENV NOT SET", (f"ENV NOT SET: {source['env']} is not set or empty in this process; set it "
                                         f"where Claude Code starts, or run `dbwise set-password {name}` in a "
                                         "terminal to switch to the keychain")
        return value, None, None
    try:
        path = passwords.check_password_file(source["file"])
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except passwords.PasswordError as e:
        return None, "PASSWORD FILE REFUSED", f"PASSWORD FILE REFUSED: {e}"
    except OSError as e:
        return None, "PASSWORD FILE UNREADABLE", f"PASSWORD FILE UNREADABLE: {source['file']} ({type(e).__name__})"
    if not value:
        return None, "PASSWORD FILE EMPTY", f"PASSWORD FILE EMPTY: {source['file']}"
    return value, None, None


def vault_hint(data: dict) -> list[str]:
    section = data.get("vault")
    if not section:
        return [f"{INDENT * 2}this credential is mapped to Vault, but the config has no vault section"]
    settings = vault.Settings.from_config(section)
    state = vault.check_token(settings)
    lines = [f"{INDENT * 2}Vault token at {settings.addr}: {state}",
             f"{INDENT * 2}fix: refresh the password from Vault: `dbwise import --from-vault`"]
    if state in ("missing", "expired"):
        lines.append(f"{INDENT * 2}the import starts the login itself; to log in by hand first:")
        lines += [f"{INDENT * 2}{line.strip()}" for line in vault.login_command_text(settings).splitlines()]
    return lines


# the role check

def role_check_lines(found: Inspection, level: str, mode: str, database: str, served: list,
                     server: str = "", reported: set | None = None) -> tuple[list[str], bool]:
    """(lines, refused): the gate verdict and the audit findings for one credential in one database.

    The PUBLIC EXECUTE finding is a property of the server, so it prints once per server and function
    list; `reported` carries what earlier calls printed."""
    pad = INDENT * 4
    lines = []
    reasons = engine_postgres.gate_reasons(found.gate, level)
    refused = False
    if not reasons:
        lines.append(f"{pad}role check ({level}, {mode}): passed in {found.gate_ms:.0f} ms")
    elif mode == "warn":
        lines.append(f"{pad}role check ({level}, warn): WARNING, the alias still opens: {'; '.join(reasons)}")
    else:
        refused = True
        lines.append(f"{pad}role check ({level}, {mode}): REFUSED: {'; '.join(reasons)}")
    lines += [f"{pad}audit: the {label} query could not run ({kind})" for label, kind in found.errors.items()]
    if found.owned:
        owned = ", ".join(f"{row['total']} {row['kind']}(s) ({', '.join(row['examples'])})" for row in found.owned)
        lines.append(f"{pad}audit: the role owns {owned}")
    create = found.create
    if create["schemas"] or create["database_create"]:
        where = []
        if create["schemas"]:
            where.append(f"schema(s) {', '.join(create['schemas'])}")
        if create["database_create"]:
            where.append("the database")
        lines.append(f"{pad}audit: the role holds CREATE on {' and on '.join(where)}")
    if found.grants:
        grants = ", ".join(f"{row['relation']} ({', '.join(row['privileges'])})"
                           for row in found.grants[:engine_postgres.WRITE_GRANTS_SHOWN])
        if len(found.grants) > engine_postgres.WRITE_GRANTS_SHOWN:
            grants += ", ..."
        lines.append(f"{pad}audit: write grants reach the role on {grants}")
    key = (server, tuple(found.public_execute))
    if found.public_execute and (reported is None or key not in reported):
        if reported is not None:
            reported.add(key)
        lines.append(f"{pad}audit: PUBLIC holds EXECUTE on {', '.join(found.public_execute)}")
        qualified = ", ".join(f"pg_catalog.{function}" for function in found.public_execute)
        lines.append(f"{pad}advice: a superuser can run REVOKE EXECUTE ON FUNCTION {qualified} FROM PUBLIC;")
    if level == "diagnose" and "diagnose" in served:
        if not set(MONITORING_ROLES) & set(found.gate["monitor_roles"]):
            lines.append(f"{pad}audit (diagnose): the credential is a member of neither pg_monitor nor "
                         "pg_read_all_stats")
        notes = {"pg_stat_statements": "get_top_queries reads it",
                 "hypopg": "the hypothetical index tests of the diagnose tools use it"}
        for extension in engine_postgres.AUDIT_EXTENSIONS:
            if extension not in found.extensions:
                lines.append(f"{pad}note: extension {extension} is not installed in {database}; {notes[extension]}")
    return lines, refused


def check_role(dsn: str, database: str, name: str, label: str, level: str, mode: str, served: list,
               server: str, reported: set, prefix: str, problems: list[str]) -> None:
    """Run one credential's role check in one database and print it; a failure to run is a problem."""
    try:
        found = inspect_database(dsn)
        lines, refused = role_check_lines(found, level, mode, database, served, server, reported)
    except Exception as e:  # noqa: BLE001 - the error type only: a driver message can quote the DSN
        problems.append(f"{name}: {label} role check FAILED in {database}")
        print(f"{prefix}{INDENT * 2}role check ({level}, {mode}): FAILED to run ({type(e).__name__})")
        return
    if refused:
        problems.append(f"{name}: {label} role check REFUSED in {database}")
    print("\n".join(lines))


# the report

def credential_groups(alias: dict, entry: dict) -> list[tuple[str, list[str]]]:
    """(credential name, the levels it serves) in level order; one credential for several levels is one group."""
    groups: dict[str, list[str]] = {}
    for level in entry["connections"]:
        groups.setdefault(levels.credential_for(alias, level), []).append(level)
    return list(groups.items())


def check_alias(name: str, data: dict, entry: dict, backend, outcomes: dict, reach_cache: dict,
                reported: set | None = None) -> list[str]:
    """Prints the alias's report and returns its problems. `outcomes` collects, per credential name,
    which aliases it connected for and which refused it."""
    alias = data["aliases"][name]
    level, mode = entry["level"], entry["role_check"]
    problems = []
    out = print
    out(f"\n[{name}] -> {entry['server']}, level {level}, role_check {mode}")
    server = data["servers"][entry["server"]]
    base = entry["connections"]["read"]
    host, port = base.get("host"), base.get("port", engine_postgres.DEFAULT_PORT)
    out(f"{INDENT}server {entry['server']}: {host or '(no host)'}:{port}, sslmode {base.get('sslmode')}, "
        f"discovery database {entry['dbname']}")
    if not host:
        fix = "`dbwise import --from-vault`" if "host" in server.get("vault", {}).get("fields", {}) \
            else f"`dbwise server add {entry['server']} --host H ...`"
        problems.append(f"{name}: NO HOST")
        out(f"{INDENT}NO HOST: the host is not in the config; run {fix}")
        return problems
    if (host, port) not in reach_cache:
        reach_cache[(host, port)] = reachable(host, port)
    if not reach_cache[(host, port)]:
        problems.append(f"{name}: NETWORK")
        out(f"{INDENT}NETWORK: cannot reach {host}:{port} within {CONNECT_TIMEOUT} s; check the VPN or the tunnel, "
            "and the host and port (`dbwise server list`). Not a password problem.")
        return problems
    out(f"{INDENT}network: {host}:{port} reachable")
    if level == "write":
        out(f"{INDENT}no role check for a write alias")
    for credential_name, served in credential_groups(alias, entry):
        label = ", ".join(served)
        spec = entry["connections"][served[0]]
        credential = data["credentials"][credential_name]
        user = spec.get("user")
        out(f"{INDENT}{label}: credential {credential_name} (user {user or '(none)'}, "
            f"password {source_label(credential)})")
        prefix = INDENT * 2
        if not user:
            fix = "`dbwise import --from-vault`" if vault_supplies(credential, "user") \
                else f"`dbwise credential add {credential_name} --user U`"
            problems.append(f"{name}: {label} NO USER")
            out(f"{prefix}NO USER: the user is not in the config; run {fix}")
            continue
        password, key, message = password_for(credential_name, credential, backend)
        if key:
            problems.append(f"{name}: {label} {key}")
            out(f"{prefix}{message}")
            continue
        ok, kind, detail = try_connect(launch.build_dsn(spec, password))
        detail = first_line(mask(detail, password))
        if ok:
            outcomes.setdefault(credential_name, {})[name] = True
            out(f"{prefix}SELECT 1 on {entry['dbname']}: OK")
        elif kind == "auth":
            outcomes.setdefault(credential_name, {})[name] = False
            problems.append(f"{name}: {label} AUTH FAILED")
            out(f"{prefix}AUTH FAILED: the server refused user {user} ({detail})")
            if vault_supplies(credential, "password"):
                out("\n".join(vault_hint(data)))
            else:
                out(f"{prefix}fix: the password changed or is wrong; run `dbwise set-password {credential_name}` "
                    "in a terminal")
            continue
        else:
            problems.append(f"{name}: {label} {'CONNECT FAILED' if kind == 'network' else 'ERROR'}")
            out(f"{prefix}{'CONNECT FAILED' if kind == 'network' else 'ERROR'} on {entry['dbname']}: {detail}")
            continue
        # the server opens the discovery database with the read credential only
        if level != "write" and "read" in served and entry["dbname"] not in entry["databases"]:
            out(f"{prefix}discovery database {entry['dbname']}:")
            discovery_dsn = launch.build_dsn({**spec, "dbname": entry["dbname"]}, password)
            check_role(discovery_dsn, entry["dbname"], name, label, level, mode, served, entry["server"], reported,
                       prefix, problems)
        out(f"{prefix}databases ({len(entry['databases'])}):")
        statuses = set()
        for database in entry["databases"]:
            dsn = launch.build_dsn({**spec, "dbname": database}, password)
            status, database_detail = probe_database(dsn)
            statuses.add(status)
            line = f"{prefix}{INDENT}- {database}: {status}"
            if status != "OK":
                line += f" ({first_line(mask(database_detail, password))})"
                problems.append(f"{name}: {label} {database} {status}")
            out(line)
            if status != "OK" or level == "write":
                continue
            check_role(dsn, database, name, label, level, mode, served, entry["server"], reported, prefix, problems)
        if "NOT FOUND" in statuses:
            out(f"{prefix}fix NOT FOUND: correct the list with `dbwise server add {entry['server']} ...` or the "
                "team file")
        if "NO ACCESS" in statuses:
            out(f"{prefix}fix NO ACCESS: the database owner grants CONNECT to {user}, or drop the database "
                "from the list")
    return problems


def split_hints(data: dict, outcomes: dict) -> list[str]:
    """A credential that one alias's server accepts and another's refuses is probably two logins with
    different passwords filed under one name."""
    hints = []
    for credential_name, by_alias in sorted(outcomes.items()):
        failed = sorted(a for a, accepted in by_alias.items() if not accepted)
        passed = sorted(a for a, accepted in by_alias.items() if accepted)
        if not (failed and passed):
            continue
        new = f"{credential_name}-{failed[0]}"
        alias = data["aliases"][failed[0]]
        user = data["credentials"][credential_name].get("user", "<user>")
        level = levels.alias_level(alias)
        if isinstance(alias["credential"], str):
            option = f"--credential {new}"
        else:
            option = " ".join(f"--credential {lvl}={new if name == credential_name else name}"
                              for lvl, name in alias["credential"].items())
        hints.append(
            f"credential {credential_name} is used by aliases {', '.join(sorted(by_alias))}: {', '.join(passed)} "
            f"accepted its password and {', '.join(failed)} refused it. The same user can have a different "
            f"password on each server; give {failed[0]} its own credential: `dbwise credential add {new} "
            f"--user {user}`, point the alias to it (`dbwise alias add {failed[0]} {alias['server']} --level "
            f"{level} {option}`), then `dbwise set-password {new}` in a terminal.")
    return hints


def verdict(problems: list[str]) -> int:
    print()
    if problems:
        print("VERDICT: not ready: " + "; ".join(problems))
        return 1
    print("VERDICT: ready")
    return 0


def run(alias: str | None = None) -> int:
    print("dbwise doctor")
    print(f"config: {config.config_path()}")
    environment, problems, backend = environment_lines()
    for line in environment:
        print(line)
    data, config_problem = load_config()
    if config_problem:
        print(config_problem)
        return verdict(problems + [config_problem])
    entries = config.entries(data)
    if alias:
        if alias not in entries:
            print(f"unknown alias {alias}; known: {', '.join(sorted(entries)) or '(none)'}")
            return verdict(problems + [f"unknown alias {alias}"])
        entries = {alias: entries[alias]}
    if not entries:
        print("no aliases in the config; run /dbwise:config")
        return verdict(problems + ["no aliases; run /dbwise:config"])
    outcomes: dict = {}
    reach_cache: dict = {}
    reported: set = set()
    for name in sorted(entries):
        problems += check_alias(name, data, entries[name], backend, outcomes, reach_cache, reported)
    hints = split_hints(data, outcomes)
    if hints:
        print()
        for hint in hints:
            print(f"hint: {hint}")
    return verdict(problems)
