---
name: doctor
description: >
  Find out why a dbwise database alias does not work, and name the command that fixes it. It checks
  the OS keychain, the plugin's Python environment, and each alias at its level (read, the default,
  diagnose or write): host, user, password source, network reach, login, each exposed database, and
  for aliases below write the database role's privileges, in each exposed database and in the
  server's discovery database. Use when a database tool is missing, shows
  only dbwise_status, an alias is unreachable or refused, or the operator
  asks whether dbwise is set up correctly.
argument-hint: "[alias]"
allowed-tools:
  - Bash(dbwise *)
---

Arguments: `$ARGUMENTS` (empty means every alias).

Run `dbwise doctor < /dev/null`, or `dbwise doctor --alias ALIAS < /dev/null` for one alias,
through the Bash tool. If `dbwise` is not found, run `"${CLAUDE_PLUGIN_ROOT}/bin/dbwise"` with the
same arguments. The command prints no password and opens no write path; it ends with one `VERDICT:`
line, `ready` or the list of problems, and exits 0 only when ready.

Then tell the operator, in a few lines:

- the verdict and, for each problem, the fix the output names, as printed;
- for a role the role gate refuses, each reason it lists: they name the grant or membership to
  remove on the database side, which only a database administrator can change.

## When the doctor is not the first step

- Only `dbwise_status` is offered: the plugin is still building its Python environment, or the
  build failed. Call `dbwise_status` and relay its answer as written; the build log is
  `bootstrap.log` in `~/.dbwise` (or `$DBWISE_HOME`). After the build, reconnect
  `plugin:dbwise:postgres` in `/mcp` or start a new session.
- A tool answers with an error that names why the alias could not be opened at start-up (no usable
  `read` credential, a timeout, a refused role). Call `list_databases` for the `error` of the alias,
  then run the doctor for it. If the cause was the network, the `reconnect` tool
  probes the alias again; after any change to the configuration or a password, reconnect
  `plugin:dbwise:postgres` in `/mcp` or start a new session.
- A call is refused for its level: the alias's level is lower than the tool's. Raising it is the
  operator's choice; use `/dbwise:config`.

The operator types passwords in a terminal with `dbwise set-password NAME`; never ask for one in the
chat.
