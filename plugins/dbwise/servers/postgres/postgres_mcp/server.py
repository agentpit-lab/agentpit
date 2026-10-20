# ruff: noqa: B008
import asyncio
import logging
import signal
import sys
from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Union

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic import validate_call

from postgres_mcp.index.dta_calc import DatabaseTuningAdvisor

from .artifacts import ErrorResult
from .artifacts import ExplainPlanArtifact
from .database_health import DatabaseHealthTool
from .database_health import HealthType
from .explain import ExplainPlanTool
from .index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from .index.presentation import TextPresentation
from .sql import DatabaseValidationError
from .sql import DbConnPoolRegistry
from .sql import MarkedDriver
from .sql import PolicyDriver
from .sql import SqlDriver
from .sql import check_hypopg_installation_status
from .sql import obfuscate_password
from .top_queries import TopQueriesCalc

# Constants
PG_STAT_STATEMENTS = "pg_stat_statements"
HYPOPG_EXTENSION = "hypopg"
ALIAS_PARAM_DESC = (
    "Required. Target alias; a call without it is refused and names the available aliases. "
    "Call list_databases for the aliases, their levels and their databases."
)
DATABASE_NAME_PARAM_DESC = "Target database name within the alias. Call list_databases for available names."
PG_STAT_STATEMENTS_SCOPE_NOTE = (
    f"\n\nNote: {PG_STAT_STATEMENTS} has server-global scope; results include queries from all "
    "databases on this PG server, not just the selected database_name."
)
# Server-side and client-side limit of every call at read and diagnose, in seconds.
POLICY_TIMEOUT_SECONDS = 30
EXPLAIN_ANALYZE_SENTENCE = (
    "which executes the explained SELECT on the server, with its full load, inside a transaction that is rolled back"
)
WARN_SENTENCE = (
    "role check relaxed (warn): the guarantee rests on dbwise's SQL policy and the read-only transaction only"
)

ResponseType = List[types.TextContent | types.ImageContent | types.EmbeddedResource]

logger = logging.getLogger(__name__)

# Global variables; run() replaces the registry with a fresh one.
db_registry = DbConnPoolRegistry()
shutdown_in_progress = False


def _levels() -> Any:
    """The shared level table. The launcher puts the scripts directory on the path before it starts the server."""
    import levels

    return levels


async def get_sql_driver(alias: Optional[str], database_name: Optional[str], level: str) -> Union[MarkedDriver, PolicyDriver]:
    """Return the driver for a call of a tool at ``level`` against (alias, database).

    The call rule: the tool's level must be at or below the alias's level, and the call connects with
    the alias's credential for the tool's level. A refused call raises before any pool is opened, so
    no SQL is sent. ``read`` and ``diagnose`` calls run under the SQL policy of their level; ``write``
    runs without a policy, with the level marker in front of the text.
    """
    alias_level = db_registry.alias_level(alias)  # raises DatabaseValidationError listing the aliases
    if not _levels().admits(alias_level, level):
        raise DatabaseValidationError(
            f"Alias '{alias}' is at level '{alias_level}', but this tool is at level '{level}'. Nothing was sent to the database."
        )
    pool = await db_registry.get_pool(database_name, alias, level)
    base_driver = SqlDriver(conn=pool)
    if level == "write":
        return MarkedDriver(sql_driver=base_driver, level=level)
    return PolicyDriver(sql_driver=base_driver, level=level, timeout=POLICY_TIMEOUT_SECONDS)


def format_text_response(text: Any) -> ResponseType:
    """Format a text response."""
    return [types.TextContent(type="text", text=str(text))]


def format_error_response(error: str) -> ResponseType:
    """Format an error response."""
    return format_text_response(f"Error: {error}")


async def list_schemas(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
) -> ResponseType:
    """List all schemas in the database."""
    try:
        sql_driver = await get_sql_driver(alias, database_name, "read")
        rows = await sql_driver.execute_query(
            """
            SELECT
                schema_name,
                schema_owner,
                CASE
                    WHEN schema_name LIKE 'pg_%' THEN 'System Schema'
                    WHEN schema_name = 'information_schema' THEN 'System Information Schema'
                    ELSE 'User Schema'
                END as schema_type
            FROM information_schema.schemata
            ORDER BY schema_type, schema_name
            """
        )
        schemas = [row.cells for row in rows] if rows else []
        return format_text_response(schemas)
    except Exception as e:
        logger.error(f"Error listing schemas: {e}")
        return format_error_response(str(e))


