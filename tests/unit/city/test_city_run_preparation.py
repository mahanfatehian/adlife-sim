"""City run evaluation is deterministic, side-effect free, and separately published."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adlife.city import runs
from adlife.city.run_store import CityRunStore
from adlife.city.workbench_input import WorkbenchRunDraft
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.domain.city_run import CityRunManifestV7
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import DuplicateRun
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_workbench_input import draft_data


def _validated(*, run_id: str = "launch-run", seed: int = 42, agents: int = 2, days: int = 2):
    document = draft_data(phone=True, roadside=True)
    document["scenario"]["roadside"]["road_id"] = "middle-west"
    document["settings"].update(
        {"run_id": run_id, "seed": seed, "agent_count": agents, "days": days}
    )
    return construct_workbench_run(WorkbenchRunDraft.model_validate_json(json.dumps(document)))


def test_prepare_city_run_evaluates_without_touching_the_filesystem(tmp_path: Path) -> None:
    validated = _validated()

    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="launch-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    assert list(tmp_path.iterdir()) == []
    assert isinstance(prepared.manifest, CityRunManifestV7)
    assert prepared.workbench_input == validated.workbench_input
    assert prepared.mobility.agents == validated.mobility.agents
    assert prepared.mobility.frame(1_440) == validated.mobility.frame(1_440)
    assert prepared.response_evaluation is not None


def test_explicit_prepare_publish_matches_the_compatibility_wrapper(tmp_path: Path) -> None:
    validated = _validated()
    arguments = {
        "run_id": "equivalent-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "spatial_response": validated.response_input,
    }

    prepared = runs.prepare_city_run(validated.pack, **arguments)
    explicit = runs.publish_city_run(prepared, root=tmp_path / "explicit")
    wrapped = runs.create_city_run(validated.pack, root=tmp_path / "wrapped", **arguments)

    assert explicit.manifest == wrapped.manifest
    assert explicit.mobility.agents == wrapped.mobility.agents
    assert explicit.opportunity_evaluation == wrapped.opportunity_evaluation
    assert explicit.attention_evaluation == wrapped.attention_evaluation
    assert explicit.response_evaluation == wrapped.response_evaluation
    assert (explicit.directory / "run.json").read_bytes() == (
        wrapped.directory / "run.json"
    ).read_bytes()


def test_publish_is_no_clobber_and_preserves_the_first_artifact(tmp_path: Path) -> None:
    validated = _validated()
    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="no-clobber",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
    )
    first = runs.publish_city_run(prepared, root=tmp_path)
    before = (first.directory / "run.json").read_bytes()

    with pytest.raises(DuplicateRun):
        runs.publish_city_run(prepared, root=tmp_path)

    assert (first.directory / "run.json").read_bytes() == before
    assert CityRunStore(tmp_path).load("no-clobber").manifest == first.manifest


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"run_id": "different-run"}, "run identifier"),
        ({"seed": 43}, "seed"),
        ({"agent_count": 1}, "population"),
        ({"days": 1}, "duration"),
    ],
)
def test_prepare_refuses_workbench_settings_that_do_not_match_execution(
    overrides: dict[str, object], message: str
) -> None:
    validated = _validated()
    arguments: dict[str, object] = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "spatial_response": validated.response_input,
        "workbench_input": validated.workbench_input,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        runs.prepare_city_run(validated.pack, **arguments)


def test_prepare_refuses_unrepresented_places_and_incomplete_workbench_evidence() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "spatial_response": validated.response_input,
        "workbench_input": validated.workbench_input,
    }

    with pytest.raises(ValueError, match="place"):
        runs.prepare_city_run(validated.pack, places=mobility_place_set(), **common)

    with pytest.raises(ValueError, match="scenario"):
        runs.prepare_city_run(
            validated.pack,
            **{**common, "spatial_scenario": None, "spatial_response": None},
        )


def test_prepare_refuses_scenario_response_and_creative_drift() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "workbench_input": validated.workbench_input,
    }

    changed_scenario = validated.scenario.model_copy(update={"name": "Different study"})
    with pytest.raises(ValueError, match="scenario"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=changed_scenario,
            spatial_response=validated.response_input,
            **common,
        )

    response_campaign = validated.response_input.campaigns[0].model_copy(
        update={"target_interests": frozenset({"outdoors"})}
    )
    changed_response = validated.response_input.model_copy(
        update={"campaigns": (response_campaign,)}
    )
    with pytest.raises(ValueError, match="response input"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=validated.scenario,
            spatial_response=changed_response,
            **common,
        )

    changed_template = validated.workbench_input.creative_template.model_copy(
        update={"message": "A different fictional creative."}
    )
    changed_workbench = validated.workbench_input.model_copy(
        update={"creative_template": changed_template}
    )
    with pytest.raises(ValueError, match="campaign"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            **{**common, "workbench_input": changed_workbench},
        )


def test_prepare_refuses_city_and_placement_drift() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_response": validated.response_input,
    }
    other_city_draft = validated.workbench_input.draft.model_copy(
        update={"city_id": "another-fictional-city"}
    )
    other_city_input = validated.workbench_input.model_copy(update={"draft": other_city_draft})
    with pytest.raises(ValueError, match="city"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=validated.scenario,
            workbench_input=other_city_input,
            **common,
        )

    placements = list(validated.scenario.placements)
    phone_index = next(
        index
        for index, placement in enumerate(placements)
        if placement.placement_id == "phone-placement"
    )
    placements[phone_index] = placements[phone_index].model_copy(
        update={"opportunity_probability_per_minute": 0.1}
    )
    phone_drift = validated.scenario.model_copy(update={"placements": tuple(placements)})
    with pytest.raises(ValueError, match="phone placement"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=phone_drift,
            workbench_input=validated.workbench_input,
            **common,
        )

    placements = list(validated.scenario.placements)
    roadside_index = next(
        index
        for index, placement in enumerate(placements)
        if placement.placement_id == "roadside-placement"
    )
    placements[roadside_index] = placements[roadside_index].model_copy(
        update={"max_view_distance_meters": 121.0}
    )
    roadside_drift = validated.scenario.model_copy(update={"placements": tuple(placements)})
    with pytest.raises(ValueError, match="roadside placement"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=roadside_drift,
            workbench_input=validated.workbench_input,
            **common,
        )


def test_prepare_refuses_response_profile_and_initial_state_drift() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "workbench_input": validated.workbench_input,
    }
    profiles = list(validated.response_input.profiles)
    changed_traits = profiles[0].traits.model_copy(update={"novelty_seeking": 0.1})
    profiles[0] = profiles[0].model_copy(update={"traits": changed_traits})
    profile_drift = validated.response_input.model_copy(update={"profiles": tuple(profiles)})
    with pytest.raises(ValueError, match="response population"):
        runs.prepare_city_run(
            validated.pack,
            spatial_response=profile_drift,
            **common,
        )

    states = list(validated.response_input.initial_states)
    states[0] = states[0].model_copy(update={"brand_sentiment": 0.5})
    state_drift = validated.response_input.model_copy(update={"initial_states": tuple(states)})
    with pytest.raises(ValueError, match="initial response state"):
        runs.prepare_city_run(
            validated.pack,
            spatial_response=state_drift,
            **common,
        )


def test_workbench_preparation_publishes_only_with_v7_sidecar_support(tmp_path: Path) -> None:
    validated = _validated()
    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="launch-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    stored = runs.publish_city_run(prepared, root=tmp_path)

    assert isinstance(stored.manifest, CityRunManifestV7)
    assert stored.workbench_input == validated.workbench_input
    assert (stored.directory / "inputs" / "workbench.json").is_file()


def test_different_receipt_run_ids_do_not_change_normalized_scientific_evidence() -> None:
    first_input = _validated(run_id="first-run")
    second_input = _validated(run_id="second-run")
    first = runs.prepare_city_run(
        first_input.pack,
        run_id="first-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=first_input.scenario,
        spatial_response=first_input.response_input,
        workbench_input=first_input.workbench_input,
    )
    second = runs.prepare_city_run(
        second_input.pack,
        run_id="second-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=second_input.scenario,
        spatial_response=second_input.response_input,
        workbench_input=second_input.workbench_input,
    )

    first_receipt = first.manifest.model_dump(mode="json")
    second_receipt = second.manifest.model_dump(mode="json")
    del first_receipt["run_id"]
    del second_receipt["run_id"]
    first_workbench_sha256 = first_receipt.pop("workbench_input_sha256")
    second_workbench_sha256 = second_receipt.pop("workbench_input_sha256")
    assert first_workbench_sha256 != second_workbench_sha256
    assert canonical_json(first_receipt) == canonical_json(second_receipt)
    assert first.mobility.agents == second.mobility.agents
    assert first.opportunity_evaluation == second.opportunity_evaluation
    assert first.attention_evaluation == second.attention_evaluation
    assert first.response_evaluation == second.response_evaluation
