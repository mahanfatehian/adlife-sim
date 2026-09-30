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


def valid_manifest_v3() -> dict[str, object]:
    return {
        **valid_manifest(),
        "schema_version": 3,
        "model_id": "illustrative-road-mobility-v3",
        "city_schema_version": 2,
        "place_schema_version": 1,
        "place_set_sha256": "d" * 64,
        "place_assignments_sha256": "e" * 64,
    }


def valid_manifest_v4() -> dict[str, object]:
    return {
        **valid_manifest(),
        "schema_version": 4,
        "model_id": "illustrative-road-spatial-study-v1",
        "city_schema_version": 2,
        "spatial_scenario_schema_version": 1,
        "spatial_opportunity_schema_version": 1,
        "scenario_sha256": "d" * 64,
        "opportunity_stream_sha256": "e" * 64,
        "opportunity_summary_sha256": "f" * 64,
        "opportunity_stream_bytes": 4096,
        "opportunity_count": 7,
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


def test_v3_manifest_binds_place_set_and_assignments_with_exact_versions() -> None:
    model = getattr(city_run, "CityRunManifestV3", None)
    assert model is not None, "CityRunManifestV3 is not implemented"
    manifest = city_run.parse_city_run_manifest_json(json.dumps(valid_manifest_v3()))
    assert isinstance(manifest, model)
    assert manifest.model_id == "illustrative-road-mobility-v3"
    assert manifest.place_schema_version == 1
    assert manifest.place_set_sha256 == "d" * 64
    assert manifest.place_assignments_sha256 == "e" * 64

    for field, value in (
        ("city_schema_version", True),
        ("city_schema_version", 3),
        ("place_schema_version", True),
        ("place_schema_version", 2),
        ("place_set_sha256", "short"),
        ("place_assignments_sha256", "F" * 64),
        ("model_id", "illustrative-road-mobility-v2"),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**valid_manifest_v3(), field: value})


def test_v4_manifest_binds_spatial_artifacts_and_allows_no_place_set() -> None:
    model = getattr(city_run, "CityRunManifestV4", None)
    assert model is not None, "CityRunManifestV4 is not implemented"
    manifest = city_run.parse_city_run_manifest_json(json.dumps(valid_manifest_v4()))
    assert isinstance(manifest, model)
    assert manifest.model_id == "illustrative-road-spatial-study-v1"
    assert manifest.city_schema_version == 2
    assert manifest.spatial_scenario_schema_version == 1
    assert manifest.spatial_opportunity_schema_version == 1
    assert manifest.scenario_sha256 == "d" * 64
    assert manifest.opportunity_stream_sha256 == "e" * 64
    assert manifest.opportunity_summary_sha256 == "f" * 64
    assert manifest.opportunity_stream_bytes == 4096
    assert manifest.opportunity_count == 7
    assert manifest.place_schema_version is None
    assert manifest.place_set_sha256 is None
    assert manifest.place_assignments_sha256 is None


def test_v4_manifest_accepts_only_a_complete_place_binding() -> None:
    model = getattr(city_run, "CityRunManifestV4", None)
    assert model is not None, "CityRunManifestV4 is not implemented"
    with_places = {
        **valid_manifest_v4(),
        "place_schema_version": 1,
        "place_set_sha256": "1" * 64,
        "place_assignments_sha256": "2" * 64,
    }
    manifest = model.model_validate(with_places)
    assert manifest.place_schema_version == 1
    assert manifest.place_set_sha256 == "1" * 64
    assert manifest.place_assignments_sha256 == "2" * 64

    for missing in (
        "place_schema_version",
        "place_set_sha256",
        "place_assignments_sha256",
    ):
        document = dict(with_places)
        del document[missing]
        with pytest.raises(ValidationError, match="place"):
            model.model_validate(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", "illustrative-road-mobility-v3"),
        ("city_schema_version", True),
        ("city_schema_version", 3),
        ("spatial_scenario_schema_version", True),
        ("spatial_scenario_schema_version", 2),
        ("spatial_opportunity_schema_version", True),
        ("spatial_opportunity_schema_version", 2),
        ("scenario_sha256", "short"),
        ("opportunity_stream_sha256", "E" * 64),
        ("opportunity_summary_sha256", "not-a-hash"),
        ("opportunity_stream_bytes", -1),
        ("opportunity_stream_bytes", 536_870_913),
        ("opportunity_count", -1),
        ("opportunity_count", 520_801),
    ],
)
def test_v4_manifest_refuses_incompatible_or_unbounded_fields(field: str, value: object) -> None:
    model = getattr(city_run, "CityRunManifestV4", None)
    assert model is not None, "CityRunManifestV4 is not implemented"
    with pytest.raises(ValidationError):
        model.model_validate({**valid_manifest_v4(), field: value})


@pytest.mark.parametrize("version", [None, True, 1.0, 0, 5])
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