async def list_objects(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    schema_name: str = Field(description="Schema name"),
    object_type: str = Field(description="Object type: 'table', 'view', 'sequence', or 'extension'", default="table"),
) -> ResponseType:
    """List objects of a given type in a schema."""
    try:
        sql_driver = await get_sql_driver(alias, database_name, "read")

        if object_type in ("table", "view"):
            table_type = "BASE TABLE" if object_type == "table" else "VIEW"
            rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = {} AND table_type = {}
                ORDER BY table_name
                """,
                [schema_name, table_type],
            )
            objects = (
                [{"schema": row.cells["table_schema"], "name": row.cells["table_name"], "type": row.cells["table_type"]} for row in rows]
                if rows
                else []
            )

        elif object_type == "sequence":
            rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT sequence_schema, sequence_name, data_type
                FROM information_schema.sequences
                WHERE sequence_schema = {}
                ORDER BY sequence_name
                """,
                [schema_name],
            )
            objects = (
                [{"schema": row.cells["sequence_schema"], "name": row.cells["sequence_name"], "data_type": row.cells["data_type"]} for row in rows]
                if rows
                else []
            )

        elif object_type == "extension":
            # Extensions are not schema-specific
            rows = await sql_driver.execute_query(
                """
                SELECT extname, extversion, extrelocatable
                FROM pg_extension
                ORDER BY extname
                """
            )
            objects = (
                [{"name": row.cells["extname"], "version": row.cells["extversion"], "relocatable": row.cells["extrelocatable"]} for row in rows]
                if rows
                else []
            )

        else:
            return format_error_response(f"Unsupported object type: {object_type}")

        return format_text_response(objects)
    except Exception as e:
        logger.error(f"Error listing objects: {e}")
        return format_error_response(str(e))


async def get_object_details(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    schema_name: str = Field(description="Schema name"),
    object_name: str = Field(description="Object name"),
    object_type: str = Field(description="Object type: 'table', 'view', 'sequence', or 'extension'", default="table"),
) -> ResponseType:
    """Get detailed information about a database object."""
    try:
        sql_driver = await get_sql_driver(alias, database_name, "read")

        if object_type in ("table", "view"):
            # Get columns
            col_rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = {} AND table_name = {}
                ORDER BY ordinal_position
                """,
                [schema_name, object_name],
            )
            columns = (
                [
                    {
                        "column": r.cells["column_name"],
                        "data_type": r.cells["data_type"],
                        "is_nullable": r.cells["is_nullable"],
                        "default": r.cells["column_default"],
                    }
                    for r in col_rows
                ]
                if col_rows
                else []
            )

            # Get constraints
            con_rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
                FROM information_schema.table_constraints AS tc
                LEFT JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = {} AND tc.table_name = {}
                """,
                [schema_name, object_name],
            )

            constraints = {}
            if con_rows:
                for row in con_rows:
                    cname = row.cells["constraint_name"]
                    ctype = row.cells["constraint_type"]
                    col = row.cells["column_name"]

                    if cname not in constraints:
                        constraints[cname] = {"type": ctype, "columns": []}
                    if col:
                        constraints[cname]["columns"].append(col)

            constraints_list = [{"name": name, **data} for name, data in constraints.items()]

            # Get indexes
            idx_rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = {} AND tablename = {}
                """,
                [schema_name, object_name],
            )

            indexes = [{"name": r.cells["indexname"], "definition": r.cells["indexdef"]} for r in idx_rows] if idx_rows else []

            result = {
                "basic": {"schema": schema_name, "name": object_name, "type": object_type},
                "columns": columns,
                "constraints": constraints_list,
                "indexes": indexes,
            }

        elif object_type == "sequence":
            rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT sequence_schema, sequence_name, data_type, start_value, increment
                FROM information_schema.sequences
                WHERE sequence_schema = {} AND sequence_name = {}
                """,
                [schema_name, object_name],
            )

            if rows and rows[0]:
                row = rows[0]
                result = {
                    "schema": row.cells["sequence_schema"],
                    "name": row.cells["sequence_name"],
                    "data_type": row.cells["data_type"],
                    "start_value": row.cells["start_value"],
                    "increment": row.cells["increment"],
                }
            else:
                result = {}

        elif object_type == "extension":
            rows = await PolicyDriver.execute_param_query(
                sql_driver,
                """
                SELECT extname, extversion, extrelocatable
                FROM pg_extension
                WHERE extname = {}
                """,
                [object_name],
            )

            if rows and rows[0]:
                row = rows[0]
                result = {"name": row.cells["extname"], "version": row.cells["extversion"], "relocatable": row.cells["extrelocatable"]}
            else:
                result = {}

        else:
            return format_error_response(f"Unsupported object type: {object_type}")

        return format_text_response(result)
    except Exception as e:
        logger.error(f"Error getting object details: {e}")
        return format_error_response(str(e))


