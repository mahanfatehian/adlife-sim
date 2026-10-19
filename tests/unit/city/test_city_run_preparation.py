"""City run evaluation is deterministic, side-effect free, and separately published."""

from __future__ import annotations

import json
import warnings
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from adlife.city import runs
from adlife.city.provider_profiles import (
    ProviderProfileDraftV1,
    ProviderProfileRegistry,
    ProviderProfileRevisionV1,
)
from adlife.city.run_store import CityRunStore
from adlife.city.workbench_input import (
    WorkbenchRunDraft,
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
)
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_PROMPT_CONTRACT_SHA256,
    CITY_COGNITION_RESULT_SCHEMA_SHA256,
)
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
)
from adlife.core.domain.city_run import CityRunManifestV7
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import DuplicateRun
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_workbench_input import draft_data, draft_v3_data
from tests.unit.city.test_workbench_validation import cognition_draft, persona_draft


def _v2_prepare_api():
    function = getattr(runs, "prepare_city_scientific_run_v2", None)
    assert function is not None, "prepare_city_scientific_run_v2 is not implemented"
    return function


def _validated_v4(*, mode="rules", limit=60, profile=None, mutation=None, agents=3):
    from adlife.city.workbench_input import WorkbenchRunDraftV4
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    document = draft_v4_data(mode=mode, limit=limit)
    document["settings"]["agent_count"] = agents
    document["population"]["personas"][0]["count"] = agents
    if profile is not None:
        document["cognition"].update(
            provider_profile_id=profile.profile_id,
            provider_revision_sha256=profile.revision_sha256,
        )
    if mutation is not None:
        mutation(document)
    return construct_workbench_run(WorkbenchRunDraftV4.model_validate_json(json.dumps(document)))


def _v2_arguments(validated):
    settings = validated.workbench_input.draft.settings
    return dict(
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )


def _bypass_v2(model, **updates):
    return type(model).model_construct(
        **({name: getattr(model, name) for name in type(model).model_fields} | updates)
    )


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_v2_preparation_freezes_exact_staging_and_offline_reservations(tmp_path, mode):
    prepare = _v2_prepare_api()
    validated = _validated_v4(mode=mode)
    prepared = prepare(validated.pack, **_v2_arguments(validated))

    assert type(prepared) is runs.PreparedCityScientificRunV2
    assert [field.name for field in fields(prepared)] == [
        "pack",
        "mobility",
        "spatial_scenario",
        "opportunity_evaluation",
        "attention_evaluation",
        "response_input",
        "baseline_evaluation",
        "workbench_input",
        "cognition_input",
        "cognition_requests",
    ]
    assert not hasattr(prepared, "__dict__")
    with pytest.raises(FrozenInstanceError):
        prepared.cognition_requests = ()
    for field in ("manifest", "directory", "path", "publish", "initial_state_document"):
        assert not hasattr(prepared, field)
    assert prepared.workbench_input == validated.workbench_input
    assert prepared.workbench_input is not validated.workbench_input
    frozen = prepared.cognition_input
    assert frozen.schema_version == 2 and frozen.effect_scope == "bounded-signals-v1"
    assert frozen.mode == mode and frozen.run_input_sha256 == validated.workbench_input.fingerprint
    assert frozen.provider_profile is None and frozen.effective_settings is None
    assert frozen.funded_response_limit == 60 and frozen.aggregate_output_token_ceiling == 0
    assert len(prepared.cognition_requests) > 0
    assert len(frozen.reservations) == len(prepared.cognition_requests)
    assert [r.selection_index for r in frozen.reservations] == list(range(len(frozen.reservations)))
    assert [(r.request_id, r.request_record_sha256) for r in frozen.reservations] == [
        (r.request_id, r.request_record_sha256) for r in prepared.cognition_requests
    ]
    assert all(r.funded and r.reserved_output_tokens == 0 for r in frozen.reservations)
    assert list(tmp_path.iterdir()) == []


