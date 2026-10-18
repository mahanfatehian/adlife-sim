"""Real spatial evidence exercises the V2 request and funding boundaries."""

from __future__ import annotations

import importlib
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from itertools import product
from math import fsum
from types import SimpleNamespace

import pytest

from adlife.core.domain.city_cognition import (
    CityCognitionCreativeSnapshotV1,
    CityCognitionProviderProfileSnapshotV1,
)
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CityCognitionCausalAgentV2,
    CityCognitionCausalCampaignV2,
    CityCognitionInitialRuleStateV2,
    CityCognitionRunContextV2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from adlife.core.simulation.spatial_response import evaluate_spatial_responses
from tests.unit.city.test_city_cognition import _evidence
from tests.unit.city.test_city_cognition_v2_contract import causal_data, profile_data
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _billboard, _mobility, _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input


def _module():
    return importlib.import_module("adlife.core.simulation.city_cognition_v2")


def _bypass(value, **updates):
    return type(value).model_construct(
        **{name: getattr(value, name) for name in type(value).model_fields} | updates
    )


def test_mock_signal_uses_only_causal_request_id_and_frozen_digest_order() -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    orders = (
        ("annoyed", "skeptical", "neutral", "curious", "positive"),
        ("none", "low", "medium", "high"),
        ("low", "medium", "high"),
        ("none", "low", "medium", "high"),
    )
    for request in requests:
        digest = sha256(("city-cognition-mock-signal-v1\0" + request.request_id).encode()).digest()
        result = module.build_city_mock_result_v2(request)
        assert tuple(
            getattr(result, f) for f in ("affect", "relevance", "credibility", "salience")
        ) == tuple(order[digest[i] % len(order)] for i, order in enumerate(orders))
        other_context = context.model_copy(update={"run_input_sha256": "e" * 64})
        other = module.build_city_cognition_requests_v2(other_context, mobility, baseline)
        corresponding = next(r for r in other if r.request_id == request.request_id)
        assert module.build_city_mock_result_v2(corresponding) == result
        neutral = module.build_city_rules_result_v2(request)
        assert (neutral.affect, neutral.relevance, neutral.credibility, neutral.salience) == (
            "neutral",
            "medium",
            "medium",
            "medium",
        )
        assert not neutral.safety_flags
        assert module.build_city_budget_skipped_result_v2(request) == neutral
        for reason in ("invalid-result", "authentication-refused", "timeout-exhausted"):
            assert module.build_city_fallback_result_v2(request, reason=reason) == neutral
        with pytest.raises(ValueError):
            module.build_city_fallback_result_v2(request, reason="budget-skipped")
    invalid = _bypass(requests[0], request_id="cog-" + "0" * 64)
    for builder in (
        module.build_city_mock_result_v2,
        module.build_city_rules_result_v2,
        module.build_city_budget_skipped_result_v2,
    ):
        with pytest.raises(ValueError):
            builder(invalid)


def test_influence_policy_exhaustively_maps_applicable_categories_and_neutralizes_whole_vector():
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    request = module.build_city_cognition_requests_v2(context, mobility, baseline)[0]
    neutral = module.build_city_rules_result_v2(request)
    apply = module.apply_city_cognition_influence_v1
    affects = {
        "annoyed": -0.04,
        "skeptical": -0.02,
        "neutral": 0.0,
        "curious": 0.02,
        "positive": 0.04,
    }
    relevance = {"none": 0.5, "low": 0.75, "medium": 1.0, "high": 1.15}
    credibility = {"low": 0.75, "medium": 1.0, "high": 1.1}
    salience = relevance
    combinations = list(product(affects, relevance, credibility, salience))
    assert len(combinations) == 240
    for affect, rel, cred, sal in combinations:
        result = neutral.model_copy(
            update=dict(affect=affect, relevance=rel, credibility=cred, salience=sal)
        )
        for s, r in ((0.19, 0.29), (-0.19, 0.01), (0.0, 0.0), (0.2, 0.3), (-0.2, 0.3)):
            vector = apply(s, r, source="provider", result=result)
            assert vector.applied_sentiment_delta == max(
                -0.2, min(0.2, fsum((s, affects[affect])) * relevance[rel] * credibility[cred])
            )
            assert vector.applied_recall_delta == max(
                0.0, min(0.3, r * relevance[rel] * credibility[cred] * salience[sal])
            )
            assert vector.neutralization_status == "none"
    for field in ("affect", "relevance", "credibility", "salience"):
        for flags in ((), ("stereotype-risk",), ("unsupported-inference",), ("unsafe-content",)):
            result = neutral.model_copy(update={field: "uncertain", "safety_flags": flags})
            vector = apply(0.03, 0.17, source="cache", result=result)
            assert (vector.applied_sentiment_delta, vector.applied_recall_delta) == (0.03, 0.17)
            assert vector.neutralization_status == ("safety" if flags else "uncertain")
    for flag in ("stereotype-risk", "unsupported-inference", "unsafe-content"):
        vector = apply(
            0.03,
            0.17,
            source="provider",
            result=neutral.model_copy(update={"safety_flags": (flag,)}),
        )
        assert (
            vector.applied_sentiment_delta,
            vector.applied_recall_delta,
            vector.neutralization_status,
        ) == (0.03, 0.17, "safety")
    for source in ("rules", "fallback", "budget-skipped", "unselected"):
        vector = apply(
            0.03, 0.17, source=source, result=None if source == "unselected" else neutral
        )
        assert (
            vector.applied_sentiment_delta,
            vector.applied_recall_delta,
            vector.neutralization_status,
        ) == (0.03, 0.17, source)
    mock = module.build_city_mock_result_v2(request)
    apply(0.03, 0.17, source="mock", result=mock)
    for source in ("rules", "fallback", "budget-skipped", "mock", "provider", "cache"):
        with pytest.raises(ValueError):
            apply(0.03, 0.17, source=source, result=None)
    for source, result in (("unselected", neutral), ("invalid", neutral), ("rules", mock)):
        with pytest.raises(ValueError):
            apply(0.03, 0.17, source=source, result=result)
    for bad in (True, float("nan"), float("inf"), -0.21, 0.31, 10**1000):
        with pytest.raises((TypeError, ValueError)):
            apply(bad, 0.1, source="provider", result=neutral)
        with pytest.raises((TypeError, ValueError)):
            apply(0.1, bad, source="provider", result=neutral)


def test_influence_refuses_signed_zero_policy_authority_and_preserves_identity_zero() -> None:
    from math import copysign

    from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1

    module = _module()
    policy = CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1
    weights = tuple(
        row.model_copy(update={"weight": -0.0}) if row.category == "neutral" else row
        for row in policy.affect_adjustments
    )
    changed = policy.model_copy(update={"affect_adjustments": weights})
    with pytest.raises(ValueError):
        module.apply_city_cognition_influence_v1(
            0.0, 0.0, source="unselected", result=None, policy=changed
        )
    vector = module.apply_city_cognition_influence_v1(-0.0, -0.0, source="unselected", result=None)
    assert copysign(1.0, vector.applied_sentiment_delta) == -1.0
    assert copysign(1.0, vector.applied_recall_delta) == -1.0


