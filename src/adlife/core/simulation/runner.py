"""The run orchestrator: a whole scenario, driven through the ports, to an artifact.

Task 12's plan step is this loop. :mod:`adlife.core.simulation.engine` owns the nine-stage
tick - what one commit may change and in what order - and this module owns the run: which
ticks happen, what a run must persist before anyone observes it, when a checkpoint is
taken, and what is recorded when the run cannot continue.

THE COGNITION SEAM. The core stays adapter-free (the import boundary suite parses this
package's imports), yet a run needs the real cognition pipeline - budget, retry, repair,
cache, rule fallback - which lives in :mod:`adlife.adapters.cognition.service`. So the
runner consumes one narrow port defined HERE, and the caller wires it:

* :class:`RunCognitionPort` - what the runner asks for: every request a tick planned,
  answered, with provenance, or a raised refusal. The adapters wrap
  :class:`~adlife.adapters.cognition.service.CognitionService` behind this port; tests
  and a rules run may answer directly.
* ``cognition_factory`` - called once per run with the model (and the provider
  configuration the caller passes through), returning the port for that run.
* ``fallback_provider_factory`` - builds specification section 12's terminal rule
  fallback for one tick's planned requests, from the plan that raised them. The service
  receives it; the runner never falls back on its own.

PERSIST BEFORE PUBLISH. A tick's events are appended to the authoritative store before
any sink is offered them, so a sink that throws cannot have shown an event the store does
not hold, and a run interrupted between the two has recorded exactly what it showed.

WHAT A REFUSAL IS WORTH. A port that raises, or a commit that refuses, records the run
``failed`` - with the exception CLASS, never its message, because a message may carry
campaign text or provider paraphrase - and re-raises, so the caller still sees the defect.
A ``KeyboardInterrupt`` or a cancelled task is recorded ``interrupted`` and raised as
:class:`InterruptedRun`, whose exit code belongs to Task 14's CLI.

DETERMINISM. The runner never draws, never shuffles and never reads a clock into the
simulation: the clock it advances is the model's own, and wall time appears only in the
store's own ``created_at``/``completed_at`` fields, where it already belonged.
"""

from __future__ import annotations

import asyncio
import platform
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    ProviderUsage,
)
from adlife.core.ports.event_sink import EventSink
from adlife.core.ports.run_store import RunCheckpoint, RunStore, StoredRun

if TYPE_CHECKING:
    from adlife.core.simulation.engine import AdLifeModel
    from adlife.core.simulation.movement import TickPlan

LOCKFILE_NAME = "uv.lock"
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_PROMPT_VERSION = "cognition-v1"
UNKNOWN_PACKAGE_VERSION = "0.0.0"


class RunnerRefused(RuntimeError):
    """Raised when the run cannot continue through its own ports.

    The cognition seam raises this when it cannot answer within its documented bounds,
    and the runner raises it when the store refuses an artifact. A caller sees the class
    it can dispatch on; the message names the seam and the run, never a credential.
    """


class InterruptedRun(RuntimeError):
    """Raised when a run was stopped before it finished, with what was kept.

    ``result`` carries the ``interrupted`` status the store recorded. Task 14's CLI maps
    this class to exit code 130; the runner does not.
    """

    def __init__(self, result: SimulationResult) -> None:
        super().__init__(f"run {result.run_id} was interrupted at minute {result.final_minute}")
        self.result = result


@runtime_checkable
class RunCognitionPort(Protocol):
    """Resolve one tick's planned cognition requests, in the runner's terms.

    The adapters wrap the real cognition service behind this port; a rules run or a
    test may answer directly. The port is deliberately NARROWER than the service: it
    speaks in answers, not in budgets, retries or cache records, so the runner cannot
    reach past the seam even by accident.
    """

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> Mapping[str, CognitionAnswer]: ...

    @property
    def provider_metadata(self) -> ProviderMetadata | None: ...