def test_v2_preparation_preserves_exact_v1_baseline_and_purchase_bytes():
    from adlife.core.simulation.spatial_response import spatial_response_lines

    prepare = _v2_prepare_api()
    validated = _validated_v4()
    prepared = prepare(validated.pack, **_v2_arguments(validated))
    independent = runs.evaluate_spatial_responses(
        validated.response_input,
        validated.scenario,
        prepared.opportunity_evaluation,
        prepared.attention_evaluation,
        agent_ids=tuple(a.agent_id for a in prepared.mobility.agents),
    )
    assert prepared.baseline_evaluation == independent
    assert b"".join(spatial_response_lines(prepared.baseline_evaluation)) == b"".join(
        spatial_response_lines(independent)
    )
    assert tuple(map(canonical_json, prepared.baseline_evaluation.final_states)) == tuple(
        map(canonical_json, independent.final_states)
    )
    assert [s.purchase_intention for s in prepared.baseline_evaluation.final_states] == [
        s.purchase_intention for s in independent.final_states
    ]


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
def test_v2_preparation_does_not_consult_provider_cache_credentials_or_storage(
    tmp_path, monkeypatch, mode
):
    import os
    import socket

    from adlife.adapters.cognition.city_cache_storage import CityCognitionCacheByteStorage
    from adlife.adapters.cognition.city_openai_compatible_v2 import CityOpenAICompatibleProviderV2

    prepare = _v2_prepare_api()
    profile = _provider_revision(tmp_path, mode=mode) if mode in {"local", "remote"} else None
    validated = _validated_v4(mode=mode, profile=profile)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preparation performed an external effect")

    with monkeypatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(os, "getenv", forbidden)
        patch.setattr(CityOpenAICompatibleProviderV2, "__init__", forbidden)
        patch.setattr(CityCognitionCacheByteStorage, "__init__", forbidden)
        patch.setattr(ProviderProfileRegistry, "__init__", forbidden)
        patch.setattr(CityRunStore, "__init__", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "exists", forbidden)
        prepared = prepare(validated.pack, provider_profile=profile, **_v2_arguments(validated))
    assert prepared.cognition_requests
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("mode", ["local", "remote"])
@pytest.mark.parametrize("limit", [10, 20, 40, 60])
@pytest.mark.parametrize("tokens", [96, 384])
def test_v2_preparation_binds_profile_caps_output_and_reserves_whole_requests(
    tmp_path, mode, limit, tokens
):
    prepare = _v2_prepare_api()
    profile = _provider_revision(tmp_path, mode=mode, max_output_tokens=tokens)
    validated = _validated_v4(mode=mode, limit=limit, profile=profile, agents=8)
    prepared = prepare(validated.pack, provider_profile=profile, **_v2_arguments(validated))
    frozen = prepared.cognition_input
    assert canonical_json(frozen.provider_profile) == canonical_json(profile)
    settings = frozen.effective_settings
    assert settings.max_output_tokens == min(tokens, CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT)
    for field in (
        "protocol",
        "temperature",
        "top_p",
        "seed_policy",
        "seed",
        "attempt_timeout_seconds",
        "transient_retries",
        "repair_attempts",
    ):
        assert getattr(settings, field) == getattr(profile, field)
    assert frozen.funded_response_limit == limit
    assert (
        frozen.aggregate_output_token_ceiling
        == limit * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
    )
    assert frozen.max_output_tokens_per_attempt == CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT
    assert frozen.reservation_tokens_per_request == CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
    assert [(r.funded, r.reserved_output_tokens) for r in frozen.reservations] == [
        (index < limit, CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST if index < limit else 0)
        for index in range(len(prepared.cognition_requests))
    ]


