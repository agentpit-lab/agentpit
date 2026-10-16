# dbWise

dbWise is a Claude Code plugin that gives a session MCP tools for databases, starting with
PostgreSQL. Each database alias in your configuration has an access level, `read` (the default),
`diagnose` or `write`, and a call never does more than its alias's level allows. Each level can
connect with its own database login, so the logins can be granted and revoked apart. At `read` and
`diagnose` the server also refuses a database role that holds more than its level allows.

## Install

```
/plugin install dbwise@agentpit
```

Requirements:

- Python 3.12 to 3.14 on the `PATH` of the environment Claude Code starts in.
- Access to PyPI on the first start: the plugin builds its own Python environment from a
  hash-locked requirements file, in `~/.dbwise/venvs`. Native Windows ARM64 Python is refused.
- An OS keychain for passwords, or an environment variable or a file that holds each password.
- Optional: the `vault` CLI, for `dbwise import --from-vault`.

## First run

Start a session. The first start builds the Python environment, and until it is ready the plugin
offers one tool, `dbwise_status`, which says how far the build is. Then reconnect
`plugin:dbwise:postgres` in `/mcp`, or start a new session.

Add a database with `/dbwise:config`, or run these commands (the `dbwise` command is on the `PATH` of
the agent's Bash tool only):

```
dbwise server add main --host db.example.com --databases app
dbwise credential add main-read --user dbwise_read
dbwise set-password main-read
dbwise alias add main main --level read --credential main-read
```

`dbwise set-password` asks for the password in a terminal; a password never goes through the chat.
`dbwise credential add` prints the exact command line to run there. On Windows use PowerShell or cmd,
since Git Bash cannot hide typed input.
After a change, reconnect `plugin:dbwise:postgres` in `/mcp` and run `/dbwise:doctor`, which checks
every alias and names the command that fixes what it finds. A team can share its aliases in a file
without passwords; see [`examples/team.example.json`](examples/team.example.json) and
`dbwise import`.

## Contents

- [Getting started](docs/getting-started.html): install, one alias at read, raising it to diagnose,
  and what each level may run. Online:
  [getting started](https://agentpit-lab.github.io/agentpit/plugins/dbwise/docs/getting-started.html).
- [How-to guides](docs/how-to.md): add an alias, give an agent diagnose with its own role, import
  credentials from Vault, restrict a project to some aliases, create the database roles.
  Online page:
  [how-to guides](https://agentpit-lab.github.io/agentpit/plugins/dbwise/docs/how-to.html).
- [Explanation](docs/explanation.md): how each level is enforced and what its SQL policy admits.
  Online page:
  [explanation](https://agentpit-lab.github.io/agentpit/plugins/dbwise/docs/explanation.html).
- [Reference](docs/reference.md): commands, tools and their levels, the configuration format, files,
  environment variables, exit codes, the role gate's refusal reasons, Vault.
  Online page:
  [reference](https://agentpit-lab.github.io/agentpit/plugins/dbwise/docs/reference.html).
- [Troubleshooting](docs/troubleshooting.md): by symptom, with cause, check and fix.
  Online page:
  [troubleshooting](https://agentpit-lab.github.io/agentpit/plugins/dbwise/docs/troubleshooting.html).
- [Limitations](docs/limitations.md): what the plugin cannot do, and what still works.
  Online page:
  [limitations](https://agentpit-lab.github.io/agentpit/plugins/dbwise/docs/limitations.html).
- [Changelog](CHANGELOG.md).
- Skills: `/dbwise:config` sets up and changes aliases, `/dbwise:doctor` finds why one does not work.
