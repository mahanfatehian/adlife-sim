"""The sensitivity runner: perturb one parameter, rank the campaigns, watch for flips.

Every arm of a sweep is a set of complete runs driven with one
:class:`~adlife.core.simulation.parameters.ModelParameters` set, and every run's
manifest records that exact set - so a sensitivity claim is auditable against its own
artifacts, not against the sweep's memory. The baseline arm runs the caller's
parameters unchanged; each perturbation arm scales one target parameter by one
documented delta. A campaign ranking is computed per seed - purchases first, then
notice rate, then campaign identifier for a stable tie-break - and an arm records the
fraction of seeds whose ranking differs from the baseline's ranking of the same seed.

The documented families: attention, persuasion, memory decay, homophily and word of
mouth, each perturbed at -20, -10, +10 and +20 percent (:data:`DEFAULT_DELTAS`), one
canonical knob per family (:data:`DEFAULT_TARGETS`). A sweep needs no randomness of
its own - the runs draw everything - so two sweeps over the same inputs produce
identical arms.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from adlife.core.experiments.comparison import fold_stored_run
from adlife.core.experiments.metrics import MetricsCalculator, RunMetrics
from adlife.core.ports.run_store import RunStore, StoredRun
from adlife.core.simulation.parameters import (
    DEFAULT_PARAMETERS,
    ModelParameters,
    ParameterName,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner

DEFAULT_DELTAS: tuple[float, ...] = (-0.20, -0.10, 0.10, 0.20)
"""The documented perturbation fractions, relative to the parameter's baseline value."""

DEFAULT_TARGETS: tuple[ParameterName, ...] = (
    "notice_scale",
    "sentiment_gain",
    "recall_retention",
    "social_similarity_weight",
    "social_proof_gain",
)
"""One canonical knob per documented family: attention, persuasion, memory decay,
homophily, and word of mouth."""


@dataclass(frozen=True)
class SensitivityArm:
    """One sweep arm: a parameter set, its runs, and whether the ranking moved."""

    parameter: str | None
    delta: float | None
    run_ids: tuple[str, ...]
    parameter_set: Mapping[str, float]
    per_seed_rankings: Mapping[int, tuple[str, ...]]
    per_seed_metrics: Mapping[int, RunMetrics]
    mean_metrics: RunMetrics
    flip_fraction: float


@dataclass(frozen=True)
class SensitivityResult:
    """The baseline arm plus one arm per (target, delta) pair, in declared order."""

    baseline: SensitivityArm
    arms: tuple[SensitivityArm, ...]