@pytest.mark.parametrize("case", ["missing", "offline", "mode", "revision", "profile-id", "nested"])
def test_v2_preparation_refuses_profile_drift_before_simulation(tmp_path, monkeypatch, case):
    prepare = _v2_prepare_api()
    profile = _provider_revision(tmp_path)
    validated = _validated_v4(
        mode="rules" if case == "offline" else "local",
        profile=(None if case in {"revision", "profile-id", "offline"} else profile),
    )
    supplied = None if case == "missing" else profile
    if case == "mode":
        supplied = _provider_revision(tmp_path, mode="remote")
    elif case == "profile-id":
        supplied = _provider_revision(tmp_path / "other", profile_id="other-provider")
    elif case == "nested":
        supplied = _bypass_v2(profile, max_output_tokens=True)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("profile was checked after mobility started")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    with pytest.raises(ValueError, match="provider profile"):
        prepare(validated.pack, provider_profile=supplied, **_v2_arguments(validated))


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", True),
        ("seed", -1),
        ("seed", 2**63),
        ("seed", 42.0),
        ("agent_count", True),
        ("agent_count", 0),
        ("agent_count", 31),
        ("days", True),
        ("days", 0),
        ("days", 8),
        ("days", "2"),
        ("run_id", "different-run"),
        ("seed", 43),
        ("agent_count", 2),
        ("days", 1),
    ],
)
def test_v2_preparation_refuses_scalar_and_execution_drift_before_simulation(
    monkeypatch, field, value
):
    prepare = _v2_prepare_api()
    validated = _validated_v4()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid execution reached mobility")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    with pytest.raises(ValueError):
        prepare(validated.pack, **(_v2_arguments(validated) | {field: value}))


@pytest.mark.parametrize(
    "case",
    [
        "schema3",
        "pack",
        "city",
        "scenario",
        "missing-scenario",
        "response",
        "places",
        "creative",
        "profile",
        "state",
    ],
)
def test_v2_preparation_refuses_wrong_versions_and_scientific_drift_before_simulation(
    monkeypatch, case
):
    prepare = _v2_prepare_api()
    validated = _validated_v4()
    pack = validated.pack
    arguments = _v2_arguments(validated)
    if case == "schema3":
        arguments["workbench_input"] = _validated_v3().workbench_input
    elif case == "pack":
        from tests.unit.city.test_city_pack import load_pack, pack_data

        pack = load_pack(pack_data())
    elif case == "scenario":
        arguments["spatial_scenario"] = validated.scenario.model_copy(update={"name": "drift"})
    elif case == "city":
        pack = validated.pack.model_copy(update={"city_id": "different-fictional-city"})
    elif case == "missing-scenario":
        arguments["spatial_scenario"] = None
    elif case == "response":
        arguments["spatial_response"] = None
    elif case == "places":
        arguments["places"] = mobility_place_set()
    elif case == "creative":
        template = validated.workbench_input.creative_template.model_copy(
            update={"message": "drift"}
        )
        arguments["workbench_input"] = validated.workbench_input.model_copy(
            update={"creative_template": template}
        )
    elif case == "profile":
        profile, *others = validated.response_input.profiles
        traits = profile.traits.model_copy(update={"novelty_seeking": 0.1})
        arguments["spatial_response"] = validated.response_input.model_copy(
            update={"profiles": (profile.model_copy(update={"traits": traits}), *others)}
        )
    else:
        state, *others = validated.response_input.initial_states
        arguments["spatial_response"] = validated.response_input.model_copy(
            update={
                "initial_states": (state.model_copy(update={"purchase_intention": 0.9}), *others)
            }
        )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid science reached mobility")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    with pytest.raises((TypeError, ValueError)):
        prepare(pack, **arguments)


