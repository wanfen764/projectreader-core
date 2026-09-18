# ProjectReader Core

**Status: Alpha — 0.1.0a1**

- Author: Asagiri Nightrin
- Version: `0.1.0a1`
- License: Apache-2.0
- Repository: [wanfen764/projectreader-core](https://github.com/wanfen764/projectreader-core)

ProjectReader Core is a model-agnostic, controlled, and auditable
repository-engineering layer for AI coding agents.

## What it is

ProjectReader Core is a Python library, command-line interface, and standalone
MCP server for working with a local source repository through structured
operations. It indexes repository content, returns bounded source context,
applies complete-file changes through a reversible transaction, runs a trusted
verifier, and records an audit trail.

ProjectReader does not include a model. An MCP-compatible coding agent can use
it as a repository-control layer, while a Python program or human-operated CLI
can use the same Core directly.

## Why it exists

Coding agents often need more authority than read-only source browsing but less
authority than an unrestricted shell. ProjectReader provides a narrower
boundary: indexed inspection, structured actions, repository-scoped mutation,
operator-bound verification, and explicit accept or rollback.

It is a controlled repository mutation boundary, not a security sandbox.

## Features

- Repository registration and deterministic indexing
- Bounded lexical search over indexed source
- Precise inspection through repository-issued opaque references
- Optional bounded focus/context selection
- Strict structured action validation and typed rejections
- Transactional complete-file patch application
- Trusted verification using an operator-configured argv command
- Explicit accept and rollback lifecycle
- Factual changed-file and changed-symbol attribution
- Ordered, immutable audit events
- Scriptable JSON CLI
- Model- and provider-agnostic stdio MCP server

## Installation

ProjectReader Core requires Python 3.10 or newer.

Install a built wheel:

```console
python -m pip install projectreader_core-0.1.0a1-py3-none-any.whl
projectreader --help
```

For local development from this checkout:

```console
python -m pip install -e .
```

The package has no mandatory third-party runtime dependencies.

## Python Quickstart

The following read-only example runs against the bundled demonstration
repository from the project root.

<!-- BEGIN PYTHON QUICKSTART -->
```python
from projectreader import ProjectReader

reader = ProjectReader.open("examples/demo_repository")
summary = reader.index()
results = reader.search("greeting")
target = next(item for item in results if item.target_type == "symbol")
context = reader.inspect(target.inspection_ref)

print(summary["source_count"])
print(context.target_ref, context.mode)
```
<!-- END PYTHON QUICKSTART -->

The complete deterministic example also demonstrates patch, verification,
accept, rollback selection, and audit status:

```console
python examples/quickstart.py
```

A verifier is configured by the repository owner when the session is opened:

```python
import sys

from projectreader import ProjectReader

reader = ProjectReader.open(
    "path/to/repository",
    verifier=(sys.executable, "verify_project.py"),
)
reader.index()
patch = reader.patch(
    [{"path": "package/module.py", "content": "VALUE = 2\n"}],
    description="Replace the complete file",
)
verification = reader.verify(patch_id=patch.patch_id)
if verification.passed:
    reader.accept(patch_id=patch.patch_id)
else:
    reader.rollback(patch_id=patch.patch_id)
```

## CLI Quickstart

CLI commands emit JSON on stdout.

```console
projectreader --help
projectreader index examples/demo_repository
projectreader search examples/demo_repository greeting
projectreader inspect examples/demo_repository greeting
projectreader mcp --repo examples/demo_repository
```

`inspect` searches and inspects one exact indexed result in the same process.
Use `projectreader inspect --help` for result and segment selection options.

## MCP Quickstart

Start the standalone stdio server with an explicit repository root:

```console
projectreader-mcp --repo path/to/repository
```

or:

```console
projectreader mcp --repo path/to/repository
```

To enable verification, the process owner may bind one trusted command at
startup:

```console
projectreader-mcp --repo path/to/repository --verifier-command-json '["python", "-m", "unittest"]'
```

The MCP client cannot replace that command. Current tools are:

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

The consumer can be any compatible coding agent. ProjectReader does not
require a specific model, provider SDK, subscription, or credential.
See [the MCP contract](docs/mcp.md) for protocol details.

## Patch Lifecycle

1. `replace_files` validates repository-relative paths and freezes old/new
   bytes before mutation.
2. The workspace transaction applies complete-file replacements and compensates
   attempted operations if apply fails.
3. `verify_patch` runs only the verifier bound by the process owner.
4. A passing patch may be accepted; any applied patch may be rolled back.
5. Changed files and Python scopes are recorded as facts. They are not treated
   as an automatic root-cause claim.

Only one applied patch is active in a session at a time.

## Safety Model

- The repository root is explicit.
- Search and inspection operate on registered index content.
- Opaque inspection references cannot be used as arbitrary file paths or line
  ranges.
- Patch paths are checked before mutation and cannot escape the repository.
- Patches accept complete file contents, not shell commands or diff programs.
- Verifiers are exact argv sequences configured by the server operator and run
  with `shell=False`.
- Unknown internal errors fail closed at the MCP boundary.

This is not an operating-system sandbox. The process still has the permissions
of the account that launches it, and a configured verifier is trusted code.
See [Security boundaries](docs/security.md).

## Auditability

Each session maintains ordered audit events for repository opening, indexing,
search, inspection, focus, patch application, verification, acceptance,
rollback, and transaction failures. Callers receive copies and cannot rewrite
the in-memory event history.

The audit trail records observable actions and results. It does not claim to
capture a model's private reasoning or establish causal truth.

## Model Agnosticism

Repository indexing, inspection, patching, verification, and audit behavior are
ordinary deterministic Python services. The base package has no model SDK,
provider authentication, or hosted-service dependency.

MCP is one integration surface; the Python API and CLI are equally supported.

## Architecture

```text
Python API / CLI / MCP
          |
    ProjectReader session
          |
 repository index + inspection registry
          |
 structured actions + patch lifecycle
          |
 workspace transaction + trusted verifier
          |
 attribution + audit trail
```

See [Architecture](docs/architecture.md) for authority boundaries and public
surface details.

## Current Alpha Status

Version `0.1.0a1` is a functional standalone alpha. APIs and behavior may evolve
before a stable release. Teams evaluating production adoption should validate
the package against their own repository layout, verifier, permissions, and
failure-recovery workflow.

No claim is made in this release about improving model intelligence, coding
accuracy, task success, token usage, or latency.

## Known Limitations

- Python receives semantic symbol indexing; other supported text files use
  bounded text chunks.
- Indexes, patch records, and audit events are in memory for one process.
- Transactions use exception compensation, not filesystem-level atomicity.
- There is no crash-durable transaction journal or crash-resume reconciliation.
- Concurrent external repository mutation is not locked or reconciled.
- Side effects produced by verifier commands are outside the file transaction.
- The verifier command and repository permissions remain the operator's
  responsibility.
- This package is not a security sandbox and includes no built-in model.
- Platform validation covers the automated test suite, not every repository,
  filesystem, verifier command, or deployment environment.

## Development / Tests

The [pre-release GitHub Actions run](https://github.com/wanfen764/projectreader-core/actions/runs/35375547220)
passed all 12 combinations of Ubuntu, Windows, and macOS with Python 3.10–3.13
at commit `b4eadcb3022d15570220634463b7e6648fd4b99c`. Results are revision-specific;
check [Actions](https://github.com/wanfen764/projectreader-core/actions) for the
commit you intend to use.

Run the deterministic local suite:

```console
python -B -m unittest discover -s tests -v
```

Build distributions after installing the development extra:

```console
python -m pip install -e ".[dev]"
python -m build
```

The test suite requires no model credentials and makes no provider calls.

## License

ProjectReader Core is licensed under the Apache License 2.0. See
[LICENSE](LICENSE) for the complete, unmodified license text.

Copyright 2026 Asagiri Nightrin.

## Roadmap

Possible post-alpha work includes durable transaction recovery, detection of
concurrent external changes, persistent indexes, broader language-aware
indexing, and additional repository-control integrations. Roadmap items are not
commitments and are not part of the `0.1.0a1` contract.
