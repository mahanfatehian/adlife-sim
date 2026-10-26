"""Selection policy V3: spread-evenly selection beside the untouched V2 contract."""

from __future__ import annotations

import importlib
import itertools
from collections import Counter
from hashlib import sha256
from typing import get_args

import pytest
from pydantic import ValidationError

from adlife.core.domain import city_cognition_v2 as domain
from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.spatial_response import SpatialRuleResponse
from tests.unit.city.test_city_cognition_v2 import _dense_evidence
from tests.unit.city.test_city_cognition_v2_contract import profile_data

V2_CONTRACT_SHA256 = "6a7a0bc16b60c95bb1dc09794e46f04662b5f5c0a76db48002e78707389ae23b"
V2_SELECTION_SHA256 = "547d141edb2706a972ff6d98fe26433f48bf6398ef60b4438aee27335b2563e3"


def _module():
    return importlib.import_module("adlife.core.simulation.city_cognition_v2")


def _sorted_responses(baseline):
    return sorted(
        (record for record in baseline.records if isinstance(record, SpatialRuleResponse)),
        key=lambda item: (item.model_minute, item.agent_id, item.campaign_id, item.event_id),
    )


@pytest.mark.parametrize(
    ("eligible", "limit"),
    [(0, 60), (1, 60), (59, 60), (60, 60), (61, 60), (157, 60), (363, 250), (1000, 1000), (5, 1)],
)
def test_spread_indices_are_strictly_increasing_and_cover_the_run(eligible, limit) -> None:
    indices = _module().select_spread_indices(eligible, limit)
    selected = min(eligible, limit)
    assert len(indices) == selected
    assert indices == tuple((i * eligible) // selected for i in range(selected))
    assert all(a < b for a, b in itertools.pairwise(indices))
    assert all(0 <= index < eligible for index in indices)
    if selected:
        assert indices[0] == 0
    if eligible <= limit:
        assert indices == tuple(range(eligible))


@pytest.mark.parametrize(
    ("eligible", "limit"), [(-1, 60), (True, 60), (10, 0), (10, True), (10, 1.0), ("3", 60)]
)
def test_spread_indices_refuse_inexact_counts(eligible, limit) -> None:
    with pytest.raises(ValueError):
        _module().select_spread_indices(eligible, limit)


def test_v2_documents_stay_byte_identical_and_v3_is_a_new_version() -> None:
    v2 = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2
    v3 = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3
    assert v2.fingerprint == V2_CONTRACT_SHA256
    assert v2.selection_policy_sha256 == V2_SELECTION_SHA256
    assert domain.CITY_COGNITION_DEFAULT_SELECTION_POLICY_V2.fingerprint == V2_SELECTION_SHA256
    # New runs use contract V4: selection V3 with the persona prompt contract v3.
    current = domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT
    assert current is domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V4
    assert current.selection_policy_sha256 == v3.selection_policy_sha256
    assert domain.CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION == 3
    assert (v3.selection_policy_version, v3.selection_policy.policy_id) == (
        3,
        "city-cognition-selection-v3",
    )
    assert v3.selection_policy.strategy == "spread-evenly"
    assert v3.fingerprint != v2.fingerprint
    assert v3.selection_policy_sha256 == domain.CITY_COGNITION_SELECTION_POLICY_V3_SHA256
    # Only the selection document differs between the two contracts.
    left, right = v2.model_dump(mode="json"), v3.model_dump(mode="json")
    assert {key for key in left if left[key] != right[key]} == {
        "selection_policy_version",
        "selection_policy",
        "selection_policy_sha256",
    }
    for contract in (v2, v3):
        parsed = domain.CityCognitionCausalContractV2.model_validate_json(canonical_json(contract))
        assert parsed.fingerprint == contract.fingerprint


def test_contract_refuses_mismatched_selection_versions_and_documents() -> None:
    v2 = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2.model_dump(mode="json")
    v3 = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3.model_dump(mode="json")
    for data in (
        v2 | {"selection_policy_version": 3},
        v3 | {"selection_policy_version": 2},
        v2 | {"selection_policy": v3["selection_policy"]},
        v3 | {"selection_policy_sha256": v2["selection_policy_sha256"]},
        v3 | {"selection_policy": v3["selection_policy"] | {"selected_requests_per_run": 60}},
        v3 | {"selection_policy_version": 4},
    ):
        with pytest.raises(ValidationError):
            domain.CityCognitionCausalContractV2.model_validate(data)


def test_limit_table_matches_the_spec_exactly() -> None:
    table = domain.CITY_COGNITION_SELECTION_LIMITS
    assert set(table) == {2, 3, 4}
    v2, v3 = table[2], table[3]
    assert (v2.selected_requests_per_agent, v2.selected_requests_per_run) == (4, 60)
    assert (v3.selected_requests_per_agent, v3.selected_requests_per_run) == (1000, 1000)
    assert v2.funded_response_limits == (10, 20, 40, 60)
    assert v3.funded_response_limits == (60, 120, 250, 500, 1000)
    assert (v2.offline_funded_response_limit, v3.offline_funded_response_limit) == (60, 1000)
    assert (v2.concurrency_limit("remote"), v2.concurrency_limit("local")) == (4, 4)
    assert (v3.concurrency_limit("remote"), v3.concurrency_limit("local")) == (8, 4)
    assert (v2.aggregate_deadline_seconds, v3.aggregate_deadline_seconds) == (180, 1800)
    assert (v2.transient_retries_ceiling, v3.transient_retries_ceiling) == (2, 3)
    assert (v2.repair_attempts_ceiling, v3.repair_attempts_ceiling) == (1, 2)
    assert (v2.max_attempts_per_request, v3.max_attempts_per_request) == (4, 6)
    assert (v2.reservation_tokens_per_request, v3.reservation_tokens_per_request) == (1024, 1536)
    assert v2.aggregate_output_token_ceiling("local", 60) == 60 * 1024
    assert v3.aggregate_output_token_ceiling("remote", 1000) == 1000 * 1536
    assert v3.aggregate_output_token_ceiling("mock", 1000) == 0
    assert (v2.max_cognition_input_bytes, v3.max_cognition_input_bytes) == (131_072, 524_288)
    assert (v2.max_terminal_stream_bytes, v3.max_terminal_stream_bytes) == (
        1_048_576,
        8_388_608,
    )
    assert (v2.max_terminal_records, v3.max_terminal_records) == (60, 1000)
    assert (v2.max_summary_attempts, v3.max_summary_attempts) == (240, 6000)
    # Version 4 selects exactly as version 3 and only widens each answer for the thoughts.
    v4 = table[4]
    same = {field for field in v3.__dataclass_fields__ if getattr(v3, field) == getattr(v4, field)}
    assert set(v3.__dataclass_fields__) - same == {
        "selection_policy_version",
        "policy_id",
        "reservation_tokens_per_request",
        "output_tokens_per_attempt",
        "fixed_output_tokens",
    }
    assert (v4.output_tokens_per_attempt, v4.reservation_tokens_per_request) == (640, 3840)
    assert v4.fixed_output_tokens and v4.effective_output_tokens(256) == 640
    assert v3.effective_output_tokens(384) == 256 and v3.effective_output_tokens(128) == 128
    with pytest.raises(TypeError):
        table[5] = v3  # type: ignore[index]
    with pytest.raises(ValueError):
        domain.city_cognition_selection_limits(5)


def _v3_run(*, mode="mock", limit=1000, agent_count=16, minutes=20, profile=None):
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=agent_count, minutes=minutes)
    contract = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3
    requests = module.build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=contract, funded_response_limit=limit
    )
    if mode in {"local", "remote"} and profile is None:
        profile = CityCognitionProviderProfileSnapshotV1.model_validate(profile_data(mode))
    frozen = module.build_city_cognition_input_v2(
        context,
        requests,
        mode=mode,
        funded_response_limit=limit,
        provider_profile=profile,
        causal_contract=contract,
    )
    return context, mobility, baseline, requests, frozen


