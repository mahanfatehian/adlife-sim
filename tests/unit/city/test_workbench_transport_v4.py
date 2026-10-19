"""Schema 4 freezes bounded cognition without authorizing execution."""

from __future__ import annotations

import importlib
import json
from hashlib import sha256

import pytest
from pydantic import TypeAdapter, ValidationError

from adlife.city import workbench_transport as transport
from adlife.city.workbench_security import strict_json_object
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CITY_COGNITION_V2_MAX_AGGREGATE_OUTPUT_TOKENS,
    CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
)
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_workbench_input import (
    draft_v3_data,
    normalized_v2_input,
    normalized_v3_input,
)
from tests.unit.city.test_workbench_validation import _draft


def v4_model(name):
    model = getattr(importlib.import_module("adlife.city.workbench_input"), name, None)
    assert model is not None, f"{name} is not implemented"
    return model


def draft_v4_data(*, mode="rules", limit=None):
    payload = draft_v3_data(mode=mode)
    payload["schema_version"] = 4
    payload["cognition"].pop("aggregate_output_token_ceiling")
    payload["cognition"]["effect_scope"] = "bounded-signals-v1"
    if limit is not None:
        payload["cognition"]["funded_response_limit"] = limit
    return payload


def compiled_v4(*, mode="rules", limit=None, mutation=None):
    payload = draft_v4_data(mode=mode, limit=limit)
    if mutation is not None:
        mutation(payload)
    draft = v4_model("WorkbenchRunDraftV4").model_validate_json(json.dumps(payload))
    return construct_workbench_run(draft)


def test_schema4_normalizes_exact_bounded_cognition_contract():
    payload = draft_v4_data(mode="local", limit=20)
    payload["settings"]["seed"] = str(payload["settings"]["seed"])
    compiled = construct_workbench_run(transport.parse_workbench_http_draft(payload))
    assert type(compiled.workbench_input) is v4_model("WorkbenchRunInputV4")
    cognition = compiled.workbench_input.draft.cognition
    assert cognition.effect_scope == "bounded-signals-v1"
    assert cognition.causal_contract == CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2
    assert cognition.causal_contract_sha256 == cognition.causal_contract.fingerprint
    assert (
        cognition.aggregate_output_token_ceiling
        == 20 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
    )
    expected = {
        "request_schema_version": 2,
        "selected_requests_per_agent": 4,
        "selected_requests_per_run": 60,
        "concurrency_limit": 4,
        "max_request_bytes": 8192,
        "max_terminal_record_bytes": 8192,
        "max_wire_response_bytes": 1_048_576,
        "max_output_tokens_per_attempt": CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
        "reservation_tokens_per_request": CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
        "initial_attempts": 1,
        "transient_retries": 2,
        "repair_attempts": 1,
        "attempt_timeout_seconds": 30,
        "aggregate_deadline_seconds": 180,
        "response_model": "spatial-response-v2",
        "rule_state_noninterference": True,
    }
    assert {name: getattr(cognition, name) for name in expected} == expected
    assert len(canonical_json(compiled.workbench_input).encode()) <= 131_072


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
def test_schema4_funding_defaults_and_derived_ceiling(mode):
    cognition = compiled_v4(mode=mode).workbench_input.draft.cognition
    assert cognition.funded_response_limit == (60 if mode in {"rules", "mock"} else 20)
    assert cognition.aggregate_output_token_ceiling == (
        0 if mode in {"rules", "mock"} else 20 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
    )
    assert set(v4_model("WorkbenchCognitionDraftV4").model_fields) == {
        "effect_scope",
        "mode",
        "provider_profile_id",
        "provider_revision_sha256",
        "funded_response_limit",
    }