@pytest.mark.parametrize("source", ["provider", "cache", "mock"])
@pytest.mark.parametrize("sentiment,recall", [(-0.0, -0.0), (-0.0, 0.1), (0.1, -0.0)])
def test_exact_neutral_signal_preserves_canonical_baseline_identity(source, sentiment, recall):
    from adlife.core.domain.city_cognition_v2 import CityCognitionResultV2

    # Independent digest search supplies a legal exact-neutral mock signal as well.
    request_id = next(
        f"cog-{i:064x}"
        for i in range(10000)
        if tuple(
            b % n
            for b, n in zip(
                sha256(("city-cognition-mock-signal-v1\0" + f"cog-{i:064x}").encode()).digest()[:4],
                (5, 4, 3, 4),
                strict=True,
            )
        )
        == (2, 2, 1, 2)
    )
    result = CityCognitionResultV2(
        schema_version=2,
        request_id=request_id,
        affect="neutral",
        relevance="medium",
        credibility="medium",
        salience="medium",
        safety_flags=(),
        disclosure="synthetic-bounded-cognition-not-observed-behavior",
    )
    vector = _module().apply_city_cognition_influence_v1(
        sentiment, recall, source=source, result=result
    )
    assert vector.neutralization_status == "none"
    assert canonical_json(
        dict(sentiment=vector.applied_sentiment_delta, recall=vector.applied_recall_delta)
    ) == canonical_json(dict(sentiment=sentiment, recall=recall))


@pytest.mark.parametrize("boundary", ["context-input", "baseline-evaluation"])
@pytest.mark.parametrize("part", ["agent-trait", "initial-sentiment", "initial-recall"])
def test_canonical_snapshot_substitution_rejects_rehashed_signed_zero(boundary, part):
    module = _module()
    context, _, _ = _dense_evidence(agent_count=2)
    traits = context.agents[0].traits.model_copy(update={"novelty_seeking": 0.0})
    context, mobility, baseline = _dense_evidence(
        agent_count=2, traits=traits, sentiment=0.0, recall=0.0
    )
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    changed = []
    for request in requests:
        identity = request.causal_identity
        if part == "agent-trait":
            agent = identity.agent.model_dump(mode="python")
            agent["traits"]["novelty_seeking"] = -0.0
            updates = {"agent": agent}
        else:
            initial = identity.initial_rule_state.model_dump(mode="python")
            initial["brand_sentiment" if part == "initial-sentiment" else "recall_strength"] = -0.0
            updates = {"initial_rule_state": initial}
        changed.append(_rehash_request(request, **updates))
    changed = tuple(changed)
    if boundary == "context-input":
        with pytest.raises(ValueError):
            module.build_city_cognition_input_v2(
                context, changed, mode="rules", funded_response_limit=60
            )
    else:
        frozen = module.build_city_cognition_input_v2(
            context, requests, mode="rules", funded_response_limit=60
        )
        frozen = frozen.model_copy(
            update={
                "reservations": tuple(
                    reservation.model_copy(
                        update={
                            "request_id": request.request_id,
                            "request_record_sha256": request.request_record_sha256,
                        }
                    )
                    for reservation, request in zip(frozen.reservations, changed, strict=True)
                )
            }
        )
        records = tuple(
            module.build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=i,
                source="rules",
                result=module.build_city_rules_result_v2(request),
            )
            for i, request in enumerate(changed)
        )
        with pytest.raises(ValueError):
            module.validate_city_cognition_evaluation_v2(
                frozen, changed, records, baseline_evaluation=baseline
            )


def test_context_input_binds_even_unused_agent_trait_canonical_bytes():
    module = _module()
    context, _, _ = _dense_evidence(agent_count=2)
    traits = context.agents[0].traits.model_copy(update={"roadside_recall_encoding": 0.0})
    context, mobility, baseline = _dense_evidence(agent_count=2, traits=traits)
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    changed = []
    for request in requests:
        assert request.causal_identity.baseline.channel == "mobile-feed"
        agent = request.causal_identity.agent.model_dump(mode="python")
        agent["traits"]["roadside_recall_encoding"] = -0.0
        changed.append(_rehash_request(request, agent=agent))
    # Rule assumptions cannot observe this unused channel trait. The complete
    # context snapshot must still refuse its distinct canonical identity.
    with pytest.raises(ValueError, match="frozen run context"):
        module.build_city_cognition_input_v2(
            context, tuple(changed), mode="rules", funded_response_limit=60
        )


def _terminal_evidence(*, mode="rules", agent_count=2, minutes=20, limit=60, profile=None):
    context, mobility, baseline = _dense_evidence(agent_count=agent_count, minutes=minutes)
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    if mode in {"local", "remote"} and profile is None:
        profile = CityCognitionProviderProfileSnapshotV1.model_validate(profile_data(mode))
    frozen = module.build_city_cognition_input_v2(
        context, requests, mode=mode, funded_response_limit=limit, provider_profile=profile
    )
    records = tuple(
        module.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=i,
            source=mode if mode in {"rules", "mock"} else "cache",
            result=module.build_city_mock_result_v2(request)
            if mode == "mock"
            else module.build_city_rules_result_v2(request),
        )
        for i, request in enumerate(requests)
    )
    return context, baseline, frozen, requests, records


def _rehash_terminal(record, **changes):
    from adlife.core.domain.city_cognition_v2 import CityCognitionTerminalRecordV2

    data = record.model_dump(mode="python", exclude={"record_hash"}) | changes
    return CityCognitionTerminalRecordV2.model_validate(
        data | {"record_hash": sha256(canonical_json(data).encode()).hexdigest()}
    )


