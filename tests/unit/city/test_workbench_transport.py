from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import adlife.city.workbench_transport as transport
from adlife.city.catalog import CityCatalogError, load_city_catalog, select_catalog_city
from adlife.city.runs import create_city_run
from adlife.city.workbench_transport import (
    WorkbenchCityDetail,
    WorkbenchDraftTransportError,
    build_workbench_city_detail,
    parse_workbench_http_draft,
)
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_run_preparation import _validated
from tests.unit.city.test_workbench_input import draft_data


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0", 0),
        ("42", 42),
        ("9223372036854775807", 2**63 - 1),
    ],
)
def test_http_draft_translates_canonical_seed_string_exactly(
    token: str,
    expected: int,
) -> None:
    payload = draft_data()
    payload["settings"]["seed"] = token
    original = deepcopy(payload)

    draft = parse_workbench_http_draft(payload)

    assert draft.settings.seed == expected
    assert payload == original
    assert payload["settings"]["seed"] == token


@pytest.mark.parametrize(
    "token",
    [
        0,
        42,
        True,
        False,
        None,
        "+1",
        "-1",
        " 1",
        "1 ",
        "1.0",
        "1e3",
        "01",
        "",
        "0000000000000000000",
        "12345678901234567890",
        "9223372036854775808",
    ],
)
def test_http_draft_refuses_noncanonical_or_out_of_range_seed(token: object) -> None:
    payload = draft_data()
    payload["settings"]["seed"] = token

    with pytest.raises(WorkbenchDraftTransportError) as caught:
        parse_workbench_http_draft(payload)

    assert caught.value.field == "settings.seed"
    assert caught.value.message == ("Enter a whole-number seed from 0 through 9223372036854775807.")
    assert str(caught.value) == caught.value.message


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"settings": None},
        {"settings": []},
        {"settings": {}},
    ],
)
def test_http_draft_refuses_missing_or_non_object_seed_location(
    payload: dict[str, object],
) -> None:
    with pytest.raises(WorkbenchDraftTransportError) as caught:
        parse_workbench_http_draft(payload)

    assert caught.value.field == "settings.seed"
    assert caught.value.message == ("Enter a whole-number seed from 0 through 9223372036854775807.")


def test_http_draft_leaves_all_other_fields_to_strict_domain_validation() -> None:
    payload = draft_data()
    payload["settings"]["seed"] = "42"
    payload["unexpected"] = True

    with pytest.raises(ValidationError) as caught:
        parse_workbench_http_draft(payload)

    assert caught.value.errors()[0]["type"] == "extra_forbidden"


def test_city_detail_contains_verified_path_free_catalog_data() -> None:
    catalog = load_city_catalog()
    entry = catalog.entries[0]
    pack = select_catalog_city(entry.city_id)

    detail = build_workbench_city_detail(entry.city_id)
    document = detail.model_dump(mode="json")

    assert isinstance(detail, WorkbenchCityDetail)
    assert document == {
        "schema_version": 1,
        "city_id": entry.city_id,
        "display_name": entry.display_name,
        "pack_schema_version": entry.pack_schema_version,
        "data_origin": entry.data_origin,
        "qualification": entry.qualification,
        "reviewer_role": entry.reviewer_role,
        "reviewed_on": entry.reviewed_on,
        "coverage": entry.coverage.model_dump(mode="json"),
        "time_zone": entry.time_zone,
        "source": entry.source.model_dump(mode="json"),
        "known_omissions": list(entry.known_omissions),
        "city_sha256": pack.fingerprint,
        "pack": pack.model_dump(mode="json"),
    }
    assert "resource_name" not in document
    assert "pack_sha256" not in document


def test_city_detail_is_strict_frozen_and_forbids_unknown_fields() -> None:
    detail = build_workbench_city_detail("fictional-grid-v2")
    document: dict[str, Any] = detail.model_dump(mode="python", round_trip=True)
    document["resource_name"] = "private-resource.json"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkbenchCityDetail.model_validate(document)
    frozen_field = "city_id"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(detail, frozen_field, "changed")


def test_city_detail_cross_checks_catalog_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adlife.city.workbench_transport as transport

    catalog = load_city_catalog()
    entry = catalog.entries[0]
    mismatched = entry.model_copy(update={"pack_sha256": "0" * 64})
    mismatched_catalog = catalog.model_copy(update={"entries": (mismatched,)})
    monkeypatch.setattr(transport, "load_city_catalog", lambda: mismatched_catalog)

    with pytest.raises(CityCatalogError, match="integrity"):
        build_workbench_city_detail(entry.city_id)


