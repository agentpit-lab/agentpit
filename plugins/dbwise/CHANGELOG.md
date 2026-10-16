# Changelog

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