def test_evaluation_rejects_duplicate_or_cross_bound_terminal_records() -> None:
    module = _module()
    _, baseline, frozen, requests, records = _terminal_evidence()
    validate = module.validate_city_cognition_evaluation_v2
    evaluation = validate(frozen, requests, records, baseline_evaluation=baseline)
    lines = tuple(module.city_cognition_terminal_lines_v2(evaluation))
    assert lines == tuple((canonical_json(r) + "\n").encode() for r in records)
    assert evaluation.summary.terminal_stream_sha256 == sha256(b"".join(lines)).hexdigest()
    assert evaluation.summary.coverage.eligible_response_count == baseline.counts.response_count
    for field in (
        "request_record_sha256",
        "run_input_sha256",
        "response_input_sha256",
        "scenario_sha256",
        "city_sha256",
        "baseline_sha256",
        "baseline_response_id",
        "notice_event_id",
        "opportunity_id",
        "baseline_state_update_event_id",
    ):
        changed = _rehash_terminal(records[0], **{field: "f" * 64})
        with pytest.raises(ValueError):
            validate(frozen, requests, (changed, *records[1:]), baseline_evaluation=baseline)
    for field, value in {
        "agent_id": "person-099",
        "campaign_id": "other",
        "placement_id": "other",
        "channel": "roadside-billboard",
        "activity": "commute",
        "model_minute": 1439,
        "millisecond_within_minute": 59999,
        "baseline_state_update_cause_count": 2,
    }.items():
        changed = _rehash_terminal(records[0], **{field: value})
        with pytest.raises(ValueError):
            validate(frozen, requests, (changed, *records[1:]), baseline_evaluation=baseline)
    for bad in (
        records[1:],
        (*records, records[0]),
        tuple(reversed(records)),
        (records[0], *records[:-1]),
    ):
        with pytest.raises(ValueError):
            validate(frozen, requests, bad, baseline_evaluation=baseline)
    with pytest.raises(ValueError):
        type(evaluation).model_validate(
            evaluation.model_dump(mode="python") | {"records": tuple(reversed(records))}
        )
    # Rebind every surviving reservation: cardinality consistency is insufficient.
    shorter_input = frozen.model_copy(update={"reservations": frozen.reservations[:-1]})
    with pytest.raises(ValueError):
        validate(shorter_input, requests[:-1], records[:-1], baseline_evaluation=baseline)
    for i, (request, record) in enumerate(zip(requests, records, strict=True)):
        proposal = request.causal_identity.baseline
        for field in type(proposal).model_fields:
            if field not in {"schema_version", "pre_minute_rule_state_sha256", "rule_factors"}:
                assert getattr(record, field) == getattr(proposal, field)
        with pytest.raises(ValueError):
            module.build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=(i + 1) % len(records),
                source="rules",
                result=record.result,
            )


def test_terminal_uses_frozen_input_when_current_defaults_are_unavailable(monkeypatch) -> None:
    module = _module()
    domain = importlib.import_module("adlife.core.domain.city_cognition_v2")
    _, baseline, frozen, requests, records = _terminal_evidence(mode="mock")
    for owner in (module, domain):
        for name in vars(owner):
            if name.startswith("CITY_COGNITION_"):
                monkeypatch.setattr(owner, name, None)
    for i, request in enumerate(requests):
        assert (
            module.build_city_cognition_terminal_record_v2(
                request, frozen, selection_index=i, source="mock", result=records[i].result
            )
            == records[i]
        )
    module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    vector = module.apply_city_cognition_influence_v1(
        0.1,
        0.1,
        source="mock",
        result=records[0].result,
        policy=frozen.causal_contract.influence_policy,
    )
    assert vector.neutralization_status == "none"


def test_provider_funding_attempt_capacity_usage_and_overlapping_diagnostics() -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=4)
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    facts = profile_data() | {"transient_retries": 0, "repair_attempts": 0}
    facts.pop("revision_sha256")
    profile = CityCognitionProviderProfileSnapshotV1.model_validate(
        facts | {"revision_sha256": sha256((canonical_json(facts) + "\n").encode()).hexdigest()}
    )
    frozen = module.build_city_cognition_input_v2(
        context, requests, mode="local", funded_response_limit=10, provider_profile=profile
    )
    assert len(requests) > 10
    records = []
    for i, request in enumerate(requests):
        neutral = module.build_city_rules_result_v2(request)
        source, result, reason = (
            ("budget-skipped", neutral, "budget-skipped") if i >= 10 else ("cache", neutral, None)
        )
        if i == 0:
            result = neutral.model_copy(
                update={"affect": "uncertain", "safety_flags": ("unsafe-content",)}
            )
        if i == 1:
            source, reason = "fallback", "authentication-refused"
        if i == 2:
            source = "provider"
        record = module.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=i,
            source=source,
            result=result,
            reason=reason,
            attempt_count=1 if source == "provider" else 0,
            prompt_tokens=2_147_483_647 if source == "cache" else None,
            completion_tokens=2_147_483_647 if source == "cache" else None,
        )
        records.append(record)
        with pytest.raises(ValueError):
            module.build_city_cognition_terminal_record_v2(
                request, frozen, selection_index=i, source="rules", result=neutral
            )
        if i < 10:
            with pytest.raises(ValueError):
                module.build_city_cognition_terminal_record_v2(
                    request,
                    frozen,
                    selection_index=i,
                    source="budget-skipped",
                    result=neutral,
                    reason="budget-skipped",
                )
            for bad_source in ("provider", "fallback"):
                with pytest.raises(ValueError):
                    module.build_city_cognition_terminal_record_v2(
                        request,
                        frozen,
                        selection_index=i,
                        source=bad_source,
                        result=neutral,
                        attempt_count=2,
                        reason="timeout-exhausted" if bad_source == "fallback" else None,
                    )
        else:
            with pytest.raises(ValueError):
                module.build_city_cognition_terminal_record_v2(
                    request, frozen, selection_index=i, source="cache", result=neutral
                )
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, tuple(records), baseline_evaluation=baseline
    )
    summary = evaluation.summary
    assert summary.neutralization.uncertain_result_count == 1
    assert summary.neutralization.safety_neutralized_result_count == 1
    assert summary.neutralization.neutralized_response_count == baseline.counts.response_count - 8
    assert summary.coverage.funded_request_count == 10


def test_empty_terminal_evidence_has_exact_empty_lf_stream() -> None:
    module = _module()
    context, _, baseline = _dense_evidence(agent_count=2, placements=[_phone(probability=0.0)])
    requests = ()
    records = ()
    frozen = module.build_city_cognition_input_v2(
        context, requests, mode="rules", funded_response_limit=60
    )
    assert not requests
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    assert tuple(module.city_cognition_terminal_lines_v2(evaluation)) == ()
    assert evaluation.summary.terminal_stream_bytes == 0
    assert evaluation.summary.terminal_stream_sha256 == sha256(b"").hexdigest()