def test_v2_preparation_refuses_nested_bypass_without_warnings_or_secret_context(monkeypatch):
    prepare = _v2_prepare_api()
    validated = _validated_v4()
    secret = "Authorization: Bearer opaquevalue1234567890"
    response = _bypass_v2(validated.response_input, profiles=(secret,))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("malformed response reached mobility")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as caught:
            prepare(validated.pack, **(_v2_arguments(validated) | {"spatial_response": response}))
    assert captured == []
    assert secret not in str(caught.value) and secret not in repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_v2_preparation_preserves_programming_type_error(monkeypatch):
    prepare = _v2_prepare_api()
    validated = _validated_v4()

    def defect(*_args, **_kwargs):
        raise TypeError("implementation defect")

    monkeypatch.setattr(runs, "revalidate_model", defect)
    with pytest.raises(TypeError, match="implementation defect"):
        prepare(validated.pack, **_v2_arguments(validated))


def _validated(*, run_id: str = "launch-run", seed: int = 42, agents: int = 2, days: int = 2):
    document = draft_data(phone=True, roadside=True)
    document["scenario"]["roadside"]["road_id"] = "middle-west"
    document["settings"].update(
        {"run_id": run_id, "seed": seed, "agent_count": agents, "days": days}
    )
    return construct_workbench_run(WorkbenchRunDraft.model_validate_json(json.dumps(document)))


def _provider_revision(
    tmp_path: Path,
    *,
    mode: str = "local",
    **updates: object,
) -> ProviderProfileRevisionV1:
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "provider-main",
        "display_label": "Bound provider",
        "mode": mode,
        "base_url": (
            "http://127.0.0.1:11434/v1" if mode == "local" else "https://provider.example/v1"
        ),
        "model_id": "fictional-explainer",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 96,
        "attempt_timeout_seconds": 7,
        "transient_retries": 1,
        "repair_attempts": 0,
        "origin_approval_id": "local-ollama" if mode == "local" else "provider-main",
    }
    document.update(updates)
    registry = ProviderProfileRegistry(
        prepare_workbench_workspace(tmp_path / f"{mode}-provider-workspace")
    )
    return registry.create(ProviderProfileDraftV1.model_validate(document))


def _validated_v3(
    *,
    mode: str = "rules",
    budget: int = 0,
    provider_profile: ProviderProfileRevisionV1 | None = None,
):
    document = draft_v3_data(
        mode=mode,
        aggregate_output_token_ceiling=budget,
    )
    if provider_profile is not None:
        document["cognition"]["provider_profile_id"] = provider_profile.profile_id
        document["cognition"]["provider_revision_sha256"] = provider_profile.revision_sha256
    draft = WorkbenchRunDraftV3.model_validate_json(json.dumps(document))
    return construct_workbench_run(draft)


def test_prepare_city_run_evaluates_without_touching_the_filesystem(tmp_path: Path) -> None:
    validated = _validated()

    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="launch-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    assert list(tmp_path.iterdir()) == []
    assert isinstance(prepared.manifest, CityRunManifestV7)
    assert prepared.workbench_input == validated.workbench_input
    assert prepared.mobility.agents == validated.mobility.agents
    assert prepared.mobility.frame(1_440) == validated.mobility.frame(1_440)
    assert prepared.response_evaluation is not None


def test_schema2_preparation_selects_v8_without_touching_the_filesystem(tmp_path: Path) -> None:
    validated = construct_workbench_run(persona_draft())
    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="launch-run",
        seed=42,
        agent_count=3,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )
    assert prepared.manifest.schema_version == 8
    assert prepared.manifest.workbench_input_schema_version == 2
    assert prepared.workbench_input == validated.workbench_input
    assert prepared.response_input == validated.response_input
    assert list(tmp_path.iterdir()) == []