async def explain_query(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    sql: str = Field(description="SQL query to explain"),
    analyze: bool = Field(
        description="When True, runs EXPLAIN with ANALYZE, which executes the explained SELECT on the server, with its "
        "full load, inside a transaction that is rolled back, and shows real execution statistics instead of "
        "estimates. Takes longer but provides more accurate information.",
        default=False,
    ),
    hypothetical_indexes: list[dict[str, Any]] = Field(
        description="""A list of hypothetical indexes to simulate. Each index must be a dictionary with these keys:
    - 'table': The table name to add the index to (e.g., 'users')
    - 'columns': List of column names to include in the index (e.g., ['email'] or ['last_name', 'first_name'])
    - 'using': Optional index method (default: 'btree', other options include 'hash', 'gist', etc.)

Examples: [
    {"table": "users", "columns": ["email"], "using": "btree"},
    {"table": "orders", "columns": ["user_id", "created_at"]}
]
If there is no hypothetical index, you can pass an empty list.""",
        default=[],
    ),
) -> ResponseType:
    """
    Explains the execution plan for a SQL query.

    Args:
        sql: The SQL query to explain
        analyze: When True, runs EXPLAIN with ANALYZE, which executes the explained SELECT in a transaction that is rolled back, for real statistics
        hypothetical_indexes: Optional list of indexes to simulate
    """
    try:
        sql_driver = await get_sql_driver(alias, database_name, "diagnose")
        explain_tool = ExplainPlanTool(sql_driver=sql_driver)
        result: ExplainPlanArtifact | ErrorResult | None = None

        # If hypothetical indexes are specified, check for HypoPG extension
        if hypothetical_indexes and len(hypothetical_indexes) > 0:
            if analyze:
                return format_error_response("Cannot use analyze and hypothetical indexes together")
            try:
                # Use the common utility function to check if hypopg is installed
                (
                    is_hypopg_installed,
                    hypopg_message,
                ) = await check_hypopg_installation_status(sql_driver)

                # If hypopg is not installed, return the message
                if not is_hypopg_installed:
                    return format_text_response(hypopg_message)

                # HypoPG is installed, proceed with explaining with hypothetical indexes
                result = await explain_tool.explain_with_hypothetical_indexes(sql, hypothetical_indexes)
            except Exception:
                raise  # Re-raise the original exception
        elif analyze:
            try:
                # Use EXPLAIN ANALYZE
                result = await explain_tool.explain_analyze(sql)
            except Exception:
                raise  # Re-raise the original exception
        else:
            try:
                # Use basic EXPLAIN
                result = await explain_tool.explain(sql)
            except Exception:
                raise  # Re-raise the original exception

        if result and isinstance(result, ExplainPlanArtifact):
            return format_text_response(result.to_text())
        else:
            error_message = "Error processing explain plan"
            if isinstance(result, ErrorResult):
                error_message = result.to_text()
            return format_error_response(error_message)
    except Exception as e:
        logger.error(f"Error explaining query: {e}")
        return format_error_response(str(e))