@pytest.mark.parametrize("limit", [60, 120, 250])
def test_v3_requests_take_every_response_or_spread_them_over_the_run(limit) -> None:
    _context, _mobility, baseline, requests, frozen = _v3_run(mode="remote", limit=limit)
    responses = _sorted_responses(baseline)
    eligible = len(responses)
    assert eligible == 157
    positions = _module().select_spread_indices(eligible, limit)
    assert tuple(r.causal_identity.baseline.baseline_response_id for r in requests) == tuple(
        responses[i].event_id for i in positions
    )
    assert len(requests) == min(eligible, limit)
    if eligible <= limit:
        assert {r.causal_identity.baseline.baseline_response_id for r in requests} == {
            r.event_id for r in responses
        }
    else:
        # The last selection lies in the final stretch of the run, not its first hour.
        assert requests[-1].causal_identity.baseline.model_minute >= responses[-3].model_minute
        # No per-person cap: some person gets more than the V2 four.
        counts = Counter(r.causal_identity.baseline.agent_id for r in requests)
        assert max(counts.values()) > 4
    assert frozen.concurrency_limit == 8
    assert all(item.funded for item in frozen.reservations)
    assert {item.reserved_output_tokens for item in frozen.reservations} == {1536}
    assert frozen.aggregate_output_token_ceiling == limit * 1536
    assert (frozen.transient_retries_ceiling, frozen.repair_attempts_ceiling) == (3, 2)
    assert frozen.aggregate_deadline_seconds == 1800
    assert (frozen.selected_requests_per_agent, frozen.selected_requests_per_run) == (1000, 1000)
    request_ids = {r.request_id for r in requests}
    v2 = _module().build_city_cognition_requests_v2(*_dense_evidence(agent_count=16))
    assert not request_ids & {r.request_id for r in v2}


