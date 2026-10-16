"""Access levels and the tool table (standard library only).

An alias has a level; a tool has a level or none. A call is allowed when the tool's level is at or
below the alias's level, and it connects with the alias's credential for the tool's level. The
server and the documentation generator both read this table.
"""
from __future__ import annotations

LEVELS = ("read", "diagnose", "write")

# None: the tool is offered on every alias and uses the read credential.
TOOLS = {
    "list_databases": None,
    "reconnect": None,
    "list_schemas": "read",
    "list_objects": "read",
    "get_object_details": "read",
    "read_sql": "read",
    "explain_query": "diagnose",
    "get_top_queries": "diagnose",
    "analyze_db_health": "diagnose",
    "analyze_workload_indexes": "diagnose",
    "analyze_query_indexes": "diagnose",
    "diagnose_sql": "diagnose",
    "write_sql": "write",
}

DEFAULT_LEVEL = "read"


def _rank(level: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError:
        raise ValueError(f"unknown level '{level}' (allowed: {', '.join(LEVELS)})") from None


def alias_level(alias: dict) -> str:
    """The level of an alias configuration; absent means read."""
    return alias.get("level", DEFAULT_LEVEL)


def admits(level: str, tool_level: str | None) -> bool:
    """Whether an alias of `level` may run a tool of `tool_level`."""
    rank = _rank(level)
    return tool_level is None or _rank(tool_level) <= rank


def credential_for(alias: dict, tool_level: str | None) -> str:
    """The credential name an alias uses for a tool of `tool_level`.

    A tool without a level uses the read credential. A level above the alias's has no credential,
    so asking for one is an error.
    """
    level = DEFAULT_LEVEL if tool_level is None else tool_level
    if not admits(alias_level(alias), level):
        raise ValueError(f"the alias is at level '{alias_level(alias)}' and has no credential for '{level}'")
    credential = alias["credential"]
    return credential if isinstance(credential, str) else credential[level]
