from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import parse_spatial_study_definition_json


def _study_document(
    *,
    design: str = "paired-contrast",
    analysis_scope: str = "attention-and-response",
    pair_count: int = 4,
) -> dict[str, Any]:
    pairs = []
    for seed in range(pair_count):
        control_run_id = f"control-{seed}"
        treatment_run_id = control_run_id if design == "a-a" else f"treatment-{seed}"
        pairs.append(
            {
                "seed": seed,
                "control_run_id": control_run_id,
                "treatment_run_id": treatment_run_id,
            }
        )
    return {
        "schema_version": 1,
        "study_id": "sample-study",
        "design": design,
        "analysis_scope": analysis_scope,
        "pairs": pairs,
    }


@settings(max_examples=24, deadline=None)
@given(
    pair_order=st.permutations((0, 1, 2, 3)),
    document_key_order=st.permutations(
        ("schema_version", "study_id", "design", "analysis_scope", "pairs")
    ),
    pair_key_order=st.permutations(("seed", "control_run_id", "treatment_run_id")),
    design=st.sampled_from(("a-a", "paired-contrast")),
    analysis_scope=st.sampled_from(("attention", "attention-and-response")),
)
def test_definition_canonicalizes_every_accepted_input_permutation(
    pair_order: list[int],
    document_key_order: list[str],
    pair_key_order: list[str],
    design: str,
    analysis_scope: str,
) -> None:
    baseline_document = _study_document(
        design=design,
        analysis_scope=analysis_scope,
    )
    source_pairs = baseline_document["pairs"]
    permuted_pairs = [
        {key: source_pairs[index][key] for key in pair_key_order} for index in pair_order
    ]
    permuted_values = baseline_document | {"pairs": permuted_pairs}
    permuted_document = {key: permuted_values[key] for key in document_key_order}

    baseline = parse_spatial_study_definition_json(
        json.dumps(baseline_document, ensure_ascii=False)
    )
    actual = parse_spatial_study_definition_json(
        json.dumps(permuted_document, ensure_ascii=False, indent=2)
    )

    assert actual == baseline
    assert tuple(pair.seed for pair in actual.pairs) == (0, 1, 2, 3)
    assert tuple(pair.control_run_id for pair in actual.pairs) == tuple(
        f"control-{seed}" for seed in range(4)
    )
    assert canonical_json(actual) == canonical_json(baseline)
    assert actual.fingerprint == baseline.fingerprint


def test_definition_fingerprint_is_exact_sha256_of_canonical_json() -> None:
    document = _study_document(pair_count=3)
    definition = parse_spatial_study_definition_json(json.dumps(document))
    expected_json = (
        '{"analysis_scope":"attention-and-response","design":"paired-contrast",'
        '"pairs":[{"control_run_id":"control-0","seed":0,'
        '"treatment_run_id":"treatment-0"},{"control_run_id":"control-1",'
        '"seed":1,"treatment_run_id":"treatment-1"},{"control_run_id":'
        '"control-2","seed":2,"treatment_run_id":"treatment-2"}],'
        '"schema_version":1,"study_id":"sample-study"}'
    )

    assert canonical_json(definition) == expected_json
    assert definition.fingerprint == sha256(expected_json.encode("utf-8")).hexdigest()
    assert definition.fingerprint == (
        "430971da22e8fd92a6513d03512c010922e53aaa7d27b5f6b62c06cf7eaff87e"
    )


def test_definition_bytes_and_fingerprint_do_not_depend_on_python_hash_seed() -> None:
    script = """
import json

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import parse_spatial_study_definition_json

unordered_pairs = {
    (seed, f"control-{seed}", f"treatment-{seed}")
    for seed in range(8)
}
document = {
    "schema_version": 1,
    "study_id": "hash-seed-study",
    "design": "paired-contrast",
    "analysis_scope": "attention-and-response",
    "pairs": [
        {
            "seed": seed,
            "control_run_id": control_run_id,
            "treatment_run_id": treatment_run_id,
        }
        for seed, control_run_id, treatment_run_id in unordered_pairs
    ],
}
definition = parse_spatial_study_definition_json(json.dumps(document))
print(definition.fingerprint)
print(canonical_json(definition))
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
    fingerprint, serialized = outputs[0].splitlines()
    result = json.loads(serialized)
    assert len(fingerprint) == 64
    assert [pair["seed"] for pair in result["pairs"]] == list(range(8))
