"""`adlife report PROJECT RUN_ID`: render a stored run as a self-contained file.

The report is a pure adapter over the artifact: the store is the only source, the
fold is the same :class:`~adlife.core.experiments.metrics.MetricsCalculator` the
experiments use, and the output is one HTML file that renders identically with the
network disconnected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_json, info


@command_boundary
def command(
    project_path: Annotated[Path, typer.Argument(help="The study directory holding the run.")],
    run_id: Annotated[str, typer.Argument(help="The stored run identifier to report on.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Where to write the HTML (default: reports/<run-id>.html)."
        ),
    ] = None,
) -> None:
    """Write one self-contained HTML report for a stored run."""
    from adlife.adapters.storage.sqlite_store import SQLiteRunStore
    from adlife.reporting.html import render_report

    root = project_path.resolve()
    if not root.is_dir():
        raise CommandError(f"project directory {root} does not exist")
    store = SQLiteRunStore(root)
    stored = store.load_run(run_id)
    usage = store.load_provider_usage(run_id)

    destination = output if output is not None else root / "reports" / f"{run_id}.html"
    path = render_report(stored, destination, usage=usage)

    if output_format() in {"json", "jsonl"}:
        emit_json({"report": str(path), "run_id": run_id})
    else:
        info(f"report written to {path}")


__all__ = ["command"]
