---
name: config
description: >
  Set up or change the database aliases dbwise offers to this session: add, list or remove an
  alias, a server or a credential, choose an alias's access level, import a team file or import
  from Vault, and export aliases as a team file. An alias has one of three levels, read (the
  default), diagnose and write, and the tools of a level run with that level's own database login.
  Passwords never pass through the chat: the operator types each one in a terminal, and dbwise keeps
  it in the OS keychain. Use when the operator asks to add or change a database, to give an alias
  more access, to import credentials, or to see which databases are configured.
argument-hint: "list | add <alias> | import <team file> | export"
allowed-tools:
  - Bash(dbwise *)
---

Arguments: `$ARGUMENTS` (empty means `list`).

dbwise keeps its configuration in `config.json` in its home directory (`$DBWISE_HOME`, default
`~/.dbwise`) and its passwords in the OS keychain. Run
the `dbwise` command through the Bash tool, always with `< /dev/null`. If `dbwise` is not found, run
`"${CLAUDE_PLUGIN_ROOT}/bin/dbwise"` with the same arguments.

## The three levels

An alias has the level it is allowed to reach, and a call never does more than that:

- `read`, the default: schemas, objects and SELECT or SHOW statements.
- `diagnose`: everything at `read`, plus EXPLAIN with ANALYZE, health checks, top queries and index
  advice. It can connect with a second login.
- `write`: everything at `diagnose`, plus any SQL, committed. It can connect with a third login.

Give an alias the lowest level the task needs. Ask the operator before you pick `diagnose` or
`write`. Each level can have its own credential, ideally its own database role, so one credential
can be revoked without the others: `--credential read=NAME --credential diagnose=NAME`, or a single
`--credential NAME` when one login serves every level up to the alias's level.

## What to run

- `list`: run `dbwise alias list`, `dbwise server list` and `dbwise credential list`, and tell the
  operator what each alias reaches and at which level.
- `add <alias>`: ask for the host, the databases and the database user. Then run, in this order,
  `dbwise server add NAME --host H --databases a,b`, `dbwise credential add NAME --user U` and
  `dbwise alias add ALIAS SERVER --level LEVEL --credential NAME`. Leave `--sslmode` at its default
  `require` unless the operator names another.
- `import <team file>`: run `dbwise import FILE`. A team file has the same format as the config and
  holds no password.
- `import` from Vault: the address in the team file's `vault.addr` receives the operator's Vault
  token, because the `vault` CLI sends it there. So a team file is read like code before its first
  `dbwise import FILE --from-vault`: read `vault.addr` to the operator and ask whether to go on.
  After a yes, run it; an OIDC login finishes in the operator's browser.
- `export`: run `dbwise export --aliases a,b --output PATH`.

## Passwords

Never ask for a password in the chat, put one in a command, a file you write, or a connection URL.
`dbwise credential add` without a source option leaves the credential without a stored password. The
operator types it in a terminal with `dbwise set-password NAME`; the command's output gives the line
to run on their system. A password may also come from an environment variable (`--password-env VAR`)
or a file (`--password-file PATH`) the operator prepared.

## After a change

The dbwise server reads `config.json` in the dbwise home directory (`$DBWISE_HOME`, default
`~/.dbwise`) and resolves every password when it starts. Tell the operator to reconnect
`plugin:dbwise:postgres` in `/mcp` or to start a new session, then run `/dbwise:doctor` to check
every alias. Until then the running tools still use the old configuration.
