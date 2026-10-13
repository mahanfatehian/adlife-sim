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
from tests.integration.test_city_workbench_artifacts import _schema2_copy
from tests.unit.city.test_city_run_preparation import _validated
from tests.unit.city.test_workbench_input import draft_data, draft_v2_data


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


def test_v7_input_view_explicitly_refuses_schema2_sidecar(tmp_path: Path) -> None:
    stored = _stored_workbench_run(tmp_path)
    schema2 = _schema2_copy(stored.workbench_input)
    candidate = replace(
        stored,
        workbench_input=schema2,
        manifest=stored.manifest.model_copy(update={"workbench_input_sha256": schema2.fingerprint}),
    )
    with pytest.raises(ValueError, match="schema 1"):
        _build_run_input_view(candidate)


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


@pytest.mark.parametrize("version", [1, 2])
def test_http_dispatch_preserves_exact_version_and_maximum_seed(version: int) -> None:
    payload = draft_data() if version == 1 else draft_v2_data()
    payload["settings"]["seed"] = "9223372036854775807"
    original = deepcopy(payload)
    result = parse_workbench_http_draft(payload)
    assert result.schema_version == version
    assert result.settings.seed == 9223372036854775807
    assert payload == original


@pytest.mark.parametrize("version", [True, False, 1.0, 2.0, "2", None, 0, 3])
def test_http_dispatch_refuses_noninteger_and_future_versions(version: object) -> None:
    payload = draft_v2_data()
    payload["schema_version"] = version
    payload["settings"]["seed"] = "42"
    with pytest.raises(ValidationError):
        parse_workbench_http_draft(payload)


def _stored_persona_run(root: Path):
    from adlife.city.workbench_input import WorkbenchRunDraftV2
    from adlife.city.workbench_validation import construct_workbench_run

    payload = draft_v2_data(agent_count=1)
    payload["settings"]["seed"] = 9223372036854775807
    validated = construct_workbench_run(
        WorkbenchRunDraftV2.model_validate_json(canonical_json(payload))
    )
    settings = validated.workbench_input.draft.settings
    return create_city_run(
        validated.pack,
        root=root,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )


def test_v8_input_view_projects_verified_population_and_exact_receipts(tmp_path: Path) -> None:
    from adlife.city.workbench_input import NormalizedWorkbenchPopulationV2

    stored = _stored_persona_run(tmp_path)
    view = _build_run_input_view(stored)
    model = getattr(transport, "WorkbenchRunInputViewV2", None)
    assert model is not None
    assert isinstance(view, model)
    assert isinstance(view.population, NormalizedWorkbenchPopulationV2)
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
        "population",
        "settings",
        "creative_template",
    }
    assert document["run_schema_version"] == 8
    assert document["workbench_input_schema_version"] == 2
    assert document["settings"]["seed"] == "9223372036854775807"
    assert document["population"] == stored.workbench_input.draft.population.model_dump(mode="json")
    assert (
        document["manifest_sha256"]
        == sha256((stored.directory / "run.json").read_bytes()).hexdigest()
    )
    assert document["workbench_input_sha256"] == stored.workbench_input.fingerprint
    assert document["city_sha256"] == stored.pack.fingerprint
    assert document["scenario_sha256"] == stored.spatial_scenario.fingerprint
    assert document["creative_sha256"] == stored.workbench_input.creative_template.fingerprint
    assert model.model_validate_json(view.model_dump_json()) == view
    with pytest.raises(ValidationError, match="frozen"):
        view.run_id = "changed"
    for field, value in [
        ("run_schema_version", True),
        ("run_schema_version", 8.0),
        ("run_schema_version", 9),
        ("workbench_input_schema_version", 1),
        ("workbench_input_schema_version", 2.0),
        ("private_path", "private"),
        ("creative_sha256", "0" * 64),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate_json(canonical_json({**document, field: value}))


@pytest.mark.parametrize(
    "mutation", ["crossed", "missing", "workbench", "city", "scenario", "response", "causal"]
)
def test_v8_input_view_independently_refuses_missing_hashes_and_rehashed_misbinding(
    tmp_path: Path,
    mutation: str,
) -> None:
    stored = _stored_persona_run(tmp_path)
    if mutation == "missing":
        candidate = replace(stored, workbench_input=None)
    elif mutation == "crossed":
        candidate = replace(stored, workbench_input=_validated(agents=1).workbench_input)
    elif mutation == "causal":
        source = stored.workbench_input
        population = source.draft.population
        first = population.personas[0]
        changed = first.model_copy(
            update={"traits": first.traits.model_copy(update={"impulsivity": 0.97})}
        )
        source = source.model_copy(
            update={
                "draft": source.draft.model_copy(
                    update={"population": population.model_copy(update={"personas": (changed,)})}
                )
            }
        )
        candidate = replace(
            stored,
            workbench_input=source,
            manifest=stored.manifest.model_copy(
                update={"workbench_input_sha256": source.fingerprint}
            ),
        )
    else:
        field = {
            "workbench": "workbench_input_sha256",
            "city": "city_sha256",
            "scenario": "scenario_sha256",
            "response": "response_input_sha256",
        }[mutation]
        candidate = replace(stored, manifest=stored.manifest.model_copy(update={field: "0" * 64}))
    with pytest.raises(ValueError, match="workbench"):
        _build_run_input_view(candidate)
