# Standalone architecture

ProjectReader Core keeps repository authority separate from protocol clients.

```text
Python API / CLI / MCP
          |
    ProjectReader session
          |
 repository index + inspection registry
          |
 structured action and patch lifecycle
          |
 workspace transaction + trusted verifier
          |
 attribution + audit trail
```

The public `ProjectReader` facade is the small entry surface. Repository
internals may use navigation identities and bounded context selections, but a
caller does not need to understand internal navigation lifecycle states.

## Explicit exclusions

The standalone runtime contains no model-planning, dynamic-ranking,
evaluation-orchestration, or presentation-experiment state. These concerns are
outside the public package and are not imported at runtime.

## Authority boundaries

- The repository index discovers registered source.
- The inspection registry issues repository-scoped opaque references.
- The materializer returns bounded source for indexed targets.
- Navigation selects context but does not establish a root-cause claim.
- Patch attribution describes facts actually changed; it is not causal truth.
- The workspace transaction owns reversible filesystem mutation.
- The verifier runs only a human-configured command.
- The audit log records lifecycle facts without becoming model memory.

## Public, internal, and experimental

The package root exports only the documented facade and public value/error
types. Modules and names prefixed with `_` are internal. Experimental planning
or transport features are not part of the Core package.
