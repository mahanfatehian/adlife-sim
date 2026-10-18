"""Real spatial evidence exercises the V2 request and funding boundaries."""

from __future__ import annotations

import importlib
from collections import Counter
from dataclasses import replace
from hashlib import sha256
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
    *, agent_count=16, minutes=20, placements=None, roadside=False, attention_seed=10
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
    inputs = _response_input(scenario, agent_ids=ids)
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
