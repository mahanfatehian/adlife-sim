"""V10 receipts bind deeply validated V2 evidence to dual-state manifests."""

import json
from hashlib import sha256

import pytest

from adlife.core.domain import city_run
from adlife.core.domain.city_cognition import city_cognition_provider_profile_revision_sha256
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition_v2 import validate_city_cognition_evaluation_v2
from tests.unit.city.test_city_cognition_v2 import _bypass, _rehash_terminal, _terminal_evidence
from tests.unit.city.test_city_cognition_v2_contract import evaluation_data
from tests.unit.city.test_city_run_contract import valid_cognition_receipt, valid_manifest_v6


def _frozen_evidence(mode="rules"):
    _, baseline, frozen, requests, records = _terminal_evidence(mode=mode, minutes=4)
    evaluation = validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    return frozen, evaluation


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
def test_v2_manifest_receipt_is_derived_only_from_validated_frozen_evidence(mode) -> None:
    builder = getattr(city_run, "build_city_cognition_manifest_receipt_v2", None)
    model = getattr(city_run, "CityCognitionManifestReceiptV2", None)
    assert builder is not None and model is not None, "V2 receipt contract is not implemented"
    frozen, evaluation = _frozen_evidence(mode)
    receipt = builder(frozen, evaluation)
    contract, summary, profile = frozen.causal_contract, evaluation.summary, frozen.provider_profile
    expected = {
        "cognition_input_sha256": sha256(canonical_json(frozen).encode()).hexdigest(),
        "cognition_input_bytes": len((canonical_json(frozen) + "\n").encode()),
        "causal_contract_sha256": frozen.causal_contract_sha256,
        "request_schema_version": 2,
        "result_schema_version": 2,
        "effect_scope": "bounded-signals-v1",
        "mode": mode,
        "result_schema_contract_version": 2,
        "result_schema_contract_sha256": contract.result_schema_contract_sha256,
        "provider_profile_snapshot_sha256": None if profile is None else profile.fingerprint,
        "provider_revision_sha256": None if profile is None else profile.revision_sha256,
        "effective_settings_sha256": sha256(
            canonical_json(
                {"effective_settings": None}
                if frozen.effective_settings is None
                else frozen.effective_settings
            ).encode()
        ).hexdigest(),
        "budget_allocation_sha256": sha256(
            canonical_json(
                {
                    "aggregate_output_token_ceiling": frozen.aggregate_output_token_ceiling,
                    "reservations": [r.model_dump(mode="json") for r in frozen.reservations],
                }
            ).encode()
        ).hexdigest(),
        "funded_response_limit": 60,
        "summary_sha256": sha256((canonical_json(summary) + "\n").encode()).hexdigest(),
    }
    for name in (
        "prompt_contract",
        "selection_policy",
        "fallback_policy",
        "mock_policy",
        "influence_policy",
    ):
        for suffix in ("version", "sha256"):
            expected[f"{name}_{suffix}"] = getattr(contract, f"{name}_{suffix}")
    for name in (
        "coverage",
        "outcomes",
        "reasons",
        "neutralization",
        "attempt_count",
        "prompt_tokens",
        "completion_tokens",
        "terminal_stream_sha256",
        "terminal_stream_bytes",
        "terminal_record_count",
    ):
        expected[name] = summary.model_dump(mode="json")[name]
    assert receipt.model_dump(mode="json") == expected
    assert model.model_validate_json(canonical_json(receipt)) == receipt
    stream = b"".join((canonical_json(r) + "\n").encode() for r in evaluation.records)
    assert receipt.terminal_stream_sha256 == sha256(stream).hexdigest()
    assert receipt.terminal_stream_bytes == len(stream)
    assert receipt.terminal_record_count == len(evaluation.records)

    for bad_input, bad_evaluation in (
        (
            _bypass(frozen, provider_profile=None) if profile else _bypass(frozen, mode="local"),
            evaluation,
        ),
        (
            _bypass(frozen, causal_contract=_bypass(contract, prompt_contract_version=True)),
            evaluation,
        ),
        (frozen, _bypass(evaluation, summary=_bypass(summary, terminal_stream_bytes=1))),
        (frozen, _bypass(evaluation, cognition_input_sha256="f" * 64)),
        (frozen, _bypass(evaluation, run_input_sha256="f" * 64)),
        (
            frozen,
            _bypass(
                evaluation,
                records=(
                    _bypass(evaluation.records[0], attempt_count=True),
                    *evaluation.records[1:],
                ),
            ),
        ),
    ):
        with pytest.raises(ValueError):
            builder(bad_input, bad_evaluation)
    for bad_input, bad_evaluation in (({}, evaluation), (frozen, {})):
        with pytest.raises(TypeError):
            builder(bad_input, bad_evaluation)
    # Both objects remain individually valid after rehashing; admission must still
    # bind every terminal to the exact reservation and frozen provider authority.
    reservation = frozen.reservations[0].model_copy(update={"request_record_sha256": "f" * 64})
    other_input = frozen.model_copy(
        update={"reservations": (reservation, *frozen.reservations[1:])}
    )
    forged = evaluation.model_copy(update={"cognition_input_sha256": other_input.fingerprint})
    with pytest.raises(ValueError):
        builder(other_input, forged)
    for field in ("provider_profile_snapshot_sha256", "provider_revision_sha256"):
        with pytest.raises(ValueError):
            model.model_validate(expected | {field: "f" * 64 if profile is None else None})
    for field in (
        "request_schema_version",
        "result_schema_version",
        "prompt_contract_version",
        "result_schema_contract_version",
        "selection_policy_version",
        "fallback_policy_version",
        "mock_policy_version",
        "influence_policy_version",
        "funded_response_limit",
    ):
        for invalid in (True, float(expected[field]), str(expected[field]), 11):
            with pytest.raises(ValueError):
                model.model_validate_json(json.dumps(expected | {field: invalid}))


