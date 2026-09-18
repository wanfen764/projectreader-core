# MCP contract

`projectreader-mcp --repo <path>` opens one repository-scoped Core session and
serves strict UTF-8 JSON-RPC 2.0 messages over stdin/stdout. Logs and startup
diagnostics go to stderr and never contaminate protocol stdout.

The server supports MCP initialize, `notifications/initialized`, ping,
`tools/list`, and `tools/call`. Its tools are:

- `repository_info`
- `index_repository`
- `search_repository`
- `inspect_repository_target`
- `focus_repository_target`
- `replace_files`
- `verify_patch`
- `accept_patch`
- `rollback_patch`
- `get_audit_log`

`search_repository` returns an opaque `inspection_ref` for every indexed hit.
Only those registered references can be passed to inspect or focus. The server
does not expose arbitrary file reads.

`replace_files` accepts complete file contents or `null` for deletion. It does
not accept diff syntax, edit hunks, or shell commands. `verify_patch` accepts no
command argument: the operator can bind one trusted verifier while starting the
server.

Tool results include equivalent JSON text and structured content for MCP client
compatibility. No claim is made that every client includes both representations
in a model context.
