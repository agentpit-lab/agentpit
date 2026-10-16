"""SQL driver adapter for PostgreSQL connections."""

import logging
import re
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import urlparse
from urllib.parse import urlunparse

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from typing_extensions import LiteralString

logger = logging.getLogger(__name__)


def obfuscate_password(text: str | None) -> str | None:
    """
    Obfuscate password in any text containing connection information.
    Works on connection URLs, error messages, and other strings.
    """
    if text is None:
        return None

    if not text:
        return text

    # Try first as a proper URL
    try:
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc and parsed.password:
            # Replace password with asterisks in proper URL
            netloc = parsed.netloc.replace(parsed.password, "****")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass

    # Handle strings that contain connection strings but aren't proper URLs
    # Match a Postgres connection URL embedded in text and redact its password
    url_pattern = re.compile(r"(postgres(?:ql)?:\/\/[^:]+:)([^@]+)(@[^\/\s]+)")
    text = re.sub(url_pattern, r"\1****\3", text)

    # Match connection string parameters (password=xxx)
    # This simpler pattern captures password without quotes
    param_pattern = re.compile(r'(password=)([^\s&;"\']+)', re.IGNORECASE)
    text = re.sub(param_pattern, r"\1****", text)

    # Match password in DSN format with single quotes
    dsn_single_quote = re.compile(r"(password\s*=\s*')([^']+)(')", re.IGNORECASE)
    text = re.sub(dsn_single_quote, r"\1****\3", text)

    # Match password in DSN format with double quotes
    dsn_double_quote = re.compile(r'(password\s*=\s*")([^"]+)(")', re.IGNORECASE)
    text = re.sub(dsn_double_quote, r"\1****\3", text)

    return text


READ_ONLY_LEVELS = ("read", "diagnose")
GATED_LEVELS = READ_ONLY_LEVELS
ROLE_CHECKS = ("enforce", "warn")
# Server-side limit of every call at read and diagnose; the write level has none.
READ_STATEMENT_TIMEOUT = "30s"
# With the setting off, pglast and the server can split one string differently, so a statement the
# policy saw as one string could run a second one.
PIN_STRINGS_SQL = "SET standard_conforming_strings = on"
# The extension's schema: DISCARD ALL resets search_path, so the reset calls name it.
HYPOPG_LOOKUP_SQL = (
    "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'hypopg'"
)