@pytest.mark.parametrize(
    "change",
    ["initial-doc", "initial-state", "baseline-group", "science", "rule-factors", "pre-state"],
)
def test_evaluation_rejects_coordinated_rehashed_request_mutations(change) -> None:
    module = _module()
    _, baseline, frozen, requests, _ = _terminal_evidence()
    request = requests[0]
    identity = request.causal_identity
    if change == "initial-doc":
        updates = {"initial_cognition_state_sha256": "f" * 64}
    elif change == "initial-state":
        updates = {
            "initial_rule_state": identity.initial_rule_state.model_dump(mode="python")
            | {"brand_sentiment": 0.9}
        }
    elif change == "science":
        updates = {"city_sha256": "f" * 64}
    else:
        proposal = identity.baseline.model_dump(mode="python")
        if change == "baseline-group":
            proposal["baseline_state_update_event_id"] = "f" * 64
        elif change == "pre-state":
            proposal["pre_minute_rule_state_sha256"] = "f" * 64
        else:
            proposal["rule_factors"]["baseline_recall_delta"] = 0.299
        updates = {"baseline": proposal}
    changed = tuple(
        _rehash_request(r, **updates)
        if i == 0
        or change in {"initial-doc", "science"}
        or (
            change == "initial-state"
            and r.causal_identity.baseline.agent_id == identity.baseline.agent_id
        )
        else r
        for i, r in enumerate(requests)
    )
    reservations = tuple(
        _bypass(item, request_id=r.request_id, request_record_sha256=r.request_record_sha256)
        for item, r in zip(frozen.reservations, changed, strict=True)
    )
    frozen = frozen.model_copy(update={"reservations": reservations})
    terminals = tuple(
        module.build_city_cognition_terminal_record_v2(
            r,
            frozen,
            selection_index=i,
            source="rules",
            result=module.build_city_rules_result_v2(r),
        )
        for i, r in enumerate(changed)
    )
    with pytest.raises(ValueError):
        module.validate_city_cognition_evaluation_v2(
            frozen, changed, terminals, baseline_evaluation=baseline
        )


def test_evaluation_regenerates_global_selection_and_full_same_minute_group() -> None:
    module = _module()
    _, baseline, frozen, requests, records = _terminal_evidence(agent_count=16)
    assert len(requests) == 60
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    assert (
        evaluation.summary.coverage.unselected_response_count == baseline.counts.response_count - 60
    )
    for count in (0, 59):
        shortened = frozen.model_copy(update={"reservations": frozen.reservations[:count]})
        with pytest.raises(ValueError):
            module.validate_city_cognition_evaluation_v2(
                shortened, requests[:count], records[:count], baseline_evaluation=baseline
            )
    context, mobility, baseline = _same_minute_evidence()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    frozen = module.build_city_cognition_input_v2(
        context, requests, mode="mock", funded_response_limit=60
    )
    records = tuple(
        module.build_city_cognition_terminal_record_v2(
            r,
            frozen,
            selection_index=i,
            source="mock",
            result=module.build_city_mock_result_v2(r, policy=frozen.causal_contract.mock_policy),
        )
        for i, r in enumerate(requests)
    )
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    assert all(
        r.baseline_state_update_cause_count == baseline.counts.response_count for r in records
    )
    assert evaluation.summary.coverage.eligible_response_count > 4


def test_evaluation_rejects_valid_but_later_selection_substitution() -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=2)
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    selected_ids = {r.causal_identity.baseline.baseline_response_id for r in requests}
    original = next(
        r for r in reversed(requests) if r.causal_identity.baseline.agent_id == "person-001"
    )
    later = next(
        r
        for r in baseline.records
        if r.event_type == "spatial.response"
        and r.agent_id == "person-001"
        and r.event_id not in selected_ids
    )
    update = next(
        r
        for r in baseline.records
        if r.event_type == "spatial.state-updated" and later.event_id in r.caused_by_event_ids
    )
    proposal = original.causal_identity.baseline.model_dump(mode="python")
    proposal.update(
        baseline_response_id=later.event_id,
        notice_event_id=later.caused_by,
        opportunity_id=later.opportunity_id,
        baseline_state_update_event_id=update.event_id,
        baseline_state_update_cause_count=len(update.caused_by_event_ids),
        model_minute=later.model_minute,
        millisecond_within_minute=later.millisecond_within_minute,
        pre_minute_rule_state_sha256=later.state_before_sha256,
    )
    for name in proposal["rule_factors"]:
        proposal["rule_factors"][name] = (
            2
            if name == "schema_version"
            else later.sentiment_delta
            if name == "baseline_sentiment_delta"
            else later.recall_delta
            if name == "baseline_recall_delta"
            else getattr(later, name)
        )
    proposal["activity"] = next(
        p.activity for p in mobility.frame(later.model_minute) if p.agent_id == later.agent_id
    )
    replacement = _rehash_request(original, baseline=proposal)
    requests = tuple(
        sorted(
            (replacement if r == original else r for r in requests),
            key=lambda r: (
                r.causal_identity.baseline.model_minute,
                r.causal_identity.baseline.agent_id,
                r.causal_identity.baseline.campaign_id,
                r.causal_identity.baseline.baseline_response_id,
            ),
        )
    )
    # The allocator accepts every local invariant and binds exact replacement IDs.
    reservations = module.allocate_city_cognition_reservations_v2(
        requests, mode="rules", funded_response_limit=60
    )
    frozen = module.build_city_cognition_input_v2(
        context, requests, mode="rules", funded_response_limit=60
    )
    assert frozen.reservations == reservations
    records = tuple(
        module.build_city_cognition_terminal_record_v2(
            r,
            frozen,
            selection_index=i,
            source="rules",
            result=module.build_city_rules_result_v2(r),
        )
        for i, r in enumerate(requests)
    )
    with pytest.raises(ValueError, match="exact canonical baseline selection"):
        module.validate_city_cognition_evaluation_v2(
            frozen, requests, records, baseline_evaluation=baseline
        )


@pytest.mark.parametrize(
    "boundary", ["request", "input", "record", "baseline", "result", "policy", "evaluation"]
)
def test_task4_recursively_revalidates_constructed_nested_evidence(boundary) -> None:
    module = _module()
    _, baseline, frozen, requests, records = _terminal_evidence()
    if boundary == "request":
        requests = (
            _bypass(
                requests[0],
                causal_identity=_bypass(
                    requests[0].causal_identity,
                    baseline=_bypass(requests[0].causal_identity.baseline, model_minute=True),
                ),
            ),
            *requests[1:],
        )
    elif boundary == "input":
        frozen = _bypass(
            frozen,
            reservations=(_bypass(frozen.reservations[0], funded=1), *frozen.reservations[1:]),
        )
    elif boundary == "record":
        records = (
            _bypass(records[0], result=_bypass(records[0].result, affect="invalid")),
            *records[1:],
        )
    elif boundary == "baseline":
        state = baseline.final_states[0]
        baseline = _bypass(
            baseline,
            final_states=(_bypass(state, recall_strength=True), *baseline.final_states[1:]),
        )
    elif boundary == "result":
        with pytest.raises(ValueError):
            module.build_city_cognition_terminal_record_v2(
                requests[0],
                frozen,
                selection_index=0,
                source="rules",
                result=_bypass(records[0].result, safety_flags=("invalid",)),
            )
        return
    elif boundary == "policy":
        policy = frozen.causal_contract.influence_policy
        with pytest.raises(ValueError):
            module.apply_city_cognition_influence_v1(
                0.1,
                0.1,
                source="provider",
                result=records[0].result,
                policy=_bypass(
                    policy,
                    affect_adjustments=(
                        _bypass(policy.affect_adjustments[0], weight=True),
                        *policy.affect_adjustments[1:],
                    ),
                ),
            )
        return
    else:
        evaluation = module.validate_city_cognition_evaluation_v2(
            frozen, requests, records, baseline_evaluation=baseline
        )
        with pytest.raises(ValueError):
            tuple(
                module.city_cognition_terminal_lines_v2(
                    _bypass(
                        evaluation, summary=_bypass(evaluation.summary, terminal_stream_bytes=True)
                    )
                )
            )
        return
    with pytest.raises(ValueError):
        module.validate_city_cognition_evaluation_v2(
            frozen, requests, records, baseline_evaluation=baseline
        )


