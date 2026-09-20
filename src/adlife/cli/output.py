"""Machine-output helpers: one document on stdout, diagnostics on stderr.

Every command emits through this module so the global ``--format`` contract is enforced
in exactly one place: JSON modes print machine-readable documents and nothing else,
human mode may use light emphasis, and color obeys ``--no-color``, ``NO_COLOR`` and a
``dumb`` terminal regardless of anything else. Nothing here ever writes a warning to
stdout in a JSON mode, because a stray warning is a broken pipe waiting to happen.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from adlife.cli.errors import output_format


def color_enabled(no_color_flag: bool) -> bool:
    """Light emphasis is allowed only when every signal agrees to allow it."""
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return sys.stdout.isatty()


def emit_json(document: Any) -> None:
    """Print one JSON document to stdout, terminating the line."""
    print(json.dumps(document, ensure_ascii=False, sort_keys=False), flush=True)


def emit_jsonl(documents: Any) -> None:
    """Print an iterable of JSON documents, one per line."""
    for document in documents:
        print(json.dumps(document, ensure_ascii=False), flush=True)


def emit_result(document: Any, *, fmt: str | None = None) -> None:
    """Print a command result in the active mode: a document, or human prose.

    Human callers pass ``lines`` of prose; JSON callers pass the document.
    """
    mode = fmt or output_format()
    if mode == "json":
        emit_json(document)
    elif mode == "jsonl":
        emit_jsonl(document if isinstance(document, list) else [document])
    else:
        for line in document.get("_lines", []) if isinstance(document, dict) else [str(document)]:
            print(line, flush=True)


def info(message: str) -> None:
    """A diagnostic that must never corrupt a machine-readable stdout."""
    if output_format() in {"json", "jsonl"}:
        print(f"note: {message}", file=sys.stderr)
    else:
        print(message, file=sys.stderr)


def warn(message: str) -> None:
    """A warning; in JSON modes this is a stderr diagnostic by contract."""
    if output_format() in {"json", "jsonl"}:
        print(f"warning: {message}", file=sys.stderr)
    else:
        print(f"warning: {message}", file=sys.stderr)


__all__ = ["color_enabled", "emit_json", "emit_jsonl", "emit_result", "info", "warn"]
