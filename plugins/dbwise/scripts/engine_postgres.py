"""PostgreSQL parts of the configuration: field validation, defaults and the connection spec.

Standard library only. The shared modules call this one by the engine name of a server.
"""
from __future__ import annotations

from errors import FieldError

DEFAULT_PORT = 5432
DEFAULT_SSLMODE = "require"
SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")

# Keys the engine owns; `engine` and `role_check` belong to every engine and are checked by config.
SERVER_KEYS = ("host", "port", "sslmode", "dbname", "databases", "vault")


def _string(value, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FieldError(path, "must be a non-empty string")
    return value


def validate_server(server: dict, path: str, mapped: set) -> None:
    """Checks the PostgreSQL fields of a server entry.

    `mapped` holds the fields a Vault mapping supplies at import time; they may be absent until then.
    """
    unknown = sorted(set(server) - set(SERVER_KEYS) - {"engine", "role_check"})
    if unknown:
        raise FieldError(f"{path}.{unknown[0]}", f"unknown key (allowed: {', '.join(sorted(SERVER_KEYS))})")
    if "host" in server or "host" not in mapped:
        _string(server.get("host"), f"{path}.host")
    if "port" in server:
        port = server["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise FieldError(f"{path}.port", "must be an integer from 1 to 65535")
    if "sslmode" in server and server["sslmode"] not in SSLMODES:
        raise FieldError(f"{path}.sslmode", f"must be one of {', '.join(SSLMODES)}")
    _string(server.get("dbname"), f"{path}.dbname")
    if "databases" in server or "databases" not in mapped:
        databases = server.get("databases")
        if not isinstance(databases, list) or not databases:
            raise FieldError(f"{path}.databases", "must be a non-empty list of database names")
        for i, name in enumerate(databases):
            _string(name, f"{path}.databases[{i}]")
        if len(set(databases)) != len(databases):
            raise FieldError(f"{path}.databases", "lists a database twice")


def connection_spec(server: dict, user: str | None, password: dict) -> dict:
    """One connection spec for a server and a credential.

    Fields still unknown (a Vault-mapped host or user before import) are left out; the server then
    records the alias as unreachable instead of failing at start-up.
    """
    spec = {
        "host": server.get("host"),
        "port": server.get("port", DEFAULT_PORT),
        "user": user,
        "dbname": server["dbname"],
        "sslmode": server.get("sslmode", DEFAULT_SSLMODE),
        "password": {k: (dict(v) if isinstance(v, dict) else v) for k, v in password.items()},
    }
    return {k: v for k, v in spec.items() if v is not None}


# Predefined roles that let a member write, reach the server's files or processes, or signal other sessions.
GATE_PREDEFINED_ROLES = (
    "pg_write_all_data", "pg_signal_backend", "pg_maintain", "pg_read_server_files", "pg_write_server_files",
    "pg_execute_server_program", "pg_checkpoint", "pg_create_subscription",
)
# Predefined roles that show other sessions' activity; they separate the diagnose level from read.
GATE_MONITOR_ROLES = ("pg_monitor", "pg_read_all_stats", "pg_stat_scan_tables")


def _names(names: tuple) -> str:
    return ", ".join(f"'{name}'" for name in names)


# Schemas whose relations carry grants the gate ignores: PUBLIC holds UPDATE on pg_catalog.pg_settings.
GATE_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

# One row, run as the pool's role in the database of the pool, a snapshot of the grants at that moment.
# A role name the server's version lacks matches no pg_roles row, so it is skipped. Ownership is
# tested with membership, so a member of an owning role (or of pg_database_owner, which owns schema
# public from PostgreSQL 15) is caught. The inner CASE keeps the MAINTAIN privilege, which PostgreSQL
# 16 and older reject, from being evaluated there: CASE evaluates lazily, and the privilege functions
# are STABLE, so the planner does not fold them ahead of the version test. EXISTS stops at the first hit.
ROLE_GATE_SQL = f"""SELECT
  r.rolsuper AS superuser,
  r.rolbypassrls AS bypassrls,
  r.rolcreaterole AS createrole,
  r.rolcreatedb AS createdb,
  r.rolreplication AS replication,
  ARRAY(SELECT p.rolname FROM pg_roles p
        WHERE p.rolname IN ({_names(GATE_PREDEFINED_ROLES)}) AND pg_has_role(r.oid, p.oid, 'MEMBER')
        ORDER BY p.rolname) AS predefined_roles,
  ARRAY(SELECT p.rolname FROM pg_roles p
        WHERE p.rolname IN ({_names(GATE_MONITOR_ROLES)}) AND pg_has_role(r.oid, p.oid, 'MEMBER')
        ORDER BY p.rolname) AS monitor_roles,
  ARRAY(SELECT p.rolname FROM pg_roles p
        WHERE p.oid <> r.oid AND left(p.rolname, 3) <> 'pg_' AND pg_has_role(r.oid, p.oid, 'MEMBER')
        ORDER BY p.rolname) AS other_roles,
  EXISTS (SELECT 1 FROM pg_database d
          WHERE d.datname = current_database() AND pg_has_role(r.oid, d.datdba, 'MEMBER')) AS owns_database,
  EXISTS (SELECT 1 FROM pg_class c
          WHERE c.relpersistence <> 't' AND pg_has_role(r.oid, c.relowner, 'MEMBER')) AS owns_relations,
  EXISTS (SELECT 1 FROM pg_proc f WHERE pg_has_role(r.oid, f.proowner, 'MEMBER')) AS owns_functions,
  EXISTS (SELECT 1 FROM pg_namespace n WHERE pg_has_role(r.oid, n.nspowner, 'MEMBER')) AS owns_schemas,
  ARRAY(SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND c.relpersistence <> 't'
          AND n.nspname NOT IN ({_names(GATE_SYSTEM_SCHEMAS)})
          AND CASE WHEN c.relkind = 'S'
                   THEN has_sequence_privilege(c.oid, 'USAGE') OR has_sequence_privilege(c.oid, 'UPDATE')
                   ELSE has_table_privilege(c.oid, 'INSERT') OR has_table_privilege(c.oid, 'UPDATE')
                        OR has_table_privilege(c.oid, 'DELETE') OR has_table_privilege(c.oid, 'TRUNCATE')
                        OR has_any_column_privilege(c.oid, 'INSERT') OR has_any_column_privilege(c.oid, 'UPDATE')
                        OR CASE WHEN current_setting('server_version_num')::int >= 170000
                                THEN has_table_privilege(c.oid, 'MAINTAIN') ELSE false END
              END
        ORDER BY n.nspname, c.relname
        LIMIT 3) AS write_grants,
  EXISTS (SELECT 1 FROM pg_depend d
          JOIN pg_extension e ON e.oid = d.refobjid AND d.refclassid = 'pg_extension'::regclass
          WHERE e.extname = 'dblink' AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
            AND has_function_privilege(d.objid, 'EXECUTE')) AS dblink_execute,
  EXISTS (SELECT 1 FROM pg_foreign_server s WHERE has_server_privilege(s.oid, 'USAGE')) AS foreign_server_usage
FROM pg_roles r
WHERE r.rolname = current_user"""

_GATE_FLAGS = (
    ("superuser", "the role is a superuser"),
    ("bypassrls", "the role has BYPASSRLS"),
    ("createrole", "the role has CREATEROLE"),
    ("createdb", "the role has CREATEDB"),
    ("replication", "the role has REPLICATION"),
)
_GATE_OWNERSHIP = (
    ("owns_database", "the role owns the current database"),
    ("owns_relations", "the role owns relations of this database"),
    ("owns_functions", "the role owns functions of this database"),
    ("owns_schemas", "the role owns schemas of this database"),
)
_GATE_REMOTE = (
    ("dblink_execute", "the role can execute functions of the dblink extension"),
    ("foreign_server_usage", "the role has USAGE on a foreign server"),
)


def gate_reasons(row: dict, level: str) -> list:
    """The reasons the role gate refuses an alias of `level`, from the row of `ROLE_GATE_SQL`.

    A `write` alias has no gate. At `read` a membership in a monitoring role is a reason as well,
    since those roles belong to `diagnose`. `write_grants` is the list of up to three relations on
    which the role holds a write privilege.
    """
    if level not in ("read", "diagnose", "write"):
        raise ValueError(f"unknown level '{level}'")
    if level == "write":
        return []
    reasons = [text for key, text in _GATE_FLAGS if row[key]]
    if row["predefined_roles"]:
        reasons.append(f"the role is a member of predefined role(s) {', '.join(row['predefined_roles'])}")
    if row["other_roles"]:
        reasons.append(f"the role is a member of non-predefined role(s) {', '.join(row['other_roles'])}")
    reasons.extend(text for key, text in _GATE_OWNERSHIP if row[key])
    if row["write_grants"]:
        reasons.append("the role holds a write privilege on relation(s) "
                       f"{', '.join(row['write_grants'])} (at most 3 are listed)")
    reasons.extend(text for key, text in _GATE_REMOTE if row[key])
    if level == "read" and row["monitor_roles"]:
        names = row["monitor_roles"]
        reasons.append(f"the role is a member of {', '.join(names)}, "
                       f"which {'belongs' if len(names) == 1 else 'belong'} to the diagnose level")
    return reasons


# The queries below feed `dbwise doctor`. They report findings and refuse nothing. Each runs as the
# credential under test, in the database being inspected, and returns the columns named in its AS clauses.
AUDIT_EXTENSIONS = ("pg_stat_statements", "hypopg")
# The functions PostgreSQL leaves executable by PUBLIC that a read-only transaction does not stop.
# The first entry is a function name, the others are name prefixes of a family. Session-level advisory
# locks, taken by pg_advisory_lock and pg_try_advisory_lock, outlive a ROLLBACK.
PUBLIC_EXECUTE_FUNCTIONS = ("pg_logical_emit_message", "pg_advisory_", "pg_try_advisory_")
WRITE_GRANTS_SHOWN = 20

# Objects the role owns, one row per kind; ownership is tested with membership, as in the gate.
AUDIT_OWNED_SQL = f"""SELECT kind, count(*) AS total, (array_agg(name ORDER BY name))[1:3] AS examples
FROM (
  SELECT CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned table' WHEN 'v' THEN 'view'
                        WHEN 'm' THEN 'materialized view' WHEN 'S' THEN 'sequence' ELSE 'foreign table' END AS kind,
         quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS name
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND c.relpersistence <> 't'
    AND n.nspname NOT IN ({_names(GATE_SYSTEM_SCHEMAS)})
    AND pg_has_role(current_user, c.relowner, 'MEMBER')
  UNION ALL
  SELECT CASE f.prokind WHEN 'p' THEN 'procedure' WHEN 'a' THEN 'aggregate' ELSE 'function' END AS kind,
         quote_ident(n.nspname) || '.' || quote_ident(f.proname) AS name
  FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
  WHERE n.nspname NOT IN ({_names(GATE_SYSTEM_SCHEMAS)})
    AND pg_has_role(current_user, f.proowner, 'MEMBER')
  UNION ALL
  SELECT 'schema' AS kind, quote_ident(n.nspname) AS name
  FROM pg_namespace n
  WHERE n.nspname NOT IN ({_names(GATE_SYSTEM_SCHEMAS)})
    AND left(n.nspname, 8) <> 'pg_temp_' AND left(n.nspname, 14) <> 'pg_toast_temp_'
    AND pg_has_role(current_user, n.nspowner, 'MEMBER')
) owned
GROUP BY kind
ORDER BY kind"""

# CREATE on schemas and on the database stays out of the gate: PostgreSQL 14 and older grant CREATE on
# schema public to PUBLIC.
AUDIT_CREATE_SQL = f"""SELECT has_database_privilege(current_database(), 'CREATE') AS database_create,
  ARRAY(SELECT quote_ident(n.nspname) FROM pg_namespace n
        WHERE n.nspname NOT IN ({_names(GATE_SYSTEM_SCHEMAS)})
          AND left(n.nspname, 8) <> 'pg_temp_' AND left(n.nspname, 14) <> 'pg_toast_temp_'
          AND has_schema_privilege(n.oid, 'CREATE')
        ORDER BY n.nspname) AS schemas"""

# Write privileges the role holds through the ACL of a relation, directly, through PUBLIC or through a
# role it inherits. The CASE keeps pg_has_role away from the PUBLIC entry, whose grantee is 0.
AUDIT_WRITE_GRANTS_SQL = f"""SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS relation,
  array_agg(DISTINCT a.privilege_type ORDER BY a.privilege_type) AS privileges
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL aclexplode(c.relacl) a
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND c.relpersistence <> 't'
  AND n.nspname NOT IN ({_names(GATE_SYSTEM_SCHEMAS)})
  AND a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
  AND CASE WHEN a.grantee = 0 THEN true ELSE pg_has_role(current_user, a.grantee, 'USAGE') END
GROUP BY n.nspname, c.relname
ORDER BY n.nspname, c.relname
LIMIT {WRITE_GRANTS_SHOWN + 1}"""

_PUBLIC_EXECUTE_MATCH = "\n       OR ".join(
    [f"p.proname = '{PUBLIC_EXECUTE_FUNCTIONS[0]}'"]
    + [f"left(p.proname, {len(prefix)}) = '{prefix}'" for prefix in PUBLIC_EXECUTE_FUNCTIONS[1:]])

# A NULL ACL is the default, which grants EXECUTE to PUBLIC.
AUDIT_PUBLIC_EXECUTE_SQL = f"""SELECT p.oid::regprocedure::text AS function
FROM pg_proc p
WHERE p.pronamespace = 'pg_catalog'::regnamespace
  AND ({_PUBLIC_EXECUTE_MATCH})
  AND (p.proacl IS NULL
       OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))
ORDER BY p.proname, p.oid"""

AUDIT_EXTENSIONS_SQL = f"""SELECT e.extname AS extname
FROM pg_extension e
WHERE e.extname IN ({_names(AUDIT_EXTENSIONS)})
ORDER BY e.extname"""
