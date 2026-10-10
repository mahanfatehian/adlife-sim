"""Saved mobility artifacts are versioned and have strict cross-field invariants."""

import json
from collections.abc import Callable
from hashlib import sha256

import pytest
from pydantic import ValidationError

from adlife.core.domain import city_run
from adlife.core.domain.city_run import CityRunManifest, CityTraceSummary
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json


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


def valid_manifest_v5() -> dict[str, object]:
    return {
        **valid_manifest_v4(),
        "schema_version": 5,
        "model_id": "illustrative-road-spatial-attention-study-v1",
        "spatial_attention_schema_version": 1,
        "spatial_attention_model_id": "spatial-attention-v1",
        "attention_stream_sha256": "1" * 64,
        "attention_summary_sha256": "2" * 64,
        "attention_stream_bytes": 8192,
        "impression_count": 7,
        "noticed_count": 3,
    }


def valid_manifest_v6() -> dict[str, object]:
    return {
        **valid_manifest_v5(),
        "schema_version": 6,
        "model_id": "illustrative-road-spatial-response-study-v1",
        "spatial_response_schema_version": 1,
        "spatial_response_model_id": "spatial-response-v1",
        "response_input_sha256": "3" * 64,
        "response_stream_sha256": "4" * 64,
        "response_state_sha256": "5" * 64,
        "response_summary_sha256": "6" * 64,
        "response_stream_bytes": 12_288,
        "response_count": 3,
        "state_update_count": 2,
        "response_campaign_count": 2,
        "final_state_count": 4,
    }


def valid_manifest_v7() -> dict[str, object]:
    return {
        **valid_manifest_v6(),
        "schema_version": 7,
        "workbench_input_schema_version": 1,
        "workbench_input_sha256": "7" * 64,
    }


@pytest.mark.parametrize(
    "factory",
    [
        valid_manifest,
        valid_manifest_v2,
        valid_manifest_v3,
        valid_manifest_v4,
        valid_manifest_v5,
        valid_manifest_v6,
        valid_manifest_v7,
    ],
)
@pytest.mark.parametrize(
    "version",
    [
        "0.1.0\rX",
        "0.1.0\nX",
        r"C:\Users\Example\private",
        "/private/local/version",
        "ADLIFE_API_KEY=fixture-secret",
        "0.1.0+sk-ant-" + "a" * 20,
        "not-a-version",
    ],
)
def test_all_manifest_versions_refuse_unsafe_package_version_text(factory, version):
    document = {**factory(), "package_version": version}
    with pytest.raises(ValueError):
        city_run.parse_city_run_manifest_json(canonical_json(document))


@pytest.mark.parametrize(
    "factory",
    [
        valid_manifest,
        valid_manifest_v2,
        valid_manifest_v3,
        valid_manifest_v4,
        valid_manifest_v5,
        valid_manifest_v6,
        valid_manifest_v7,
    ],
)
@pytest.mark.parametrize(
    "version",
    [
        "0.1.0",
        "1",
        "01.002.0",
        "v1.0.0",
        "2!1.4.0rc2",
        "1.2.3a1",
        "1.2.3b2",
        "1.2.3.post1",
        "1.2.3.dev2",
        "1.2.3rc1.post2.dev3+local.4",
        "1.0-1",
        "1.0rev2",
        "1.0-preview3",
        "1.0+linux_amd64.2",
    ],
)
def test_all_manifest_versions_preserve_supported_pep440_package_versions(factory, version):
    document = {**factory(), "package_version": version}
    manifest = city_run.parse_city_run_manifest_json(canonical_json(document))
    assert manifest.package_version == version
    assert manifest.schema_version == document["schema_version"]


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


