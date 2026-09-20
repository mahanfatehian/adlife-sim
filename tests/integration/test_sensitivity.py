"""Parameter sensitivity sweeps: perturb one knob, rank campaigns, watch for flips.

Every arm is a set of complete runs whose runs record the exact parameter set that
produced them in the run manifest, so a sensitivity claim is auditable against its own
artifacts. The sweep here is deliberately tiny - one parameter family, two deltas,
three seeds - because the sweep's MACHINERY is under test, not the size of the study.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adlife.adapters.cognition.rules import RULE_MODEL_ID
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.core.experiments.sensitivity import SensitivityRunner
from adlife.core.simulation.parameters import DEFAULT_PARAMETERS, ModelParameters
from adlife.core.simulation.runner import RunIdentity
from tests.builders import (
    RuleCognitionPort,
    rule_fallback_for,
    small_three_agent_scenario,
)

SEEDS = (1, 2, 3)


def base_scenario(valid_scenario: Scenario) -> Scenario:
    """The three-agent world the built-in routines can actually move in."""
    return small_three_agent_scenario(valid_scenario)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_sensitivity_runner(tmp_path: Path) -> tuple[SensitivityRunner, SQLiteRunStore]:
    store = SQLiteRunStore(tmp_path / "sensitivity-store")
    identity = RunIdentity.for_project(
        project_root=_repo_root(),
        provider="rules",
        model_id=RULE_MODEL_ID,
    )
    runner = SensitivityRunner(
        store_factory=lambda: store,
        cognition_factory=lambda model: RuleCognitionPort(),
        fallback_provider_factory=rule_fallback_for,
        identity=identity,
    )
    return runner, store


async def test_baseline_arm_records_its_parameter_set_in_manifests(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, store = build_sensitivity_runner(tmp_path)

    result = await runner.run(
        base_scenario(valid_scenario),
        parameters=DEFAULT_PARAMETERS,
        targets=("recall_retention",),
        deltas=(0.20,),
        seeds=SEEDS,
        run_id_prefix="sens-base-check",
    )

    assert result.baseline.parameter is None
    assert result.baseline.parameter_set == DEFAULT_PARAMETERS.as_mapping()
    manifest = store.load_run(result.baseline.run_ids[0]).manifest
    assert manifest.parameters == DEFAULT_PARAMETERS.as_mapping()
    assert result.baseline.flip_fraction == 0.0
    assert set(result.baseline.per_seed_rankings) == set(SEEDS)


async def test_retention_moves_mean_recall_in_the_documented_direction(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, _ = build_sensitivity_runner(tmp_path)

    result = await runner.run(
        base_scenario(valid_scenario),
        parameters=DEFAULT_PARAMETERS,
        targets=("recall_retention",),
        deltas=(-0.20, 0.20),
        seeds=SEEDS,
        run_id_prefix="sens-recall",
    )

    by_delta = {arm.delta: arm for arm in result.arms}
    assert by_delta[0.20].mean_metrics.recall >= result.baseline.mean_metrics.recall
    assert by_delta[-0.20].mean_metrics.recall <= result.baseline.mean_metrics.recall
    effective = {arm.parameter_set["recall_retention"] for arm in result.arms}
    assert effective == {
        DEFAULT_PARAMETERS.recall_retention * 0.8,
        min(1.0, DEFAULT_PARAMETERS.recall_retention * 1.2),
    }


async def test_rankings_are_complete_and_flip_fraction_is_a_fraction(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, _ = build_sensitivity_runner(tmp_path)
    campaign_ids = tuple(
        campaign.campaign_id for campaign in base_scenario(valid_scenario).campaigns
    )

    result = await runner.run(
        base_scenario(valid_scenario),
        parameters=DEFAULT_PARAMETERS,
        targets=("notice_scale",),
        deltas=(-0.20, 0.20),
        seeds=SEEDS,
        run_id_prefix="sens-rank",
    )

    for arm in (result.baseline, *result.arms):
        assert 0.0 <= arm.flip_fraction <= 1.0
        for ranking in arm.per_seed_rankings.values():
            assert sorted(ranking) == sorted(campaign_ids)
            assert len(ranking) == len(set(ranking))


async def test_identical_sweeps_produce_identical_arms(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, _ = build_sensitivity_runner(tmp_path)

    first = await runner.run(
        base_scenario(valid_scenario),
        parameters=DEFAULT_PARAMETERS,
        targets=("recall_retention",),
        deltas=(0.10,),
        seeds=SEEDS,
        run_id_prefix="sens-again-a",
    )
    second = await runner.run(
        base_scenario(valid_scenario),
        parameters=DEFAULT_PARAMETERS,
        targets=("recall_retention",),
        deltas=(0.10,),
        seeds=SEEDS,
        run_id_prefix="sens-again-b",
    )

    assert first.baseline.parameter_set == second.baseline.parameter_set
    for arm_a, arm_b in zip(first.arms, second.arms, strict=True):
        assert arm_a.delta == arm_b.delta
        for seed in SEEDS:
            assert (
                arm_a.per_seed_metrics[seed].impressions == arm_b.per_seed_metrics[seed].impressions
            )
            assert arm_a.per_seed_metrics[seed].recall == arm_b.per_seed_metrics[seed].recall


async def test_unknown_parameter_target_is_refused(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    runner, _ = build_sensitivity_runner(tmp_path)

    with pytest.raises(ValueError, match="recall_retention"):
        await runner.run(
            valid_scenario,
            parameters=DEFAULT_PARAMETERS,
            targets=("recall_retention", "no-such-parameter"),
            deltas=(0.10,),
            seeds=SEEDS,
        )


def test_parameters_round_trip_through_the_mapping() -> None:
    parameters = DEFAULT_PARAMETERS.with_("notice_scale", 1.4)
    assert parameters.notice_scale == 1.4
    assert parameters.recall_retention == DEFAULT_PARAMETERS.recall_retention
    restored = ModelParameters.model_validate(parameters.as_mapping())
    assert restored == parameters
    clamped = DEFAULT_PARAMETERS.with_("recall_retention", 1.02)
    assert clamped.recall_retention == 1.0
    floored = DEFAULT_PARAMETERS.with_("notice_scale", -1.0)
    assert floored.notice_scale == 0.0
    with pytest.raises(ValueError, match="no-such-parameter"):
        DEFAULT_PARAMETERS.with_("no-such-parameter", 0.5)  # type: ignore[arg-type]