def test_v3_requests_require_an_allowed_funded_limit() -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=4)
    contract = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3
    for limit in (None, 10, 61, True, 60.0):
        with pytest.raises(ValueError):
            module.build_city_cognition_requests_v2(
                context,
                mobility,
                baseline,
                causal_contract=contract,
                funded_response_limit=limit,
            )
    # V2 selection never depends on the funded limit.
    assert module.build_city_cognition_requests_v2(
        context, mobility, baseline, funded_response_limit=10
    ) == module.build_city_cognition_requests_v2(context, mobility, baseline)


def test_v3_input_cross_checks_every_versioned_field() -> None:
    _context, _mobility, _baseline, _requests, frozen = _v3_run(mode="local", limit=120)
    assert frozen.concurrency_limit == 4
    data = frozen.model_dump(mode="json")
    v2_values = {
        "selected_requests_per_agent": 4,
        "selected_requests_per_run": 60,
        "concurrency_limit": 8,
        "reservation_tokens_per_request": 1024,
        "transient_retries_ceiling": 2,
        "repair_attempts_ceiling": 1,
        "aggregate_deadline_seconds": 180,
        "funded_response_limit": 10,
    }
    for field, value in v2_values.items():
        with pytest.raises(ValidationError):
            domain.CityCognitionInputV2.model_validate_json(canonical_json(data | {field: value}))
    assert domain.CityCognitionInputV2.model_validate_json(canonical_json(data)) == frozen


def test_v2_input_refuses_v3_values_and_v3_retry_settings() -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=4)
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    with pytest.raises(ValueError, match="10, 20, 40, or 60"):
        module.build_city_cognition_input_v2(
            context, requests, mode="mock", funded_response_limit=1000
        )
    profile = profile_data("local")
    facts = profile | {"transient_retries": 3, "repair_attempts": 2}
    facts.pop("revision_sha256")
    from adlife.core.domain.city_cognition import city_cognition_provider_profile_revision_sha256

    draft = CityCognitionProviderProfileSnapshotV1.model_construct(**facts, revision_sha256="0")
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        facts | {"revision_sha256": city_cognition_provider_profile_revision_sha256(draft)}
    )
    with pytest.raises(ValidationError, match="retry ceilings"):
        module.build_city_cognition_input_v2(
            context, requests, mode="local", funded_response_limit=10, provider_profile=snapshot
        )
    contract = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3
    v3_requests = module.build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=contract, funded_response_limit=60
    )
    frozen = module.build_city_cognition_input_v2(
        context,
        v3_requests,
        mode="local",
        funded_response_limit=60,
        provider_profile=snapshot,
        causal_contract=contract,
    )
    assert frozen.effective_settings is not None
    assert (
        frozen.effective_settings.transient_retries,
        frozen.effective_settings.repair_attempts,
    ) == (
        3,
        2,
    )


