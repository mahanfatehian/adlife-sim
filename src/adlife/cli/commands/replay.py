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
import re
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

import typer

from adlife.adapters.cognition.cache import CacheMiss, CognitionCache, CorruptCacheRecord
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.cognition import (
    RuleCognitionPort,
    rule_fallback_for,
)
from adlife.cli.errors import CommandError, ExitCode, command_boundary
from adlife.cli.output import emit_json, info
from adlife.core.domain.events import DomainEvent
from adlife.core.ports.cognition import CognitionAnswer, CognitionRequest, ProviderMetadata
from adlife.core.ports.run_store import StoredRun
from adlife.core.simulation.engine import AdLifeModel
from adlife.core.simulation.movement import TickOutcome, TickPlan
from adlife.core.simulation.runner import (
    InterruptedRun,
    RunCognitionPort,
    RunIdentity,
    SimulationRunner,
)


class RecordedCognitionPort:
    """Answer under the source request identity; never construct a network provider."""

    def __init__(self, stored: StoredRun, mode: str, cache_dir: Path) -> None:
        self._source_id = stored.manifest.run_id
        self._seed = stored.manifest.seed
        self._mode = mode
        self._cache = CognitionCache(cache_dir)
        self._fallbacks = {
            event.caused_by_event_ids[0]: event.payload["fallback_reason"]
            for event in stored.events
            if event.event_type == "cognition.fallback" and event.caused_by_event_ids
        }

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return None

    async def resolve(
        self, requests: Sequence[CognitionRequest], *, fallback_provider: object
    ) -> dict[str, CognitionAnswer]:
        from adlife.adapters.cognition.mock import MockCognitionProvider

        answers = {}
        for request in requests:
            source = request.model_copy(
                update={
                    "run_id": self._source_id,
                    "request_id": self._source_id + request.request_id[len(request.run_id) :],
                }
            )
            if self._mode == "mock":
                answer = await MockCognitionProvider(seed=self._seed).answer(source)
            elif source.request_id in self._fallbacks:
                from adlife.adapters.cognition.rules import RuleCognitionProvider

                assert isinstance(fallback_provider, RuleCognitionProvider)
                fallback = await fallback_provider.answer(request)
                answer = CognitionAnswer(
                    result=fallback.result,
                    usage=fallback.usage.model_copy(
                        update={
                            "provider_kind": "fallback",
                            "fallback_reason": self._fallbacks[source.request_id],
                        }
                    ),
                )
            else:
                answer = self._cached_answer(source)
            answers[request.request_id] = CognitionAnswer(
                result=answer.result.model_copy(update={"request_id": request.request_id}),
                usage=answer.usage,
            )
        return answers

    def _cached_answer(self, request: CognitionRequest) -> CognitionAnswer:
        import json

        matches = []
        for path in sorted(self._cache.directory.glob("*.json")):
            # Inspect only bounded records; unrelated cache entries do not affect this run.
            if path.stat().st_size > 1024 * 1024:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not isinstance(raw, dict) or not isinstance(raw.get("request"), dict):
                continue
            if raw["request"].get("request_id") != request.request_id:
                continue
            try:
                record = self._cache.get(path.stem)
            except ValueError:
                raise CorruptCacheRecord("cached replay record has an invalid key") from None
            if record is None or record.request != request:
                raise CorruptCacheRecord("cached replay record answers a different request")
            matches.append(record)
        if len(matches) != 1:
            if not matches:
                raise CacheMiss(request.request_id)
            raise CorruptCacheRecord("multiple cache records answer the same replay request")
        return CognitionAnswer(result=matches[0].result, usage=matches[0].usage)


def _port_for(stored: StoredRun, mode: str, cache_dir: Path) -> RunCognitionPort:
    if mode == "rules":
        return RuleCognitionPort()
    return RecordedCognitionPort(stored, mode, cache_dir)