def test_schema3_preparation_refuses_before_v9_evaluation_or_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = construct_workbench_run(cognition_draft())

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("schema-3 refusal happened after deterministic evaluation started")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    monkeypatch.setattr(runs, "evaluate_spatial_opportunities", forbidden)
    monkeypatch.setattr(runs, "evaluate_spatial_attention", forbidden)
    monkeypatch.setattr(runs, "evaluate_spatial_responses", forbidden)

    with pytest.raises(ValueError, match=r"schema 3.*schema 9"):
        runs.prepare_city_run(
            validated.pack,
            run_id="launch-run",
            seed=42,
            agent_count=3,
            days=2,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_scientific_preparation_freezes_offline_requests_without_a_manifest_or_writes(
    tmp_path: Path,
    mode: str,
) -> None:
    validated = _validated_v3(mode=mode)
    settings = validated.workbench_input.draft.settings

    prepared = runs.prepare_city_scientific_run(
        validated.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    assert isinstance(prepared, runs.PreparedCityScientificRun)
    assert not hasattr(prepared, "manifest")
    assert prepared.pack == validated.pack
    assert prepared.workbench_input == validated.workbench_input
    assert prepared.mobility.agents == validated.mobility.agents
    assert prepared.response_evaluation is not None
    assert prepared.cognition_requests
    assert prepared.cognition_input.mode == mode
    assert prepared.cognition_input.run_input_sha256 == validated.workbench_input.fingerprint
    assert prepared.cognition_input.provider_profile is None
    assert prepared.cognition_input.effective_settings is None
    assert prepared.cognition_input.prompt_contract_sha256 == (
        CITY_COGNITION_PROMPT_CONTRACT_SHA256
    )
    assert prepared.cognition_input.result_schema_contract_sha256 == (
        CITY_COGNITION_RESULT_SCHEMA_SHA256
    )
    assert tuple(
        reservation.request_id for reservation in prepared.cognition_input.reservations
    ) == tuple(request.request_id for request in prepared.cognition_requests)
    assert all(
        reservation.reserved_output_tokens == 0
        for reservation in prepared.cognition_input.reservations
    )
    assert list(tmp_path.iterdir()) == []


def test_scientific_preparation_freezes_exact_provider_revision_and_budget(
    tmp_path: Path,
) -> None:
    profile = _provider_revision(tmp_path)
    validated = _validated_v3(
        mode="local",
        budget=1_536,
        provider_profile=profile,
    )
    settings = validated.workbench_input.draft.settings
    untouched = tmp_path / "published-runs"

    prepared = runs.prepare_city_scientific_run(
        validated.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
        provider_profile=profile,
    )

    snapshot = prepared.cognition_input.provider_profile
    effective = prepared.cognition_input.effective_settings
    assert snapshot is not None
    assert effective is not None
    assert snapshot.profile_id == profile.profile_id
    assert snapshot.revision_sha256 == profile.revision_sha256
    assert snapshot.credential_slot_id == profile.credential_slot_id
    assert effective.max_output_tokens == 96
    assert effective.attempt_timeout_seconds == 7
    assert effective.transient_retries == 1
    assert effective.repair_attempts == 0
    assert prepared.cognition_input.reservation_tokens_per_request == 1_536
    assert prepared.cognition_input.max_output_tokens_per_attempt == 384
    assert [
        reservation.reserved_output_tokens for reservation in prepared.cognition_input.reservations
    ] == [1_536, *([0] * (len(prepared.cognition_requests) - 1))]
    assert not untouched.exists()


@pytest.mark.parametrize(
    "case",
    [
        "missing-profile",
        "offline-profile",
        "wrong-mode",
        "wrong-revision",
    ],
)
def test_scientific_preparation_refuses_incompatible_profile_before_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    local_profile = _provider_revision(tmp_path)
    provider_profile: ProviderProfileRevisionV1 | None
    if case == "missing-profile":
        validated = _validated_v3(mode="local", budget=1_536)
        provider_profile = None
    elif case == "offline-profile":
        validated = _validated_v3(mode="rules")
        provider_profile = local_profile
    elif case == "wrong-mode":
        validated = _validated_v3(
            mode="remote",
            budget=1_536,
            provider_profile=local_profile,
        )
        provider_profile = local_profile
    else:
        validated = _validated_v3(mode="local", budget=1_536)
        provider_profile = local_profile

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("profile refusal happened after deterministic evaluation started")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    settings = validated.workbench_input.draft.settings
    with pytest.raises(ValueError, match="provider profile"):
        runs.prepare_city_scientific_run(
            validated.pack,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
            provider_profile=provider_profile,
        )


def test_scientific_preparation_requires_schema3_before_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = construct_workbench_run(persona_draft())

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("schema refusal happened after deterministic evaluation started")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    settings = validated.workbench_input.draft.settings
    with pytest.raises(TypeError, match="schema-3"):
        runs.prepare_city_scientific_run(
            validated.pack,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
        )


def test_scientific_preparation_preserves_schema2_numeric_science() -> None:
    schema3_document = draft_v3_data(mode="rules")
    schema2_document = json.loads(json.dumps(schema3_document))
    schema2_document["schema_version"] = 2
    schema2_document.pop("cognition")
    schema2 = construct_workbench_run(
        WorkbenchRunDraftV2.model_validate_json(json.dumps(schema2_document))
    )
    schema3 = construct_workbench_run(
        WorkbenchRunDraftV3.model_validate_json(json.dumps(schema3_document))
    )
    settings = schema3.workbench_input.draft.settings

    scientific = runs.prepare_city_scientific_run(
        schema3.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=schema3.scenario,
        spatial_response=schema3.response_input,
        workbench_input=schema3.workbench_input,
    )
    legacy = runs.prepare_city_run(
        schema2.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=schema2.scenario,
        spatial_response=schema2.response_input,
        workbench_input=schema2.workbench_input,
    )

    assert scientific.mobility.agents == legacy.mobility.agents
    assert scientific.mobility.frame(0) == legacy.mobility.frame(0)
    assert scientific.mobility.frame(settings.days * 1_440 - 1) == legacy.mobility.frame(
        settings.days * 1_440 - 1
    )
    assert scientific.opportunity_evaluation == legacy.opportunity_evaluation
    assert scientific.attention_evaluation == legacy.attention_evaluation
    assert scientific.response_input == legacy.response_input
    assert scientific.response_evaluation == legacy.response_evaluation


def test_scientific_preparation_refuses_bypass_input_without_warning_or_value_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = _validated_v3()
    secret = "Authorization: Bearer opaquevalue1234567890"
    invalid_response = type(validated.response_input).model_construct(
        **{
            **{
                name: getattr(validated.response_input, name)
                for name in type(validated.response_input).model_fields
            },
            "profiles": (secret,),
        }
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("input refusal happened after deterministic evaluation started")

    monkeypatch.setattr(runs, "CityMobility", forbidden)
    settings = validated.workbench_input.draft.settings
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="response input failed validation") as caught:
            runs.prepare_city_scientific_run(
                validated.pack,
                run_id=settings.run_id,
                seed=settings.seed,
                agent_count=settings.agent_count,
                days=settings.days,
                spatial_scenario=validated.scenario,
                spatial_response=invalid_response,
                workbench_input=validated.workbench_input,
            )

    assert captured == []
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_scientific_preparation_does_not_disguise_revalidation_defects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = _validated_v3()

    def broken_revalidation(*_args: object, **_kwargs: object) -> None:
        raise TypeError("implementation defect")

    monkeypatch.setattr(runs, "revalidate_model", broken_revalidation)
    settings = validated.workbench_input.draft.settings
    with pytest.raises(TypeError, match="implementation defect"):
        runs.prepare_city_scientific_run(
            validated.pack,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
        )


def test_explicit_prepare_publish_matches_the_compatibility_wrapper(tmp_path: Path) -> None:
    validated = _validated()
    arguments = {
        "run_id": "equivalent-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "spatial_response": validated.response_input,
    }

    prepared = runs.prepare_city_run(validated.pack, **arguments)
    explicit = runs.publish_city_run(prepared, root=tmp_path / "explicit")
    wrapped = runs.create_city_run(validated.pack, root=tmp_path / "wrapped", **arguments)

    assert explicit.manifest == wrapped.manifest
    assert explicit.mobility.agents == wrapped.mobility.agents
    assert explicit.opportunity_evaluation == wrapped.opportunity_evaluation
    assert explicit.attention_evaluation == wrapped.attention_evaluation
    assert explicit.response_evaluation == wrapped.response_evaluation
    assert (explicit.directory / "run.json").read_bytes() == (
        wrapped.directory / "run.json"
    ).read_bytes()


def test_publish_is_no_clobber_and_preserves_the_first_artifact(tmp_path: Path) -> None:
    validated = _validated()
    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="no-clobber",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
    )
    first = runs.publish_city_run(prepared, root=tmp_path)
    before = (first.directory / "run.json").read_bytes()

    with pytest.raises(DuplicateRun):
        runs.publish_city_run(prepared, root=tmp_path)

    assert (first.directory / "run.json").read_bytes() == before
    assert CityRunStore(tmp_path).load("no-clobber").manifest == first.manifest


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"run_id": "different-run"}, "run identifier"),
        ({"seed": 43}, "seed"),
        ({"agent_count": 1}, "population"),
        ({"days": 1}, "duration"),
    ],
)
def test_prepare_refuses_workbench_settings_that_do_not_match_execution(
    overrides: dict[str, object], message: str
) -> None:
    validated = _validated()
    arguments: dict[str, object] = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "spatial_response": validated.response_input,
        "workbench_input": validated.workbench_input,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        runs.prepare_city_run(validated.pack, **arguments)