def test_v3_terminal_evidence_validates_and_records_six_attempts() -> None:
    module = _module()
    _context, _mobility, baseline, requests, frozen = _v3_run(mode="mock", limit=1000)
    records = tuple(
        module.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=index,
            source="mock",
            result=module.build_city_mock_result_v2(request),
        )
        for index, request in enumerate(requests)
    )
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    coverage = evaluation.summary.coverage
    assert (
        coverage.eligible_response_count,
        coverage.selected_request_count,
        coverage.funded_request_count,
        coverage.budget_skipped_count,
        coverage.unselected_response_count,
    ) == (157, 157, 157, 0, 0)
    assert evaluation.summary.outcomes.mock == 157


def _with_attempts(record, attempts: int) -> dict:
    data = record.model_dump(mode="json") | {"attempt_count": attempts}
    facts = {key: value for key, value in data.items() if key != "record_hash"}
    return data | {"record_hash": sha256(canonical_json(facts).encode("utf-8")).hexdigest()}


def test_terminal_attempt_and_index_ceilings_follow_the_record_version() -> None:
    module = _module()
    _c, _m, _b, v3_requests, v3_frozen = _v3_run(mode="local", limit=60)
    context, mobility, baseline = _dense_evidence(agent_count=16)
    v2_requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    v2_frozen = module.build_city_cognition_input_v2(
        context,
        v2_requests,
        mode="local",
        funded_response_limit=60,
        provider_profile=CityCognitionProviderProfileSnapshotV1.model_validate(
            profile_data("local")
        ),
    )
    for requests, frozen, ceiling in ((v3_requests, v3_frozen, 6), (v2_requests, v2_frozen, 4)):
        record = module.build_city_cognition_terminal_record_v2(
            requests[0],
            frozen,
            selection_index=0,
            source="fallback",
            result=module.build_city_fallback_result_v2(requests[0], reason="invalid-result"),
            attempt_count=1,
            reason="invalid-result",
        )
        model = domain.CityCognitionTerminalRecordV2
        assert (
            model.model_validate_json(canonical_json(_with_attempts(record, ceiling))).attempt_count
            == ceiling
        )
        with pytest.raises(ValidationError):
            model.model_validate_json(canonical_json(_with_attempts(record, ceiling + 1)))
        data = record.model_dump(mode="json") | {"selection_index": 60}
        facts = {key: value for key, value in data.items() if key != "record_hash"}
        data["record_hash"] = sha256(canonical_json(facts).encode("utf-8")).hexdigest()
        if ceiling == 4:
            with pytest.raises(ValidationError):
                model.model_validate_json(canonical_json(data))
        else:
            assert model.model_validate_json(canonical_json(data)).selection_index == 60


def test_v3_summary_and_receipt_widths_follow_the_version() -> None:
    module = _module()
    _context, _mobility, baseline, requests, frozen = _v3_run(mode="mock", limit=1000)
    records = tuple(
        module.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=index,
            source="mock",
            result=module.build_city_mock_result_v2(request),
        )
        for index, request in enumerate(requests)
    )
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    from adlife.core.domain.city_run import build_city_cognition_manifest_receipt_v2

    receipt = build_city_cognition_manifest_receipt_v2(frozen, evaluation)
    assert receipt.selection_policy_version == 3
    assert receipt.funded_response_limit == 1000
    assert receipt.coverage.selected_request_count == 157
    summary = evaluation.summary.model_dump(mode="json")
    # The V2 contract hash with V3 widths is refused: a V2 summary keeps its V2 caps.
    v2 = summary | {"causal_contract_sha256": V2_CONTRACT_SHA256}
    with pytest.raises(ValidationError, match="version limits"):
        domain.CityCognitionSummaryV2.model_validate(v2)
    assert get_args(domain.CityCognitionFundedResponseLimit) == (
        10,
        20,
        40,
        60,
        120,
        250,
        500,
        1000,
    )