@pytest.mark.parametrize("binding", ["profile", "revision", "settings", "attempts"])
def test_receipt_refuses_individually_valid_rehashed_provider_mismatch(binding) -> None:
    frozen, evaluation = _frozen_evidence("local")
    if binding == "attempts":
        facts = frozen.model_dump(mode="python")
        facts["provider_profile"] |= {"transient_retries": 0, "repair_attempts": 0}
        facts["provider_profile"]["revision_sha256"] = (
            city_cognition_provider_profile_revision_sha256(facts["provider_profile"])
        )
        facts["effective_settings"] |= {"transient_retries": 0, "repair_attempts": 0}
        frozen = CityCognitionInputV2.model_validate(facts)
        changes = dict(
            source="provider",
            attempt_count=2,
            provider_revision_sha256=frozen.provider_profile.revision_sha256,
            provider_profile_snapshot_sha256=frozen.provider_profile.fingerprint,
            provider_effective_settings_sha256=frozen.effective_settings.fingerprint,
        )
    else:
        field = {
            "profile": "provider_profile_snapshot_sha256",
            "revision": "provider_revision_sha256",
            "settings": "provider_effective_settings_sha256",
        }[binding]
        changes = {field: "f" * 64}
    records = tuple(_rehash_terminal(r, **changes) for r in evaluation.records)
    facts = evaluation_data(tuple(r.model_dump(mode="json") for r in records), "local")
    facts |= {
        "cognition_input_sha256": frozen.fingerprint,
        "run_input_sha256": frozen.run_input_sha256,
    }
    forged = CityCognitionEvaluationV2.model_validate_json(json.dumps(facts))
    with pytest.raises(ValueError):
        city_run.build_city_cognition_manifest_receipt_v2(frozen, forged)


def _manifest_v10():
    frozen, evaluation = _frozen_evidence()
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    count = receipt.coverage.eligible_response_count
    return valid_manifest_v6() | dict(
        schema_version=10,
        spatial_response_schema_version=2,
        spatial_response_model_id="spatial-response-v2",
        workbench_input_schema_version=4,
        workbench_input_sha256="7" * 64,
        opportunity_count=count,
        impression_count=count,
        noticed_count=count,
        response_count=count,
        baseline_response_count=count,
        applied_response_count=count,
        state_update_count=1,
        dual_state_update_count=1,
        final_cognition_state_count=4,
        rule_state_document_sha256="8" * 64,
        cognition_state_document_sha256="9" * 64,
        influence_policy_id="city-cognition-influence-v1",
        influence_policy_sha256=receipt.influence_policy_sha256,
        cognition=receipt.model_dump(mode="json"),
    )