@validate_call
# alias and database_name come last (both optional-with-default) because of @validate_call's
# required-before-optional constraint; the alias is required, and the call rule enforced, at
# runtime by the registry and get_sql_driver.
async def analyze_workload_indexes(
    max_index_size_mb: int = Field(description="Max index size in MB", default=10000),
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
) -> ResponseType:
    """Analyze frequently executed queries in the database and recommend optimal indexes."""
    try:
        sql_driver = await get_sql_driver(alias, database_name, "diagnose")
        index_tuning = DatabaseTuningAdvisor(sql_driver)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        result = await dta_tool.analyze_workload(max_index_size_mb=max_index_size_mb)
        return format_text_response(f"{result}{PG_STAT_STATEMENTS_SCOPE_NOTE}")
    except Exception as e:
        logger.error(f"Error analyzing workload: {e}")
        return format_error_response(str(e))


@validate_call
# alias and database_name come last (both optional-with-default) because of @validate_call's
# required-before-optional constraint; the alias is required, and the call rule enforced, at
# runtime by the registry and get_sql_driver.
async def analyze_query_indexes(
    queries: list[str] = Field(description="List of Query strings to analyze"),
    max_index_size_mb: int = Field(description="Max index size in MB", default=10000),
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
) -> ResponseType:
    """Analyze a list of SQL queries and recommend optimal indexes."""
    if len(queries) == 0:
        return format_error_response("Please provide a non-empty list of queries to analyze.")
    if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
        return format_error_response(f"Please provide a list of up to {MAX_NUM_INDEX_TUNING_QUERIES} queries to analyze.")

    try:
        sql_driver = await get_sql_driver(alias, database_name, "diagnose")
        index_tuning = DatabaseTuningAdvisor(sql_driver)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        result = await dta_tool.analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)
        return format_text_response(result)
    except Exception as e:
        logger.error(f"Error analyzing queries: {e}")
        return format_error_response(str(e))


async def analyze_db_health(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    health_type: str = Field(
        description=f"Optional. Valid values are: {', '.join(sorted([t.value for t in HealthType]))}.",
        default="all",
    ),
) -> ResponseType:
    """Analyze database health for specified components.

    Args:
        health_type: Comma-separated list of health check types to perform.
                    Valid values: index, connection, vacuum, sequence, replication, buffer, constraint, all
    """
    try:
        sql_driver = await get_sql_driver(alias, database_name, "diagnose")
        health_tool = DatabaseHealthTool(sql_driver)
        result = await health_tool.health(health_type=health_type)
        return format_text_response(result)
    except Exception as e:
        logger.error(f"Error analyzing database health: {e}")
        return format_error_response(str(e))


async def get_top_queries(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    sort_by: str = Field(
        description="Ranking criteria: 'total_time' for total execution time or 'mean_time' for mean execution time per call, or 'resources' "
        "for resource-intensive queries",
        default="resources",
    ),
    limit: int = Field(description="Number of queries to return when ranking based on mean_time or total_time", default=10),
) -> ResponseType:
    try:
        sql_driver = await get_sql_driver(alias, database_name, "diagnose")
        top_queries_tool = TopQueriesCalc(sql_driver=sql_driver)

        if sort_by == "resources":
            result = await top_queries_tool.get_top_resource_queries()
            return format_text_response(f"{result}{PG_STAT_STATEMENTS_SCOPE_NOTE}")
        elif sort_by == "mean_time" or sort_by == "total_time":
            # Map the sort_by values to what get_top_queries_by_time expects
            result = await top_queries_tool.get_top_queries_by_time(limit=limit, sort_by="mean" if sort_by == "mean_time" else "total")
        else:
            return format_error_response("Invalid sort criteria. Please use 'resources' or 'mean_time' or 'total_time'.")
        return format_text_response(f"{result}{PG_STAT_STATEMENTS_SCOPE_NOTE}")
    except Exception as e:
        logger.error(f"Error getting slow queries: {e}")
        return format_error_response(str(e))


async def read_sql(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    sql: str = Field(description="SQL to run"),
) -> ResponseType:
    """Runs SELECT or SHOW statements with the alias's read credential."""
    return await _run_sql(alias, database_name, sql, "read")