def _context(legacy, baseline):
    first = {}
    for record in baseline.records:
        if record.event_type == "spatial.state-updated":
            first.setdefault((record.agent_id, record.campaign_id), record.previous_state)
    for state in baseline.final_states:
        first.setdefault((state.agent_id, state.campaign_id), state)
    return CityCognitionRunContextV2(
        schema_version=2,
        run_input_sha256=legacy.run_input_sha256,
        response_input_sha256=legacy.response_input_sha256,
        scenario_sha256=legacy.scenario_sha256,
        city_sha256=legacy.city_sha256,
        agents=tuple(
            CityCognitionCausalAgentV2(
                schema_version=2, agent_id=a.agent_id, interests=a.interests, traits=a.traits
            )
            for a in legacy.agents
        ),
        campaigns=tuple(
            CityCognitionCausalCampaignV2(
                schema_version=2,
                campaign_id=c.campaign_id,
                creative_sha256=c.creative_sha256,
                creative=c.creative,
                target_interests=c.target_interests,
                relative_price=c.relative_price,
            )
            for c in legacy.campaigns
        ),
        initial_states=tuple(
            CityCognitionInitialRuleStateV2(
                schema_version=2,
                agent_id=s.agent_id,
                campaign_id=s.campaign_id,
                brand_sentiment=s.brand_sentiment,
                recall_strength=s.recall_strength,
            )
            for s in first.values()
        ),
    )


def test_descriptive_provenance_changes_record_hash_not_causal_identity() -> None:
    compiled, baseline, legacy = _evidence()
    context = _context(legacy, baseline)
    second_context = context.model_copy(update={"run_input_sha256": "e" * 64})
    build = _module().build_city_cognition_requests_v2
    first = build(context, compiled.mobility, baseline)[0]
    second = build(second_context, compiled.mobility, baseline)[0]
    assert first.request_id == second.request_id
    assert first.causal_identity == second.causal_identity
    assert first.request_record_sha256 != second.request_record_sha256
    assert (
        first.request_id
        == "cog-" + sha256(canonical_json(first.causal_identity).encode("utf-8")).hexdigest()
    )
    record = first.model_dump(mode="json", exclude={"request_record_sha256"})
    assert first.request_record_sha256 == sha256(canonical_json(record).encode()).hexdigest()
    causal_json = canonical_json(first.causal_identity)
    for forbidden in (
        "display_name",
        "age",
        "occupation",
        "biography",
        "background",
        "mbti",
        "persona_id",
        "purchase_intention",
        "cumulative",
        "provider_result",
        "committed_state",
    ):
        assert '"' + forbidden + '"' not in causal_json


def _dense_evidence(
    *,
    agent_count=16,
    minutes=20,
    placements=None,
    roadside=False,
    attention_seed=10,
    traits=None,
    sentiment=0.1,
    recall=0.1,
):
    pack = load_pack(pack_data())
    mobility = (
        _mobility(pack)
        if roadside
        else CityMobility(pack, seed=42, agent_count=agent_count, days=1)
    )
    creative = CityCognitionCreativeSnapshotV1.model_validate(causal_data()["campaign"]["creative"])
    scenario = _scenario(
        pack, placements or [_phone(cap=20, windows=[{"start_minute": 0, "end_minute": minutes}])]
    )
    scenario = scenario.model_copy(
        update={
            "campaigns": tuple(
                c.model_copy(update={"creative_sha256": creative.fingerprint})
                for c in scenario.campaigns
            )
        }
    )
    ids = tuple(a.agent_id for a in mobility.agents)
    inputs = _response_input(
        scenario, agent_ids=ids, traits=traits, sentiment=sentiment, recall=recall
    )
    opportunities = evaluate_spatial_opportunities(mobility, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=attention_seed)
    baseline = evaluate_spatial_responses(inputs, scenario, opportunities, attention, agent_ids=ids)
    legacy = SimpleNamespace(
        run_input_sha256="b" * 64,
        response_input_sha256=inputs.fingerprint,
        scenario_sha256=scenario.fingerprint,
        city_sha256=pack.fingerprint,
        agents=tuple(
            SimpleNamespace(
                agent_id=a.agent_id, interests=tuple(sorted(a.interests)), traits=a.traits
            )
            for a in inputs.profiles
        ),
        campaigns=tuple(
            SimpleNamespace(
                campaign_id=c.campaign_id,
                creative_sha256=c.creative_sha256,
                creative=creative,
                target_interests=tuple(sorted(c.target_interests)),
                relative_price=c.relative_price,
            )
            for c in inputs.campaigns
        ),
    )
    return _context(legacy, baseline), mobility, baseline


def test_request_selection_is_first_four_per_agent_then_first_sixty() -> None:
    context, mobility, baseline = _dense_evidence()
    ordered = sorted(
        (r for r in baseline.records if r.event_type == "spatial.response"),
        key=lambda r: (r.model_minute, r.agent_id, r.campaign_id, r.event_id),
    )
    assert min(Counter(r.agent_id for r in ordered).values()) > 4
    counts = Counter()
    eligible = []
    for response in ordered:
        counts[response.agent_id] += 1
        if counts[response.agent_id] <= 4:
            eligible.append(response.event_id)
    assert len(eligible) == 64
    build = _module().build_city_cognition_requests_v2
    requests = build(context, mobility, baseline)
    assert [r.causal_identity.baseline.baseline_response_id for r in requests] == eligible[:60]
    permuted = context.model_copy(
        update={
            "agents": tuple(reversed(context.agents)),
            "initial_states": tuple(reversed(context.initial_states)),
        }
    )
    assert build(permuted, mobility, baseline) == requests


