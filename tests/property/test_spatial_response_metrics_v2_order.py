"""Order properties for version-two bounded-cognition metrics."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.city_cognition_v2 import CityCognitionEvaluationV2, CityCognitionInputV2
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from adlife.core.domain.spatial_response import parse_spatial_response_input_json
from adlife.core.simulation.spatial_response_v2 import SpatialResponseEvaluationV2
from tests.unit.experiments.test_spatial_response_metrics_v2 import (
    _dense,
    _derive,
    _metric_inputs,
)


def _shuffled(value: object, rng: random.Random) -> object:
    """Permute JSON object member order recursively; arrays keep their order."""
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: _shuffled(item, rng) for key, item in items}
    if isinstance(value, list):
        return [_shuffled(item, rng) for item in value]
    return value


def test_v2_metrics_are_invariant_to_declared_agent_order_and_container(tmp_path: Path) -> None:
    _, _, arguments = _metric_inputs(tmp_path, mode="mock", mutation=_dense)
    expected = canonical_json(_derive(arguments))
    agents = arguments["agent_ids"]

    @settings(max_examples=8, deadline=None)
    @given(
        order=st.permutations(agents),
        container=st.sampled_from((tuple, list, frozenset)),
    )
    def check(order: list[str], container: type) -> None:
        reordered = dict(arguments)
        reordered["agent_ids"] = container(order)
        assert canonical_json(_derive(reordered)) == expected

    check()


def test_v2_metrics_normalize_permuted_input_mappings_and_cohort_lists(tmp_path: Path) -> None:
    _, _, arguments = _metric_inputs(tmp_path, mode="local", source="provider", agents=8)
    expected = canonical_json(_derive(arguments))
    response_document = arguments["response_input"].model_dump(mode="json")
    scenario_document = arguments["scenario"].model_dump(mode="json")
    cognition_document = arguments["cognition_input"].model_dump(mode="json")

    @settings(max_examples=8, deadline=None)
    @given(seed=st.integers(min_value=0, max_value=2**32 - 1), reverse_lists=st.booleans())
    def check(seed: int, reverse_lists: bool) -> None:
        rng = random.Random(seed)
        document = _shuffled(response_document, rng)
        assert isinstance(document, dict)
        if reverse_lists:
            for name in ("profiles", "campaigns", "initial_states"):
                document[name].reverse()
            for profile in document["profiles"]:
                profile["interests"].reverse()
            for campaign in document["campaigns"]:
                campaign["target_interests"].reverse()
        changed = dict(arguments)
        changed["response_input"] = parse_spatial_response_input_json(json.dumps(document))
        changed["scenario"] = parse_spatial_campaign_scenario_json(
            json.dumps(_shuffled(scenario_document, rng))
        )
        changed["cognition_input"] = CityCognitionInputV2.model_validate_json(
            json.dumps(_shuffled(cognition_document, rng))
        )
        assert canonical_json(_derive(changed)) == expected

    check()


def test_noncanonical_response_record_order_is_refused_not_normalized(tmp_path: Path) -> None:
    _, _, arguments = _metric_inputs(tmp_path, mode="mock")
    response = arguments["response"]
    assert isinstance(response, SpatialResponseEvaluationV2)
    records = response.records
    expected = canonical_json(_derive(arguments))
    assert len(records) > 2

    def derive_with(order: tuple[int, ...]) -> str:
        changed = dict(arguments)
        changed["response"] = SpatialResponseEvaluationV2.model_construct(
            **(
                response.model_dump(mode="python")
                | {"records": tuple(records[index] for index in order)}
            )
        )
        return canonical_json(_derive(changed))

    assert derive_with(tuple(range(len(records)))) == expected

    @settings(max_examples=12, deadline=None)
    @given(order=st.permutations(tuple(range(len(records)))))
    def check(order: list[int]) -> None:
        if tuple(order) == tuple(range(len(records))):
            assert derive_with(tuple(order)) == expected
        else:
            with pytest.raises(ValueError):
                derive_with(tuple(order))

    check()


def test_terminal_completion_order_cannot_reorder_persisted_evidence(tmp_path: Path) -> None:
    _, _, arguments = _metric_inputs(tmp_path, mode="local", source="provider", agents=8)
    cognition = arguments["cognition"]
    assert isinstance(cognition, CityCognitionEvaluationV2)
    records = cognition.records
    expected = canonical_json(_derive(arguments))
    assert len(records) > 2

    def derive_with(order: tuple[int, ...]) -> str:
        changed = dict(arguments)
        changed["cognition"] = CityCognitionEvaluationV2.model_construct(
            **(
                cognition.model_dump(mode="python")
                | {"records": tuple(records[index] for index in order)}
            )
        )
        return canonical_json(_derive(changed))

    assert derive_with(tuple(range(len(records)))) == expected

    @settings(max_examples=12, deadline=None)
    @given(order=st.permutations(tuple(range(len(records)))))
    def check(order: list[int]) -> None:
        if tuple(order) == tuple(range(len(records))):
            assert derive_with(tuple(order)) == expected
        else:
            with pytest.raises(ValueError):
                derive_with(tuple(order))

    check()


def test_v2_metric_bytes_do_not_depend_on_python_hash_seed(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path

from adlife.core.domain.serialization import canonical_json
from tests.unit.experiments.test_spatial_response_metrics_v2 import (
    _dense,
    _derive,
    _metric_inputs,
)

_, _, arguments = _metric_inputs(Path(sys.argv[1]), mode="mock", mutation=_dense)
print(canonical_json(_derive(arguments)))
"""
    outputs = []
    for hash_seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / f"seed-{hash_seed}")],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert "Traceback" not in completed.stderr
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    document = json.loads(outputs[0])
    assert document["model_id"] == "spatial-response-metrics-v2"
    assert document["source_run_schema_version"] == 10
    assert document["provider_dependent_cognition"]["coverage"]["unselected_response_count"] > 0