def _stream(events: Sequence[DomainEvent], run_id: str) -> tuple[str, ...]:
    """Each event's canonical JSON with the run identifier normalized away.

    Event identifiers are derived from the run id, so a replay under a different run id
    cannot - and must not - reuse the original's identifiers. The comparison therefore
    masks the run id everywhere it appears (identifiers, causes, memory references)
    and compares everything else byte for byte.
    """
    from adlife.core.domain.serialization import canonical_json

    def normalize(value: object, key: str = "") -> object:
        if isinstance(value, dict):
            return {name: normalize(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [normalize(item, key) for item in value]
        if isinstance(value, str):
            if key == "run_id" and value == run_id:
                return "RUNID"
            if key.endswith(("_id", "_ids")) and re.fullmatch(
                re.escape(run_id) + r":event-[0-9]{8}(?::[a-z-]+)?", value
            ):
                return "RUNID" + value[len(run_id) :]
        return value

    documents = []
    for event in events:
        document = normalize(event.model_dump(mode="json"))
        assert isinstance(document, dict)
        documents.append(canonical_json(document))
    return tuple(documents)


@command_boundary
def command(
    project_path: Annotated[Path, typer.Argument(help="The study directory holding the run.")],
    run_id: Annotated[str, typer.Argument(help="The stored run identifier to replay.")],
    provider_mode: Annotated[
        Literal["rules", "hybrid", "mock"] | None,
        typer.Option(
            "--provider-mode",
            help="Override the recorded cognition mode (default: infer from the artifact).",
        ),
    ] = None,
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
    if stored.status not in {"completed", "interrupted"} or stored.result is None:
        raise CommandError(
            "replay requires a completed or interrupted run with a recorded terminal boundary",
            ExitCode.ARTIFACT_ERROR,
        )
    source_result = stored.result
    if source_result.final_minute % stored.scenario.tick_minutes != 0:
        raise CommandError("recorded stop is not on a tick boundary", ExitCode.ARTIFACT_ERROR)

    resolved_cache = cache_dir if cache_dir is not None else root / "cache"
    replay_id = f"replay-{run_id}"
    if len(replay_id) > 40:
        replay_id = f"replay-{run_id[:20]}-{sha256(run_id.encode()).hexdigest()[:12]}"
    if (root / "runs" / replay_id).exists():
        raise CommandError(
            f"replay destination {replay_id} already exists in {root / 'runs'}; "
            "the CLI refuses to overwrite run artifacts - replaying is deterministic, "
            "so the stored replay already holds this exact comparison",
            exit_code=ExitCode.CONFLICT,
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
    resolved_mode = provider_mode or (
        stored.manifest.provider if stored.manifest.provider in {"rules", "mock"} else "hybrid"
    )
    cognition = _port_for(stored, resolved_mode, resolved_cache)
    boundary_reached = False

    def cognition_factory(model: AdLifeModel) -> RunCognitionPort:
        nonlocal boundary_reached
        if source_result.status == "interrupted" and source_result.final_minute == 0:
            boundary_reached = True
            raise KeyboardInterrupt
        return cognition

    async def stop_at_recorded_boundary(
        plan: TickPlan, outcome: TickOutcome, model: AdLifeModel
    ) -> None:
        nonlocal boundary_reached
        if (
            source_result.status == "interrupted"
            and model.clock.current_minute == source_result.final_minute
        ):
            boundary_reached = True
            raise KeyboardInterrupt

    runner = SimulationRunner(
        cognition_factory=cognition_factory,
        fallback_provider_factory=rule_fallback_for,
        identity=identity,
    )
    from adlife.core.simulation.parameters import ModelParameters

    parameters = (
        ModelParameters.model_validate(dict(stored.manifest.parameters))
        if stored.manifest.parameters is not None
        else None
    )
    try:
        result = asyncio.run(
            runner.run(
                stored.scenario,
                seed=stored.manifest.seed,
                store=store,
                sinks=(),
                run_id=replay_id,
                provider_name="replay",
                model_id=stored.manifest.model_id,
                parameters=parameters,
                tick_observer=stop_at_recorded_boundary,
            )
        )
    except InterruptedRun as interrupted:
        if not boundary_reached:
            raise
        result = interrupted.result
    replayed = store.load_run(replay_id)

    identical = (
        _stream(replayed.events, replay_id) == _stream(stored.events, run_id)
        and result.status == source_result.status
        and result.final_minute == source_result.final_minute
    )
    document = {
        "run_id": run_id,
        "replay_run_id": replay_id,
        "replay-identical": identical,
        "event_count": len(replayed.events),
        "status": result.status,
        "final_minute": result.final_minute,
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
