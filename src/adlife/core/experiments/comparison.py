"""The paired-comparison runner: two arms, one seed at a time, whole runs.

For every seed the runner executes a complete control run and a complete treatment run
through the same :class:`~adlife.core.simulation.runner.SimulationRunner` seams the CLI
uses - the same cognition port, the same store, the same identity - so each paired
difference is the treatment's effect on one seed and nothing else. Runs are sequential:
the rules engine is CPU-bound, and determinism outranks wall-clock here.

Each arm's run is folded into :class:`~adlife.core.experiments.metrics.RunMetrics` from
the artifact alone - the stored events, the last checkpoint's states, the stored usage
log - never from live model state, so what a comparison reports is exactly what a
report could re-derive later from the store.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from adlife.core.experiments.design import (
    ComparisonResult,
    ExperimentDesign,
    ExperimentDesignError,
    _slug,
    paired_statistics,
    validate_paired_design,
    validate_paired_manifests,
)
from adlife.core.experiments.metrics import METRIC_NAMES, MetricsCalculator, RunMetrics
from adlife.core.ports.run_store import RunStore, StoredRun
from adlife.core.simulation.runner import RunIdentity, SimulationRunner

DEFAULT_DESIGN = ExperimentDesign(label="paired", treatment_paths=("campaigns",))
"""The design a ``compare`` call without one assumes: the campaigns are the treatment."""


class ExperimentRunner:
    """Drive paired whole-run comparisons through the documented runner seams."""

    def __init__(
        self,
        *,
        store_factory: Callable[[], RunStore],
        cognition_factory: Callable[..., object],
        fallback_provider_factory: Callable[..., object],
        identity: RunIdentity,
    ) -> None:
        if not isinstance(identity, RunIdentity):
            raise TypeError("identity must be a RunIdentity")
        self._store_factory = store_factory
        self._runner = SimulationRunner(
            cognition_factory=cognition_factory,
            fallback_provider_factory=fallback_provider_factory,
            identity=identity,
        )
        self._calculator = MetricsCalculator()

    async def compare(
        self,
        control: object,
        treatment: object,
        seeds: Sequence[int],
        *,
        design: ExperimentDesign | None = None,
        run_id_prefix: str | None = None,
    ) -> ComparisonResult:
        """Run both arms per seed and pair their metrics by seed.

        The seeds must be distinct nonnegative integers; they are used as-is, because a
        paired difference across different seeds would measure the seed, not the
        treatment. Each run's identifier is ``<prefix>-a|b-<seed>`` under the design
        label's slug, so every arm of every comparison is auditable in the store.
        """
        from adlife.core.domain.scenario import Scenario

        resolved_design = design or DEFAULT_DESIGN
        if not isinstance(control, Scenario) or not isinstance(treatment, Scenario):
            raise TypeError("compare requires two Scenario objects")
        validate_paired_design(control, treatment, resolved_design)

        seed_tuple = _validated_seeds(seeds)
        prefix = _slug(run_id_prefix or f"exp-{resolved_design.label}")

        store = self._store_factory()
        control_runs: dict[int, RunMetrics] = {}
        treatment_runs: dict[int, RunMetrics] = {}
        for seed in seed_tuple:
            control_id = f"{prefix}-a-{seed:06d}"
            treatment_id = f"{prefix}-b-{seed:06d}"
            control_result = await self._runner.run(
                control,
                seed=seed,
                store=store,
                sinks=(),
                run_id=control_id,
            )
            treatment_result = await self._runner.run(
                treatment,
                seed=seed,
                store=store,
                sinks=(),
                run_id=treatment_id,
            )
            del control_result, treatment_result  # the artifact is the authority
            stored_control = store.load_run(control_id)
            stored_treatment = store.load_run(treatment_id)
            validate_paired_manifests(stored_control.manifest, stored_treatment.manifest)
            control_runs[seed] = self._fold(store, control, stored_control)
            treatment_runs[seed] = self._fold(store, treatment, stored_treatment)

        differences = {
            name: tuple(
                getattr(treatment_runs[seed], name) - getattr(control_runs[seed], name)
                for seed in seed_tuple
            )
            for name in METRIC_NAMES
        }
        return ComparisonResult(
            label=resolved_design.label,
            seeds=seed_tuple,
            metrics=paired_statistics(differences),
            control_runs=control_runs,
            treatment_runs=treatment_runs,
        )

    def _fold(self, store: RunStore, scenario: object, stored: StoredRun) -> RunMetrics:
        """Fold one stored run from its artifact: events, last checkpoint, usage log."""
        return fold_stored_run(store, scenario, stored, self._calculator)


def fold_stored_run(
    store: RunStore,
    scenario: object,
    stored: StoredRun,
    calculator: MetricsCalculator,
) -> RunMetrics:
    """Fold one stored run from its artifact: events, last checkpoint, usage log.

    The artifact is the only authority: the events and the last checkpoint come from
    the store, never from a live model, so what a comparison reports is exactly what a
    report could re-derive later from the store.
    """
    from adlife.core.domain.scenario import Scenario

    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario")
    final_states = stored.checkpoints[-1].states if stored.checkpoints else ()
    loader = getattr(store, "load_provider_usage", None)
    usage = loader(stored.manifest.run_id) if callable(loader) else None
    return calculator.calculate(
        stored.events,
        initial_states=scenario.initial_states,
        final_states=final_states,
        usage=usage,
    )


def _validated_seeds(seeds: Sequence[int]) -> tuple[int, ...]:
    seed_tuple = tuple(seeds)
    if not seed_tuple:
        raise ExperimentDesignError("a paired comparison needs at least one seed")
    for seed in seed_tuple:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ExperimentDesignError(f"seed {seed!r} must be a nonnegative integer")
    if len(set(seed_tuple)) != len(seed_tuple):
        raise ExperimentDesignError("seeds must be distinct for a paired comparison")
    return seed_tuple


__all__ = ["DEFAULT_DESIGN", "ExperimentRunner", "fold_stored_run"]
