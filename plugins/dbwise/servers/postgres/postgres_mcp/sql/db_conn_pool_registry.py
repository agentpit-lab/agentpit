"""Registry of connection pools keyed by (alias, level, database).

``server.run`` registers every configured alias through :meth:`DbConnPoolRegistry.register_environments`
(an "environment" here is an alias). Registration is non-fatal: every alias is probed in parallel with
a short timeout and the outcome is recorded in an availability map, so an unreachable alias never
aborts start-up. A replica is just another alias; nothing here depends on the kind of server.
"""

import asyncio
import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse
from urllib.parse import urlunparse

from .sql_driver import GATED_LEVELS
from .sql_driver import ROLE_CHECKS
from .sql_driver import DbConnPool
from .sql_driver import RoleGateError
from .sql_driver import obfuscate_password

logger = logging.getLogger(__name__)

# Environment key used when a pool is requested without an alias; a registered alias never uses this name.
DEFAULT_ENV = "default"

# Per-alias probe timeout. Kept short so one unreachable alias (VPN/PG down) cannot stall start-up.
PROBE_TIMEOUT_SECONDS = 5.0

LEVELS = ("read", "diagnose", "write")
# The level of an alias whose spec names none.
DEFAULT_ALIAS_LEVEL = "read"


@dataclass
class EnvAvailability:
    """Per-environment reachability snapshot used to build the availability map."""

    reachable: bool
    dbs_ok: List[str] = field(default_factory=list)
    dbs_missing: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # Reasons of the role gate: the cause of an unreachable alias under enforce, warnings under warn.
    role_check: str = "enforce"
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reachable": self.reachable,
            "dbs_ok": list(self.dbs_ok),
            "dbs_missing": list(self.dbs_missing),
            "error": self.error,
            "role_check": self.role_check,
            "reasons": list(self.reasons),
        }


class DatabaseValidationError(Exception):
    """Raised when a tool targets an unknown / missing database_name or environment."""

    def __init__(self, message: str, available_databases: Optional[List[str]] = None):
        super().__init__(message)
        self.available_databases = available_databases or []