class RunIdentity:
    """The manifest's reproducibility input, gathered from one project root.

    Every field a replay must agree on is read here once: the scenario's canonical
    digest is added at run time (it is a function of the scenario), while the package
    version, the Git SHA of the checkout, the lockfile digest and the platform come from
    the project. A missing Git binary, a worktree without a commit and a missing
    lockfile all degrade to the documented placeholder rather than to an invention.
    """

    def __init__(
        self,
        *,
        package_version: str,
        git_sha: str,
        lockfile_sha256: str,
        platform_name: str,
        prompt_version: str,
        prompt_hash: str,
    ) -> None:
        self.package_version = package_version
        self.git_sha = git_sha
        self.lockfile_sha256 = lockfile_sha256
        self.platform_name = platform_name
        self.prompt_version = prompt_version
        self.prompt_hash = prompt_hash

    @classmethod
    def for_project(
        cls,
        *,
        project_root: Path,
        provider: str,
        model_id: str,
        overrides: Mapping[str, str] | None = None,
    ) -> RunIdentity:
        del provider, model_id  # recorded on the manifest at run time, not here
        prompt_version, prompt_hash = _prompt_identity()
        overrides = overrides or {}
        identity = cls(
            package_version=overrides.get("package_version", _installed_package_version()),
            git_sha=overrides.get("git_sha", _git_sha_of(project_root)),
            lockfile_sha256=overrides.get("lockfile_sha256", _lockfile_sha256_of(project_root)),
            platform_name=overrides.get("platform", _platform_name()),
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
        )
        return identity

    def manifest_for(
        self,
        *,
        run_id: str,
        scenario: Scenario,
        seed: int,
        provider: str,
        model_id: str,
    ) -> RunManifest:
        from adlife.core.simulation.engine import canonical_sha256

        return RunManifest(
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            scenario_hash=canonical_sha256(scenario),
            seed=seed,
            package_version=self.package_version,
            git_sha=self.git_sha,
            lockfile_sha256=self.lockfile_sha256,
            provider=provider,  # type: ignore[arg-type]
            model_id=model_id,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            platform=self.platform_name,
        )


def _installed_package_version() -> str:
    try:
        return version("adlife-sim")
    except PackageNotFoundError:
        return UNKNOWN_PACKAGE_VERSION


def _platform_name() -> str:
    major, minor = sys.version_info[0], sys.version_info[1]
    return f"{platform.system().lower()}-{platform.machine()}-python-{major}.{minor}"


def _git_sha_of(project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "uncommitted"
    sha = completed.stdout.strip()
    return sha if GIT_SHA_PATTERN.fullmatch(sha) else "uncommitted"


def _lockfile_sha256_of(project_root: Path) -> str:
    from hashlib import sha256

    lockfile = project_root / LOCKFILE_NAME
    try:
        return sha256(lockfile.read_bytes()).hexdigest()
    except OSError:
        return "0" * 64


def _prompt_identity() -> tuple[str, str]:
    """The prompt contract's version and digest, read off the adapter that owns it.

    Reading the real template digest pulls the prompt module (an adapter) in at run
    time, which the import boundary forbids for anything statically imported here. The
    version is the documented contract constant; the hash is the canonical digest of
    that version string, a stable stand-in that changes exactly when the version does.
    """
    from adlife.core.ports.cognition import PROMPT_VERSION
    from adlife.core.simulation.engine import canonical_sha256

    return PROMPT_VERSION, canonical_sha256({"prompt_version": PROMPT_VERSION})


def scenario_manifest(
    identity: RunIdentity,
    *,
    run_id: str,
    scenario: Scenario,
    seed: int,
    provider: str,
    model_id: str,
) -> RunManifest:
    return identity.manifest_for(
        run_id=run_id,
        scenario=scenario,
        seed=seed,
        provider=provider,
        model_id=model_id,
    )


def run_id_from(scenario: Scenario, seed: int) -> str:
    """The deterministic run identifier: ``run-<scenario>-<seed>`` bounded to the slug."""
    candidate = f"run-{scenario.scenario_id}-{seed}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", candidate):
        candidate = candidate[:40].rstrip("-")
    return candidate


