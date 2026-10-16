from dataclasses import dataclass

from ..sql import SqlDriver


@dataclass
class SequenceMetrics:
    schema: str
    table: str
    column: str
    sequence: str
    last_value: int
    max_value: int
    is_healthy: bool
    readable: bool = True

    @property
    def percent_used(self) -> float:
        """Calculate what percentage of the sequence has been used."""
        return (self.last_value / self.max_value) * 100 if self.max_value else 0


class SequenceHealthCalc:
    def __init__(self, sql_driver: SqlDriver, threshold: float = 0.9):
        """Initialize sequence health calculator.

        Args:
            sql_driver: SQL driver for database access
            threshold: Percentage (as decimal) of sequence usage that triggers warning
        """
        self.sql_driver = sql_driver
        self.threshold = threshold

    async def sequence_danger_check(self) -> str:
        """Check if any sequences are approaching their maximum values."""
        metrics = await self._get_sequence_metrics()

        if not metrics:
            return "No sequences found in the database."

        # Sort by remaining values ascending to show most critical first
        metrics.sort(key=lambda x: x.max_value - x.last_value)

        unreadable = [m for m in metrics if not m.readable]
        metrics = [m for m in metrics if m.readable]
        unhealthy = [m for m in metrics if not m.is_healthy]
        result = []
        if unhealthy:
            result.append("Sequences approaching maximum value:")
        elif metrics:
            result.append("All sequences have healthy usage levels.")
        for metric in unhealthy:
            remaining = metric.max_value - metric.last_value
            result.append(
                f"Sequence '{metric.schema}.{metric.sequence}' used for {metric.table}.{metric.column} "
                f"has used {metric.percent_used:.1f}% of available values "
                f"({metric.last_value:,} of {metric.max_value:,}, {remaining:,} remaining)"
            )
        if unreadable:
            names = ", ".join(f"'{m.schema}.{m.sequence}'" for m in unreadable)
            result.append(
                f"{len(unreadable)} sequence(s) could not be read with this role's privileges and were not "
                f"checked: {names}"
            )
        return "\n".join(result)

    async def _get_sequence_metrics(self) -> list[SequenceMetrics]:
        """Get metrics for the sequences a column owns or takes its default from."""
        # pg_sequences reports last_value as NULL, not an error, for a role without a privilege on the
        # sequence. A sequence that was never used is NULL too, so the privilege is read separately.
        # A column reaches its sequence through the ownership link (serial and identity columns), through
        # the dependency of its default expression (a sequence attached by hand) or through a default that
        # looks the sequence up by name, nextval(('name'::text)::regclass), which leaves no dependency.
        # The maximum is the lesser of the sequence's own and the range of the column type only when the
        # column takes the sequence's value unchanged: ownership, or a default that is exactly a nextval
        # call with an optional cast. A default such as nextval(...) % 1000 is held to the sequence's own
        # maximum. A pair reached by several routes is capped if any route says so.
        # Descending sequences are not checked. A partition is skipped when one of its ancestors has a row
        # for the same sequence (it copies its parent's default); a partition with a sequence of its own is
        # kept. The regular expressions match the text pg_get_expr prints for the default.
        rows = await self.sql_driver.execute_query(r"""
            WITH RECURSIVE defaults AS (
                SELECT
                    ad.oid AS default_oid,
                    ad.adrelid,
                    ad.adnum,
                    pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) AS expr
                FROM pg_catalog.pg_attrdef ad
            ),
            candidates AS (
                SELECT d.objid AS sequence_oid, d.refobjid AS table_oid, d.refobjsubid AS attnum, true AS capped
                FROM pg_catalog.pg_depend d
                WHERE d.classid = 'pg_catalog.pg_class'::regclass
                    AND d.refclassid = 'pg_catalog.pg_class'::regclass
                    AND d.refobjsubid > 0
                    AND d.deptype IN ('a', 'i')
                UNION ALL
                SELECT
                    d.refobjid,
                    df.adrelid,
                    df.adnum,
                    df.expr ~ '^\(?nextval\((''[^'']*''::regclass|\(''[^'']*''::text\)::regclass)\)\)?(::[a-z0-9 ]+)?$'
                FROM pg_catalog.pg_depend d
                INNER JOIN defaults df ON df.default_oid = d.objid
                WHERE d.classid = 'pg_catalog.pg_attrdef'::regclass
                    AND d.refclassid = 'pg_catalog.pg_class'::regclass
                    AND d.deptype = 'n'
                UNION ALL
                SELECT
                    pg_catalog.to_regclass(
                        pg_catalog.substring(df.expr, 'nextval\(\(''([^'']*)''::text\)::regclass\)')
                    )::oid,
                    df.adrelid,
                    df.adnum,
                    df.expr ~ '^\(?nextval\(\(''[^'']*''::text\)::regclass\)\)?(::[a-z0-9 ]+)?$'
                FROM defaults df
                WHERE df.expr LIKE '%nextval((%::text)::regclass)%'
            ),
            column_sequences AS (
                SELECT sequence_oid, table_oid, attnum, bool_or(capped) AS capped
                FROM candidates
                GROUP BY sequence_oid, table_oid, attnum
            ),
            ancestors AS (
                SELECT i.inhrelid AS relid, i.inhparent AS ancestor
                FROM pg_catalog.pg_inherits i
                UNION
                SELECT an.relid, i.inhparent
                FROM ancestors an
                INNER JOIN pg_catalog.pg_inherits i ON i.inhrelid = an.ancestor
            )
            SELECT
                s.schemaname AS sequence_schema,
                s.sequencename AS sequence,
                n.nspname AS table_schema,
                c.relname AS table,
                a.attname AS column,
                CASE WHEN cs.capped THEN
                    LEAST(s.max_value, CASE a.atttypid
                        WHEN 'pg_catalog.int2'::regtype THEN 32767
                        WHEN 'pg_catalog.int4'::regtype THEN 2147483647
                        ELSE 9223372036854775807 END)
                ELSE s.max_value END AS max_value,
                s.last_value AS last_value,
                has_sequence_privilege(sc.oid, 'SELECT,USAGE') AS readable
            FROM
                pg_catalog.pg_sequences s
            INNER JOIN
                pg_catalog.pg_namespace sn ON sn.nspname = s.schemaname
            INNER JOIN
                pg_catalog.pg_class sc ON sc.relnamespace = sn.oid AND sc.relname = s.sequencename
            INNER JOIN
                column_sequences cs ON cs.sequence_oid = sc.oid
            INNER JOIN
                pg_catalog.pg_class c ON c.oid = cs.table_oid
            INNER JOIN
                pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            INNER JOIN
                pg_catalog.pg_attribute a ON a.attrelid = cs.table_oid AND a.attnum = cs.attnum
            WHERE
                NOT a.attisdropped
                AND s.increment_by > 0
                AND NOT (
                    c.relispartition
                    AND EXISTS (
                        SELECT 1
                        FROM ancestors an
                        INNER JOIN column_sequences parent_cs
                            ON parent_cs.table_oid = an.ancestor AND parent_cs.sequence_oid = cs.sequence_oid
                        WHERE an.relid = cs.table_oid
                    )
                )
                AND n.nspname NOT LIKE 'pg\_temp\_%'
        """)

        sequence_metrics = []
        for row in rows or []:
            seq = dict(row.cells)
            max_value = seq["max_value"]
            readable = bool(seq["readable"])
            last_value = seq["last_value"] if seq["last_value"] is not None else 0
            sequence_metrics.append(
                SequenceMetrics(
                    schema=seq["sequence_schema"],
                    table=seq["table"],
                    column=seq["column"],
                    sequence=seq["sequence"],
                    last_value=last_value,
                    max_value=max_value,
                    readable=readable,
                    is_healthy=max_value <= 0 or last_value / max_value <= self.threshold,
                )
            )

        return sequence_metrics