@pytest.mark.parametrize(
    "change",
    ["city", "scenario", "response", "sentiment", "recall", "traits", "interests", "campaign"],
)
def test_context_must_match_complete_scientific_inputs(change) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    updates = {}
    if change in {"city", "scenario", "response"}:
        updates[change + ("_input_sha256" if change == "response" else "_sha256")] = "a" * 64
    elif change in {"sentiment", "recall"}:
        field = "brand_sentiment" if change == "sentiment" else "recall_strength"
        updates["initial_states"] = (
            context.initial_states[0].model_copy(update={field: 0.45}),
            *context.initial_states[1:],
        )
    elif change == "traits":
        agent = context.agents[0]
        updates["agents"] = (
            agent.model_copy(
                update={"traits": agent.traits.model_copy(update={"roadside_recall_encoding": 0.9})}
            ),
            *context.agents[1:],
        )
    elif change == "interests":
        updates["agents"] = (
            context.agents[0].model_copy(update={"interests": ("coffee", "travel")}),
            *context.agents[1:],
        )
    else:
        updates["campaigns"] = (context.campaigns[0].model_copy(update={"relative_price": 1.0}),)
    with pytest.raises(ValueError):
        _module().build_city_cognition_requests_v2(
            context.model_copy(update=updates), mobility, baseline
        )


@pytest.mark.parametrize("change", ["missing", "duplicate", "invalid-activity"])
def test_every_eligible_response_requires_one_valid_mobility_position(monkeypatch, change) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    original = mobility.frame

    def frame(minute):
        positions = original(minute)
        if change == "missing":
            return ()
        if change == "duplicate":
            return (*positions, positions[0])
        return tuple(replace(p, activity="invented") for p in positions)

    monkeypatch.setattr(mobility, "frame", frame)
    with pytest.raises(ValueError):
        _module().build_city_cognition_requests_v2(context, mobility, baseline)


def test_untouched_initial_state_is_checked() -> None:
    context, mobility, baseline = _dense_evidence(agent_count=16, minutes=1)
    untouched = next(s for s in baseline.final_states if s.response_count == 0)
    states = tuple(
        s.model_copy(update={"brand_sentiment": -0.4}) if s.agent_id == untouched.agent_id else s
        for s in context.initial_states
    )
    with pytest.raises(ValueError):
        _module().build_city_cognition_requests_v2(
            context.model_copy(update={"initial_states": states}), mobility, baseline
        )


@pytest.mark.parametrize(
    "change",
    [
        "missing-update",
        "multiple-updates",
        "duplicate-response",
        "nested-bool",
        "wrong-pre-state",
        "wrong-minute",
    ],
)
def test_bypassed_baseline_evidence_is_revalidated(change) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    records = list(baseline.records)
    update = next(r for r in records if r.event_type == "spatial.state-updated")
    response = next(r for r in records if r.event_type == "spatial.response")
    if change == "missing-update":
        records.remove(update)
    elif change == "multiple-updates":
        records.append(update)
    elif change == "duplicate-response":
        records.append(response)
    elif change == "nested-bool":
        records[records.index(update)] = _bypass(
            update, previous_state=_bypass(update.previous_state, recall_strength=True)
        )
    elif change == "wrong-pre-state":
        records[records.index(response)] = _bypass(response, state_before_sha256="f" * 64)
    else:
        records[records.index(response)] = _bypass(response, model_minute=response.model_minute + 1)
    with pytest.raises(ValueError):
        _module().build_city_cognition_requests_v2(
            context, mobility, _bypass(baseline, records=tuple(records))
        )


def test_reservations_distinguish_offline_funding_from_provider_tokens() -> None:
    context, mobility, baseline = _dense_evidence()
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    allocate = module.allocate_city_cognition_reservations_v2
    for mode in ("rules", "mock"):
        reservations = allocate(requests, mode=mode, funded_response_limit=60)
        assert len(reservations) == 60
        assert all(r.funded and r.reserved_output_tokens == 0 for r in reservations)
    for mode in ("local", "remote"):
        for limit in (10, 20, 40, 60):
            reservations = allocate(requests, mode=mode, funded_response_limit=limit)
            assert [(r.funded, r.reserved_output_tokens) for r in reservations] == [
                (True, 512)
            ] * limit + [(False, 0)] * (60 - limit)
            assert [r.selection_index for r in reservations] == list(range(60))
            assert [r.request_id for r in reservations] == [r.request_id for r in requests]
            assert [r.request_record_sha256 for r in reservations] == [
                r.request_record_sha256 for r in requests
            ]


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
@pytest.mark.parametrize("count", [0, 1, 60])
def test_input_freezes_complete_policy_and_profile_without_mutation(mode, count) -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)[:count]
    profile = (
        None
        if mode in {"rules", "mock"}
        else CityCognitionProviderProfileSnapshotV1.model_validate(profile_data(mode))
    )
    before = None if profile is None else canonical_json(profile)
    document = module.build_city_cognition_input_v2(
        context,
        requests,
        mode=mode,
        funded_response_limit=60 if profile is None else 10,
        provider_profile=profile,
    )
    assert document.run_input_sha256 == context.run_input_sha256
    assert document.aggregate_output_token_ceiling == (0 if profile is None else 5120)
    assert len(document.reservations) == count
    assert document.causal_contract_sha256 == document.causal_contract.fingerprint
    if profile:
        assert document.provider_profile == profile
        assert canonical_json(profile) == before
        assert document.effective_settings.max_output_tokens == 128
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
            assert getattr(document.effective_settings, field) == getattr(profile, field)
    else:
        assert document.effective_settings is None


@pytest.mark.parametrize(
    "mode,limit",
    [
        ("rules", 10),
        ("mock", 20),
        ("invented", 60),
        ("local", True),
        ("remote", 10.0),
        ("local", 15),
        ("local", "20"),
    ],
)
def test_reservations_reject_invalid_modes_and_limit_tokens(mode, limit) -> None:
    with pytest.raises((TypeError, ValueError)):
        _module().allocate_city_cognition_reservations_v2(
            (), mode=mode, funded_response_limit=limit
        )


def _rehash_request(request, **identity_updates):
    identity = request.causal_identity.model_dump(mode="python") | identity_updates
    baseline = identity["baseline"]
    identity["baseline_sha256"] = sha256(canonical_json(baseline).encode()).hexdigest()
    facts = dict(
        schema_version=2,
        run_input_sha256=request.run_input_sha256,
        causal_identity=identity,
        request_id="cog-" + sha256(canonical_json(identity).encode()).hexdigest(),
    )
    return type(request).model_validate(
        facts | {"request_record_sha256": sha256(canonical_json(facts).encode()).hexdigest()}
    )