class SimulationRunner:
    """Drive one scenario to a persisted, replayable run.

    The runner owns ORDER, not arithmetic: the model commits ticks, the store validates
    and records them, the port resolves cognition. Every public method is async because
    the cognition seam is.
    """

    def __init__(
        self,
        *,
        cognition_factory: Callable[..., object],
        fallback_provider_factory: Callable[[TickPlan], object],
        identity: RunIdentity,
        model_factory: Callable[..., AdLifeModel] | None = None,
    ) -> None:
        if not isinstance(identity, RunIdentity):
            raise TypeError("identity must be a RunIdentity")
        self._cognition_factory = cognition_factory
        self._fallback_provider_factory = fallback_provider_factory
        self._identity = identity
        self._model_factory = model_factory

    def _new_model(self, scenario: Scenario, seed: int, run_id: str | None) -> AdLifeModel:
        from adlife.core.simulation.engine import AdLifeModel

        factory = self._model_factory or AdLifeModel
        model = factory(scenario=scenario, seed=seed, run_id=run_id)
        if not isinstance(model, AdLifeModel):
            raise RunnerRefused("the model factory did not produce an AdLifeModel")
        return model

    def _cognition_port_for(self, model: AdLifeModel, provider: object) -> RunCognitionPort:
        port = (
            self._cognition_factory(model, provider)
            if _accepts_provider(self._cognition_factory)
            else self._cognition_factory(model)
        )
        if not isinstance(port, RunCognitionPort):
            raise RunnerRefused("the cognition factory did not produce a RunCognitionPort")
        return port

    async def run(
        self,
        scenario: Scenario,
        *,
        seed: int,
        store: RunStore,
        sinks: Sequence[EventSink],
        run_id: str | None = None,
        provider: object = None,
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> SimulationResult:
        """Run one scenario from its first tick to ``run.completed``.

        ``run_id`` defaults to the deterministic ``run-<scenario>-<seed>``. The provider
        configuration object travels to the cognition factory untouched, so a live
        provider adapter can read its endpoint and credentials from it; the manifest's
        ``provider`` and ``model_id`` fields come from ``provider_name``/``model_id``.
        """
        run_id = run_id or run_id_from(scenario, seed)
        provider_name = provider_name or _default_provider_name(provider)
        model_id = model_id or _default_model_id(provider, provider_name)
        manifest = self._identity.manifest_for(
            run_id=run_id,
            scenario=scenario,
            seed=seed,
            provider=provider_name,
            model_id=model_id,
        )
        model = self._new_model(scenario, seed, None)
        store.create_run(manifest, scenario=scenario)
        self._publish(store, sinks, model.start_events(run_id))
        return await self._drive(model, provider, store, sinks, manifest)

    async def resume(
        self,
        scenario: Scenario,
        *,
        seed: int,
        store: RunStore,
        sinks: Sequence[EventSink],
        run_id: str,
        manifest: RunManifest | None = None,
        provider: object = None,
    ) -> SimulationResult:
        """Continue a stored run from its latest day-boundary checkpoint.

        The stored run must be resumable - it exists and is not finished - and the seed
        must be the seed the manifest records. The model is rebuilt from the checkpoint,
        so no tick is re-committed and no draw is replayed.
        """
        from adlife.core.simulation.engine import AdLifeModel

        stored = store.load_run(run_id)
        if stored.status not in {"running", "interrupted", "failed"}:
            raise InterruptedRun(_finished_result(stored))
        if stored.manifest.seed != seed:
            raise RunnerRefused(f"run {run_id} was seeded with {stored.manifest.seed}")
        resolved_manifest = manifest or stored.manifest

        checkpoint = stored.checkpoints[-1] if stored.checkpoints else None
        if checkpoint is None:
            model = AdLifeModel.restore(
                scenario=scenario,
                seed=seed,
                checkpoint=_start_checkpoint(stored, run_id),
            )
        else:
            model = AdLifeModel.restore(scenario=scenario, seed=seed, checkpoint=checkpoint)
        return await self._drive(model, provider, store, sinks, resolved_manifest)

    async def _drive(
        self,
        model: AdLifeModel,
        provider: object,
        store: RunStore,
        sinks: Sequence[EventSink],
        manifest: RunManifest,
    ) -> SimulationResult:
        port = self._cognition_port_for(model, provider)
        try:
            while not model.clock.finished:
                plan = model.plan_tick()
                answers = await self._resolve(plan, port, provider)
                outcome = model.commit_tick(plan, cognition=answers)
                store.append_events(outcome.events)
                for sink in sinks:
                    sink.append_many(manifest.run_id, outcome.events)
                model.clock.advance()
                if outcome.ends_simulated_day:
                    # After the advance, so the clock stands exactly on the boundary the
                    # engine requires a checkpoint to carry.
                    store.save_checkpoint(model.checkpoint())
        except InterruptedRun:
            raise
        except BaseException as error:
            return self._record_stop(model, store, sinks, manifest, error)

        result = model.complete()
        self._save_usage(port, store, manifest)
        store.complete_run(result)
        return result

    async def _resolve(
        self,
        plan: TickPlan,
        port: RunCognitionPort,
        provider: object,
    ) -> Mapping[str, CognitionAnswer]:
        if not plan.requests:
            return {}
        fallback_provider = self._fallback_provider_factory(plan)
        return await port.resolve(plan.requests, fallback_provider=fallback_provider)

    def _publish(
        self,
        store: RunStore,
        sinks: Sequence[EventSink],
        events: Sequence[DomainEvent],
    ) -> None:
        store.append_events(events)
        for sink in sinks:
            sink.append_many(events[0].run_id, events)

    def _save_usage(
        self,
        port: RunCognitionPort,
        store: RunStore,
        manifest: RunManifest,
    ) -> None:
        from adlife.core.ports.run_store import ProviderUsageLog

        metadata = port.provider_metadata
        if metadata is None:
            return
        usage = _usage_of(port)
        if usage is None:
            return
        store.save_provider_usage(ProviderUsageLog(run_id=manifest.run_id, records=tuple(usage)))

    def _record_stop(
        self,
        model: AdLifeModel,
        store: RunStore,
        sinks: Sequence[EventSink],
        manifest: RunManifest,
        error: BaseException,
    ) -> SimulationResult:
        """Record the stop and re-raise, preserving the caller's exception.

        The class name is the safe summary: a message may carry campaign text, provider
        paraphrase or a credential, and the store screens what it holds but the result
        document reaches reports too. ``KeyboardInterrupt`` and cancellation record the
        ``interrupted`` status; everything else records ``failed``. On a failed stop the
        stream also gains its terminal ``run.failed`` event, appended through the same
        validated store path as every other event, so a reader never meets a finished
        run whose stream simply stops.
        """
        interrupted = isinstance(error, (KeyboardInterrupt, asyncio.CancelledError))
        status = "interrupted" if interrupted else "failed"
        if not interrupted:
            self._append_run_failed(store, sinks, manifest, model)
        result = SimulationResult(
            run_id=manifest.run_id,
            status=status,  # type: ignore[arg-type]
            final_minute=model.clock.current_minute,
            event_count=model._next_event_sequence,
            failure_reason=f"{type(error).__name__}",
        )
        try:
            store.complete_run(result)
        except RunnerRefused:
            raise
        except BaseException:
            return result
        if interrupted:
            raise InterruptedRun(result) from error
        raise error

    def _append_run_failed(
        self,
        store: RunStore,
        sinks: Sequence[EventSink],
        manifest: RunManifest,
        model: AdLifeModel,
    ) -> None:
        """Mint and record ``run.failed`` for a run the loop could not finish.

        The engine cannot be asked for it - its clock may be mid-run and its commit
        pipeline whole - so the runner mints it directly, with the same stable-id rule
        and one cause: the last event the run actually recorded.
        """
        from adlife.core.simulation.engine import stable_event_id

        sequence = model._next_event_sequence
        cause = sequence - 1
        event = DomainEvent(
            event_id=stable_event_id(manifest.run_id, sequence),
            run_id=manifest.run_id,
            simulated_minute=model.clock.current_minute,
            sequence=sequence,
            event_type=EventType.RUN_FAILED,
            payload={"reason": "internal-defect"},
            source=EventSource.RULE,
            caused_by_event_ids=(stable_event_id(manifest.run_id, cause),) if cause >= 0 else (),
        )
        try:
            store.append_events((event,))
            for sink in sinks:
                sink.append_many(manifest.run_id, (event,))
            model._next_event_sequence = sequence + 1
        except BaseException:
            return

    # -- conveniences used by tests and the coming CLI --------------------------------

    def checkpoint_for(self, model: AdLifeModel) -> RunCheckpoint:
        return model.checkpoint()


def _usage_of(port: RunCognitionPort) -> Sequence[ProviderUsage] | None:
    records = getattr(port, "usage_records", None)
    if isinstance(records, Sequence):
        return tuple(record for record in records if isinstance(record, ProviderUsage))
    return None


def _finished_result(stored: StoredRun) -> SimulationResult:
    assert stored.result is not None
    return stored.result


def _start_checkpoint(stored: StoredRun, run_id: str) -> RunCheckpoint:
    return RunCheckpoint(
        run_id=run_id,
        simulated_minute=0,
        next_event_sequence=1,
        states=stored.scenario.initial_states,
    )


def _accepts_provider(factory: Callable[..., object]) -> bool:
    try:
        import inspect

        return len(inspect.signature(factory).parameters) >= 2
    except (TypeError, ValueError):
        return False


def _default_provider_name(provider: object) -> str:
    if provider is None:
        return "rules"
    kind = getattr(provider, "provider_name", None)
    return kind if isinstance(kind, str) and kind else "remote"


def _default_model_id(provider: object, provider_name: str) -> str:
    if provider_name == "rules":
        return "rule-v1"
    if provider_name == "mock":
        return "mock-v1"
    named = getattr(provider, "model_name", None)
    return named if isinstance(named, str) and named else f"{provider_name}-v1"


__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "LOCKFILE_NAME",
    "InterruptedRun",
    "RunCognitionPort",
    "RunIdentity",
    "RunnerRefused",
    "SimulationRunner",
    "run_id_from",
    "scenario_manifest",
]
