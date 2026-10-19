"""Saved-run cognition projections dispatch by the exact verified manifest version."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from adlife.city import workbench_transport as transport
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.workbench_cognition_views import (
    WorkbenchCognitionRecordsViewV1,
    WorkbenchCognitionRecordsViewV2,
    WorkbenchCognitionSummaryViewV1,
    WorkbenchCognitionSummaryViewV2,
    build_saved_workbench_run_input_view,
    build_workbench_cognition_records_view,
    build_workbench_cognition_summary_view,
    build_workbench_run_input_view_v4,
)
from tests.integration.test_city_cognition_v2_artifacts import _saved_v10
from tests.integration.test_city_run_replay import _create_v9_run

V2_SUMMARY_KEYS = [
    "schema_version",
    "run_id",
    "run_schema_version",
    "effect_scope",
    "cognition_input_sha256",
    "mode",
    "provider_profile_id",
    "provider_revision_sha256",
    "model_id",
    "funded_response_limit",
    "causal_contract_sha256",
    "prompt_contract_id",
    "prompt_contract_sha256",
    "result_schema_id",
    "result_schema_contract_sha256",
    "selection_policy_sha256",
    "fallback_policy_sha256",
    "mock_policy_sha256",
    "influence_policy_id",
    "influence_policy_sha256",
    "summary",
]


def dense_phone(document: dict[str, object]) -> None:
    """Enough notices for unselected and budget-skipped responses in four agents."""
    phone = document["scenario"]["phone"]  # type: ignore[index]
    phone["frequency_cap_per_agent_per_day"] = 10  # type: ignore[index]
    phone["opportunity_probability_per_minute"] = 0.3  # type: ignore[index]


@pytest.fixture(scope="module")
def saved(tmp_path_factory: pytest.TempPathFactory) -> dict[str, StoredCityRun]:
    root = tmp_path_factory.mktemp("cognition-views")
    _saved_v10(root, mode="rules", run_id="rules-v10")
    _saved_v10(root, mode="mock", run_id="mock-v10")
    _saved_v10(root, mode="local", run_id="provider-v10", agents=4, mutation=dense_phone)
    _create_v9_run(root, run_id="explanation-v9")
    store = CityRunStore(root)
    return {
        name: store.load(name)
        for name in ("rules-v10", "mock-v10", "provider-v10", "explanation-v9")
    }


def _legacy_summary(stored: StoredCityRun) -> dict[str, object]:
    """The exact pre-Task-5 route body, kept here as the V9 compatibility oracle."""
    cognition_input = stored.cognition_input
    evaluation = stored.cognition_evaluation
    assert cognition_input is not None and evaluation is not None
    profile = cognition_input.provider_profile
    return {
        "schema_version": 1,
        "run_id": stored.manifest.run_id,
        "run_schema_version": 9,
        "cognition_input_sha256": cognition_input.fingerprint,
        "mode": cognition_input.mode,
        "explainer_id": cognition_input.explainer_id,  # type: ignore[union-attr]
        "provider_profile_id": None if profile is None else profile.profile_id,
        "provider_revision_sha256": None if profile is None else profile.revision_sha256,
        "model_id": None if profile is None else profile.model_id,
        "aggregate_output_token_ceiling": cognition_input.aggregate_output_token_ceiling,
        "summary": evaluation.summary.model_dump(mode="json"),
    }


def test_v10_summary_view_is_path_free_and_fully_bound(saved) -> None:
    for run_id in ("rules-v10", "mock-v10", "provider-v10"):
        stored = saved[run_id]
        view = build_workbench_cognition_summary_view(stored)
        assert type(view) is WorkbenchCognitionSummaryViewV2
        document = view.model_dump(mode="json")
        assert list(document) == V2_SUMMARY_KEYS
        cognition_input = stored.cognition_input
        contract = cognition_input.causal_contract
        receipt = stored.manifest.cognition
        assert document["run_id"] == run_id
        assert (document["schema_version"], document["run_schema_version"]) == (2, 10)
        assert document["effect_scope"] == "bounded-signals-v1"
        assert document["cognition_input_sha256"] == cognition_input.fingerprint
        assert document["cognition_input_sha256"] == receipt.cognition_input_sha256
        assert document["funded_response_limit"] == cognition_input.funded_response_limit
        assert document["summary"] == stored.cognition_evaluation.summary.model_dump(mode="json")
        for field in (
            "causal_contract_sha256",
            "prompt_contract_sha256",
            "result_schema_contract_sha256",
            "selection_policy_sha256",
            "fallback_policy_sha256",
            "mock_policy_sha256",
            "influence_policy_sha256",
        ):
            assert document[field] == getattr(receipt, field)
        assert document["prompt_contract_id"] == contract.prompt_contract_id
        assert document["result_schema_id"] == contract.result_schema_id
        assert document["influence_policy_id"] == stored.manifest.influence_policy_id
        encoded = view.model_dump_json()
        assert str(stored.directory) not in encoded
        assert "prompt_contract_text" not in encoded and "Select one closed" not in encoded
        assert WorkbenchCognitionSummaryViewV2.model_validate_json(encoded) == view
    offline = saved["mock-v10"]
    assert (
        build_workbench_cognition_summary_view(offline).model_dump(mode="json")[
            "provider_profile_id"
        ]
        is None
    )
    provider = build_workbench_cognition_summary_view(saved["provider-v10"])
    profile = saved["provider-v10"].cognition_input.provider_profile
    assert (provider.provider_profile_id, provider.provider_revision_sha256, provider.model_id) == (
        profile.profile_id,
        profile.revision_sha256,
        profile.model_id,
    )
    # Transport facts of the frozen profile never reach the browser projection.
    for secretless in (profile.base_url, profile.credential_slot_id, profile.display_label):
        assert secretless not in provider.model_dump_json()


def test_v10_records_bind_selected_applied_responses_and_never_unselected(saved) -> None:
    stored = saved["provider-v10"]
    view = build_workbench_cognition_records_view(stored)
    assert type(view) is WorkbenchCognitionRecordsViewV2
    document = view.model_dump(mode="json")
    assert list(document) == [
        "schema_version",
        "run_id",
        "run_schema_version",
        "cognition_input_sha256",
        "records",
    ]
    evaluation = stored.cognition_evaluation
    assert document["records"] == [record.model_dump(mode="json") for record in evaluation.records]
    applied = [
        record
        for record in stored.response_evaluation.records
        if record.event_type == "spatial.applied-response"
    ]
    by_hash = {record.record_hash: record for record in evaluation.records}
    coverage = evaluation.summary.coverage
    assert coverage.unselected_response_count > 0 and coverage.budget_skipped_count > 0
    assert {record["source"] for record in document["records"]} == {"provider", "budget-skipped"}
    bound = [item for item in applied if item.terminal_record_hash is not None]
    assert sorted(item.terminal_record_hash for item in bound) == sorted(by_hash)
    for item in bound:
        terminal = by_hash[item.terminal_record_hash]
        assert terminal.baseline_response_id == item.baseline.event_id
        assert (terminal.agent_id, terminal.campaign_id, terminal.model_minute) == (
            item.baseline.agent_id,
            item.baseline.campaign_id,
            item.baseline.model_minute,
        )
        assert item.source == terminal.source
    selected_baselines = {record.baseline_response_id for record in evaluation.records}
    unselected = [item for item in applied if item.source == "unselected"]
    assert len(unselected) == coverage.unselected_response_count
    assert all(item.baseline.event_id not in selected_baselines for item in unselected)
    assert WorkbenchCognitionRecordsViewV2.model_validate_json(view.model_dump_json()) == view


def test_v9_views_keep_the_exact_legacy_documents(saved) -> None:
    stored = saved["explanation-v9"]
    summary = build_workbench_cognition_summary_view(stored)
    records = build_workbench_cognition_records_view(stored)
    assert type(summary) is WorkbenchCognitionSummaryViewV1
    assert type(records) is WorkbenchCognitionRecordsViewV1
    # Byte equality of the serialized JSON proves both keys and their order.
    assert json.dumps(summary.model_dump(mode="json")) == json.dumps(_legacy_summary(stored))
    assert json.dumps(records.model_dump(mode="json")) == json.dumps(
        {
            "schema_version": 1,
            "run_id": "explanation-v9",
            "run_schema_version": 9,
            "cognition_input_sha256": stored.cognition_input.fingerprint,
            "records": [
                record.model_dump(mode="json") for record in stored.cognition_evaluation.records
            ],
        }
    )


def test_saved_workbench_input_view_dispatches_v3_and_the_exact_v4_contract(saved) -> None:
    stored = saved["provider-v10"]
    view = build_saved_workbench_run_input_view(stored)
    assert type(view) is transport.WorkbenchRunInputViewV4
    assert view == build_workbench_run_input_view_v4(stored)
    document = view.model_dump(mode="json")
    source = stored.workbench_input
    assert (document["run_schema_version"], document["workbench_input_schema_version"]) == (10, 4)
    assert document["workbench_input_sha256"] == source.fingerprint
    assert (
        document["manifest_sha256"]
        == sha256((stored.directory / "run.json").read_bytes()).hexdigest()
    )
    assert document["cognition"] == source.draft.cognition.model_dump(mode="json")
    assert document["population"] == source.draft.population.model_dump(mode="json")
    assert document["settings"]["seed"] == str(source.draft.settings.seed)
    assert str(stored.directory) not in view.model_dump_json()
    assert (
        TypeAdapter(transport.WorkbenchRunInputViewDocument).validate_json(view.model_dump_json())
        == view
    )
    legacy = saved["explanation-v9"]
    assert build_saved_workbench_run_input_view(legacy) == transport.build_workbench_run_input_view(
        legacy
    )
    assert type(build_saved_workbench_run_input_view(legacy)) is transport.WorkbenchRunInputViewV3


def _with_manifest(stored: StoredCityRun, **update: object) -> StoredCityRun:
    # Bypass validation so a hostile in-memory manifest reaches the builders.
    return replace(stored, manifest=BaseModel.model_copy(stored.manifest, update=update))


@pytest.mark.parametrize("version", [True, 9.0, 10.0, 11, "10", 8])
@pytest.mark.parametrize("source", ["explanation-v9", "mock-v10"])
def test_versions_refuse_before_reading_version_specific_fields(saved, version, source) -> None:
    # Garbage evidence proves the version gate runs before any evidence is read.
    hostile = replace(
        _with_manifest(saved[source], schema_version=version),
        cognition_input=object(),
        cognition_evaluation=object(),
        workbench_input=object(),
    )
    for builder in (build_workbench_cognition_summary_view, build_workbench_cognition_records_view):
        with pytest.raises(ValueError, match="schema-v9 or schema-v10"):
            builder(hostile)
    with pytest.raises(ValueError, match=r"versions are inconsistent|requires a schema-v10 run"):
        build_saved_workbench_run_input_view(hostile)


@pytest.mark.parametrize(
    ("target", "donor"),
    [("explanation-v9", "mock-v10"), ("mock-v10", "explanation-v9")],
)
def test_one_version_cannot_project_the_other_versions_evidence(saved, target, donor) -> None:
    crossed = replace(
        saved[target],
        cognition_input=saved[donor].cognition_input,
        cognition_evaluation=saved[donor].cognition_evaluation,
    )
    for builder in (build_workbench_cognition_summary_view, build_workbench_cognition_records_view):
        with pytest.raises(ValueError, match="cognition evidence"):
            builder(crossed)


@pytest.mark.parametrize("donor", ["rules-v10", "provider-v10"])
@pytest.mark.parametrize("field", ["cognition_input", "cognition_evaluation", "manifest"])
def test_v10_views_refuse_evidence_bound_to_another_run(saved, donor, field) -> None:
    crossed = replace(saved["mock-v10"], **{field: getattr(saved[donor], field)})
    for builder in (
        build_workbench_cognition_summary_view,
        build_workbench_cognition_records_view,
        build_workbench_run_input_view_v4,
    ):
        with pytest.raises(ValueError):
            builder(crossed)


def test_builders_refuse_values_that_are_not_verified_stored_runs(saved) -> None:
    for builder in (
        build_workbench_cognition_summary_view,
        build_workbench_cognition_records_view,
        build_workbench_run_input_view_v4,
        build_saved_workbench_run_input_view,
    ):
        with pytest.raises(TypeError):
            builder(saved["mock-v10"].manifest)  # type: ignore[arg-type]


def _summary_payload(saved) -> dict[str, object]:
    return build_workbench_cognition_summary_view(saved["mock-v10"]).model_dump(mode="json")


@pytest.mark.parametrize(
    "mutation",
    [
        {"run_schema_version": True},
        {"run_schema_version": 10.0},
        {"run_schema_version": 11},
        {"schema_version": 1},
        {"funded_response_limit": 20},
        {"funded_response_limit": 60.0},
        {"provider_profile_id": "provider-main"},
        {"model_id": "fictional-explainer"},
        {"mode": "rules"},
        {"prompt_contract_sha256": "0" * 64},
        {"influence_policy_sha256": "0" * 64},
        {"influence_policy_id": "city-cognition-influence-v2"},
        {"base_url": "http://127.0.0.1:11434/v1"},
        {"run_id": "../escape"},
    ],
)
def test_v2_summary_document_revalidates_every_binding(saved, mutation) -> None:
    payload = _summary_payload(saved) | mutation
    with pytest.raises(ValidationError):
        WorkbenchCognitionSummaryViewV2.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", "sk-proj-" + "a" * 40),
        ("model_id", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"),
        ("provider_profile_id", None),
        ("provider_revision_sha256", None),
    ],
)
def test_v2_provider_summary_refuses_secret_shaped_or_partial_provenance(
    saved, field, value
) -> None:
    payload = build_workbench_cognition_summary_view(saved["provider-v10"]).model_dump(mode="json")
    assert WorkbenchCognitionSummaryViewV2.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError) as refused:
        WorkbenchCognitionSummaryViewV2.model_validate_json(json.dumps(payload | {field: value}))
    if value is not None:
        assert value not in str(refused.value)


@pytest.mark.parametrize("mutation", ["duplicate", "reorder", "schema", "orphan-run"])
def test_v2_records_document_refuses_noncanonical_streams(saved, mutation) -> None:
    payload = build_workbench_cognition_records_view(saved["provider-v10"]).model_dump(mode="json")
    records = payload["records"]
    assert isinstance(records, list) and len(records) > 2
    if mutation == "duplicate":
        records.append(records[0])
    elif mutation == "reorder":
        records[0], records[1] = records[1], records[0]
    elif mutation == "schema":
        payload["run_schema_version"] = 9
    else:
        other = build_workbench_cognition_records_view(saved["mock-v10"]).model_dump(mode="json")
        records.append(other["records"][0] | {"selection_index": len(records)})
    with pytest.raises(ValidationError):
        WorkbenchCognitionRecordsViewV2.model_validate_json(json.dumps(payload))


def test_views_do_not_read_provider_cache_keyring_environment_or_files(
    saved, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    import keyring

    import adlife.adapters.cognition.city_cache_v2 as cache_v2
    import adlife.adapters.cognition.city_credentials as credentials

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a saved-run projection performed side-effecting I/O")

    monkeypatch.setattr(keyring, "get_password", forbidden)
    monkeypatch.setattr(
        credentials.OSKeyringCityCredentialStore, "resolve", forbidden, raising=False
    )
    monkeypatch.setattr(cache_v2.CityCognitionCacheV2, "get", forbidden, raising=False)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    for stored in saved.values():
        build_workbench_cognition_summary_view(stored)
        build_workbench_cognition_records_view(stored)
        build_saved_workbench_run_input_view(stored)
