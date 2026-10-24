# Changelog

## 0.1.4 - 2026-10-25

### Fixed

- The first start builds the server's environment again. The lock pinned pglast 7.11, which PyPI no
  longer serves, so every new environment failed to install; it now pins pglast 8.7. The relock also
  moves certifi, more-itertools, pycparser, pydantic-settings, sse-starlette and starlette by patch or
  minor releases.
- The refusal on native Windows ARM64 Python names `psycopg-binary` as the package without a wheel;
  pglast 8.7 publishes one.
- `explain_query` replaces each `$n` parameter with a sample value from the column it is compared
  with. With several parameters, a parameter could take the column of another one, depending on the
  server process, so `... = $1 AND note LIKE $2` could put a number into the `LIKE` pattern and be
  refused.

### Changed

- SQL is parsed with the PostgreSQL 18 grammar. A statement that only PostgreSQL 18 accepts passes
  the parse and is refused by an older server; the limitations page says so.

## 0.1.3 - 2026-10-20

### Changed

- The `alias` parameter of every tool says it is required; a call without it is refused and the
  reply names the available aliases.
- The descriptions of `write_sql`, `read_sql`, `list_schemas`, `list_objects` and `get_object_details`
  name the tool's limits (no SQL policy and no time limit at `write`; the SQL policy and the
  30-second limit at `read`), the neighbouring tool to use instead, and the reply format.

## 0.1.2 - 2026-10-19

### Added

- The `diagnose` level admits `pg_current_wal_lsn` and `pg_wal_lsn_diff`, so an agent can read the
  WAL a replication slot holds back with `diagnose_sql`. The `read` level does not.
- The replication health check prints, for each slot, the WAL it holds back, its `wal_status` and its
  `safe_wal_size`.

## 0.1.1 - 2026-10-16

### Added

- The documentation pages are available in Ukrainian in `docs/uk/`.
- Every documentation page has an En/Uk switch that opens the page in the other language.
- The En/Uk switch remembers the reader's choice, and the first visit follows the browser's language.

### Changed

- The documentation pages mark product and command names as untranslated, so a translation
  tool leaves them as written.

## 0.1.0 - 2026-10-16

First release.

- Three access levels per alias, `read` (the default), `diagnose` and `write`, each of which can
  have its own credential; passwords in the OS keychain, an environment variable or a file; team
  files and import from Vault; `dbwise doctor` and the skills `/dbwise:config` and `/dbwise:doctor`;
  a role check that refuses a `read` or `diagnose` alias whose database role holds more than the
  level allows.
- The first start builds the plugin's own Python environment (Python 3.12 to 3.14, packages from PyPI
  checked by hash); until it is ready, the server offers one tool, `dbwise_status`, which says how far
  the build is. Native Windows ARM64 Python is not supported.
- The how-to guides, reference, explanation, troubleshooting and limitations are pages in `docs/`
  beside the getting-started page, readable from a local install without the network; `docs/*.md` is
  generated from them.
- Edits to the PostgreSQL server this plugin is based on (an MIT-licensed project, named in
  `servers/postgres/README.md`):
  - A policy driver for each level: `read` and `diagnose` statements are parsed, prefixed with
    `/* dbwise <level> */`, run in a read-only transaction with a 30 second timeout; `write` prefixes
    `/* dbwise write */` and runs the SQL as written with its own connection.
  - A connection returned to the pool is reset with `DISCARD ALL`, the hypothetical and hidden
    indexes of `hypopg` are cleared with its own functions, and the string-syntax setting is pinned
    again.
  - `prepare_threshold` is turned off, because `DISCARD ALL` drops the server-side prepared
    statements that psycopg would otherwise still believe it holds.
  - The sequence health check is rewritten: it follows ownership, serial defaults and defaults that
    name a sequence, caps a sequence at the range of its column's type, skips partitions, and lists
    sequences the role cannot read as not checked.