@pytest.mark.parametrize(
    "change",
    [
        "run",
        "science",
        "initial-doc",
        "agent",
        "campaign",
        "initial-state",
        "rule-math",
        "contract",
    ],
)
def test_input_rejects_rehashed_requests_that_disagree_with_context(change) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    request = module.build_city_cognition_requests_v2(context, mobility, baseline)[0]
    identity = request.causal_identity
    updates = {}
    if change == "run":
        facts = request.model_dump(mode="python", exclude={"request_record_sha256"}) | {
            "run_input_sha256": "f" * 64
        }
        request = type(request).model_validate(
            facts | {"request_record_sha256": sha256(canonical_json(facts).encode()).hexdigest()}
        )
    elif change in {"science", "initial-doc", "contract"}:
        field = {
            "science": "response_input_sha256",
            "initial-doc": "initial_cognition_state_sha256",
            "contract": "causal_contract_sha256",
        }[change]
        updates[field] = "f" * 64
    elif change == "agent":
        updates["agent"] = identity.agent.model_dump(mode="python") | {
            "interests": ("coffee", "travel")
        }
    elif change == "campaign":
        updates["campaign"] = identity.campaign.model_dump(mode="python") | {"relative_price": 0.9}
    elif change == "initial-state":
        updates["initial_rule_state"] = identity.initial_rule_state.model_dump(mode="python") | {
            "brand_sentiment": -0.5
        }
    else:
        updates["baseline"] = identity.baseline.model_dump(mode="python")
        updates["baseline"]["rule_factors"]["baseline_recall_delta"] = 0.29
    request = _rehash_request(request, **updates)
    with pytest.raises(ValueError):
        module.build_city_cognition_input_v2(
            context, (request,), mode="rules", funded_response_limit=60
        )


@pytest.mark.parametrize(
    "change",
    ["reverse", "duplicate", "duplicate-baseline", "more-than-four", "mixed-run", "nested-invalid"],
)
def test_allocator_rejects_invalid_request_sets(change) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    if change == "reverse":
        requests = tuple(reversed(requests))
    elif change == "duplicate":
        requests = (requests[0], requests[0])
    elif change == "duplicate-baseline":
        changed = requests[0].causal_identity.baseline.model_dump(mode="python") | {
            "activity": "leisure"
        }
        requests = (requests[0], _rehash_request(requests[0], baseline=changed))
    elif change == "mixed-run":
        other_context = context.model_copy(update={"run_input_sha256": "c" * 64})
        requests = (
            requests[0],
            module.build_city_cognition_requests_v2(other_context, mobility, baseline)[1],
        )
    elif change == "nested-invalid":
        requests = (
            _bypass(
                requests[0],
                causal_identity=_bypass(
                    requests[0].causal_identity,
                    agent=_bypass(
                        requests[0].causal_identity.agent,
                        traits=_bypass(
                            requests[0].causal_identity.agent.traits, price_sensitivity=True
                        ),
                    ),
                ),
            ),
        )
    else:
        selected_ids = {r.causal_identity.baseline.baseline_response_id for r in requests}
        later = next(
            r
            for r in baseline.records
            if r.event_type == "spatial.response"
            and r.agent_id == "person-001"
            and r.event_id not in selected_ids
        )
        first = next(r for r in requests if r.causal_identity.baseline.agent_id == "person-001")
        update = next(
            r
            for r in baseline.records
            if r.event_type == "spatial.state-updated" and later.event_id in r.caused_by_event_ids
        )
        proposal = first.causal_identity.baseline.model_dump(mode="python")
        proposal.update(
            {
                "baseline_response_id": later.event_id,
                "notice_event_id": later.caused_by,
                "opportunity_id": later.opportunity_id,
                "baseline_state_update_event_id": update.event_id,
                "baseline_state_update_cause_count": len(update.caused_by_event_ids),
                "model_minute": later.model_minute,
                "millisecond_within_minute": later.millisecond_within_minute,
                "pre_minute_rule_state_sha256": later.state_before_sha256,
            }
        )
        proposal["rule_factors"].update(
            {
                "prior_notices_today": later.prior_notices_today,
                "frequency_fatigue": later.frequency_fatigue,
                "baseline_sentiment_delta": later.sentiment_delta,
                "baseline_recall_delta": later.recall_delta,
            }
        )
        requests = tuple(
            sorted(
                (*requests, _rehash_request(first, baseline=proposal)),
                key=lambda r: (
                    r.causal_identity.baseline.model_minute,
                    r.causal_identity.baseline.agent_id,
                    r.causal_identity.baseline.campaign_id,
                    r.causal_identity.baseline.baseline_response_id,
                ),
            )
        )
    with pytest.raises((TypeError, ValueError)):
        module.allocate_city_cognition_reservations_v2(
            requests, mode="rules", funded_response_limit=60
        )


@pytest.mark.parametrize(
    "mode,profile_mode",
    [
        ("rules", "local"),
        ("mock", "remote"),
        ("local", None),
        ("remote", "local"),
        ("local", "remote"),
    ],
)
def test_input_requires_exact_profile_mode_pairing(mode, profile_mode) -> None:
    context, _, _ = _dense_evidence(agent_count=2)
    profile = (
        None
        if profile_mode is None
        else CityCognitionProviderProfileSnapshotV1.model_validate(profile_data(profile_mode))
    )
    with pytest.raises(ValueError):
        _module().build_city_cognition_input_v2(
            context, (), mode=mode, funded_response_limit=60, provider_profile=profile
        )


def _same_minute_evidence():
    return _dense_evidence(
        roadside=True,
        placements=[
            _billboard(
                placement_id=f"board-{i:02}",
                fraction=0.4 + i * 0.005,
                longitude=0.004 + i * 0.00005,
            )
            for i in range(20)
        ],
        attention_seed=5,
    )


def test_same_minute_requests_bind_full_group_including_unselected_siblings() -> None:
    context, mobility, baseline = _same_minute_evidence()
    requests = _module().build_city_cognition_requests_v2(context, mobility, baseline)
    responses = [r for r in baseline.records if r.event_type == "spatial.response"]
    updates = [r for r in baseline.records if r.event_type == "spatial.state-updated"]
    assert len(updates) == 1
    assert len(responses) > 4
    assert len(requests) == 4
    assert [r.causal_identity.baseline.baseline_response_id for r in requests] == sorted(
        r.event_id for r in responses
    )[:4]
    for request in requests:
        proposal = request.causal_identity.baseline
        assert proposal.baseline_state_update_cause_count == len(responses)
        assert proposal.baseline_state_update_event_id == updates[0].event_id
        assert (
            proposal.pre_minute_rule_state_sha256
            == sha256(canonical_json(updates[0].previous_state).encode()).hexdigest()
        )
        assert proposal.activity == "commute"


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseline_state_update_cause_count", 1),
        ("baseline_state_update_event_id", "f" * 64),
        ("pre_minute_rule_state_sha256", "f" * 64),
    ],
)
def test_allocator_refuses_rehashed_shared_group_disagreement(field, value) -> None:
    context, mobility, baseline = _same_minute_evidence()
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    altered = requests[1].causal_identity.baseline.model_dump(mode="python") | {field: value}
    with pytest.raises(ValueError):
        module.allocate_city_cognition_reservations_v2(
            (requests[0], _rehash_request(requests[1], baseline=altered)),
            mode="rules",
            funded_response_limit=60,
        )


