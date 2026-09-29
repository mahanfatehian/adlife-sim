"""Saved mobility artifacts are versioned and have strict cross-field invariants."""

import json

import pytest
from pydantic import ValidationError

from adlife.core.domain import city_run
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


def valid_manifest_v2() -> dict[str, object]:
    return {
        **valid_manifest(),
        "schema_version": 2,
        "model_id": "illustrative-road-mobility-v2",
        "city_schema_version": 2,
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


def test_v2_manifest_round_trips_only_with_v2_model_and_city_schema() -> None:
    model = getattr(city_run, "CityRunManifestV2", None)
    parser = getattr(city_run, "parse_city_run_manifest_json", None)
    assert model is not None, "CityRunManifestV2 is not implemented"
    assert parser is not None, "run manifest version dispatch is not implemented"
    manifest = parser(model.model_validate(valid_manifest_v2()).model_dump_json())
    assert isinstance(manifest, model)
    assert manifest.city_schema_version == 2
    for field, value in (
        ("model_id", "illustrative-road-mobility-v1"),
        ("city_schema_version", 1),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**valid_manifest_v2(), field: value})


@pytest.mark.parametrize("version", [None, True, 1, 2.0, 3, "2"])
def test_v2_manifest_city_schema_version_is_an_exact_integer(version: object) -> None:
    model = city_run.CityRunManifestV2
    parser = city_run.parse_city_run_manifest_json
    document = {**valid_manifest_v2(), "city_schema_version": version}
    if version is None:
        del document["city_schema_version"]
    with pytest.raises(ValidationError, match="city_schema_version"):
        model.model_validate(document)
    with pytest.raises((ValidationError, ValueError), match="city_schema_version"):
        parser(json.dumps(document))


@pytest.mark.parametrize("version", [None, True, 1.0, 0, 3])
def test_run_manifest_version_dispatch_fails_closed(version: object) -> None:
    parser = getattr(city_run, "parse_city_run_manifest_json", None)
    assert parser is not None, "run manifest version dispatch is not implemented"
    document = valid_manifest_v2()
    if version is None:
        del document["schema_version"]
    else:
        document["schema_version"] = version
    with pytest.raises((ValidationError, ValueError), match=r"schema_version|version"):
        parser(json.dumps(document))
