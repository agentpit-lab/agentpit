from dataclasses import dataclass
from typing import Optional

from ..sql import SqlDriver


@dataclass
class ReplicationSlot:
    slot_name: str
    database: str
    active: bool
    lag_bytes: Optional[int] = None
    wal_status: Optional[str] = None
    safe_wal_size: Optional[int] = None


@dataclass
class ReplicationMetrics:
    is_replica: bool
    replication_lag_seconds: Optional[float]
    is_replicating: bool
    replication_slots: list[ReplicationSlot]


class ReplicationCalc:
    def __init__(self, sql_driver: SqlDriver):
        self.sql_driver = sql_driver
        self._server_version: Optional[int] = None
        self._feature_support: dict[str, bool] = {}

    async def replication_health_check(self) -> str:
        """Check replication health including lag and slots."""
        metrics = await self._get_replication_metrics()
        result = []

        if metrics.is_replica:
            result.append("This is a replica database.")
            # Check replication status
            if not metrics.is_replicating:
                result.append("WARNING: Replica is not actively replicating from primary!")
            else:
                result.append("Replica is actively replicating from primary.")

            # Check replication lag
            if metrics.replication_lag_seconds is not None:
                if metrics.replication_lag_seconds == 0:
                    result.append("No replication lag detected.")
                else:
                    result.append(f"Replication lag: {metrics.replication_lag_seconds:.1f} seconds")
        else:
            result.append("This is a primary database.")
            if metrics.is_replicating:
                result.append("Has active replicas connected.")
            else:
                result.append("No active replicas connected.")

        # Check replication slots for both primary and replica
        if metrics.replication_slots:
            active_slots = [s for s in metrics.replication_slots if s.active]
            inactive_slots = [s for s in metrics.replication_slots if not s.active]

            if active_slots:
                result.append("\nActive replication slots:")
                for slot in active_slots:
                    result.append(self._format_slot(slot))

            if inactive_slots:
                result.append("\nInactive replication slots:")
                for slot in inactive_slots:
                    result.append(self._format_slot(slot))
        else:
            result.append("\nNo replication slots found.")

        return "\n".join(result)

    @staticmethod
    def _format_slot(slot: ReplicationSlot) -> str:
        parts = [f"database: {slot.database}"]
        if slot.lag_bytes is not None:
            parts.append(f"WAL held back: {slot.lag_bytes} bytes")
        if slot.wal_status is not None:
            parts.append(f"WAL status: {slot.wal_status}")
        if slot.safe_wal_size is not None:
            parts.append(f"safe WAL size: {slot.safe_wal_size} bytes")
        return f"- {slot.slot_name} ({'; '.join(parts)})"

    async def _get_replication_metrics(self) -> ReplicationMetrics:
        """Get comprehensive replication metrics."""
        is_replica = await self._is_replica()
        replication_lag_seconds = await self._get_replication_lag()
        is_replicating = await self._is_replicating()
        slots = await self._get_replication_slots()
        slot_lag = await self._get_slot_lag(is_replica)
        for slot in slots:
            slot.lag_bytes = slot_lag.get(slot.slot_name)
        return ReplicationMetrics(
            is_replica=is_replica,
            replication_lag_seconds=replication_lag_seconds,
            is_replicating=is_replicating,
            replication_slots=slots,
        )

    async def _is_replica(self) -> bool:
        """Check if this database is a replica."""
        result = await self.sql_driver.execute_query("SELECT pg_is_in_recovery()")
        result_list = [dict(x.cells) for x in result] if result is not None else []
        return bool(result_list[0]["pg_is_in_recovery"]) if result_list else False

    async def _get_replication_lag(self) -> Optional[float]:
        """Get replication lag in seconds."""
        if not self._feature_supported("replication_lag"):
            return None

        # Use appropriate functions based on PostgreSQL version
        if await self._get_server_version() >= 100000:
            lag_condition = "pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn()"
        else:
            lag_condition = "pg_last_xlog_receive_location() = pg_last_xlog_replay_location()"

        try:
            result = await self.sql_driver.execute_query(f"""
                SELECT
                    CASE
                        WHEN NOT pg_is_in_recovery() OR {lag_condition} THEN 0
                        ELSE EXTRACT (EPOCH FROM NOW() - pg_last_xact_replay_timestamp())
                    END
                AS replication_lag
            """)
            result_list = [dict(x.cells) for x in result] if result is not None else []
            return float(result_list[0]["replication_lag"]) if result_list else None
        except Exception:
            self._feature_support["replication_lag"] = False
            return None

    async def _get_replication_slots(self) -> list[ReplicationSlot]:
        """Get information about replication slots."""
        if await self._get_server_version() < 90400 or not self._feature_supported("replication_slots"):
            return []

        # wal_status and safe_wal_size exist from PostgreSQL 13.
        status_columns = ", wal_status, safe_wal_size" if await self._get_server_version() >= 130000 else ""

        try:
            result = await self.sql_driver.execute_query(f"""
                SELECT
                    slot_name,
                    database,
                    active{status_columns}
                FROM pg_replication_slots
            """)
            if result is None:
                return []
            result_list = [dict(x.cells) for x in result]
            return [
                ReplicationSlot(
                    slot_name=row["slot_name"],
                    database=row["database"],
                    active=row["active"],
                    wal_status=row.get("wal_status"),
                    safe_wal_size=row.get("safe_wal_size"),
                )
                for row in result_list
            ]
        except Exception:
            self._feature_support["replication_slots"] = False
            return []

    async def _get_slot_lag(self, is_replica: bool) -> dict[str, int]:
        """Get the WAL each slot holds back, in bytes, by slot name.

        A separate query, so a server that refuses it still lists the slots. pg_current_wal_lsn cannot run
        during recovery, and a slot without restart_lsn (a lost slot) has no lag.
        """
        if is_replica or not self._feature_supported("slot_lag") or await self._get_server_version() < 100000:
            return {}

        try:
            result = await self.sql_driver.execute_query("""
                SELECT
                    slot_name,
                    pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes
                FROM pg_replication_slots
            """)
            result_list = [dict(x.cells) for x in result] if result is not None else []
            return {
                row["slot_name"]: int(row["lag_bytes"])
                for row in result_list
                if row["lag_bytes"] is not None
            }
        except Exception:
            self._feature_support["slot_lag"] = False
            return {}

    async def _is_replicating(self) -> bool:
        """Check if replication is active."""
        if not self._feature_supported("replicating"):
            return False

        try:
            result = await self.sql_driver.execute_query("SELECT state FROM pg_stat_replication")
            result_list = [dict(x.cells) for x in result] if result is not None else []
            return bool(result_list and len(result_list) > 0)
        except Exception:
            self._feature_support["replicating"] = False
            return False

    async def _get_server_version(self) -> int:
        """Get PostgreSQL server version as a number (e.g. 100000 for version 10.0)."""
        if self._server_version is None:
            result = await self.sql_driver.execute_query("SHOW server_version_num")
            result_list = [dict(x.cells) for x in result] if result is not None else []
            self._server_version = int(result_list[0]["server_version_num"]) if result_list else 0
        return self._server_version

    def _feature_supported(self, feature: str) -> bool:
        """Check if a feature is supported and cache the result."""
        return self._feature_support.get(feature, True)