def test_manifest_v10_requires_exact_schema4_dual_state_and_v2_cognition_bindings() -> None:
    document = _manifest_v10()
    accepted = city_run.parse_city_run_manifest_json(json.dumps(document))
    model = city_run.CityRunManifestV10
    assert type(accepted) is model
    assert model.model_validate_json(canonical_json(accepted)) == accepted
    assert not isinstance(accepted, city_run.CityRunManifestV9)
    assert accepted.model_dump(mode="json") == document | dict(
        place_schema_version=None, place_set_sha256=None, place_assignments_sha256=None
    )
    for field, value in (
        ("schema_version", True),
        ("schema_version", 10.0),
        ("schema_version", "10"),
        ("schema_version", 11),
        ("spatial_response_schema_version", 1),
        ("spatial_response_schema_version", True),
        ("spatial_response_schema_version", 2.0),
        ("spatial_response_schema_version", "2"),
        ("spatial_response_model_id", "spatial-response-v1"),
        ("cognition", valid_cognition_receipt()),
        ("baseline_response_count", accepted.response_count + 1),
        ("applied_response_count", accepted.response_count + 1),
        ("response_count", accepted.response_count + 1),
        ("noticed_count", accepted.response_count + 1),
        ("state_update_count", 0),
        ("dual_state_update_count", 0),
        ("final_state_count", 3),
        ("final_cognition_state_count", 3),
        ("final_cognition_state_count", 601),
        ("baseline_response_count", 520801),
        ("applied_response_count", True),
        ("dual_state_update_count", 1.0),
        ("rule_state_document_sha256", "short"),
        ("cognition_state_document_sha256", "A" * 64),
        ("influence_policy_id", "invented"),
        ("influence_policy_sha256", "f" * 64),
        ("extra", "refused"),
        ("spatial_attention_schema_version", 2),
    ):
        with pytest.raises(ValueError):
            city_run.parse_city_run_manifest_json(json.dumps(document | {field: value}))
    for manifest_version in range(1, 12):
        for input_version in (1, 2, 3, 4, 5, True, 4.0, "4"):
            if manifest_version == 10 and type(input_version) is int and input_version == 4:
                continue
            with pytest.raises(ValueError):
                city_run.parse_city_run_manifest_json(
                    json.dumps(
                        document
                        | dict(
                            schema_version=manifest_version,
                            workbench_input_schema_version=input_version,
                        )
                    )
                )
    cognition = document["cognition"]
    for changed in (
        cognition
        | {
            "coverage": cognition["coverage"]
            | dict(
                eligible_response_count=accepted.response_count + 1,
                unselected_response_count=cognition["coverage"]["unselected_response_count"] + 1,
                applied_response_count=accepted.response_count + 1,
            ),
            "neutralization": cognition["neutralization"]
            | dict(
                neutralized_response_count=cognition["neutralization"]["neutralized_response_count"]
                + 1,
            ),
        },
        cognition | {"terminal_record_count": 61},
        cognition | {"coverage": cognition["coverage"] | {"schema_version": 2.0}},
        cognition | {"outcomes": cognition["outcomes"] | {"rules": True}},
        cognition | {"unexpected": 1},
    ):
        with pytest.raises(ValueError):
            city_run.parse_city_run_manifest_json(json.dumps(document | {"cognition": changed}))
    for malformed in (
        json.dumps(document).replace(
            '"attempt_count": 0', '"attempt_count": 0, "attempt_count": 0'
        ),
        json.dumps(document).replace('"attempt_count": 0', '"attempt_count": NaN'),
        json.dumps(document).replace('"attempt_count": 0', '"attempt_count": 1e999'),
    ):
        with pytest.raises(ValueError):
            city_run.parse_city_run_manifest_json(malformed)


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
def test_empty_receipts_keep_mode_provider_and_lf_hash_scopes(mode, monkeypatch) -> None:
    import adlife.core.domain.city_cognition_v2 as domain

    frozen, _ = _frozen_evidence(mode)
    frozen = frozen.model_copy(update={"reservations": ()})
    facts = evaluation_data((), mode, unselected=0) | {
        "cognition_input_sha256": frozen.fingerprint,
        "run_input_sha256": frozen.run_input_sha256,
    }
    evaluation = CityCognitionEvaluationV2.model_validate_json(json.dumps(facts))
    for name in tuple(vars(domain)):
        if name.startswith("CITY_COGNITION_"):
            monkeypatch.setattr(domain, name, None)
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    assert receipt.terminal_record_count == receipt.terminal_stream_bytes == 0
    assert receipt.terminal_stream_sha256 == sha256(b"").hexdigest()
    assert (
        receipt.cognition_input_sha256
        != sha256((canonical_json(frozen) + "\n").encode()).hexdigest()
    )
    assert (
        receipt.summary_sha256
        == sha256((canonical_json(evaluation.summary) + "\n").encode()).hexdigest()
    )
    facts = receipt.model_dump(mode="json")
    for change in (
        {"terminal_stream_sha256": "f" * 64},
        {"terminal_stream_bytes": 1},
        {"terminal_record_count": 1},
        {"mode": "rules" if mode in {"local", "remote"} else "local"},
        {"coverage": facts["coverage"] | {"schema_version": True}},
    ):
        with pytest.raises(ValueError):
            type(receipt).model_validate_json(json.dumps(facts | change))