async def diagnose_sql(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    sql: str = Field(description="SQL to run"),
) -> ResponseType:
    """Runs what read_sql runs, and EXPLAIN with ANALYZE, with the alias's diagnose credential."""
    return await _run_sql(alias, database_name, sql, "diagnose")


async def write_sql(
    alias: Optional[str] = Field(None, description=ALIAS_PARAM_DESC),
    database_name: Optional[str] = Field(None, description=DATABASE_NAME_PARAM_DESC),
    sql: str = Field(description="SQL to run"),
) -> ResponseType:
    """Runs any SQL with the alias's write credential and commits it."""
    return await _run_sql(alias, database_name, sql, "write")


async def _run_sql(alias: Optional[str], database_name: Optional[str], sql: str, level: str) -> ResponseType:
    try:
        sql_driver = await get_sql_driver(alias, database_name, level)
        rows = await sql_driver.execute_query(sql)  # type: ignore
        if rows is None:
            return format_text_response("No results")
        return format_text_response(list([r.cells for r in rows]))
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        return format_error_response(str(e))


def _alias_listing(availability: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The availability map of the registry, with each alias's level and the databases under their listing names."""
    aliases: dict[str, Any] = {}
    for alias, snapshot in availability.items():
        aliases[alias] = {
            "level": db_registry.configured_level(alias),
            "reachable": snapshot["reachable"],
            "databases": snapshot["dbs_ok"],
            "databases_missing": snapshot["dbs_missing"],
            "error": snapshot["error"],
            "role_check": snapshot["role_check"],
            "reasons": snapshot["reasons"],
        }
    return {"aliases": aliases}


async def list_databases() -> dict[str, Any]:
    """Return every alias with its level, databases, reachability, role check mode and the reasons of the role check."""
    return _alias_listing(db_registry.availability_map())


async def reconnect(
    alias: Optional[str] = Field(
        None,
        description="Optional: re-probe only this alias. Omit to re-probe every currently unreachable alias.",
    ),
) -> dict[str, Any]:
    """Side-effecting: re-probe unreachable (or one named) aliases with the read credential; return the refreshed listing."""
    try:
        if alias is not None:
            db_registry.alias_level(alias)  # an unknown alias raises with the list of aliases
        availability = await db_registry.reconnect_all(alias)
        return _alias_listing(availability)
    except Exception as e:
        logger.error(f"Error during reconnect: {obfuscate_password(str(e))}")
        return {"error": obfuscate_password(str(e))}


@dataclass(frozen=True)
class ToolEntry:
    """What the server needs to register one tool: its function, title, text and annotations."""

    function: Callable[..., Any]
    title: str
    text: str
    annotations: ToolAnnotations


def _read_only(title: str) -> ToolAnnotations:
    # The role check, the SQL policy and the read-only transaction keep data and schema unchanged.
    return ToolAnnotations(title=title, readOnlyHint=True)


TOOL_ENTRIES: dict[str, ToolEntry] = {
    "list_databases": ToolEntry(
        list_databases,
        "List Databases",
        "Lists every alias with its level, databases, reachability, role check mode and the reasons of the role check.",
        _read_only("List Databases"),
    ),
    "reconnect": ToolEntry(
        reconnect,
        "Reconnect",
        "Re-probes unreachable aliases with their read credential and rebuilds their availability, leaving healthy "
        "aliases (and their in-flight queries) untouched. Use this to recover after an alias was unreachable (for "
        "example a VPN or the database came back) without restarting the server. Pass 'alias' to force a re-probe of "
        "one alias regardless of its state; its pools at every level are closed and the role check runs again.",
        ToolAnnotations(title="Reconnect", readOnlyHint=False),
    ),
    "list_schemas": ToolEntry(
        list_schemas,
        "List Schemas",
        "Lists all schemas in one database of an alias. Use it first to find where the objects are; list_objects "
        "then lists one schema's tables, views, sequences or extensions.",
        _read_only("List Schemas"),
    ),
    "list_objects": ToolEntry(
        list_objects,
        "List Objects",
        "Lists the objects of one type ('table' by default, or 'view', 'sequence', 'extension') in one schema; "
        "extensions are listed for the whole database, whatever the schema. Use get_object_details for the "
        "columns, constraints and indexes of one object.",
        _read_only("List Objects"),
    ),
    "get_object_details": ToolEntry(
        get_object_details,
        "Get Object Details",
        "Shows one table, view, sequence or extension, named by schema, object name and object type ('table' by "
        "default); for a table it returns the columns, constraints and indexes, for a view its columns, for a "
        "sequence its type, start value and increment, and for an extension its version.",
        _read_only("Get Object Details"),
    ),
    "read_sql": ToolEntry(
        read_sql,
        "Read SQL",
        "This tool runs one or more SELECT or SHOW statements with the alias's read credential. Other statements, "
        "including EXPLAIN and SELECT with a locking clause, are refused by the read level's SQL policy, and each "
        f"call stops after {POLICY_TIMEOUT_SECONDS} seconds. Use diagnose_sql for EXPLAIN and write_sql for a "
        "change. Returns the result rows as lists of cells, 'No results' when there are none, or "
        "'Error: <message>'.",
        _read_only("Read SQL"),
    ),
    "explain_query": ToolEntry(
        explain_query,
        "Explain Query",
        "Explains the execution plan for a SQL query, showing how the database will execute it and provides detailed "
        f"cost estimates. With analyze, EXPLAIN with ANALYZE {EXPLAIN_ANALYZE_SENTENCE}.",
        _read_only("Explain Query"),
    ),
    "get_top_queries": ToolEntry(
        get_top_queries,
        "Get Top Queries",
        f"Reports the slowest or most resource-intensive queries using data from the '{PG_STAT_STATEMENTS}' extension.",
        _read_only("Get Top Queries"),
    ),
    "analyze_db_health": ToolEntry(
        analyze_db_health,
        "Analyze Database Health",
        "Analyzes database health. Here are the available health checks:\n"
        "- index - checks for invalid, duplicate, and bloated indexes\n"
        "- connection - checks the number of connection and their utilization\n"
        "- vacuum - checks vacuum health for transaction id wraparound\n"
        "- sequence - checks sequences at risk of exceeding their maximum value\n"
        "- replication - checks replication health including lag and slots\n"
        "- buffer - checks for buffer cache hit rates for indexes and tables\n"
        "- constraint - checks for invalid constraints\n"
        "- all - runs all checks\n"
        "You can optionally specify a single health check or a comma-separated list of health checks. The default is 'all' checks.",
        _read_only("Analyze Database Health"),
    ),
    "analyze_workload_indexes": ToolEntry(
        analyze_workload_indexes,
        "Analyze Workload Indexes",
        "Analyzes frequently executed queries in the database and recommends optimal indexes.",
        _read_only("Analyze Workload Indexes"),
    ),
    "analyze_query_indexes": ToolEntry(
        analyze_query_indexes,
        "Analyze Query Indexes",
        "Analyzes a list of (up to 10) SQL queries and recommends optimal indexes.",
        _read_only("Analyze Query Indexes"),
    ),
    "diagnose_sql": ToolEntry(
        diagnose_sql,
        "Diagnose SQL",
        "This tool runs what read_sql runs with the alias's diagnose credential, and adds EXPLAIN with ANALYZE, "
        f"{EXPLAIN_ANALYZE_SENTENCE}. It can also read the statistics the diagnose role can see.",
        _read_only("Diagnose SQL"),
    ),
    "write_sql": ToolEntry(
        write_sql,
        "Write SQL",
        "This tool runs any SQL with the alias's write credential and commits it. No SQL policy and no "
        f"dbwise time limit apply, unlike the {POLICY_TIMEOUT_SECONDS}-second limit of read_sql and "
        "diagnose_sql, so a committed change cannot be undone from here. Use it only for a change the "
        "operator asked for; use read_sql or diagnose_sql when the task only reads. Returns the result rows "
        "as lists of cells, 'No results' when there are none, or 'Error: <message>'.",
        ToolAnnotations(title="Write SQL", destructiveHint=True),
    ),
}


def _description(name: str, aliases: dict[str, Any]) -> str:
    """The tool's description: its level, the aliases that admit it, what it does, and a relaxed role check if any."""
    levels = _levels()
    tool_level = levels.TOOLS[name]
    admitting = {alias: entry for alias, entry in aliases.items() if levels.admits(entry["level"], tool_level)}
    listed = ", ".join(f"{alias} ({entry['level']})" for alias, entry in admitting.items())
    text = f"Level: {'any' if tool_level is None else tool_level}. Aliases: {listed}. {TOOL_ENTRIES[name].text}"
    if tool_level in ("read", "diagnose"):
        # Only aliases at read and diagnose have a role check; a write alias has none to relax.
        relaxed = [
            alias for alias, entry in admitting.items() if entry.get("role_check") == "warn" and entry["level"] != "write"
        ]
        if relaxed:
            text += f" For {', '.join(relaxed)}: {WARN_SENTENCE}."
    return text


def _aliases_with_a_level(aliases: dict[str, Any]) -> dict[str, Any]:
    """The aliases whose level the level table knows; the registry records the others as unreachable with a reason."""
    levels = _levels()
    return {
        alias: {**entry, "level": entry.get("level", levels.DEFAULT_LEVEL)}
        for alias, entry in aliases.items()
        if entry.get("level", levels.DEFAULT_LEVEL) in levels.LEVELS
    }


def build_mcp(aliases: dict[str, Any]) -> FastMCP:
    """Build the MCP server with the tools that at least one configured alias admits (spec: tools follow the aliases)."""
    levels = _levels()
    aliases = _aliases_with_a_level(aliases)
    # FastMCP logs every request at INFO to stderr unless told otherwise; a server with no aliases must print nothing.
    app = FastMCP("postgres-mcp", log_level="WARNING")
    for name, tool_level in levels.TOOLS.items():
        if not any(levels.admits(entry["level"], tool_level) for entry in aliases.values()):
            continue
        entry = TOOL_ENTRIES[name]
        app.add_tool(entry.function, name=name, title=entry.title, description=_description(name, aliases), annotations=entry.annotations)
    return app


def _install_signal_handlers() -> None:
    try:
        loop = asyncio.get_running_loop()
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(s)))
    except NotImplementedError:
        # Windows doesn't support signals properly; the message would reach stderr of a server with no aliases.
        logger.debug("Signal handling not supported on this platform")