def test_frozen_contract_replay_ignores_unavailable_current_defaults(monkeypatch) -> None:
    import adlife.core.domain.city_cognition_v2 as domain

    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=2)
    frozen = domain.CityCognitionCausalContractV2.model_validate_json(
        canonical_json(CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2)
    )
    expected = module.build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=frozen
    )
    expected_input = module.build_city_cognition_input_v2(
        context, expected, mode="mock", funded_response_limit=60, causal_contract=frozen
    )
    for namespace in (domain, module):
        for name in tuple(vars(namespace)):
            if name.startswith("CITY_COGNITION_"):
                monkeypatch.setattr(namespace, name, None)
    requests = module.build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=frozen
    )
    assert requests == expected
    assert module.build_city_cognition_requests_v2(context, mobility, baseline) == expected
    assert (
        module.build_city_cognition_input_v2(
            context, requests, mode="mock", funded_response_limit=60, causal_contract=frozen
        )
        == expected_input
    )


@pytest.mark.parametrize("agent_count", [2, 16])
def test_invalid_unselected_activity_cannot_hide_behind_selection_caps(
    monkeypatch, agent_count
) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=agent_count)
    module = _module()
    selected = module.build_city_cognition_requests_v2(context, mobility, baseline)
    last = max(r.model_minute for r in baseline.records if r.event_type == "spatial.response")
    assert last > max(r.causal_identity.baseline.model_minute for r in selected)
    original = mobility.frame

    def frame(minute):
        positions = original(minute)
        return (
            tuple(replace(p, activity="invented") for p in positions)
            if minute == last
            else positions
        )

    monkeypatch.setattr(mobility, "frame", frame)
    with pytest.raises(ValueError, match="activity"):
        module.build_city_cognition_requests_v2(context, mobility, baseline)


@pytest.mark.parametrize("minute_token", ["off-by-one", "float", "bool"])
@pytest.mark.parametrize("target", ["selected", "per-agent-unselected", "globally-unselected"])
def test_every_response_requires_an_exact_matching_mobility_minute(
    monkeypatch, minute_token, target
) -> None:
    context, mobility, baseline = _dense_evidence(
        agent_count=16 if target == "globally-unselected" else 2
    )
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    response_minutes = [
        r.model_minute for r in baseline.records if r.event_type == "spatial.response"
    ]
    invalid_minute = min(response_minutes) if target == "selected" else max(response_minutes)
    if target != "selected":
        assert invalid_minute > max(r.causal_identity.baseline.model_minute for r in requests)
    else:
        assert invalid_minute == 0  # False aliases this integer unless exact token type is checked.
    original = mobility.frame

    def frame(minute):
        positions = original(minute)
        if minute != invalid_minute:
            return positions
        token = {"off-by-one": minute + 1, "float": float(minute), "bool": bool(minute)}[
            minute_token
        ]
        return tuple(replace(p, minute=token, activity="leisure") for p in positions)

    monkeypatch.setattr(mobility, "frame", frame)
    with pytest.raises(ValueError, match="minute"):
        module.build_city_cognition_requests_v2(context, mobility, baseline)


def test_request_materialization_retains_validated_mobility_activity(monkeypatch) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    expected = module.build_city_cognition_requests_v2(context, mobility, baseline)
    original = mobility.frame
    calls = Counter()

    def frame(minute):
        calls[minute] += 1
        positions = original(minute)
        if calls[minute] > 1:
            return tuple(replace(p, minute=minute + 1, activity="leisure") for p in positions)
        return positions

    monkeypatch.setattr(mobility, "frame", frame)
    actual = module.build_city_cognition_requests_v2(context, mobility, baseline)
    assert actual == expected
    assert [canonical_json(request) for request in actual] == [
        canonical_json(request) for request in expected
    ]
    assert set(calls.values()) == {1}


@pytest.mark.parametrize("mode", ["local", "remote"])
@pytest.mark.parametrize("tokens", [64, 128, 256])
def test_effective_settings_cap_only_output_tokens(mode, tokens) -> None:
    from adlife.core.domain.city_cognition import city_cognition_provider_profile_revision_sha256

    context, _, _ = _dense_evidence(agent_count=2)
    facts = profile_data(mode, tokens)
    facts.update(
        temperature=0.6,
        top_p=0.8,
        seed_policy="fixed",
        seed=42,
        attempt_timeout_seconds=12,
        transient_retries=1,
        repair_attempts=0,
    )
    facts["revision_sha256"] = city_cognition_provider_profile_revision_sha256(facts)
    profile = CityCognitionProviderProfileSnapshotV1.model_validate(facts)
    document = _module().build_city_cognition_input_v2(
        context, (), mode=mode, funded_response_limit=20, provider_profile=profile
    )
    settings = document.effective_settings
    assert settings.max_output_tokens == min(tokens, 128)
    assert settings.temperature == 0.6
    assert settings.top_p == 0.8
    assert settings.seed_policy == "fixed" and settings.seed == 42
    assert settings.attempt_timeout_seconds == 12
    assert settings.transient_retries == 1 and settings.repair_attempts == 0
    assert document.provider_profile.max_output_tokens == tokens


@pytest.mark.parametrize("boundary", ["initial", "requests", "input"])
def test_public_context_boundaries_refuse_constructed_nested_evidence(boundary) -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    requests = module.build_city_cognition_requests_v2(context, mobility, baseline)
    invalid = _bypass(
        context,
        agents=(
            _bypass(
                context.agents[0], traits=_bypass(context.agents[0].traits, novelty_seeking=True)
            ),
            *context.agents[1:],
        ),
    )
    with pytest.raises(ValueError):
        if boundary == "initial":
            module.build_city_cognition_initial_state_document_v2(invalid)
        elif boundary == "requests":
            module.build_city_cognition_requests_v2(invalid, mobility, baseline)
        else:
            module.build_city_cognition_input_v2(
                invalid, requests, mode="rules", funded_response_limit=60
            )


def test_population_and_final_state_identities_must_cover_context() -> None:
    context, mobility, baseline = _dense_evidence(agent_count=2)
    module = _module()
    with pytest.raises(ValueError, match="population"):
        module.build_city_cognition_requests_v2(
            context, CityMobility(mobility.pack, seed=42, agent_count=3, days=1), baseline
        )
    smaller = context.model_copy(
        update={"agents": context.agents[:1], "initial_states": context.initial_states[:1]}
    )
    with pytest.raises(ValueError):
        module.build_city_cognition_requests_v2(
            smaller, CityMobility(mobility.pack, seed=42, agent_count=1, days=1), baseline
        )
