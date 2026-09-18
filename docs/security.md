# Security boundaries

ProjectReader Core provides a controlled repository mutation boundary. It is
not an operating-system sandbox and does not reduce the permissions of the
account that launches it.

## Enforced boundaries

- A repository root is selected explicitly by the process owner.
- Indexing skips known tool directories, binary files, and filesystem links.
- Search operates on registered index content.
- Inspection requires an opaque, repository-scoped reference issued by search.
- Patch paths are repository-relative and resolved inside the registered root.
- A patch contains complete replacement content; shell commands and edit
  programs are not accepted as patch input.
- Existing bytes and desired bytes are frozen before mutation.
- Failed apply operations are compensated when possible, with partial recovery
  reported explicitly.
- The MCP client cannot choose a verifier command. The command is bound at
  server startup by the process owner and runs as an argv sequence with
  `shell=False`.
- Acceptance requires the latest verification to pass.
- Expected caller errors use a closed typed-rejection contract. Unknown internal
  errors fail closed over MCP without returning a traceback.

## Trust and limitations

The process owner must trust the registered repository and verifier. Python
source is parsed but not executed during indexing; the verifier is executable
code and can have effects outside the repository transaction. ProjectReader
does not provide process isolation, network isolation, command sandboxing,
concurrent-edit locking, or a crash-durable recovery journal.

Never include secrets or private repository content in a public issue. Follow
the [security reporting policy](../SECURITY.md) for private vulnerability
reporting and what to do if the private channel is unavailable.