async def run(aliases: dict[str, Any]) -> None:
    """Entry point: serve the tools of the given aliases over stdio.

    ``aliases`` maps alias -> ``{"level": "read" | "diagnose" | "write", "role_check": "enforce" | "warn",
    "databases": [str, ...], "dsns": {level: dsn}, "problems": {level: reason}}``: one DSN for each level from
    ``read`` up to the alias's level whose password could be resolved, and the reason for each that could not.
    Start-up is non-fatal: every alias is probed in parallel with its read credential, an unreachable one is
    recorded and shown by ``list_databases``, and the server always reaches its stdio loop, with no tools at all
    when no alias is given.
    """
    global db_registry
    db_registry = DbConnPoolRegistry()
    availability = await db_registry.register_environments(aliases)

    for alias, info in availability.items():
        if info["reachable"]:
            msg = f"Alias '{alias}' reachable: {len(info['dbs_ok'])} database(s) available"
            if info["dbs_missing"]:
                msg += f"; {len(info['dbs_missing'])} not found: {', '.join(info['dbs_missing'])}"
            logger.info(msg)
        else:
            logger.warning("Alias '%s' UNAVAILABLE (server still starting): %s", alias, info["error"])

    app = build_mcp(aliases)
    _install_signal_handlers()
    await app.run_stdio_async()


async def shutdown(sig=None):
    """Clean shutdown of the server."""
    global shutdown_in_progress

    if shutdown_in_progress:
        logger.warning("Forcing immediate exit")
        # Use sys.exit instead of os._exit to allow for proper cleanup
        sys.exit(1)

    shutdown_in_progress = True

    if sig:
        logger.info(f"Received exit signal {sig.name}")

    # Close database connections
    try:
        await db_registry.close_all()
        logger.info("Closed database connections")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")

    # Exit with appropriate status code
    sys.exit(128 + sig if sig is not None else 0)
