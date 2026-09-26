"""`adlife replay PROJECT RUN_ID`: re-execute a stored run, byte-compare the stream.

A stored run is a reproducibility claim, and replay is the audit: the recorded
scenario and manifest are the only inputs, the run is re-executed under the recorded
mode, and the fresh event stream must equal the stored one event for event. Identical
confirms the artifact; divergence and corruption are artifact errors, because a
difference between what was recorded and what the recorded inputs produce is exactly
what "corrupt" means here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

import typer

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.cognition import (
    MockCognitionPort,
    ReplayCognitionPort,
    RuleCognitionPort,
    rule_fallback_for,
)
from adlife.cli.errors import CommandError, ExitCode, command_boundary
from adlife.cli.output import emit_json, info
from adlife.core.domain.events import DomainEvent
from adlife.core.ports.run_store import StoredRun
from adlife.core.simulation.runner import RunCognitionPort, RunIdentity, SimulationRunner


def _port_for(stored: StoredRun, mode: str, cache_dir: Path) -> RunCognitionPort:
    """Wire the replay's cognition port from the recorded manifest and mode."""
    from adlife.core.ports.cognition import ProviderMetadata, SamplingSettings

    if mode == "hybrid":
        metadata = ProviderMetadata(
            kind="replay",
            model_id=stored.manifest.model_id,
            sampling=SamplingSettings(),
            prompt_sha256="d" * 64,
        )
        return ReplayCognitionPort(CognitionCache(cache_dir), metadata)
    if mode == "mock":
        return MockCognitionPort(seed=stored.manifest.seed)
    return RuleCognitionPort()


def _stream(events: Sequence[DomainEvent], run_id: str) -> tuple[str, ...]:
    """Each event's canonical JSON with the run identifier normalized away.

    Event identifiers are derived from the run id, so a replay under a different run id
    cannot - and must not - reuse the original's identifiers. The comparison therefore
    masks the run id everywhere it appears (identifiers, causes, memory references)
    and compares everything else byte for byte.
    """
    from adlife.core.domain.serialization import canonical_json

    return tuple(canonical_json(event).replace(run_id, "RUNID") for event in events)


@command_boundary
def command(
    project_path: Annotated[Path, typer.Argument(help="The study directory holding the run.")],
    run_id: Annotated[str, typer.Argument(help="The stored run identifier to replay.")],
    provider_mode: Annotated[
        Literal["rules", "hybrid", "mock"],
        typer.Option("--provider-mode", help="How the replay answers cognition requests."),
    ] = "rules",
    cache_dir: Annotated[
        Path | None,
        typer.Option("--cache-dir", help="The cognition cache directory for hybrid replay."),
    ] = None,
) -> None:
    """Re-execute a stored run from its recorded inputs and verify the stream."""

    root = project_path.resolve()
    if not root.is_dir():
        raise CommandError(f"project directory {root} does not exist")
    store = SQLiteRunStore(root)
    stored = store.load_run(run_id)  # corrupted artifacts raise StorageError -> exit 4

    resolved_cache = cache_dir if cache_dir is not None else root / "cache"
    replay_id = f"replay-{run_id}"[:40]
    if (root / "runs" / replay_id).exists():
        raise CommandError(
            f"replay destination {replay_id} already exists in {root / 'runs'}; "
            "the CLI refuses to overwrite run artifacts - replaying is deterministic, "
            "so the stored replay already holds this exact comparison",
            exit_code=ExitCode.PROVIDER_ERROR,
        )

    identity = RunIdentity.for_project(
        project_root=root,
        provider="replay",
        model_id=stored.manifest.model_id,
        overrides={
            "package_version": stored.manifest.package_version,
            "git_sha": stored.manifest.git_sha,
            "lockfile_sha256": stored.manifest.lockfile_sha256,
            "platform": stored.manifest.platform,
            "prompt_version": stored.manifest.prompt_version,
            "prompt_hash": stored.manifest.prompt_hash,
        },
    )
    cognition = _port_for(stored, provider_mode, resolved_cache)
    runner = SimulationRunner(
        cognition_factory=lambda model: cognition,
        fallback_provider_factory=rule_fallback_for,
        identity=identity,
    )
    result = asyncio.run(
        runner.run(
            stored.scenario,
            seed=stored.manifest.seed,
            store=store,
            sinks=(),
            run_id=replay_id,
            provider_name="replay",
            model_id=stored.manifest.model_id,
        )
    )
    replayed = store.load_run(replay_id)

    identical = (
        _stream(replayed.events, replay_id) == _stream(stored.events, run_id)
        and result.status == "completed"
    )
    document = {
        "run_id": run_id,
        "replay_run_id": replay_id,
        "replay-identical": identical,
        "event_count": len(replayed.events),
    }
    if not identical:
        document["error"] = {
            "exit_code": 4,
            "type": "ReplayDiverged",
            "message": f"replaying {run_id} produced a different event stream",
        }
        emit_json(document)
        info(f"replay of {run_id} diverged; the artifact does not reproduce its inputs")
        raise SystemExit(4)
    emit_json(document)
