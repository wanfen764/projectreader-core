"""Console launcher for the standalone ProjectReader MCP server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .server import serve_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="projectreader-mcp",
        description="Serve a repository through the model-agnostic ProjectReader Core MCP protocol.",
    )
    parser.add_argument("--repo", required=True, type=Path, help="Repository root to open.")
    parser.add_argument(
        "--verifier-command-json",
        metavar="JSON_ARRAY",
        help=(
            "Trusted verifier argv configured by the human operator, encoded as a "
            "JSON array of strings. MCP clients cannot override this command."
        ),
    )
    return parser


def parse_verifier(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--verifier-command-json must be valid JSON") from exc
    if not isinstance(parsed, list) or not parsed or any(not isinstance(item, str) or not item for item in parsed):
        raise ValueError("--verifier-command-json must be a non-empty array of non-empty strings")
    return tuple(parsed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        verifier = parse_verifier(args.verifier_command_json)
        serve_repository(
            args.repo,
            verifier=verifier,
            input_stream=sys.stdin.buffer,
            output_stream=sys.stdout.buffer,
        )
    except (OSError, ValueError) as exc:
        # Fixed sentinel plus exception kind is useful to process supervisors;
        # paths and arbitrary exception text are intentionally not emitted.
        print(f"projectreader MCP server error: {type(exc).__name__}", file=sys.stderr)
        return 2
    except Exception:
        print("projectreader MCP server error: internal_error", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