class DbConnPoolRegistry:
    """Holds one DbConnPool per (environment, level, database)."""

    def __init__(self) -> None:
        self._pools: Dict[Tuple[str, str, str], DbConnPool] = {}
        self._locks: Dict[Tuple[str, str, str], asyncio.Lock] = {}
        # Valid databases per environment, in registration order.
        self._databases: Dict[str, List[str]] = {}
        # Per-environment level of the alias and mode of its role gate.
        self._alias_levels: Dict[str, str] = {}
        self._role_checks: Dict[str, str] = {}
        # Gate reasons found per environment while probing and opening pools.
        self._reasons: Dict[str, List[str]] = {}
        # Per-alias connection URL of each level and the reason a level has none.
        self._dsns: Dict[str, Dict[str, str]] = {}
        self._problems: Dict[str, Dict[str, str]] = {}
        self._discovery_dbs: Dict[str, str] = {}
        # Databases requested per environment (needed to re-probe on reconnect).
        self._requested_dbs: Dict[str, List[str]] = {}
        # Per-alias availability snapshot.
        self._availability: Dict[str, EnvAvailability] = {}
        self._mode: str = "single"  # "single" until register_environments ran, then "multi"
        self._multi_env: bool = False  # True once register_environments ran

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        """Return "single" before aliases are registered and "multi" after."""
        return self._mode

    @property
    def multi_env(self) -> bool:
        """True once aliases were registered."""
        return self._multi_env

    def get_names(self) -> List[str]:
        """Database names registered under the default environment."""
        return self.get_names_for_env(DEFAULT_ENV)

    def get_names_for_env(self, environment: str) -> List[str]:
        """Database names registered under a given environment, in registration order."""
        return list(self._databases.get(environment, []))

    def get_environments(self) -> List[str]:
        """All registered aliases, in registration order."""
        return list(self._availability.keys())

    def is_registered(self, name: str, environment: str = DEFAULT_ENV) -> bool:
        """True if ``name`` is a registered database of ``environment``."""
        return name in self._databases.get(environment, [])

    def is_open(self, name: str, environment: str = DEFAULT_ENV, level: Optional[str] = None) -> bool:
        """True if a pool of (environment, name) exists AND has been opened (lazy pools start closed).

        Without ``level`` a pool of any level counts.
        """
        return any(
            pool.is_valid
            for (env, pool_level, db), pool in self._pools.items()
            if env == environment and db == name and level in (None, pool_level)
        )

    def availability_map(self) -> Dict[str, Dict[str, Any]]:
        """Return the per-environment availability map (JSON-serializable)."""
        return {env: snap.to_dict() for env, snap in self._availability.items()}

    # ------------------------------------------------------------------ #
    # Registration (server.run)
    # ------------------------------------------------------------------ #
    async def register_environments(self, connections: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Non-fatally probe & register every environment, returning the availability map.

        ``connections`` maps ``alias -> {"dsns": {level: url}, "databases": [str, ...]}`` with the
        optional ``"problems"`` (``{level: reason}`` for a level without a URL), ``"level"`` of the
        alias (``read``, ``diagnose`` or ``write``; absent means ``read``) and ``"role_check"``
        (``enforce`` or ``warn``; absent means ``enforce``). ``dsns`` is required: the pool of each
        level takes the URL of that level, and the ``read`` URL serves the probe.
        Each alias is probed in parallel with a short timeout; an unreachable alias is recorded as
        ``reachable=False`` with a masked reason and never aborts start-up.

        This method NEVER raises: every per-alias failure (unreachable host, probe timeout, or a
        malformed spec such as missing ``dsns`` or an invalid ``level``) is captured in the returned
        availability map. This is the guarantee ``server.run`` relies on to always start the server.
        """
        self._multi_env = True
        self._mode = "multi"

        async def probe_one(env: str, spec: Mapping[str, Any]) -> Tuple[str, EnvAvailability]:
            # NON-FATAL contract: a malformed or incomplete spec for ONE environment must be
            # recorded as unreachable, never raise — otherwise one bad entry would abort
            # startup for every environment. Every field access lives INSIDE the try, so a
            # missing "dsns" or an invalid level becomes a recorded reason rather than an escaping
            # exception.
            databases: List[str] = []
            try:
                databases = list(spec.get("databases") or [])
                level = spec.get("level", DEFAULT_ALIAS_LEVEL)
                role_check = spec.get("role_check", "enforce")
                if level not in LEVELS:
                    raise ValueError(f"Invalid level {level!r} for environment '{env}'; levels are {', '.join(LEVELS)}")
                if role_check not in ROLE_CHECKS:
                    raise ValueError(f"Invalid role_check {role_check!r} for environment '{env}'; values are {', '.join(ROLE_CHECKS)}")
                self._alias_levels[env] = level
                self._role_checks[env] = role_check
                dsns = spec.get("dsns")
                problems = dict(spec.get("problems") or {})
                if not isinstance(dsns, Mapping):
                    return env, EnvAvailability(
                        reachable=False,
                        dbs_ok=[],
                        dbs_missing=list(databases),
                        error=f"Invalid connection spec for alias '{env}': 'dsns' is missing",
                        role_check=role_check,
                    )
                base_dsn = dsns.get("read")
                if not isinstance(base_dsn, str) or not base_dsn:
                    return env, EnvAvailability(
                        reachable=False,
                        dbs_ok=[],
                        dbs_missing=list(databases),
                        error=f"Alias '{env}' has no usable connection at level 'read': {problems.get('read', 'no read credential')}",
                        role_check=role_check,
                    )
                self._dsns[env] = dict(dsns)
                self._problems[env] = problems
                self._discovery_dbs[env] = self.discovery_dbname(base_dsn)
                self._requested_dbs[env] = databases
                self._reasons[env] = []
                registered, missing = await asyncio.wait_for(
                    self._probe_env(env, base_dsn, databases),
                    timeout=PROBE_TIMEOUT_SECONDS,
                )
                return env, EnvAvailability(
                    reachable=True,
                    dbs_ok=registered,
                    dbs_missing=missing,
                    error=None,
                    role_check=role_check,
                    reasons=list(self._reasons[env]),
                )
            except RoleGateError as e:
                self._note_reasons(env, e.reasons)
                return env, EnvAvailability(
                    reachable=False,
                    dbs_ok=[],
                    dbs_missing=list(databases),
                    error=self._refusal_message(env, e.reasons),
                    role_check=self._role_checks.get(env, "enforce"),
                    reasons=list(self._reasons.get(env, [])),
                )
            except asyncio.TimeoutError:
                return env, EnvAvailability(
                    reachable=False,
                    dbs_ok=[],
                    dbs_missing=list(databases),
                    error=f"Timed out after {PROBE_TIMEOUT_SECONDS:g}s connecting to alias '{env}'",
                )
            except Exception as e:  # noqa: BLE001 - non-fatal by design
                return env, EnvAvailability(
                    reachable=False,
                    dbs_ok=[],
                    dbs_missing=list(databases),
                    error=obfuscate_password(str(e)) or "unexpected error during environment registration",
                )

        # return_exceptions=True is defense-in-depth: probe_one is written never to raise,
        # but if a future change regresses that, fold the failure into the map instead of
        # letting it propagate out of run and crash the whole server.
        items = list(connections.items())
        results = await asyncio.gather(
            *(probe_one(env, spec) for env, spec in items),
            return_exceptions=True,
        )
        # Assign in the caller's environment order (deterministic map ordering).
        for (env, _spec), res in zip(items, results):
            if isinstance(res, BaseException):
                self._availability[env] = EnvAvailability(
                    reachable=False,
                    dbs_ok=[],
                    dbs_missing=[],
                    error=obfuscate_password(str(res)) or "unexpected error during environment registration",
                )
            else:
                res_env, snap = res
                self._availability[res_env] = snap
        return self.availability_map()

    async def reconnect_all(self, environment: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Re-probe environments and rebuild their pools, returning the full availability map.

        By default only environments currently marked unreachable are re-probed, so healthy
        environments — and any in-flight queries against their pools — are left completely
        untouched (no blast radius on unrelated environments). Pass ``environment`` to force a
        re-probe of one specific environment regardless of its current state. Environments that
        are not re-probed keep their existing pools and availability entry. Non-fatal, like
        :meth:`register_environments`.
        """
        known = self.get_environments()
        if environment is not None:
            candidates = [environment] if environment in known else []
        else:
            candidates = [env for env, snap in self._availability.items() if not snap.reachable]
        # Skip aliases that never registered their DSNs (a malformed spec): there is
        # nothing to reconnect to, and their availability entry is left as-is.
        targets = [env for env in candidates if env in self._dsns]

        for env in targets:
            await self._close_env(env)  # closes ONLY this env's pools; healthy envs untouched
            self._availability.pop(env, None)

        if targets:
            connections = {
                env: {
                    "dsns": self._dsns[env],
                    "problems": self._problems.get(env, {}),
                    "databases": self._requested_dbs.get(env, []),
                    "level": self._alias_levels.get(env, DEFAULT_ALIAS_LEVEL),
                    "role_check": self._role_checks.get(env, "enforce"),
                }
                for env in targets
            }
            await self.register_environments(connections)
        return self.availability_map()

    def alias_level(self, alias: Optional[str]) -> str:
        """The configured level of ``alias``; a missing or unknown alias raises with the list of aliases."""
        if not alias:
            raise DatabaseValidationError(
                f"alias is required. Available aliases: {', '.join(self.get_environments())}. "
                f"Call list_databases for the current list.",
            )
        if alias not in self._availability:
            raise DatabaseValidationError(
                f"Unknown alias '{alias}'. Available aliases: {', '.join(self.get_environments())}. "
                f"Call list_databases for the current list.",
            )
        level = self._alias_levels.get(alias)
        if level is None:
            raise DatabaseValidationError(f"Alias '{alias}' is unusable: {self._availability[alias].error}")
        return level

    def configured_level(self, alias: str) -> Optional[str]:
        """The level recorded for ``alias``; ``None`` when its spec named an invalid level."""
        return self._alias_levels.get(alias)

    def _level_url(self, env: str, level: str, database_name: str) -> str:
        """The URL for a pool of ``level``: that level's own credential, never another level's."""
        dsns = self._dsns.get(env, {})
        if level not in dsns:
            reason = self._problems.get(env, {}).get(level, "no credential is configured")
            raise DatabaseValidationError(f"Alias '{env}' has no usable connection at level '{level}': {reason}")
        return self.build_db_url(database_name, dsns[level])

    async def _close_env(self, environment: str) -> None:
        """Close and drop every pool/lock registered under a single environment."""
        keys = [key for key in list(self._pools) if key[0] == environment]
        for key in keys:
            pool = self._pools.pop(key, None)
            self._locks.pop(key, None)
            if pool is not None:
                await pool.close()
        self._databases.pop(environment, None)
        self._reasons.pop(environment, None)

    # ------------------------------------------------------------------ #
    # Pool access
    # ------------------------------------------------------------------ #
    async def get_pool(self, database_name: Optional[str], environment: Optional[str] = None, level: Optional[str] = None) -> DbConnPool:
        """Return the (lazily opened) pool for (environment, level, database_name).

        Once aliases are registered ``environment`` is REQUIRED; before that it defaults to
        :data:`DEFAULT_ENV`. ``level`` is the level of the call the
        pool serves and defaults to the level of the alias. Opening a pool of a ``read`` or
        ``diagnose`` alias runs the role gate.
        """
        if self._multi_env:
            if not environment:
                raise DatabaseValidationError(
                    f"alias is required. Available aliases: {', '.join(self.get_environments())}. "
                    f"Call list_databases for the current list.",
                )
            env = environment
            if env not in self._availability:
                raise DatabaseValidationError(
                    f"Unknown alias '{env}'. Available aliases: {', '.join(self.get_environments())}. "
                    f"Call list_databases for the current list.",
                )
            snapshot = self._availability[env]
            if not snapshot.reachable:
                raise DatabaseValidationError(snapshot.error or self._refusal_message(env, snapshot.reasons))
        else:
            env = environment if environment is not None else DEFAULT_ENV

        pool_level = level if level is not None else self._alias_levels.get(env, DEFAULT_ALIAS_LEVEL)
        if pool_level not in LEVELS:
            raise DatabaseValidationError(f"Unknown level '{pool_level}'. Levels are {', '.join(LEVELS)}.")
        available = self.get_names_for_env(env)
        if not database_name:
            raise DatabaseValidationError(
                f"database_name is required. Available databases: {', '.join(available)}. Call list_databases for the current list.",
                available_databases=available,
            )
        if database_name not in available:
            raise DatabaseValidationError(
                f"Unknown database '{database_name}'. Available databases: {', '.join(available)}. Call list_databases for the current list.",
                available_databases=available,
            )
        key = (env, pool_level, database_name)
        pool = self._pools.get(key)
        if pool is None:
            pool = self._new_pool(env, pool_level, self._level_url(env, pool_level, database_name))
            self._pools[key] = pool
            self._locks[key] = asyncio.Lock()
        # Serialize concurrent first-calls so two coroutines can't each open a pool.
        async with self._locks[key]:
            try:
                await pool.pool_connect()  # idempotent: returns existing pool if valid, else opens
            except DatabaseValidationError:
                raise
            except RoleGateError as e:
                await self._mark_unreachable(env, e.reasons)
                raise DatabaseValidationError(self._refusal_message(env, e.reasons), available_databases=available) from e
            except Exception as e:
                raise DatabaseValidationError(
                    f"Could not open connection to database '{database_name}': {obfuscate_password(str(e))}",
                    available_databases=available,
                ) from e
        if pool.gate_reasons and env in self._availability:
            self._note_reasons(env, pool.gate_reasons)
            self._availability[env].reasons = list(self._reasons[env])
        return pool

    async def close_all(self) -> None:
        """Close every registered pool (used on shutdown / before a reconnect rebuild)."""
        for pool in self._pools.values():
            await pool.close()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _probe_env(self, environment: str, base_url: str, database_names: List[str]) -> Tuple[List[str], List[str]]:
        """Open a discovery pool for ``environment``, validate ``database_names`` against
        pg_database, and register the valid ones lazily. Returns ``(registered, missing)``.

        Raises DatabaseValidationError if the discovery database is unreachable. Callers on
        the non-fatal path wrap this and record the failure in the availability map.
        """
        discovery_db = self.discovery_dbname(base_url)
        discovery_url = self.build_db_url(discovery_db, base_url)
        discovery = self._new_pool(environment, "read", discovery_url)
        try:
            try:
                pool = await discovery.pool_connect()  # opens discovery pool only
                self._note_reasons(environment, discovery.gate_reasons)
            except RoleGateError:
                raise
            except Exception as e:
                masked_url = obfuscate_password(discovery_url)
                raise DatabaseValidationError(
                    f"Could not connect to discovery database '{discovery_db}' at {masked_url}: {obfuscate_password(str(e))}"
                ) from e
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT datname FROM pg_database WHERE datname = ANY(%s) AND datallowconn = true",
                        [database_names],
                    )
                    valid = {row[0] for row in await cur.fetchall()}
        finally:
            await discovery.close()  # discovery pool not retained

        registered = [n for n in database_names if n in valid]
        missing = [n for n in database_names if n not in valid]
        for name in registered:  # lazy: store, do not open
            self._register(environment, name)
        return registered, missing

    def _register(self, environment: str, name: str) -> None:
        """Record a valid database; its pools are created on the first call of each level."""
        names = self._databases.setdefault(environment, [])
        if name not in names:
            names.append(name)

    def _new_pool(self, environment: str, level: str, url: str) -> DbConnPool:
        gate_level = self._alias_levels.get(environment)
        return DbConnPool(
            url,
            level=level,
            gate_level=gate_level if gate_level in GATED_LEVELS else None,
            role_check=self._role_checks.get(environment, "enforce"),
        )

    def _note_reasons(self, environment: str, reasons: List[str]) -> None:
        """Add gate reasons to the environment's list, keeping order and dropping repeats."""
        known = self._reasons.setdefault(environment, [])
        known.extend(reason for reason in reasons if reason not in known)

    def _refusal_message(self, environment: str, reasons: List[str]) -> str:
        return f"Access refused by the role check of alias '{environment}': {'; '.join(reasons)}"

    async def _mark_unreachable(self, environment: str, reasons: List[str]) -> None:
        """Record an alias as unreachable after a pool open found reasons to refuse its role.

        The alias's other open pools close, so no model SQL runs on a role the gate refused.
        """
        self._note_reasons(environment, reasons)
        before = self._availability.get(environment)
        self._availability[environment] = EnvAvailability(
            reachable=False,
            dbs_ok=[],
            dbs_missing=list(before.dbs_missing) if before else [],
            error=self._refusal_message(environment, self._reasons[environment]),
            role_check=self._role_checks.get(environment, "enforce"),
            reasons=list(self._reasons[environment]),
        )
        for key, pool in list(self._pools.items()):
            if key[0] == environment:
                await pool.close()

    def build_db_url(self, dbname: str, base_url: str) -> str:
        """Swap the path (dbname) on a base URL, preserving credentials/host/query."""
        parsed = urlparse(base_url)
        return urlunparse(parsed._replace(path=f"/{dbname}"))

    def discovery_dbname(self, base_url: str) -> str:
        parsed = urlparse(base_url)
        return parsed.path.lstrip("/") or "postgres"
