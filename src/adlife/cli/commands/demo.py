"""`adlife demo`: the credential-free, network-free guided tour.

The demo writes a packaged-resources study, runs twenty fictional agents for three
days on the mock provider (rules fallback), and opens the live dashboard when a real
terminal is available. Everything runs offline; the run's own artifact is the proof.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer

from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_json, info
from adlife.cli.project import Project
from adlife.core.domain.results import SimulationResult
from adlife.core.simulation.runner import RunIdentity, SimulationRunner, run_id_from


@command_boundary
def command(
    headless: Annotated[
        bool,
        typer.Option("--headless", help="Print a summary instead of opening the dashboard."),
    ] = False,
    dir: Annotated[
        Path | None,
        typer.Option("--dir", help="Where to create the demo study (default: a temp dir)."),
    ] = None,
    seed: Annotated[int, typer.Option("--seed", min=0, help="The demo run's seed.")] = 42,
) -> None:
    """Create a demo study, run it offline, and show or open the result."""
    study_root = dir if dir is not None else _temp_study_dir()
    if study_root.exists():
        raise CommandError(
            f"{study_root} already exists; the demo refuses to overwrite or merge "
            "into an existing directory"
        )

    from adlife.cli.project import init_project

    init_project(study_root)
    info(f"demo study created at {study_root}")

    from adlife.cli.cognition import MockCognitionPort, rule_fallback_for
    from adlife.cli.project import load_project

    project = load_project(study_root, days=3, population_size=20, seed=seed)
    run_id = f"demo-{run_id_from(project.scenario, seed)}"
    runner = SimulationRunner(
        cognition_factory=lambda *args, **kwargs: MockCognitionPort(seed=seed),
        fallback_provider_factory=rule_fallback_for,
        identity=_identity(project),
    )

    dashboard_wanted = not headless and _terminal_available()
    if dashboard_wanted:
        from adlife.cli.commands.run import _run_live

        result = asyncio.run(
            _run_live(
                runner,
                project,
                seed,
                run_id,
                provider_name="mock",
                model_id=project.config.provider.model,
            )
        )
    else:
        result = asyncio.run(_drive(runner, project, seed, run_id))

    artifact = project.root / "runs" / result.run_id
    document = {
        "study": str(study_root),
        "run_id": result.run_id,
        "status": result.status,
        "final_minute": result.final_minute,
        "event_count": result.event_count,
        "artifact": str(artifact),
        "offline": True,
    }
    if output_format() == "json":
        emit_json(document)
    else:
        info(
            f"demo run {result.run_id}: {result.status} at minute {result.final_minute}, "
            f"{result.event_count} events"
        )
        info(f"artifacts: {artifact}")
        info("everything above was produced offline; no credentials were used")


def _identity(project: Project) -> RunIdentity:
    return RunIdentity.for_project(
        project_root=project.root,
        provider="mock",
        model_id=project.config.provider.model,
    )


def _terminal_available() -> bool:
    import os

    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


async def _drive(
    runner: SimulationRunner,
    project: Project,
    seed: int,
    run_id: str,
) -> SimulationResult:
    store = SQLiteRunStore(project.root)
    return await runner.run(
        project.scenario,
        seed=seed,
        store=store,
        sinks=(),
        run_id=run_id,
        provider_name="mock",
        model_id=project.config.provider.model,
    )


def _temp_study_dir() -> Path:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="adlife-demo-"))
    return root / "demo-study"


__all__ = ["command"]