def test_prepare_refuses_unrepresented_places_and_incomplete_workbench_evidence() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "spatial_response": validated.response_input,
        "workbench_input": validated.workbench_input,
    }

    with pytest.raises(ValueError, match="place"):
        runs.prepare_city_run(validated.pack, places=mobility_place_set(), **common)

    with pytest.raises(ValueError, match="scenario"):
        runs.prepare_city_run(
            validated.pack,
            **{**common, "spatial_scenario": None, "spatial_response": None},
        )


def test_prepare_refuses_scenario_response_and_creative_drift() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "workbench_input": validated.workbench_input,
    }

    changed_scenario = validated.scenario.model_copy(update={"name": "Different study"})
    with pytest.raises(ValueError, match="scenario"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=changed_scenario,
            spatial_response=validated.response_input,
            **common,
        )

    response_campaign = validated.response_input.campaigns[0].model_copy(
        update={"target_interests": frozenset({"outdoors"})}
    )
    changed_response = validated.response_input.model_copy(
        update={"campaigns": (response_campaign,)}
    )
    with pytest.raises(ValueError, match="response input"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=validated.scenario,
            spatial_response=changed_response,
            **common,
        )

    changed_template = validated.workbench_input.creative_template.model_copy(
        update={"message": "A different fictional creative."}
    )
    changed_workbench = validated.workbench_input.model_copy(
        update={"creative_template": changed_template}
    )
    with pytest.raises(ValueError, match="campaign"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            **{**common, "workbench_input": changed_workbench},
        )


