"""Saved mobility artifacts are versioned and have strict cross-field invariants."""

import pytest
from pydantic import ValidationError

from adlife.core.domain.city_run import CityRunManifest, CityTraceSummary


def valid_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "city-test",
        "status": "completed",
        "model_id": "illustrative-road-mobility-v1",
        "package_version": "0.1.0",
        "python_version": "3.12.6",
        "city_sha256": "a" * 64,
        "agents_sha256": "b" * 64,
        "trace_sha256": "c" * 64,
        "seed": 42,
        "agent_count": 2,
        "days": 1,
        "frame_count": 1440,
        "position_count": 2880,
    }


def test_valid_manifest_round_trips_and_is_immutable() -> None:
    manifest = CityRunManifest.model_validate(valid_manifest())
    assert CityRunManifest.model_validate_json(manifest.model_dump_json()) == manifest
    with pytest.raises(ValidationError):
        manifest.run_id = "changed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("run_id", "../escape"),
        ("run_id", "con"),
        ("status", "running"),
        ("seed", True),
        ("seed", -1),
        ("agent_count", 31),
        ("days", 8),
        ("city_sha256", "short"),
        ("agents_sha256", "not-a-hash"),
        ("trace_sha256", "Z" * 64),
        ("python_version", "unknown"),
        ("model_id", "other-model"),
        ("frame_count", 1439),
        ("position_count", 1),
    ],
)
def test_manifest_refuses_incompatible_or_inconsistent_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CityRunManifest.model_validate({**valid_manifest(), field: value})


def test_trace_summary_refuses_wrong_counts() -> None:
    with pytest.raises(ValidationError):
        CityTraceSummary.model_validate(
            {
                "trace_sha256": "a" * 64,
                "agents_sha256": "b" * 64,
                "frame_count": 1440,
                "position_count": 0,
            }
        )