def test_v5_manifest_binds_attention_artifacts_and_funnel_counts() -> None:
    model = getattr(city_run, "CityRunManifestV5", None)
    assert model is not None, "CityRunManifestV5 is not implemented"

    manifest = city_run.parse_city_run_manifest_json(json.dumps(valid_manifest_v5()))

    assert isinstance(manifest, model)
    assert manifest.model_id == "illustrative-road-spatial-attention-study-v1"
    assert manifest.spatial_attention_schema_version == 1
    assert manifest.spatial_attention_model_id == "spatial-attention-v1"
    assert manifest.attention_stream_sha256 == "1" * 64
    assert manifest.attention_summary_sha256 == "2" * 64
    assert manifest.attention_stream_bytes == 8192
    assert manifest.impression_count == manifest.opportunity_count == 7
    assert manifest.noticed_count == 3
    assert manifest.place_schema_version is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", "illustrative-road-spatial-study-v1"),
        ("city_schema_version", True),
        ("city_schema_version", 3),
        ("spatial_scenario_schema_version", True),
        ("spatial_opportunity_schema_version", 2),
        ("spatial_attention_schema_version", True),
        ("spatial_attention_schema_version", 2),
        ("spatial_attention_model_id", "other-model"),
        ("attention_stream_sha256", "A" * 64),
        ("attention_summary_sha256", "short"),
        ("attention_stream_bytes", -1),
        ("attention_stream_bytes", 1_073_741_825),
        ("impression_count", -1),
        ("impression_count", 520_801),
        ("impression_count", 6),
        ("noticed_count", -1),
        ("noticed_count", 520_801),
        ("noticed_count", 8),
    ],
)
def test_v5_manifest_refuses_incompatible_or_incoherent_attention_fields(
    field: str, value: object
) -> None:
    model = getattr(city_run, "CityRunManifestV5", None)
    assert model is not None, "CityRunManifestV5 is not implemented"
    with pytest.raises(ValidationError):
        model.model_validate({**valid_manifest_v5(), field: value})


def test_v5_manifest_accepts_only_a_complete_place_binding() -> None:
    model = city_run.CityRunManifestV5
    with_places = {
        **valid_manifest_v5(),
        "place_schema_version": 1,
        "place_set_sha256": "3" * 64,
        "place_assignments_sha256": "4" * 64,
    }
    manifest = model.model_validate(with_places)
    assert manifest.place_set_sha256 == "3" * 64

    for missing in (
        "place_schema_version",
        "place_set_sha256",
        "place_assignments_sha256",
    ):
        document = dict(with_places)
        del document[missing]
        with pytest.raises(ValidationError, match="place"):
            model.model_validate(document)


def test_v6_manifest_binds_complete_response_artifacts_and_parser_dispatch() -> None:
    model = getattr(city_run, "CityRunManifestV6", None)
    assert model is not None, "CityRunManifestV6 is not implemented"

    manifest = city_run.parse_city_run_manifest_json(json.dumps(valid_manifest_v6()))

    assert isinstance(manifest, model)
    assert not isinstance(manifest, city_run.CityRunManifestV5)
    assert manifest.model_id == "illustrative-road-spatial-response-study-v1"
    assert manifest.response_input_sha256 == "3" * 64
    assert manifest.spatial_response_model_id == "spatial-response-v1"
    assert manifest.response_count == manifest.noticed_count == 3
    assert manifest.state_update_count == 2
    assert manifest.response_campaign_count == 2
    assert manifest.final_state_count == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", "illustrative-road-spatial-attention-study-v1"),
        ("spatial_response_schema_version", True),
        ("spatial_response_schema_version", 2),
        ("spatial_response_model_id", "other-model"),
        ("response_input_sha256", "short"),
        ("response_stream_sha256", "A" * 64),
        ("response_state_sha256", "short"),
        ("response_summary_sha256", "short"),
        ("response_stream_bytes", -1),
        ("response_stream_bytes", 2_147_483_649),
        ("response_count", 2),
        ("state_update_count", 4),
        ("response_campaign_count", 0),
        ("response_campaign_count", 21),
        ("final_state_count", 3),
        ("final_state_count", 601),
    ],
)
def test_v6_manifest_refuses_incompatible_or_incoherent_response_fields(
    field: str, value: object
) -> None:
    model = getattr(city_run, "CityRunManifestV6", None)
    assert model is not None, "CityRunManifestV6 is not implemented"
    with pytest.raises(ValidationError):
        model.model_validate({**valid_manifest_v6(), field: value})


