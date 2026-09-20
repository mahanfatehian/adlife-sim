"""Paired whole-run comparisons driven through the real runner and store.

Each arm of a comparison is a complete run per seed - the same seed, the same initial
population, the same keyed random streams - so a paired difference is the treatment's
effect and nothing else. The A/A comparison is the null the whole machinery must
satisfy before any treatment result means anything: the same scenario twice, twenty
seeds, and every paired difference exactly zero.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.cognition.rules import RULE_MODEL_ID
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.experiments.comparison import ExperimentRunner
from adlife.core.experiments.design import (
    ExperimentDesign,
    ExperimentDesignError,
    validate_paired_design,
    validate_paired_manifests,
)
from adlife.core.experiments.metrics import MetricsCalculator
from adlife.core.simulation.runner import RunIdentity
from tests.builders import (
    RuleCognitionPort,
    rule_fallback_for,
    small_three_agent_scenario,
    strip_campaigns,
)

SEEDS = tuple(range(20))


def base_scenario(valid_scenario: Scenario) -> Scenario:
    """The three-agent world the built-in routines can actually move in."""
    return small_three_agent_scenario(valid_scenario)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_experiment_runner(
    tmp_path: Path,
) -> tuple[ExperimentRunner, Callable[[], SQLiteRunStore]]:
    def store_factory() -> SQLiteRunStore:
        return SQLiteRunStore(tmp_path / "experiment-store")

    identity = RunIdentity.for_project(
        project_root=_repo_root(),
        provider="rules",
        model_id=RULE_MODEL_ID,
    )
    runner = ExperimentRunner(
        store_factory=store_factory,
        cognition_factory=lambda model: RuleCognitionPort(),
        fallback_provider_factory=rule_fallback_for,
        identity=identity,
    )
    return runner, store_factory


async def test_identical_a_a_comparison_has_zero_difference(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, _ = build_experiment_runner(tmp_path)
    scenario = base_scenario(valid_scenario)

    result = await runner.compare(
        scenario,
        scenario,
        seeds=SEEDS,
    )

    assert result.metrics["recall"].mean_paired_difference == 0
    assert result.metrics["sentiment"].mean_paired_difference == 0
    assert result.metrics["impressions"].mean_paired_difference == 0
    assert result.seeds == SEEDS
    assert result.metrics["recall"].n_seeds == 20
    assert result.metrics["recall"].direction == "stable"
    assert result.metrics["recall"].ci_low == 0
    assert result.metrics["recall"].ci_high == 0


async def test_removing_campaigns_shifts_the_paired_differences(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, _ = build_experiment_runner(tmp_path)
    control = base_scenario(valid_scenario)
    treatment = strip_campaigns(control)
    design = ExperimentDesign(label="no-campaign", treatment_paths=("campaigns",))

    result = await runner.compare(
        control,
        treatment,
        seeds=(1, 2, 3),
        design=design,
    )

    assert result.metrics["impressions"].mean_paired_difference < 0
    assert result.metrics["cognition_requests"].mean_paired_difference <= 0
    assert all(
        statistic.direction in {"stable", "unstable"} for statistic in result.metrics.values()
    )
    assert result.control_runs.keys() == {1, 2, 3}


async def test_design_rejects_population_mismatch(valid_scenario: Scenario) -> None:
    from tests.builders import small_three_agent_scenario

    design = ExperimentDesign(label="mismatch", treatment_paths=("campaigns",))
    with pytest.raises(ExperimentDesignError, match="population"):
        validate_paired_design(valid_scenario, small_three_agent_scenario(valid_scenario), design)


async def test_design_rejects_duration_mismatch(valid_scenario: Scenario) -> None:
    longer = valid_scenario.model_copy(update={"days": valid_scenario.days + 1})
    design = ExperimentDesign(label="mismatch", treatment_paths=("campaigns",))
    with pytest.raises(ExperimentDesignError, match="duration"):
        validate_paired_design(valid_scenario, longer, design)


async def test_design_rejects_change_outside_declared_paths(
    valid_scenario: Scenario,
) -> None:
    renamed = valid_scenario.model_copy(update={"name": "A renamed scenario"})
    design = ExperimentDesign(label="renamed", treatment_paths=("campaigns",))
    with pytest.raises(ExperimentDesignError, match="name"):
        validate_paired_design(valid_scenario, renamed, design)


async def test_design_allows_declared_campaign_changes(valid_scenario: Scenario) -> None:
    treatment = strip_campaigns(valid_scenario)
    design = ExperimentDesign(label="no-campaign", treatment_paths=("campaigns",))
    validate_paired_design(valid_scenario, treatment, design)


def test_manifest_validation_rejects_engine_version_mismatch(
    run_manifest: RunManifest,
) -> None:
    other = run_manifest.model_copy(update={"package_version": "9.9.9"})
    with pytest.raises(ExperimentDesignError, match="package_version"):
        validate_paired_manifests(run_manifest, other)


def test_manifest_validation_rejects_seed_pairing_mismatch(
    run_manifest: RunManifest,
) -> None:
    other = run_manifest.model_copy(update={"seed": run_manifest.seed + 1})
    with pytest.raises(ExperimentDesignError, match="seed"):
        validate_paired_manifests(run_manifest, other)


async def test_per_seed_metrics_agree_with_the_stored_artifact(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    """The per-run fold reads only what the artifact holds, never live model state."""
    runner, store_factory = build_experiment_runner(tmp_path)
    control = strip_campaigns(base_scenario(valid_scenario))
    treatment = base_scenario(valid_scenario)
    design = ExperimentDesign(label="added-campaign", treatment_paths=("campaigns",))

    result = await runner.compare(
        control,
        treatment,
        seeds=(1, 2),
        design=design,
    )

    store = store_factory()
    calculator = MetricsCalculator()
    for seed, run_id in ((1, "exp-added-campaign-a-000001"), (2, "exp-added-campaign-a-000002")):
        stored = store.load_run(run_id)
        refolded = calculator.calculate(
            stored.events,
            initial_states=control.initial_states,
            final_states=stored.checkpoints[-1].states,
        )
        assert result.control_runs[seed].impressions == refolded.impressions
        assert result.control_runs[seed].recall == refolded.recall
