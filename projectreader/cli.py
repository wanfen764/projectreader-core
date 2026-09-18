"""Scriptable command-line interface for ProjectReader Core."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


def _version() -> str:
    try:
        return version("projectreader-core")
    except PackageNotFoundError:
        return "0.1.0a1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="projectreader",
        description="Index, inspect, and safely patch a local source repository.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    index_parser = subcommands.add_parser("index", help="Build the repository index.")
    index_parser.add_argument("repo", type=Path)

    search_parser = subcommands.add_parser("search", help="Search indexed source.")
    search_parser.add_argument("repo", type=Path)
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)

    inspect_parser = subcommands.add_parser(
        "inspect",
        help="Search for a target and return bounded source for one exact result.",
    )
    inspect_parser.add_argument("repo", type=Path)
    inspect_parser.add_argument("target", help="Search query used to locate the inspection target.")
    inspect_parser.add_argument("--match", type=int, default=0, help="Zero-based search result to inspect.")
    inspect_parser.add_argument("--limit", type=int, default=10)
    inspect_parser.add_argument(
        "--segment",
        type=int,
        help="Optional one-based segment from a split or chunked target.",
    )

    mcp_parser = subcommands.add_parser("mcp", help="Start the model-agnostic stdio MCP server.")
    mcp_parser.add_argument("--repo", required=True, type=Path)
    mcp_parser.add_argument(
        "--verifier-command-json",
        metavar="JSON_ARRAY",
        help="Trusted verifier argv as a JSON array; MCP clients cannot override it.",
    )
    return parser


def _open(repo: Path):
    from projectreader import ProjectReader

    return ProjectReader.open(repo)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            output = _open(args.repo).index()
        elif args.command == "search":
            if not 1 <= args.limit <= 10:
                parser.error("--limit must be between 1 and 10")
            reader = _open(args.repo)
            reader.index()
            output = reader.search(args.query, limit=args.limit)
        elif args.command == "inspect":
            if not 1 <= args.limit <= 10:
                parser.error("--limit must be between 1 and 10")
            if args.match < 0 or args.match >= args.limit:
                parser.error("--match must select a result within --limit")
            reader = _open(args.repo)
            reader.index()
            results = reader.search(args.target, limit=args.limit)
            if args.match >= len(results):
                raise ValueError("the requested search result does not exist")
            ref = _field(results[args.match], "inspection_ref")
            if args.segment is not None and args.segment < 1:
                parser.error("--segment must be a positive integer")
            output = reader.inspect(ref, segment_index=args.segment)
        elif args.command == "mcp":
            from projectreader.mcp.__main__ import parse_verifier
            from projectreader.mcp import serve_repository

            verifier = parse_verifier(args.verifier_command_json)
            serve_repository(
                args.repo,
                verifier=verifier,
                input_stream=sys.stdin.buffer,
                output_stream=sys.stdout.buffer,
            )
            return 0
        else:  # pragma: no cover - argparse enforces the command set.
            parser.error("unknown command")
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, separators=(",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(_json_value(output), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value[name]
    return getattr(value, name)


def _json_value(value: Any) -> Any:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _json_value(value.as_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported CLI result value: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