def test_v10_final_states_cover_maximum_population_campaign_product() -> None:
    facts = _manifest_v10() | dict(
        agent_count=30,
        response_campaign_count=20,
        position_count=43200,
        final_state_count=600,
        final_cognition_state_count=600,
    )
    assert city_run.parse_city_run_manifest_json(json.dumps(facts)).final_state_count == 600
    for count in (0, 599, 601, True, 600.0, "600"):
        with pytest.raises(ValueError):
            city_run.parse_city_run_manifest_json(
                json.dumps(
                    facts
                    | dict(
                        final_state_count=count,
                        final_cognition_state_count=count,
                    )
                )
            )


@pytest.mark.parametrize("limit", [10, 20, 40, 60])
def test_receipt_preserves_frozen_funding_limit_and_cached_usage_without_reservation_clamping(
    limit,
):
    _, baseline, frozen, requests, records = _terminal_evidence(
        mode="local", minutes=4, limit=limit
    )
    records = tuple(
        _rehash_terminal(
            record,
            source="provider" if index == 0 else "cache",
            attempt_count=1 if index == 0 else 0,
            prompt_tokens=1_000_000,
            completion_tokens=2_000_000,
        )
        for index, record in enumerate(records)
    )
    evaluation = validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    assert receipt.funded_response_limit == limit
    assert (
        frozen.aggregate_output_token_ceiling
        == CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST * limit
    )
    assert receipt.attempt_count == 1
    assert receipt.outcomes.provider == 1
    assert receipt.outcomes.cache == len(records) - 1
    assert receipt.prompt_tokens == 1_000_000 * len(records)
    assert receipt.completion_tokens == 2_000_000 * len(records)
    assert receipt.coverage.funded_request_count == len(requests)


@pytest.mark.parametrize("boundary", ["receipt", "manifest"])
@pytest.mark.parametrize(
    "mode,field",
    [
        (mode, field)
        for mode in ("rules", "mock", "local", "remote")
        for field in (
            "causal_contract_sha256",
            "prompt_contract_sha256",
            "result_schema_contract_sha256",
            "selection_policy_sha256",
            "fallback_policy_sha256",
            "mock_policy_sha256",
            "influence_policy_sha256",
            "summary_sha256",
            *(("effective_settings_sha256",) if mode in {"rules", "mock"} else ()),
        )
    ],
)
def test_v10_receipt_rejects_each_internally_known_hash_contradiction(boundary, mode, field):
    frozen, evaluation = _frozen_evidence(mode)
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    facts = receipt.model_dump(mode="json")
    assert facts[field] != "f" * 64
    changed = facts | {field: "f" * 64}
    if boundary == "manifest":
        document = _manifest_v10() | {"cognition": facts}
        assert city_run.parse_city_run_manifest_json(json.dumps(document)).cognition == receipt
        document["cognition"] = changed
    with pytest.raises(ValueError):
        if boundary == "receipt":
            city_run.CityCognitionManifestReceiptV2.model_validate_json(json.dumps(changed))
        else:
            city_run.parse_city_run_manifest_json(json.dumps(document))


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
@pytest.mark.parametrize("availability", ["unavailable", "replaced"])
def test_v10_receipt_parsing_uses_frozen_pins_when_current_defaults_change(
    mode, availability, monkeypatch
):
    import adlife.core.domain.city_cognition_v2 as domain
    import adlife.core.simulation.city_cognition_v2 as simulation

    frozen, evaluation = _frozen_evidence(mode)
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    document = _manifest_v10() | {"cognition": receipt.model_dump(mode="json")}
    expected = city_run.parse_city_run_manifest_json(json.dumps(document))
    receipt_json, manifest_json = canonical_json(receipt), canonical_json(expected)

    def unavailable_builder(*args, **kwargs):
        raise AssertionError("receipt parsing must not call current builders")

    for namespace in (domain, simulation, city_run):
        for name in tuple(vars(namespace)):
            if name.startswith("CITY_COGNITION_"):
                monkeypatch.setattr(
                    namespace, name, None if availability == "unavailable" else object()
                )
            elif name.startswith("build_city_"):
                monkeypatch.setattr(namespace, name, unavailable_builder)
    assert city_run.CityCognitionManifestReceiptV2.model_validate_json(receipt_json) == receipt
    assert city_run.parse_city_run_manifest_json(manifest_json) == expected
    assert canonical_json(city_run.parse_city_run_manifest_json(manifest_json)) == manifest_json