class RoleGateError(ValueError):
    """The role gate found reasons to refuse the pool's role; the pool is closed."""

    def __init__(self, reasons: List[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class DbConnPool:
    """Database connection manager using psycopg's connection pool.

    ``level`` is the level of the calls the pool serves: ``read`` and ``diagnose`` connections are
    read-only, ``write`` ones are not. ``gate_level`` is the level of the alias when the role gate
    applies (``read`` or ``diagnose``); ``role_check`` decides whether a reason refuses the pool
    (``enforce``) or is only recorded in ``gate_reasons`` (``warn``).
    """

    def __init__(
        self,
        connection_url: Optional[str] = None,
        level: str = "write",
        gate_level: Optional[str] = None,
        role_check: str = "enforce",
    ):
        self.connection_url = connection_url
        self.level = level
        self.gate_level = gate_level
        self.role_check = role_check
        self.gate_reasons: List[str] = []
        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error = None
        self._hypopg_schema: Optional[str] = None

    async def configure(self, conn: Any) -> None:
        """Prepare a new connection: pinned string syntax, hypopg lookup, read-only mode by level."""
        # psycopg prepares a statement it ran five times; DISCARD ALL in reset() drops the prepared
        # statements, and the pin that follows would then name one the server no longer has.
        conn.prepare_threshold = None
        await conn.set_autocommit(True)
        await conn.execute(PIN_STRINGS_SQL)
        cursor = await conn.execute(HYPOPG_LOOKUP_SQL)
        found = await cursor.fetchone()
        self._hypopg_schema = found[0] if found is not None else None
        await conn.set_autocommit(False)
        if self.level in READ_ONLY_LEVELS:
            await conn.set_read_only(True)

    async def reset(self, conn: Any) -> None:
        """Clear session state after a call; a failure makes the pool discard the connection.

        DISCARD ALL cannot run inside a transaction block, and it returns the string syntax to the
        server default, so the pin is applied again.
        """
        await conn.set_autocommit(True)
        await conn.execute("DISCARD ALL")
        await conn.execute(PIN_STRINGS_SQL)
        if self._hypopg_schema is not None:
            # DISCARD ALL leaves hypothetical and hidden indexes behind.
            schema = sql.Identifier(self._hypopg_schema)
            await conn.execute(sql.SQL("SELECT {}.hypopg_reset()").format(schema))
            await conn.execute(sql.SQL("SELECT {}.hypopg_unhide_all_indexes()").format(schema))
        await conn.set_autocommit(False)

    async def _role_gate_reasons(self) -> List[str]:
        # The scripts directory is on the path when the launcher starts the server.
        from engine_postgres import ROLE_GATE_SQL
        from engine_postgres import gate_reasons

        assert self.pool is not None
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(ROLE_GATE_SQL)
                row = await cursor.fetchone()
        if row is None:
            raise ValueError("the role gate query returned no row for the connected role")
        return gate_reasons(row, self.gate_level)  # type: ignore[arg-type]

    async def pool_connect(self, connection_url: Optional[str] = None) -> AsyncConnectionPool:
        """Initialize connection pool with retry logic."""
        # If we already have a valid pool, return it
        if self.pool and self._is_valid:
            return self.pool

        url = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "Database connection URL not provided"
            raise ValueError(self._last_error)

        # Close any existing pool before creating a new one
        await self.close()

        try:
            # Configure connection pool with appropriate settings
            self.pool = AsyncConnectionPool(
                conninfo=url,
                min_size=1,
                max_size=5,
                open=False,  # Don't connect immediately, let's do it explicitly
                configure=self.configure,
                reset=self.reset,
            )

            # Open the pool explicitly
            await self.pool.open()

            # Test the connection pool by executing a simple query
            async with self.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")

            self.gate_reasons = []
            if self.gate_level in GATED_LEVELS:
                reasons = await self._role_gate_reasons()
                if reasons and self.role_check == "enforce":
                    raise RoleGateError(reasons)
                self.gate_reasons = reasons

            self._is_valid = True
            self._last_error = None
            return self.pool
        except RoleGateError as e:
            self._is_valid = False
            self._last_error = str(e)
            await self.close()
            raise
        except Exception as e:
            self._is_valid = False
            self._last_error = str(e)

            # Clean up failed pool
            await self.close()

            raise ValueError(f"Connection attempt failed: {obfuscate_password(str(e))}") from e

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            try:
                # Close the pool
                await self.pool.close()
            except Exception as e:
                logger.warning(f"Error closing connection pool: {e}")
            finally:
                self.pool = None
                self._is_valid = False

    @property
    def is_valid(self) -> bool:
        """Check if the connection pool is valid."""
        return self._is_valid

    @property
    def last_error(self) -> Optional[str]:
        """Get the last error message."""
        return self._last_error


class SqlDriver:
    """Adapter class that wraps a PostgreSQL connection with the interface expected by DTA."""

    @dataclass
    class RowResult:
        """Simple class to match the Griptape RowResult interface."""

        cells: Dict[str, Any]

    def __init__(
        self,
        conn: Any = None,
        engine_url: str | None = None,
    ):
        """
        Initialize with a PostgreSQL connection or pool.

        Args:
            conn: PostgreSQL connection object or pool
            engine_url: Connection URL string as an alternative to providing a connection
        """
        if conn:
            self.conn = conn
            # Check if this is a connection pool
            self.is_pool = isinstance(conn, DbConnPool)
        elif engine_url:
            # Don't connect here since we need async connection
            self.engine_url = engine_url
            self.conn = None
            self.is_pool = False
        else:
            raise ValueError("Either conn or engine_url must be provided")

    def connect(self):
        if self.conn is not None:
            return self.conn
        if self.engine_url:
            self.conn = DbConnPool(self.engine_url)
            self.is_pool = True
            return self.conn
        else:
            raise ValueError("Connection not established. Either conn or engine_url must be provided")

    async def execute_query(
        self,
        query: LiteralString,
        params: list[Any] | None = None,
        force_readonly: bool = False,
    ) -> Optional[List[RowResult]]:
        """
        Execute a query and return results.

        Args:
            query: SQL query to execute
            params: Query parameters
            force_readonly: Whether to enforce read-only mode

        Returns:
            List of RowResult objects or None on error
        """
        try:
            if self.conn is None:
                self.connect()
                if self.conn is None:
                    raise ValueError("Connection not established")

            # Handle connection pool vs direct connection
            if self.is_pool:
                # For pools, get a connection from the pool
                pool = await self.conn.pool_connect()
                async with pool.connection() as connection:
                    return await self._execute_with_connection(connection, query, params, force_readonly=force_readonly)
            else:
                # Direct connection approach
                return await self._execute_with_connection(self.conn, query, params, force_readonly=force_readonly)
        except Exception as e:
            # Mark pool as invalid if there was a connection issue
            if self.conn and self.is_pool:
                self.conn._is_valid = False  # type: ignore
                self.conn._last_error = str(e)  # type: ignore
            elif self.conn and not self.is_pool:
                self.conn = None

            raise e

    async def _execute_with_connection(self, connection, query, params, force_readonly) -> Optional[List[RowResult]]:
        """Execute query with the given connection."""
        transaction_started = False
        try:
            async with connection.cursor(row_factory=dict_row) as cursor:
                # The pool's connections are read-only at read and diagnose, so psycopg's own BEGIN opens
                # a read-only transaction. A write-level connection is not, so this call asks for it.
                # SET TRANSACTION READ WRITE could still lift either; the policy refuses VariableSetStmt
                # and TransactionStmt, which is what stops it. The limit is local to the transaction.
                if force_readonly:
                    if not connection.read_only:
                        await cursor.execute("SET TRANSACTION READ ONLY")
                    await cursor.execute(f"SET LOCAL statement_timeout = '{READ_STATEMENT_TIMEOUT}'")
                    transaction_started = True

                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)

                # For multiple statements, move to the last statement's results
                while cursor.nextset():
                    pass

                if cursor.description is None:  # No results (like DDL statements)
                    if not force_readonly:
                        await cursor.execute("COMMIT")
                    elif transaction_started:
                        await cursor.execute("ROLLBACK")
                        transaction_started = False
                    return None

                # Get results from the last statement only
                rows = await cursor.fetchall()

                # End the transaction appropriately
                if not force_readonly:
                    await cursor.execute("COMMIT")
                elif transaction_started:
                    await cursor.execute("ROLLBACK")
                    transaction_started = False

                return [SqlDriver.RowResult(cells=dict(row)) for row in rows]

        except Exception as e:
            # Try to roll back the transaction if it's still active
            if transaction_started:
                try:
                    await connection.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error rolling back transaction: {rollback_error}")

            logger.error(f"Error executing query ({query}): {e}")
            raise e