def test_prepare_refuses_city_and_placement_drift() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_response": validated.response_input,
    }
    other_city_draft = validated.workbench_input.draft.model_copy(
        update={"city_id": "another-fictional-city"}
    )
    other_city_input = validated.workbench_input.model_copy(update={"draft": other_city_draft})
    with pytest.raises(ValueError, match="city"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=validated.scenario,
            workbench_input=other_city_input,
            **common,
        )

    placements = list(validated.scenario.placements)
    phone_index = next(
        index
        for index, placement in enumerate(placements)
        if placement.placement_id == "phone-placement"
    )
    placements[phone_index] = placements[phone_index].model_copy(
        update={"opportunity_probability_per_minute": 0.1}
    )
    phone_drift = validated.scenario.model_copy(update={"placements": tuple(placements)})
    with pytest.raises(ValueError, match="phone placement"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=phone_drift,
            workbench_input=validated.workbench_input,
            **common,
        )

    placements = list(validated.scenario.placements)
    roadside_index = next(
        index
        for index, placement in enumerate(placements)
        if placement.placement_id == "roadside-placement"
    )
    placements[roadside_index] = placements[roadside_index].model_copy(
        update={"max_view_distance_meters": 121.0}
    )
    roadside_drift = validated.scenario.model_copy(update={"placements": tuple(placements)})
    with pytest.raises(ValueError, match="roadside placement"):
        runs.prepare_city_run(
            validated.pack,
            spatial_scenario=roadside_drift,
            workbench_input=validated.workbench_input,
            **common,
        )


