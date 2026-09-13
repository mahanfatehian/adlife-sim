import os
import subprocess
import sys

from adlife.core.domain.json_values import freeze_json_mapping
from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.simulation.engine import canonical_sha256

SCENARIO_SHA256 = "f2ef074ca703ee1558cc407bd61958ba838c77f8075d338f1f9cdab0a9976907"
MANIFEST_FINGERPRINT = "1f61e8b7558fd746fc96749175db633800f5b132ae8b0696b74efb7e3673f5a3"
SET_FINGERPRINT = "21c9a53d2836983e38fcb29b7fd59a1aa9197074e092aad9a19d59b90c9e6dc3"
UTF8_FINGERPRINT = "19856dc4da9474539e9ab809717570725806deb13a81801b4c6c28293ea84080"


def test_canonical_sha256_uses_sorted_compact_utf8_json() -> None:
    first = {"z": "\u0633\u0644\u0627\u0645", "a": [1, 2]}
    reordered = {"a": [1, 2], "z": "\u0633\u0644\u0627\u0645"}

    assert canonical_sha256(first) == UTF8_FINGERPRINT
    assert canonical_sha256(reordered) == UTF8_FINGERPRINT


def test_canonical_sha256_accepts_nested_immutable_mappings() -> None:
    value = freeze_json_mapping({"nested": {"value": 1}, "labels": ["a", "b"]})

    assert canonical_sha256(value) == (
        "008b9d4d146cecd475cee371f187d4fcd095c5c5bd679d85eae6ed521c408106"
    )


def test_scenario_fingerprint_uses_pydantic_json_mode(valid_scenario: Scenario) -> None:
    assert canonical_sha256(valid_scenario) == SCENARIO_SHA256


def test_manifest_fingerprint_covers_complete_reproducibility_input(
    valid_scenario: Scenario,
) -> None:
    manifest = RunManifest(
        run_id="run-golden",
        scenario_id=valid_scenario.scenario_id,
        scenario_hash=SCENARIO_SHA256,
        seed=42,
        package_version="0.1.0",
        git_sha="uncommitted",
        lockfile_sha256="c" * 64,
        provider="mock",
        model_id="mock-v1",
        prompt_version="cognition-v1",
        prompt_hash="d" * 64,
        platform="windows-x86_64-python-3.12",
    )
    fingerprint_input = {
        "scenario": valid_scenario,
        "seed": manifest.seed,
        "package_version": manifest.package_version,
        "git_sha": manifest.git_sha,
        "lockfile_sha256": manifest.lockfile_sha256,
        "provider": manifest.provider,
        "model_id": manifest.model_id,
        "prompt_version": manifest.prompt_version,
        "prompt_hash": manifest.prompt_hash,
        "platform": manifest.platform,
    }

    assert canonical_sha256(fingerprint_input) == MANIFEST_FINGERPRINT


def test_canonical_sha256_is_stable_across_python_hash_seeds() -> None:
    script = (
        "from adlife.core.simulation.engine import canonical_sha256; "
        "print(canonical_sha256({'tags': "
        "frozenset(('fitness', 'technology', 'music', 'travel'))}))"
    )
    fingerprints = []
    for hash_seed in ("1", "2"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        fingerprints.append(completed.stdout.strip())

    assert fingerprints == [SET_FINGERPRINT, SET_FINGERPRINT]
