from __future__ import annotations

import json
import os
import subprocess
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_study import spatial_paired_statistics

KEYS = (
    "attention.overall.notice_rate",
    "response.overall.response_count",
    "response.overall.state.recall_strength.mean_change",
)
SOURCES = {
    KEYS[0]: ("outputs/spatial-attention.jsonl",),
    KEYS[1]: ("outputs/spatial-responses.jsonl",),
    KEYS[2]: ("inputs/spatial-response.json", "outputs/response-state.json"),
}
VALUES = (
    -7.0,
    0.0,
    1.0,
    1.5,
    2.0,
    3.0,
    -1.0,
    4.0,
    0.0,
    2.5,
    0.5,
    6.0,
    1.0,
    -2.0,
    4.5,
    3.5,
    0.0,
    8.0,
    2.0,
    1.0,
)


@settings(max_examples=12, deadline=None)
@given(difference_order=st.permutations(KEYS), source_order=st.permutations(KEYS))
def test_statistic_documents_are_invariant_to_independent_mapping_insertion_order(
    difference_order: list[str], source_order: list[str]
) -> None:
    baseline = spatial_paired_statistics({key: VALUES for key in KEYS}, SOURCES)

    actual = spatial_paired_statistics(
        {key: VALUES for key in difference_order},
        {key: SOURCES[key] for key in source_order},
    )

    assert tuple(item.metric_key for item in actual) == KEYS
    assert tuple(canonical_json(item) for item in actual) == tuple(
        canonical_json(item) for item in baseline
    )


def test_adding_unrelated_metrics_preserves_existing_metric_document() -> None:
    key = KEYS[1]
    baseline = spatial_paired_statistics({key: VALUES}, {key: SOURCES[key]})[0]

    expanded = spatial_paired_statistics({item: VALUES for item in KEYS}, SOURCES)
    actual = next(item for item in expanded if item.metric_key == key)

    assert canonical_json(actual) == canonical_json(baseline)


def test_statistic_documents_do_not_depend_on_python_hash_seed() -> None:
    script = """
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_study import spatial_paired_statistics

keys = {
    "attention.overall.notice_rate",
    "response.overall.response_count",
    "response.overall.state.recall_strength.mean_change",
}
artifacts = {
    "attention.overall.notice_rate": ("outputs/spatial-attention.jsonl",),
    "response.overall.response_count": ("outputs/spatial-responses.jsonl",),
    "response.overall.state.recall_strength.mean_change": (
        "inputs/spatial-response.json", "outputs/response-state.json"
    ),
}
values = (
    -7.0, 0.0, 1.0, 1.5, 2.0, 3.0, -1.0, 4.0, 0.0, 2.5,
    0.5, 6.0, 1.0, -2.0, 4.5, 3.5, 0.0, 8.0, 2.0, 1.0,
)
result = spatial_paired_statistics(
    {key: values for key in keys}, {key: artifacts[key] for key in keys}
)
for item in result:
    print(canonical_json(item))
"""
    outputs = []
    for hash_seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert completed.stderr == ""
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    documents = [json.loads(line) for line in outputs[0].splitlines()]
    assert tuple(item["metric_key"] for item in documents) == KEYS
    assert all(item["n_seeds"] == 20 for item in documents)