@pytest.mark.parametrize("mode", [[], {}, True, 4, None])
def test_schema4_invalid_mode_is_a_validation_refusal(mode):
    model = v4_model("WorkbenchRunDraftV4")
    payload = draft_v4_data()
    payload["cognition"]["mode"] = mode
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("mode", ["local", "remote"])
@pytest.mark.parametrize("limit", [10, 20, 40, 60])
def test_schema4_provider_whole_response_limits(mode, limit):
    cognition = compiled_v4(mode=mode, limit=limit).workbench_input.draft.cognition
    assert (
        cognition.aggregate_output_token_ceiling
        == limit * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
    )


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
@pytest.mark.parametrize("invalid", [True, False, 20.0, "20", 0, 1, 61, None])
def test_schema4_funding_refuses_coercion_and_invalid_limits(mode, invalid):
    model = v4_model("WorkbenchRunDraftV4")
    payload = draft_v4_data(mode=mode)
    payload["cognition"]["funded_response_limit"] = invalid
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field,value",
    [
        ("effect_scope", "explanation-only"),
        ("provider_profile_id", "con"),
        ("provider_revision_sha256", "Z" * 64),
        ("provider_profile_id", None),
        ("provider_revision_sha256", None),
    ],
)
def test_schema4_provider_selection_requires_exact_nonsecret_revision(field, value):
    model = v4_model("WorkbenchRunDraftV4")
    payload = draft_v4_data(mode="local")
    payload["cognition"][field] = value
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("mode", ["rules", "mock"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("funded_response_limit", 20),
        ("provider_profile_id", "provider-main"),
        ("provider_revision_sha256", "a" * 64),
    ],
)
def test_schema4_offline_requires_all_selected_and_no_provider(mode, field, value):
    model = v4_model("WorkbenchRunDraftV4")
    payload = draft_v4_data(mode=mode)
    payload["cognition"][field] = value
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field",
    [
        "aggregate_output_token_ceiling",
        "causal_contract",
        "causal_contract_sha256",
        "prompt_contract_sha256",
        "result_schema_version",
        "max_request_bytes",
        "response_model",
        "rule_state_noninterference",
        "api_key",
        "endpoint",
        "path",
        "state",
        "unknown",
    ],
)
def test_schema4_browser_cannot_supply_derived_policies_limits_or_secrets(field):
    model = v4_model("WorkbenchRunDraftV4")
    payload = draft_v4_data()
    payload["cognition"][field] = "client-controlled"
    with pytest.raises(ValidationError) as error:
        model.model_validate_json(json.dumps(payload))
    assert "client-controlled" not in str(error.value)


@pytest.mark.parametrize(
    "field",
    [
        "request_schema_version",
        "selected_requests_per_agent",
        "selected_requests_per_run",
        "concurrency_limit",
        "max_request_bytes",
        "max_terminal_record_bytes",
        "max_wire_response_bytes",
        "max_output_tokens_per_attempt",
        "reservation_tokens_per_request",
        "initial_attempts",
        "transient_retries",
        "repair_attempts",
        "attempt_timeout_seconds",
        "aggregate_deadline_seconds",
        "aggregate_output_token_ceiling",
        "funded_response_limit",
    ],
)
@pytest.mark.parametrize("kind", ["bool", "float", "string"])
def test_schema4_normalized_integer_fields_require_exact_tokens(field, kind):
    cognition = compiled_v4().workbench_input.draft.cognition
    payload = cognition.model_dump(mode="json")
    value = payload[field]
    payload[field] = True if kind == "bool" else float(value) if kind == "float" else str(value)
    with pytest.raises(ValidationError):
        type(cognition).model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("mutation", ["hash", "policy-hash", "policy-document", "ceiling", "bool"])
def test_schema4_normalized_contract_cannot_be_rebound(mutation):
    cognition = compiled_v4().workbench_input.draft.cognition
    payload = cognition.model_dump(mode="json")
    if mutation == "hash":
        payload["causal_contract_sha256"] = "f" * 64
    elif mutation == "policy-hash":
        payload["causal_contract"]["influence_policy_sha256"] = "f" * 64
    elif mutation == "policy-document":
        payload["causal_contract"]["influence_policy"]["affect_adjustments"][2]["weight"] = -0.0
    elif mutation == "ceiling":
        payload["aggregate_output_token_ceiling"] = CITY_COGNITION_V2_MAX_AGGREGATE_OUTPUT_TOKENS
    else:
        payload["rule_state_noninterference"] = 1
    with pytest.raises(ValidationError):
        type(cognition).model_validate_json(json.dumps(payload))


