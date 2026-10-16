# PostgreSQL server

This directory holds a modified copy of [postgres-mcp](https://github.com/crystaldba/postgres-mcp),
an MCP server for PostgreSQL that is published under the MIT licence. The licence text and copyright
line are in `LICENSE`, unchanged.

The copy is modified: dbwise starts the server over stdio only, passes it already resolved
connections, and applies its own access levels on top. The changes are summarized in
[the changelog](../../CHANGELOG.md).

Do not change the files here to fit another use; dbwise reads them as they are in the plugin.