def test_v6_manifest_allows_zero_notices_only_with_zero_state_updates() -> None:
    document = {
        **valid_manifest_v6(),
        "noticed_count": 0,
        "response_count": 0,
        "state_update_count": 0,
    }
    assert city_run.CityRunManifestV6.model_validate(document).response_count == 0

    with pytest.raises(ValidationError, match="updates"):
        city_run.CityRunManifestV6.model_validate({**document, "state_update_count": 1})


def test_v7_manifest_binds_canonical_workbench_input_and_parser_dispatch() -> None:
    model = getattr(city_run, "CityRunManifestV7", None)
    assert model is not None, "CityRunManifestV7 is not implemented"

    manifest = city_run.parse_city_run_manifest_json(json.dumps(valid_manifest_v7()))

    assert isinstance(manifest, model)
    assert manifest.schema_version == 7
    assert manifest.workbench_input_schema_version == 1
    assert manifest.workbench_input_sha256 == "7" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 6),
        ("schema_version", True),
        ("workbench_input_schema_version", True),
        ("workbench_input_schema_version", 2),
        ("workbench_input_sha256", "short"),
        ("workbench_input_sha256", "A" * 64),
    ],
)
def test_v7_manifest_refuses_invalid_workbench_bindings(field: str, value: object) -> None:
    model = getattr(city_run, "CityRunManifestV7", None)
    assert model is not None, "CityRunManifestV7 is not implemented"
    with pytest.raises(ValidationError):
        model.model_validate({**valid_manifest_v7(), field: value})


def test_manifest_parser_refuses_unknown_version_after_v7() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        city_run.parse_city_run_manifest_json(
            json.dumps({**valid_manifest_v7(), "schema_version": 8})
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("city_schema_version", True),
        ("spatial_scenario_schema_version", 1.0),
        ("spatial_opportunity_schema_version", "1"),
        ("spatial_attention_schema_version", True),
        ("frame_count", 1439),
        ("position_count", 1),
        ("impression_count", 6),
        ("noticed_count", 8),
        ("place_schema_version", 1),
        ("place_set_sha256", "7" * 64),
        ("place_assignments_sha256", "8" * 64),
    ],
)
def test_v6_manifest_preserves_complete_mobility_attention_and_place_invariants(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        city_run.CityRunManifestV6.model_validate({**valid_manifest_v6(), field: value})


@pytest.mark.parametrize(
    ("model", "factory", "byte_count", "digest"),
    [
        (
            city_run.CityRunManifest,
            valid_manifest,
            480,
            "f8ed6e685f8cb9bd9975aa3e5f3d7c727f5e27842d073f411aefaea307e60fb6",
        ),
        (
            city_run.CityRunManifestV2,
            valid_manifest_v2,
            504,
            "e056b5e81efff43923116eda572955d56604a686a7c2a2dbf87fb189671b8b41",
        ),
        (
            city_run.CityRunManifestV3,
            valid_manifest_v3,
            709,
            "b15cf1ca7e6bcef735813e217ea94fe06d32b50fbdd546bf1eec823047235a01",
        ),
        (
            city_run.CityRunManifestV4,
            valid_manifest_v4,
            998,
            "b0aadebe08c27b85090cd5f382b0bae3c99789814956f9d94c73257138b231a9",
        ),
        (
            city_run.CityRunManifestV5,
            valid_manifest_v5,
            1_353,
            "03081287daf54116505a22a56366da683631b4d49a61a2e26d92e4a8b305cb5a",
        ),
    ],
)
def test_legacy_manifest_canonical_bytes_remain_frozen(
    model: type[DomainModel],
    factory: Callable[[], dict[str, object]],
    byte_count: int,
    digest: str,
) -> None:
    document = factory()
    manifest = model.model_validate(document)
    frozen = (canonical_json(manifest) + "\n").encode("utf-8")

    assert len(frozen) == byte_count
    assert sha256(frozen).hexdigest() == digest
    assert city_run.parse_city_run_manifest_json(frozen) == manifest


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


@pytest.mark.parametrize("version", [None, True, 1.0, 0, 7])
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