@pytest.mark.parametrize("boundary", ["receipt", "manifest"])
@pytest.mark.parametrize("mode", ["local", "remote"])
def test_provider_settings_hash_requires_loaded_input_not_receipt_inference(boundary, mode):
    frozen, evaluation = _frozen_evidence(mode)
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    facts = receipt.model_dump(mode="json") | {"effective_settings_sha256": "f" * 64}
    if boundary == "receipt":
        parsed = city_run.CityCognitionManifestReceiptV2.model_validate_json(json.dumps(facts))
    else:
        parsed = city_run.parse_city_run_manifest_json(
            json.dumps(_manifest_v10() | {"cognition": facts})
        ).cognition
    assert parsed.effective_settings_sha256 == "f" * 64


@pytest.mark.parametrize("boundary", ["receipt", "manifest"])
@pytest.mark.parametrize("unselected", [0, 2])
@pytest.mark.parametrize(
    "mode,limit",
    [("rules", 60), ("mock", 60)]
    + [(mode, limit) for mode in ("local", "remote") for limit in (10, 20, 40, 60)],
)
def test_empty_receipt_budget_hash_is_internally_reconstructible(boundary, unselected, mode, limit):
    frozen, _ = _frozen_evidence(mode)
    frozen = frozen.model_copy(
        update=dict(
            reservations=(),
            funded_response_limit=limit,
            aggregate_output_token_ceiling=(
                0
                if mode in {"rules", "mock"}
                else CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST * limit
            ),
        )
    )
    evaluation = CityCognitionEvaluationV2.model_validate_json(
        json.dumps(
            evaluation_data((), mode, unselected=unselected)
            | dict(
                cognition_input_sha256=frozen.fingerprint,
                run_input_sha256=frozen.run_input_sha256,
            )
        )
    )
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    facts = receipt.model_dump(mode="json")
    assert receipt.coverage.selected_request_count == receipt.terminal_record_count == 0
    assert facts["budget_allocation_sha256"] != "f" * 64
    changed = facts | {"budget_allocation_sha256": "f" * 64}
    if boundary == "manifest":
        document = _manifest_v10() | dict(
            opportunity_count=unselected,
            impression_count=unselected,
            noticed_count=unselected,
            response_count=unselected,
            baseline_response_count=unselected,
            applied_response_count=unselected,
            state_update_count=int(bool(unselected)),
            dual_state_update_count=int(bool(unselected)),
            cognition=facts,
        )
        assert city_run.parse_city_run_manifest_json(json.dumps(document)).cognition == receipt
        document["cognition"] = changed
    with pytest.raises(ValueError):
        if boundary == "receipt":
            city_run.CityCognitionManifestReceiptV2.model_validate_json(json.dumps(changed))
        else:
            city_run.parse_city_run_manifest_json(json.dumps(document))


@pytest.mark.parametrize("boundary", ["receipt", "manifest"])
@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
def test_nonempty_reservation_hash_requires_loaded_input_not_receipt_inference(boundary, mode):
    frozen, evaluation = _frozen_evidence(mode)
    receipt = city_run.build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    assert receipt.coverage.selected_request_count > 0
    facts = receipt.model_dump(mode="json") | {"budget_allocation_sha256": "f" * 64}
    if boundary == "receipt":
        parsed = city_run.CityCognitionManifestReceiptV2.model_validate_json(json.dumps(facts))
    else:
        parsed = city_run.parse_city_run_manifest_json(
            json.dumps(_manifest_v10() | {"cognition": facts})
        ).cognition
    assert parsed.budget_allocation_sha256 == "f" * 64