class SensitivityRunner:
    """Drive parameter sensitivity sweeps through the same seams as comparisons."""

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

    async def run(
        self,
        scenario: object,
        *,
        parameters: ModelParameters = DEFAULT_PARAMETERS,
        targets: Sequence[ParameterName] = DEFAULT_TARGETS,
        deltas: Sequence[float] = DEFAULT_DELTAS,
        seeds: Sequence[int],
        run_id_prefix: str = "sens",
    ) -> SensitivityResult:
        """Run the baseline arm and every declared perturbation arm.

        The baseline arm runs the caller's parameter set unchanged; every run it drives
        records that set in its manifest. Each perturbation arm scales one target by
        one delta and revalidates the result against the parameter bounds, so a delta
        that walks a parameter out of its documented range is refused before a run
        starts, naming the parameter.
        """
        from adlife.core.domain.scenario import Scenario

        if not isinstance(scenario, Scenario):
            raise TypeError("scenario must be a Scenario")
        if not isinstance(parameters, ModelParameters):
            raise TypeError("parameters must be a ModelParameters")
        _validate_targets(targets, parameters)
        delta_tuple = tuple(float(delta) for delta in deltas)
        if not delta_tuple:
            raise ValueError("a sensitivity sweep needs at least one delta")
        seed_tuple = _validated_seeds(seeds)

        baseline = await self._arm(
            scenario,
            parameters,
            parameter=None,
            delta=None,
            seeds=seed_tuple,
            run_id_prefix=run_id_prefix,
            baseline_rankings=None,
        )
        arms: list[SensitivityArm] = []
        for target in targets:
            base_value = getattr(parameters, target)
            for delta in delta_tuple:
                perturbed = parameters.with_(target, base_value * (1.0 + delta))
                arms.append(
                    await self._arm(
                        scenario,
                        perturbed,
                        parameter=target,
                        delta=delta,
                        seeds=seed_tuple,
                        run_id_prefix=run_id_prefix,
                        baseline_rankings=baseline.per_seed_rankings,
                    )
                )
        return SensitivityResult(baseline=baseline, arms=tuple(arms))

    async def _arm(
        self,
        scenario: object,
        parameters: ModelParameters,
        *,
        parameter: str | None,
        delta: float | None,
        seeds: Sequence[int],
        run_id_prefix: str,
        baseline_rankings: Mapping[int, tuple[str, ...]] | None,
    ) -> SensitivityArm:
        from adlife.core.domain.scenario import Scenario

        assert isinstance(scenario, Scenario)
        store = self._store_factory()
        parameter_set = parameters.as_mapping()
        run_ids: list[str] = []
        rankings: dict[int, tuple[str, ...]] = {}
        per_seed: dict[int, RunMetrics] = {}
        for seed in seeds:
            run_id = _run_id(run_id_prefix, parameter, delta, seed)
            await self._runner.run(
                scenario,
                seed=seed,
                store=store,
                sinks=(),
                run_id=run_id,
                parameters=parameters,
            )
            stored = store.load_run(run_id)
            run_ids.append(run_id)
            per_seed[seed] = fold_stored_run(store, scenario, stored, self._calculator)
            rankings[seed] = self._ranking(stored, scenario)
        flipped = 0
        if baseline_rankings is not None:
            flipped = sum(1 for seed in seeds if rankings[seed] != baseline_rankings[seed])
        return SensitivityArm(
            parameter=parameter,
            delta=delta,
            run_ids=tuple(run_ids),
            parameter_set=parameter_set,
            per_seed_rankings=rankings,
            per_seed_metrics=per_seed,
            mean_metrics=RunMetrics.averaged(tuple(per_seed[seed] for seed in seeds)),
            flip_fraction=flipped / len(seeds),
        )

    def _ranking(self, stored: StoredRun, scenario: object) -> tuple[str, ...]:
        """Rank one run's campaigns: purchases, then notice rate, then identifier."""
        from adlife.core.domain.scenario import Scenario

        if not isinstance(scenario, Scenario):
            raise TypeError("scenario must be a Scenario")
        ranked: list[tuple[float, float, str]] = []
        for campaign in scenario.campaigns:
            campaign_id = campaign.campaign_id
            metrics = self._calculator.calculate(
                stored.events,
                campaign_id=campaign_id,
            )
            ranked.append((-metrics.purchases, -metrics.notice_rate, campaign_id))
        ranked.sort()
        return tuple(entry[2] for entry in ranked)


def _validate_targets(
    targets: Sequence[ParameterName],
    parameters: ModelParameters,
) -> None:
    valid = tuple(parameters.as_mapping())
    for target in targets:
        if target not in valid:
            raise ValueError(
                f"unknown model parameter: {target}; valid targets: {', '.join(valid)}"
            )


def _validated_seeds(seeds: Sequence[int]) -> tuple[int, ...]:
    seed_tuple = tuple(seeds)
    if not seed_tuple:
        raise ValueError("a sensitivity sweep needs at least one seed")
    for seed in seed_tuple:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError(f"seed {seed!r} must be a nonnegative integer")
    if len(set(seed_tuple)) != len(seed_tuple):
        raise ValueError("seeds must be distinct for a sensitivity sweep")
    return seed_tuple


def _run_id(prefix: str, parameter: str | None, delta: float | None, seed: int) -> str:
    if parameter is None or delta is None:
        run_id = f"{prefix}-base-{seed}"
    else:
        percent = round(abs(delta) * 100)
        sign = "p" if delta > 0 else "m"
        run_id = f"{prefix}-{_arm_slug(parameter)}-{sign}{percent}-{seed}"
    if len(run_id) > 40:
        raise ValueError(f"run id {run_id!r} exceeds the 40-character run-id bound")
    return run_id


def _arm_slug(parameter: str) -> str:
    return parameter.replace("_", "-")


__all__ = [
    "DEFAULT_DELTAS",
    "DEFAULT_TARGETS",
    "SensitivityArm",
    "SensitivityResult",
    "SensitivityRunner",
]
