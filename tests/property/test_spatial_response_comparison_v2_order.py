from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_observations import spatial_metric_observations
from adlife.core.experiments.spatial_response_metrics_v2 import SpatialResponseMetricsV2
from tests.unit.experiments.test_spatial_response_comparison_v2 import _compare, _signalled_pair
from tests.unit.experiments.test_spatial_response_metrics_v2 import _derive, _metric_inputs


def _reorder_mappings(value: Any, offset: int, reverse: bool) -> Any:
    if isinstance(value, dict):
        keys = list(value)
        keys = keys[offset % len(keys) :] + keys[: offset % len(keys)] if keys else keys
        if reverse:
            keys.reverse()
        return {key: _reorder_mappings(value[key], offset + 1, reverse) for key in keys}
    if isinstance(value, list):
        return [_reorder_mappings(item, offset, reverse) for item in value]
    if isinstance(value, tuple):
        return tuple(_reorder_mappings(item, offset, reverse) for item in value)
    return value


def test_v2_comparison_bytes_are_invariant_to_nested_input_mapping_order(tmp_path: Path) -> None:
    control, treatment = _signalled_pair(tmp_path)
    expected = _compare(control, treatment)
    permuted_control = SpatialResponseMetricsV2.model_validate(
        _reorder_mappings(control.model_dump(mode="python"), 3, True)
    )
    permuted_treatment = SpatialResponseMetricsV2.model_validate(
        _reorder_mappings(treatment.model_dump(mode="python"), 7, False)
    )

    actual = _compare(permuted_control, permuted_treatment)

    assert canonical_json(actual) == canonical_json(expected)
    assert tuple(series.channel for series in actual.rule_baseline.channels) == (
        "roadside",
        "mobile",
    )


def test_v2_observation_keys_are_invariant_to_nested_input_mapping_order(tmp_path: Path) -> None:
    _, _, arguments = _metric_inputs(tmp_path, mode="mock")
    metrics = _derive(arguments)
    attention = arguments["attention_metrics"]
    expected = spatial_metric_observations(attention, metrics)
    permuted = SpatialResponseMetricsV2.model_validate(
        _reorder_mappings(metrics.model_dump(mode="python"), 5, True)
    )

    actual = spatial_metric_observations(attention, permuted)

    assert [item.model_dump(mode="json") for item in actual] == [
        item.model_dump(mode="json") for item in expected
    ]


def test_v2_comparison_bytes_do_not_depend_on_python_hash_seed(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_observations import spatial_metric_observations
from tests.unit.experiments.test_spatial_response_comparison_v2 import _compare, _signalled_pair
from tests.unit.experiments.test_spatial_response_metrics_v2 import _derive, _metric_inputs

root = Path(sys.argv[1])
control, treatment = _signalled_pair(root)
comparison = _compare(control, treatment)
_, _, arguments = _metric_inputs(root / "observations", mode="mock")
observations = spatial_metric_observations(
    arguments["attention_metrics"], _derive(arguments)
)
print(canonical_json(comparison))
print("\\n".join(item.key for item in observations))
"""
    outputs = []
    for hash_seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / f"seed-{hash_seed}")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    document = json.loads(outputs[0].splitlines()[0])
    assert document["model_id"] == "spatial-response-metrics-comparison-v2"