def view_v4_data():
    source = compiled_v4().workbench_input
    draft = source.draft
    return {
        "run_id": draft.settings.run_id,
        "run_schema_version": 10,
        "workbench_input_schema_version": 4,
        "manifest_sha256": "a" * 64,
        "workbench_input_sha256": source.fingerprint,
        "city_sha256": "b" * 64,
        "scenario_sha256": "c" * 64,
        "creative_sha256": source.creative_template.fingerprint,
        "scenario": draft.scenario.model_dump(mode="json"),
        "population": draft.population.model_dump(mode="json"),
        "settings": {**draft.settings.model_dump(mode="json"), "seed": str(draft.settings.seed)},
        "cognition": draft.cognition.model_dump(mode="json"),
        "creative_template": source.creative_template.model_dump(mode="json"),
    }


def test_schema4_exact_input_and_view_union_dispatch():
    models = importlib.import_module("adlife.city.workbench_input")
    source = compiled_v4().workbench_input
    assert models.parse_workbench_run_input_json(canonical_json(source)) == source
    assert (
        TypeAdapter(models.WorkbenchRunInputDocument).validate_json(canonical_json(source))
        == source
    )
    view_model = getattr(transport, "WorkbenchRunInputViewV4", None)
    assert view_model is not None
    view = view_model.model_validate_json(json.dumps(view_v4_data()))
    assert (
        TypeAdapter(transport.WorkbenchRunInputViewDocument).validate_json(canonical_json(view))
        == view
    )


@pytest.mark.parametrize("input_version", [1, 2, 3, 4, 5, True, 4.0, "4"])
@pytest.mark.parametrize("run_version", [7, 8, 9, 10, 11, True, 10.0, "10"])
def test_schema4_view_refuses_every_unsupported_pair(input_version, run_version):
    payload = view_v4_data()
    payload.update(workbench_input_schema_version=input_version, run_schema_version=run_version)
    if (
        type(input_version) is int
        and input_version == 4
        and type(run_version) is int
        and run_version == 10
    ):
        return
    with pytest.raises(ValidationError):
        TypeAdapter(transport.WorkbenchRunInputViewDocument).validate_json(json.dumps(payload))


@pytest.mark.parametrize("version", [5, True, 4.0, "4"])
def test_schema4_transport_refuses_future_or_coerced_versions(version):
    source = compiled_v4().workbench_input.model_dump(mode="json")
    source["schema_version"] = version
    parser = importlib.import_module("adlife.city.workbench_input").parse_workbench_run_input_json
    with pytest.raises(ValueError):
        parser(json.dumps(source))
    payload = draft_v4_data()
    payload["schema_version"] = version
    payload["settings"]["seed"] = "42"
    with pytest.raises(ValueError):
        transport.parse_workbench_http_draft(payload)


@pytest.mark.parametrize(
    "kind", ["duplicate", "nested-duplicate", "nan", "infinity", "overflow", "utf8", "oversized"]
)
def test_schema4_raw_json_is_unambiguous_and_bounded(kind):
    source = canonical_json(compiled_v4().workbench_input).encode()
    if kind == "duplicate":
        source = source.replace(b'"schema_version":4', b'"schema_version":4,"schema_version":4', 1)
    elif kind == "nested-duplicate":
        source = source.replace(b'"mode":"rules"', b'"mode":"rules","mode":"rules"')
    elif kind in {"nan", "infinity", "overflow"}:
        source = source.replace(
            b'"seed":42',
            b'"seed":' + {"nan": b"NaN", "infinity": b"Infinity", "overflow": b"1e999"}[kind],
        )
    elif kind == "utf8":
        source += b"\xff"
    else:
        source += b" " * 131072
    parser = importlib.import_module("adlife.city.workbench_input").parse_workbench_run_input_json
    with pytest.raises(ValueError):
        parser(source)
    with pytest.raises(ValueError):
        strict_json_object(source)


def test_schema4_input_raw_byte_boundary_is_inclusive():
    source = canonical_json(compiled_v4().workbench_input).encode()
    parser = importlib.import_module("adlife.city.workbench_input").parse_workbench_run_input_json
    assert parser(source + b" " * (131072 - len(source))).schema_version == 4
    with pytest.raises(ValueError):
        parser(source + b" " * (131073 - len(source)))


def test_historical_workbench_canonical_bytes_are_unchanged():
    sources = [
        construct_workbench_run(_draft()).workbench_input,
        normalized_v2_input(),
        normalized_v3_input(),
    ]
    assert [sha256(canonical_json(source).encode()).hexdigest() for source in sources] == [
        "14dea7d52e3760beacf4b528b5c9fcd6ef6d5361a75c99f26fd9a51e5d61e58f",
        "36b09a67b9fd74198bc64f81f115cbf5d2c9066c5d396d3aa4fb1e1409d8a667",
        "6267c80a93ed2207277a08004733711c06430c6dfeac082b9100945d4f4563b5",
    ]


