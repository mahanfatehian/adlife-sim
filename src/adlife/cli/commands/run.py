"""`adlife run PROJECT`: the whole workflow, from a study directory to an artifact."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.cognition import (
    MockCognitionPort,
    ReplayCognitionPort,
    RuleCognitionPort,
    ServiceCognitionPort,
    cache_directory,
    rule_fallback_for,
)
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_json, info
from adlife.cli.project import Project, load_project
from adlife.core.domain.events import DomainEvent
from adlife.core.domain.results import SimulationResult
from adlife.core.ports.cognition import CognitionProvider, ProviderMetadata, SamplingSettings
from adlife.core.ports.event_sink import EventSink
from adlife.core.ports.run_store import StorageError
from adlife.core.simulation.runner import InterruptedRun, RunIdentity, SimulationRunner

RUN_ID_PATTERN_HELP = "[a-z0-9][a-z0-9-]{0,39}"


class StdoutJsonlSink:
    """Stream committed events to stdout as JSONL, in the runner's persist order."""

    def append_many(self, run_id: str, events: Sequence[DomainEvent]) -> None:
        from adlife.cli.output import emit_jsonl

        del run_id
        emit_jsonl(event.model_dump(mode="json") for event in events)


def _validate_run_id(run_id: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", run_id) is None:
        raise CommandError(
            f"run id {run_id!r} must match {RUN_ID_PATTERN_HELP}",
        )
    return run_id


def _sinks(fmt: str) -> tuple[EventSink, ...]:
    return (StdoutJsonlSink(),) if fmt == "jsonl" else ()


def _identity(project: Project, mode: str, model_id: str) -> RunIdentity:
    return RunIdentity.for_project(
        project_root=project.root,
        provider="rules" if mode == "rules" else "mock",
        model_id=model_id,
    )


def _cognition_seams(
    project: Project,
    mode: str,
    seed: int,
    cache_dir: Path,
) -> tuple[Any, Callable[[Any], object]]:
    """Wire the cognition port and the fallback factory for the chosen mode."""
    provider = project.config.provider

    if mode == "rules":
        return RuleCognitionPort(), rule_fallback_for

    if mode == "replay":
        metadata = _replay_metadata(provider.model)
        return ReplayCognitionPort(CognitionCache(cache_dir), metadata), rule_fallback_for

    # hybrid: the configured provider behind the real service.
    if provider.mode == "mock":
        return MockCognitionPort(seed=seed), rule_fallback_for

    from adlife.adapters.cognition.openai_compatible import (
        OpenAICompatibleProvider,
        resolve_api_key,
    )
    from adlife.adapters.cognition.service import CognitionBudget, CognitionService
    from adlife.core.simulation.rng import RandomOracle

    provider_object = OpenAICompatibleProvider(
        base_url=str(provider.base_url),
        model=provider.model,
        api_key=resolve_api_key(provider.mode),
        timeout_seconds=provider.timeout_seconds,
    )

    def service_factory(fallback_provider: CognitionProvider) -> CognitionService:
        return CognitionService(
            fallback=fallback_provider,
            budget=CognitionBudget(
                per_agent=project.config.simulation.max_cognition_per_agent,
                total=project.config.simulation.max_cognition_total,
            ),
            oracle=RandomOracle(seed),
            cache=CognitionCache(cache_dir),
            retries=provider.retries,
        )

    return ServiceCognitionPort(provider_object, service_factory), rule_fallback_for


def _replay_metadata(model_id: str) -> ProviderMetadata:
    return ProviderMetadata(
        kind="replay",
        model_id=model_id,
        sampling=SamplingSettings(),
        prompt_sha256="d" * 64,
    )


def _interrupted_result(root: Path, run_id: str) -> SimulationResult:
    """The recorded result of an interrupted run, for the InterruptedRun envelope."""
    from adlife.adapters.storage.sqlite_store import SQLiteRunStore as _Store

    stored = _Store(root).load_run(run_id)
    if stored.result is None:
        raise StorageError(f"run {run_id} stopped without recording its result")
    return stored.result


def _terminal_available() -> bool:
    """A live dashboard is only possible on an interactive terminal."""
    import os

    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


async def _run_live(
    runner: SimulationRunner,
    project: Project,
    seed: int,
    run_id: str,
) -> SimulationResult:
    """Drive one run beneath the Textual dashboard; the artifact is identical."""
    from adlife.tui.app import AdLifeTui
    from adlife.tui.controller import LiveRunController
    from adlife.tui.event_bus import TuiEventBus

    bus = TuiEventBus()
    controller = LiveRunController(
        runner_factory=lambda: runner,
        scenario=project.scenario,
        seed=seed,
        store=SQLiteRunStore(project.root),
        run_id=run_id,
        event_bus=bus,
    )
    app = AdLifeTui(controller, bus)
    await app.run_async()
    if controller.failure is not None:
        raise controller.failure
    if controller.result is not None:
        return controller.result
    # A quit via q/ctrl-c cancelled the task; the runner recorded the interrupted
    # artifact before the cancellation surfaced here.
    raise InterruptedRun(_interrupted_result(project.root, run_id))


@command_boundary
def command(
    project_path: Annotated[Path, typer.Argument(help="The study directory to run.")],
    campaign: Annotated[
        list[Path] | None,
        typer.Option(
            "--campaign",
            help="Replace the project's campaigns with these project-relative YAML files.",
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help=f"Run identifier, {RUN_ID_PATTERN_HELP}."),
    ] = None,
    mode: Annotated[
        Literal["rules", "hybrid", "replay"],
        typer.Option("--mode", help="The cognition mode."),
    ] = "rules",
    days: Annotated[
        int | None, typer.Option("--days", min=1, max=7, help="Override the run's days.")
    ] = None,
    population_size: Annotated[
        int | None,
        typer.Option("--population-size", min=1, max=30, help="Override the population size."),
    ] = None,
    seed: Annotated[
        int | None, typer.Option("--seed", min=0, help="Override the run seed.")
    ] = None,
    live: Annotated[
        bool, typer.Option("--live", help="Run the live Textual dashboard (Task 15).")
    ] = False,
    headless: Annotated[
        bool, typer.Option("--headless", help="Run without any terminal dashboard (the default).")
    ] = False,
    no_headless_fallback: Annotated[
        bool,
        typer.Option(
            "--no-headless-fallback",
            help="Refuse instead of falling back to headless when no terminal is available.",
        ),
    ] = False,
    cache_dir: Annotated[
        Path | None,
        typer.Option("--cache-dir", help="The cognition cache directory for hybrid/replay modes."),
    ] = None,
) -> None:
    """Run one study from its first tick to run.completed, persisting every artifact."""
    project = load_project(project_path, days=days, population_size=population_size, seed=seed)
    resolved_seed = seed if seed is not None else project.config.simulation.seed
    resolved_run_id = _validate_run_id(run_id) if run_id is not None else None

    if resolved_run_id is None:
        from adlife.core.simulation.runner import run_id_from as _default_run_id

        resolved_run_id = _default_run_id(project.scenario, resolved_seed)
    runs_dir = project.root / "runs"
    if (runs_dir / resolved_run_id).exists():
        raise CommandError(
            f"run {resolved_run_id} already exists in {runs_dir}; "
            "the CLI refuses to overwrite run artifacts",
        )

    if campaign is not None:
        project = _with_campaigns(project, [Path(item) for item in campaign])

    resolved_cache = cache_dir if cache_dir is not None else cache_directory(project.root)
    fmt = output_format()
    if live and fmt == "jsonl":
        raise CommandError(
            "--live drives the terminal dashboard and cannot also stream jsonl events "
            "on stdout; run without --format jsonl or without --live",
        )
    dashboard_requested = live and not headless and _terminal_available()
    if live and not dashboard_requested and not headless:
        if no_headless_fallback:
            raise CommandError(
                "--live needs an interactive terminal and --no-headless-fallback "
                "refuses the headless fallback",
            )
        info("the live dashboard requires an interactive terminal; running headless instead")

    cognition, fallback = _cognition_seams(project, mode, resolved_seed, resolved_cache)
    identity = _identity(project, mode, project.config.provider.model)
    from adlife.core.simulation.runner import SimulationRunner

    runner = SimulationRunner(
        cognition_factory=lambda model: cognition,
        fallback_provider_factory=fallback,
        identity=identity,
    )
    store = SQLiteRunStore(project.root)

    if dashboard_requested:
        result = asyncio.run(_run_live(runner, project, resolved_seed, resolved_run_id))
    else:
        sinks = _sinks(fmt)
        result = asyncio.run(
            runner.run(
                project.scenario,
                seed=resolved_seed,
                store=store,
                sinks=sinks,
                run_id=resolved_run_id,
                provider_name="rules" if mode == "rules" else "mock",
                model_id=project.config.provider.model,
            )
        )

    document = {
        "run_id": result.run_id,
        "status": result.status,
        "final_minute": result.final_minute,
        "event_count": result.event_count,
        "artifact": str(project.root / "runs" / result.run_id),
    }
    if fmt == "jsonl":
        # The stream itself is the output: the last event is the run's own run.completed.
        info(
            f"run {result.run_id}: {result.status} at minute {result.final_minute}, "
            f"{result.event_count} events, artifacts in {project.root / 'runs' / result.run_id}"
        )
    elif fmt == "json":
        emit_json(document)
    else:
        info(
            f"run {result.run_id}: {result.status} at minute {result.final_minute}, "
            f"{result.event_count} events, artifacts in {project.root / 'runs' / result.run_id}"
        )


def _with_campaigns(project: Project, campaign_paths: list[Path]) -> Project:
    from adlife.cli.project import _load_campaign_file

    campaigns = []
    for path in campaign_paths:
        resolved = path if path.is_absolute() else project.root / path
        if not resolved.is_file():
            raise CommandError(f"campaign file {path} does not exist in the project")
        campaigns.append(_load_campaign_file(resolved))
    scenario = project.scenario.model_copy(update={"campaigns": tuple(campaigns)})
    return Project(root=project.root, config=project.config, scenario=scenario)


__all__ = ["StdoutJsonlSink", "command"]