def test_prepare_refuses_response_profile_and_initial_state_drift() -> None:
    validated = _validated()
    common = {
        "run_id": "launch-run",
        "seed": 42,
        "agent_count": 2,
        "days": 2,
        "spatial_scenario": validated.scenario,
        "workbench_input": validated.workbench_input,
    }
    profiles = list(validated.response_input.profiles)
    changed_traits = profiles[0].traits.model_copy(update={"novelty_seeking": 0.1})
    profiles[0] = profiles[0].model_copy(update={"traits": changed_traits})
    profile_drift = validated.response_input.model_copy(update={"profiles": tuple(profiles)})
    with pytest.raises(ValueError, match="response population"):
        runs.prepare_city_run(
            validated.pack,
            spatial_response=profile_drift,
            **common,
        )

    states = list(validated.response_input.initial_states)
    states[0] = states[0].model_copy(update={"brand_sentiment": 0.5})
    state_drift = validated.response_input.model_copy(update={"initial_states": tuple(states)})
    with pytest.raises(ValueError, match="initial response state"):
        runs.prepare_city_run(
            validated.pack,
            spatial_response=state_drift,
            **common,
        )


def test_workbench_preparation_publishes_only_with_v7_sidecar_support(tmp_path: Path) -> None:
    validated = _validated()
    prepared = runs.prepare_city_run(
        validated.pack,
        run_id="launch-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    stored = runs.publish_city_run(prepared, root=tmp_path)

    assert isinstance(stored.manifest, CityRunManifestV7)
    assert stored.workbench_input == validated.workbench_input
    assert (stored.directory / "inputs" / "workbench.json").is_file()


def test_different_receipt_run_ids_do_not_change_normalized_scientific_evidence() -> None:
    first_input = _validated(run_id="first-run")
    second_input = _validated(run_id="second-run")
    first = runs.prepare_city_run(
        first_input.pack,
        run_id="first-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=first_input.scenario,
        spatial_response=first_input.response_input,
        workbench_input=first_input.workbench_input,
    )
    second = runs.prepare_city_run(
        second_input.pack,
        run_id="second-run",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=second_input.scenario,
        spatial_response=second_input.response_input,
        workbench_input=second_input.workbench_input,
    )

    first_receipt = first.manifest.model_dump(mode="json")
    second_receipt = second.manifest.model_dump(mode="json")
    del first_receipt["run_id"]
    del second_receipt["run_id"]
    first_workbench_sha256 = first_receipt.pop("workbench_input_sha256")
    second_workbench_sha256 = second_receipt.pop("workbench_input_sha256")
    assert first_workbench_sha256 != second_workbench_sha256
    assert canonical_json(first_receipt) == canonical_json(second_receipt)
    assert first.mobility.agents == second.mobility.agents
    assert first.opportunity_evaluation == second.opportunity_evaluation
    assert first.attention_evaluation == second.attention_evaluation
    assert first.response_evaluation == second.response_evaluation