def evidence_v4(*, mutation=None):
    from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
    from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
    from adlife.core.simulation.spatial_response import evaluate_spatial_responses

    compiled = compiled_v4(mode="mock", mutation=mutation)
    opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
    attention = evaluate_spatial_attention(opportunities, seed=42)
    baseline = evaluate_spatial_responses(
        compiled.response_input,
        compiled.scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
    )
    return compiled, baseline


def context_v4(compiled, baseline, **changes):
    builder = getattr(
        importlib.import_module("adlife.city.workbench_binding"),
        "build_city_cognition_run_context_v2",
        None,
    )
    assert builder is not None, "V2 causal context builder is not implemented"
    return builder(
        compiled.workbench_input,
        pack=compiled.pack,
        spatial_scenario=compiled.scenario,
        response_input=compiled.response_input,
        baseline_evaluation=baseline,
        **changes,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("display_name", "Fictional alternate"),
        ("age", 64),
        ("occupation", "Fictional artist"),
        ("background", "A different fictional background."),
        ("mbti", "ESTP"),
    ],
)
def test_schema4_descriptive_changes_preserve_causal_identity_but_change_provenance(field, value):
    from adlife.core.simulation.city_cognition_v2 import (
        build_city_cognition_input_v2,
        build_city_cognition_requests_v2,
        build_city_mock_result_v2,
    )

    original, baseline = evidence_v4()
    changed, changed_baseline = evidence_v4(
        mutation=lambda data: data["population"]["personas"][0].update({field: value})
    )
    first = context_v4(original, baseline)
    second = context_v4(changed, changed_baseline)
    assert first.agents == second.agents
    assert first.campaigns == second.campaigns
    assert first.initial_states == second.initial_states
    assert first.run_input_sha256 != second.run_input_sha256
    assert first.fingerprint != second.fingerprint
    one = build_city_cognition_requests_v2(first, original.mobility, baseline)
    two = build_city_cognition_requests_v2(second, changed.mobility, changed_baseline)
    assert one and len(one) == len(two)
    assert [r.causal_identity for r in one] == [r.causal_identity for r in two]
    assert [r.request_id for r in one] == [r.request_id for r in two]
    assert [build_city_mock_result_v2(r) for r in one] == [
        build_city_mock_result_v2(r) for r in two
    ]
    assert all(
        a.request_record_sha256 != b.request_record_sha256 for a, b in zip(one, two, strict=True)
    )
    assert (
        build_city_cognition_input_v2(first, one, mode="mock", funded_response_limit=60).fingerprint
        != build_city_cognition_input_v2(
            second, two, mode="mock", funded_response_limit=60
        ).fingerprint
    )


def test_schema4_causal_change_changes_request_identity_and_rejects_stale_baseline():
    from adlife.core.simulation.city_cognition_v2 import build_city_cognition_requests_v2

    original, baseline = evidence_v4()
    changed, changed_baseline = evidence_v4(
        mutation=lambda data: data["population"]["personas"][0]["traits"].update(
            novelty_seeking=0.99
        )
    )
    first = context_v4(original, baseline)
    second = context_v4(changed, changed_baseline)
    assert first.agents != second.agents
    assert (
        build_city_cognition_requests_v2(first, original.mobility, baseline)[0].request_id
        != build_city_cognition_requests_v2(second, changed.mobility, changed_baseline)[
            0
        ].request_id
    )
    with pytest.raises(ValueError):
        context_v4(changed, baseline)


@pytest.mark.parametrize(
    "field,value",
    [
        ("city_sha256", "f" * 64),
        ("scenario_sha256", "f" * 64),
        ("response_input_sha256", "f" * 64),
        ("attention_seed", 43),
        ("final_states", ()),
    ],
)
def test_schema4_context_revalidates_baseline_scientific_bindings(field, value):
    compiled, baseline = evidence_v4()
    changed = type(baseline).model_construct(**{**baseline.model_dump(mode="python"), field: value})
    with pytest.raises(ValueError):
        context_v4(compiled, changed)