def _stored_workbench_run(root: Path, *, seed: int = 42):
    validated = _validated(
        run_id="browser-assumptions",
        seed=seed,
        agents=1,
        days=2,
    )
    return create_city_run(
        validated.pack,
        root=root,
        run_id="browser-assumptions",
        seed=seed,
        agent_count=1,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )


def _build_run_input_view(stored: object):
    builder = getattr(transport, "build_workbench_run_input_view", None)
    assert builder is not None, "run input view builder is not implemented"
    return builder(stored)


def test_run_input_view_projects_the_complete_verified_v7_input_exactly(
    tmp_path: Path,
) -> None:
    stored = _stored_workbench_run(tmp_path, seed=2**63 - 1)
    assert stored.workbench_input is not None
    assert stored.spatial_scenario is not None
    source = stored.workbench_input

    view = _build_run_input_view(stored)
    document = view.model_dump(mode="json")

    assert set(document) == {
        "run_id",
        "run_schema_version",
        "workbench_input_schema_version",
        "manifest_sha256",
        "workbench_input_sha256",
        "city_sha256",
        "scenario_sha256",
        "creative_sha256",
        "scenario",
        "cohort",
        "settings",
        "creative_template",
    }
    assert document["run_id"] == "browser-assumptions"
    assert document["run_schema_version"] == 7
    assert document["workbench_input_schema_version"] == 1
    assert (
        document["manifest_sha256"]
        == sha256((canonical_json(stored.manifest) + "\n").encode("utf-8")).hexdigest()
    )
    assert (
        document["manifest_sha256"]
        == sha256((stored.directory / "run.json").read_bytes()).hexdigest()
    )
    assert document["workbench_input_sha256"] == source.fingerprint
    assert document["city_sha256"] == stored.pack.fingerprint
    assert document["scenario_sha256"] == stored.spatial_scenario.fingerprint
    assert document["creative_sha256"] == source.creative_template.fingerprint
    assert document["scenario"] == source.draft.scenario.model_dump(mode="json")
    assert document["cohort"] == source.draft.cohort.model_dump(mode="json")
    assert document["settings"] == {
        "run_id": "browser-assumptions",
        "agent_count": 1,
        "days": 2,
        "seed": "9223372036854775807",
        "response_mode": "deterministic-rules",
    }
    assert document["creative_template"] == source.creative_template.model_dump(mode="json")


def test_run_input_view_is_strict_frozen_and_round_trips_without_coercion(
    tmp_path: Path,
) -> None:
    view = _build_run_input_view(_stored_workbench_run(tmp_path))
    model = getattr(transport, "WorkbenchRunInputView", None)
    assert model is not None, "run input view model is not implemented"
    round_trip = model.model_validate_json(view.model_dump_json())
    document: dict[str, Any] = view.model_dump(mode="python", round_trip=True)
    document["private_path"] = r"C:\private\workbench.json"

    assert round_trip == view
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(document)
    frozen_field = "run_id"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(view, frozen_field, "changed")


@pytest.mark.parametrize("mutation", ["legacy", "missing", "mismatched-hash", "wrong-run"])
def test_run_input_view_refuses_incomplete_or_mismatched_bindings(
    tmp_path: Path,
    mutation: str,
) -> None:
    stored = _stored_workbench_run(tmp_path / "source")
    assert stored.workbench_input is not None
    if mutation == "legacy":
        validated = _validated(run_id="legacy-response", agents=1, days=2)
        candidate = create_city_run(
            validated.pack,
            root=tmp_path / "legacy",
            run_id="legacy-response",
            seed=42,
            agent_count=1,
            days=2,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
        )
    elif mutation == "missing":
        candidate = replace(stored, workbench_input=None)
    elif mutation == "mismatched-hash":
        candidate = replace(
            stored,
            manifest=stored.manifest.model_copy(update={"workbench_input_sha256": "0" * 64}),
        )
    else:
        other = _validated(run_id="other-run", agents=1, days=2).workbench_input
        candidate = replace(stored, workbench_input=other)

    with pytest.raises(ValueError, match="workbench"):
        _build_run_input_view(candidate)
