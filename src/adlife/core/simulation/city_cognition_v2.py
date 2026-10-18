"""Deterministic causal requests and frozen funding for bounded city cognition."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from hashlib import sha256
from math import fsum, isfinite
from typing import Literal, get_args

from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.city_cognition_v2 import (
    _CITY_COGNITION_INFLUENCE_POLICY_V1_SHA256,
    _CITY_COGNITION_MOCK_POLICY_V1_SHA256,
    _CITY_COGNITION_MOCK_VECTOR_V1,
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1,
    CITY_COGNITION_DEFAULT_MOCK_POLICY_V1,
    CityCognitionAppliedSourceV2,
    CityCognitionAppliedVectorV2,
    CityCognitionBaselineProposalV2,
    CityCognitionCausalAgentV2,
    CityCognitionCausalCampaignV2,
    CityCognitionCausalContractV2,
    CityCognitionCausalRequestIdentityV2,
    CityCognitionEffectiveSettingsV2,
    CityCognitionEvaluationV2,
    CityCognitionFallbackReasonV2,
    CityCognitionInfluencePolicyV1,
    CityCognitionInitialRuleStateV2,
    CityCognitionInitialStateDocumentV2,
    CityCognitionInputV2,
    CityCognitionMockPolicyV1,
    CityCognitionModeV2,
    CityCognitionNeutralizationStatusV2,
    CityCognitionRequestV2,
    CityCognitionReservationV2,
    CityCognitionResultV2,
    CityCognitionRuleFactorsV2,
    CityCognitionRunContextV2,
    CityCognitionRuntimeFallbackReasonV2,
    CityCognitionSourceV2,
    CityCognitionSummaryV2,
    CityCognitionTerminalRecordV2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_response import (
    SpatialCampaignResponseInput,
    SpatialResponseInitialState,
    SpatialResponseInput,
    SpatialResponseProfile,
)
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.city_mobility import Activity, CityMobility, CityPosition
from adlife.core.simulation.spatial_response import (
    SpatialResponseEvaluation,
    SpatialResponseState,
    SpatialRuleResponse,
    SpatialStateUpdated,
)


def _validate_context_against_science(
    context: CityCognitionRunContextV2,
    mobility: CityMobility,
    evaluation: SpatialResponseEvaluation,
) -> None:
    if mobility.pack.fingerprint != context.city_sha256:
        raise ValueError("city cognition context does not match mobility city")
    population = tuple(agent.agent_id for agent in mobility.agents)
    if len(population) != len(set(population)) or set(population) != {
        agent.agent_id for agent in context.agents
    }:
        raise ValueError("city cognition context does not match mobility population")
    for field in ("response_input_sha256", "scenario_sha256", "city_sha256"):
        if getattr(context, field) != getattr(evaluation, field):
            raise ValueError("city cognition context does not match baseline science")
    initial: dict[tuple[str, str], SpatialResponseState] = {}
    for record in evaluation.records:
        if isinstance(record, SpatialStateUpdated):
            initial.setdefault((record.agent_id, record.campaign_id), record.previous_state)
    final = {(state.agent_id, state.campaign_id): state for state in evaluation.final_states}
    if set(final) != {(state.agent_id, state.campaign_id) for state in context.initial_states}:
        raise ValueError("city cognition context does not cover baseline state identities")
    for key, state in final.items():
        initial.setdefault(key, state)
    for declared in context.initial_states:
        observed = initial[declared.agent_id, declared.campaign_id]
        observed_initial = CityCognitionInitialRuleStateV2(
            schema_version=2,
            agent_id=observed.agent_id,
            campaign_id=observed.campaign_id,
            brand_sentiment=observed.brand_sentiment,
            recall_strength=observed.recall_strength,
        )
        if (
            observed_initial.fingerprint != declared.fingerprint
            or observed.response_count != 0
            or observed.last_response_minute is not None
        ):
            raise ValueError("declared initial state disagrees with baseline initial evidence")
    # Reconstruct the complete response document, including profiles that never
    # received a notice and channel traits unused by any selected response.
    reconstructed = SpatialResponseInput(
        city_sha256=context.city_sha256,
        scenario_sha256=context.scenario_sha256,
        profiles=tuple(
            SpatialResponseProfile(
                agent_id=a.agent_id,
                fictional=True,
                interests=frozenset(a.interests),
                traits=a.traits,
            )
            for a in context.agents
        ),
        campaigns=tuple(
            SpatialCampaignResponseInput(
                campaign_id=c.campaign_id,
                creative_sha256=c.creative_sha256,
                target_interests=frozenset(c.target_interests),
                relative_price=c.relative_price,
            )
            for c in context.campaigns
        ),
        initial_states=tuple(
            SpatialResponseInitialState(
                agent_id=s.agent_id,
                campaign_id=s.campaign_id,
                brand_sentiment=s.brand_sentiment,
                recall_strength=s.recall_strength,
                purchase_intention=initial[s.agent_id, s.campaign_id].purchase_intention,
            )
            for s in context.initial_states
        ),
    )
    if reconstructed.fingerprint != context.response_input_sha256:
        raise ValueError("causal snapshots disagree with response input fingerprint")


def _validate_response_assumptions(
    response: SpatialRuleResponse,
    agent: CityCognitionCausalAgentV2,
    campaign: CityCognitionCausalCampaignV2,
) -> None:
    traits = agent.traits
    union = set(agent.interests) | set(campaign.target_interests)
    interest = len(set(agent.interests) & set(campaign.target_interests)) / len(union)
    recall = (
        traits.mobile_recall_encoding
        if response.channel == "mobile-feed"
        else traits.roadside_recall_encoding
    )
    expected = {
        field: getattr(traits, field)
        for field in (
            "price_sensitivity",
            "novelty_seeking",
            "advertising_skepticism",
            "impulsivity",
        )
    }
    expected.update(
        interest_match=interest,
        relative_price=campaign.relative_price,
        channel_recall_encoding=recall,
    )
    observed = {field: getattr(response, field) for field in expected}
    if canonical_json(observed) != canonical_json(expected):
        raise ValueError("causal snapshots disagree with baseline rule assumptions")


def _validate_rule_assumptions(
    identity: CityCognitionCausalRequestIdentityV2,
) -> None:
    """Prove all baseline math and deterministic IDs independently of its hashes."""
    baseline = identity.baseline
    factors = baseline.rule_factors
    response_facts = {
        field: getattr(factors, field)
        for field in CityCognitionRuleFactorsV2.model_fields
        if field not in {"schema_version", "baseline_sentiment_delta", "baseline_recall_delta"}
    }
    response = SpatialRuleResponse(
        event_id=baseline.baseline_response_id,
        caused_by=baseline.notice_event_id,
        opportunity_id=baseline.opportunity_id,
        response_input_sha256=identity.response_input_sha256,
        scenario_sha256=identity.scenario_sha256,
        city_sha256=identity.city_sha256,
        campaign_id=baseline.campaign_id,
        placement_id=baseline.placement_id,
        agent_id=baseline.agent_id,
        channel=baseline.channel,
        day_index=baseline.day_index,
        model_minute=baseline.model_minute,
        millisecond_within_minute=baseline.millisecond_within_minute,
        state_before_sha256=baseline.pre_minute_rule_state_sha256,
        sentiment_delta=factors.baseline_sentiment_delta,
        recall_delta=factors.baseline_recall_delta,
        **response_facts,
    )
    _validate_response_assumptions(response, identity.agent, identity.campaign)


def build_city_cognition_initial_state_document_v2(
    context: CityCognitionRunContextV2,
) -> CityCognitionInitialStateDocumentV2:
    """Freeze the declared starting cognition sentiment and recall only."""
    context = revalidate_model(context, CityCognitionRunContextV2, label="city cognition context")
    return CityCognitionInitialStateDocumentV2(
        schema_version=2,
        model_id="spatial-cognition-initial-state-v2",
        states=context.initial_states,
    )


def build_city_cognition_requests_v2(
    context: CityCognitionRunContextV2,
    mobility: CityMobility,
    baseline_evaluation: SpatialResponseEvaluation,
    *,
    causal_contract: CityCognitionCausalContractV2 = CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
) -> tuple[CityCognitionRequestV2, ...]:
    """Bind deterministic proposals to causal snapshots before any provider work."""
    context = revalidate_model(context, CityCognitionRunContextV2, label="city cognition context")
    contract = revalidate_model(
        causal_contract, CityCognitionCausalContractV2, label="city cognition causal contract"
    )
    evaluation = revalidate_model(
        baseline_evaluation, SpatialResponseEvaluation, label="spatial response evaluation"
    )
    if not isinstance(mobility, CityMobility):
        raise TypeError("mobility must be CityMobility")
    _validate_context_against_science(context, mobility, evaluation)
    agents = {item.agent_id: item for item in context.agents}
    campaigns = {item.campaign_id: item for item in context.campaigns}
    initial_states = {(s.agent_id, s.campaign_id): s for s in context.initial_states}
    initial_hash = build_city_cognition_initial_state_document_v2(context).fingerprint
    updates_by_cause: dict[str, list[SpatialStateUpdated]] = defaultdict(list)
    for record in evaluation.records:
        if isinstance(record, SpatialStateUpdated):
            for cause in record.caused_by_event_ids:
                updates_by_cause[cause].append(record)
    responses = sorted(
        (record for record in evaluation.records if isinstance(record, SpatialRuleResponse)),
        key=lambda item: (item.model_minute, item.agent_id, item.campaign_id, item.event_id),
    )
    # Validate every admitted response, including those beyond either selection cap.
    # Retain <=60 validated facts and one live frame, then materialize requests only
    # after the entire baseline passes. Never re-fetch unbound activity evidence.
    frame_minute = -1
    positions: dict[str, CityPosition] = {}
    selected: list[tuple[SpatialRuleResponse, SpatialStateUpdated, Activity]] = []
    counts: Counter[str] = Counter()
    for response in responses:
        updates = updates_by_cause[response.event_id]
        if len(updates) != 1:
            raise ValueError("baseline response must bind to exactly one state update")
        update = updates[0]
        if (
            update.model_minute != response.model_minute
            or update.agent_id != response.agent_id
            or update.campaign_id != response.campaign_id
            or response.state_before_sha256
            != sha256(canonical_json(update.previous_state).encode("utf-8")).hexdigest()
        ):
            raise ValueError("baseline response has inconsistent state update bindings")
        if response.model_minute != frame_minute:
            frame = mobility.frame(response.model_minute)
            positions = {item.agent_id: item for item in frame}
            if len(positions) != len(frame):
                raise ValueError("baseline minute has duplicate mobility positions")
            frame_minute = response.model_minute
        position = positions.get(response.agent_id)
        if position is None:
            raise ValueError("baseline response has no mobility position")
        if type(position.minute) is not int or position.minute != response.model_minute:
            raise ValueError("baseline response mobility position minute is inconsistent")
        if position.activity not in {"home", "commute", "work", "leisure"}:
            raise ValueError("baseline response mobility activity is invalid")
        _validate_response_assumptions(
            response, agents[response.agent_id], campaigns[response.campaign_id]
        )
        if (
            counts[response.agent_id] < contract.selection_policy.selected_requests_per_agent
            and len(selected) < contract.selection_policy.selected_requests_per_run
        ):
            selected.append((response, update, position.activity))
            counts[response.agent_id] += 1
    requests: list[CityCognitionRequestV2] = []
    for response, update, activity in selected:
        factors = {
            field: getattr(response, field)
            for field in CityCognitionRuleFactorsV2.model_fields
            if field not in {"schema_version", "baseline_sentiment_delta", "baseline_recall_delta"}
        }
        baseline = CityCognitionBaselineProposalV2(
            schema_version=2,
            baseline_response_id=response.event_id,
            notice_event_id=response.caused_by,
            opportunity_id=response.opportunity_id,
            baseline_state_update_event_id=update.event_id,
            baseline_state_update_cause_count=len(update.caused_by_event_ids),
            agent_id=response.agent_id,
            campaign_id=response.campaign_id,
            placement_id=response.placement_id,
            channel=response.channel,
            activity=activity,
            day_index=response.day_index,
            model_minute=response.model_minute,
            millisecond_within_minute=response.millisecond_within_minute,
            pre_minute_rule_state_sha256=response.state_before_sha256,
            rule_factors=CityCognitionRuleFactorsV2(
                schema_version=2,
                baseline_sentiment_delta=response.sentiment_delta,
                baseline_recall_delta=response.recall_delta,
                **factors,
            ),
        )
        identity = CityCognitionCausalRequestIdentityV2(
            schema_version=2,
            causal_contract_sha256=contract.fingerprint,
            response_input_sha256=context.response_input_sha256,
            scenario_sha256=context.scenario_sha256,
            city_sha256=context.city_sha256,
            initial_cognition_state_sha256=initial_hash,
            baseline=baseline,
            baseline_sha256=baseline.fingerprint,
            agent=agents[response.agent_id],
            campaign=campaigns[response.campaign_id],
            initial_rule_state=initial_states[response.agent_id, response.campaign_id],
            disclosure=contract.disclosure,
        )
        facts = dict(
            schema_version=2,
            request_id=f"cog-{identity.fingerprint}",
            run_input_sha256=context.run_input_sha256,
            causal_identity=identity.model_dump(mode="python"),
        )
        requests.append(
            CityCognitionRequestV2.model_validate(
                facts
                | {
                    "request_record_sha256": sha256(
                        canonical_json(facts).encode("utf-8")
                    ).hexdigest()
                }
            )
        )
    return tuple(requests)


def _request_key(request: CityCognitionRequestV2) -> tuple[int, str, str, str]:
    baseline = request.causal_identity.baseline
    return (
        baseline.model_minute,
        baseline.agent_id,
        baseline.campaign_id,
        baseline.baseline_response_id,
    )


def _checked_requests(
    requests: tuple[CityCognitionRequestV2, ...],
) -> tuple[CityCognitionRequestV2, ...]:
    if type(requests) is not tuple:
        raise TypeError("city cognition requests must be a tuple")
    checked = tuple(
        revalidate_model(item, CityCognitionRequestV2, label="city cognition request")
        for item in requests
    )
    if len(checked) > 60:
        raise ValueError("too many city cognition requests")
    if checked != tuple(sorted(checked, key=_request_key)):
        raise ValueError("city cognition requests must be in canonical selection order")
    for field in ("request_id", "request_record_sha256"):
        if len({getattr(item, field) for item in checked}) != len(checked):
            raise ValueError("city cognition requests must have unique IDs and record hashes")
    identities = [item.causal_identity for item in checked]
    if len({item.baseline.baseline_response_id for item in identities}) != len(checked):
        raise ValueError("city cognition requests must reference unique baselines")
    bindings = {
        (
            request.run_input_sha256,
            request.causal_identity.response_input_sha256,
            request.causal_identity.scenario_sha256,
            request.causal_identity.city_sha256,
            request.causal_identity.initial_cognition_state_sha256,
            request.causal_identity.causal_contract_sha256,
        )
        for request in checked
    }
    if len(bindings) > 1:
        raise ValueError("city cognition requests contain mixed scientific or run provenance")
    counts = Counter(item.baseline.agent_id for item in identities)
    if any(count > 4 for count in counts.values()):
        raise ValueError("city cognition requests exceed the per-agent selection limit")
    snapshots: dict[tuple[str, ...], str] = {}
    groups: dict[tuple[int, str, str], tuple[str, int, str]] = {}
    group_counts: Counter[tuple[int, str, str]] = Counter()
    update_groups: dict[str, tuple[int, str, str]] = {}
    activities: dict[tuple[int, str], str] = {}
    for identity in identities:
        _validate_rule_assumptions(identity)
        baseline = identity.baseline
        for key, snapshot in (
            (("agent", baseline.agent_id), identity.agent),
            (("campaign", baseline.campaign_id), identity.campaign),
            (("initial", baseline.agent_id, baseline.campaign_id), identity.initial_rule_state),
        ):
            if snapshots.setdefault(key, snapshot.fingerprint) != snapshot.fingerprint:
                raise ValueError("city cognition requests contain inconsistent causal snapshots")
        group = (baseline.model_minute, baseline.agent_id, baseline.campaign_id)
        binding = (
            baseline.baseline_state_update_event_id,
            baseline.baseline_state_update_cause_count,
            baseline.pre_minute_rule_state_sha256,
        )
        if groups.setdefault(group, binding) != binding:
            raise ValueError("same-minute requests disagree on state update bindings")
        if update_groups.setdefault(baseline.baseline_state_update_event_id, group) != group:
            raise ValueError("state update identifier is reused across minute groups")
        activity_key = (baseline.model_minute, baseline.agent_id)
        if activities.setdefault(activity_key, baseline.activity) != baseline.activity:
            raise ValueError("same-minute requests disagree on mobility activity")
        group_counts[group] += 1
        if group_counts[group] > baseline.baseline_state_update_cause_count:
            raise ValueError("selected requests exceed the state update cause count")
    return checked


def _validate_mode_limit(mode: CityCognitionModeV2, limit: int) -> None:
    if mode not in {"rules", "mock", "local", "remote"}:
        raise ValueError("unsupported city cognition mode")
    if type(limit) is not int or limit not in {10, 20, 40, 60}:
        raise ValueError("funded response limit must be an exact integer 10, 20, 40, or 60")
    if mode in {"rules", "mock"} and limit != 60:
        raise ValueError("rules and mock require funded response limit 60")


def allocate_city_cognition_reservations_v2(
    requests: tuple[CityCognitionRequestV2, ...],
    *,
    mode: CityCognitionModeV2,
    funded_response_limit: Literal[10, 20, 40, 60],
) -> tuple[CityCognitionReservationV2, ...]:
    """Freeze whole-request funding in canonical order before cache or provider work."""
    _validate_mode_limit(mode, funded_response_limit)
    checked = _checked_requests(requests)
    offline = mode in {"rules", "mock"}
    return tuple(
        CityCognitionReservationV2(
            schema_version=2,
            selection_index=index,
            request_id=request.request_id,
            request_record_sha256=request.request_record_sha256,
            funded=offline or index < funded_response_limit,
            reserved_output_tokens=512 if not offline and index < funded_response_limit else 0,
        )
        for index, request in enumerate(checked)
    )


def build_city_cognition_input_v2(
    context: CityCognitionRunContextV2,
    requests: tuple[CityCognitionRequestV2, ...],
    *,
    mode: CityCognitionModeV2,
    funded_response_limit: Literal[10, 20, 40, 60],
    provider_profile: CityCognitionProviderProfileSnapshotV1 | None = None,
    causal_contract: CityCognitionCausalContractV2 = CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
) -> CityCognitionInputV2:
    """Bind validated requests, causal policy, and optional provider configuration."""
    context = revalidate_model(context, CityCognitionRunContextV2, label="city cognition context")
    contract = revalidate_model(
        causal_contract, CityCognitionCausalContractV2, label="city cognition causal contract"
    )
    _validate_mode_limit(mode, funded_response_limit)
    checked = _checked_requests(requests)
    initial_hash = build_city_cognition_initial_state_document_v2(context).fingerprint
    agents = {a.agent_id: a for a in context.agents}
    campaigns = {c.campaign_id: c for c in context.campaigns}
    initials = {(s.agent_id, s.campaign_id): s for s in context.initial_states}
    for request in checked:
        identity = request.causal_identity
        baseline = identity.baseline
        agent = agents.get(baseline.agent_id)
        campaign = campaigns.get(baseline.campaign_id)
        initial = initials.get((baseline.agent_id, baseline.campaign_id))
        if (
            request.run_input_sha256 != context.run_input_sha256
            or identity.causal_contract_sha256 != contract.fingerprint
            or identity.initial_cognition_state_sha256 != initial_hash
            or any(
                getattr(identity, field) != getattr(context, field)
                for field in ("response_input_sha256", "scenario_sha256", "city_sha256")
            )
            or agent is None
            or campaign is None
            or initial is None
            or identity.agent.fingerprint != agent.fingerprint
            or identity.campaign.fingerprint != campaign.fingerprint
            or identity.initial_rule_state.fingerprint != initial.fingerprint
        ):
            raise ValueError("city cognition request disagrees with its frozen run context")
    profile = None
    settings = None
    if provider_profile is not None:
        profile = revalidate_model(
            provider_profile,
            CityCognitionProviderProfileSnapshotV1,
            label="city cognition provider profile",
        )
        fields = {
            field: getattr(profile, field)
            for field in CityCognitionEffectiveSettingsV2.model_fields
            if field != "schema_version"
        }
        fields["schema_version"] = 2
        fields["max_output_tokens"] = min(profile.max_output_tokens, 128)
        settings = CityCognitionEffectiveSettingsV2.model_validate(fields)
    return CityCognitionInputV2(
        schema_version=2,
        effect_scope="bounded-signals-v1",
        mode=mode,
        run_input_sha256=context.run_input_sha256,
        causal_contract=contract,
        causal_contract_sha256=contract.fingerprint,
        provider_profile=profile,
        effective_settings=settings,
        funded_response_limit=funded_response_limit,
        selected_requests_per_agent=4,
        selected_requests_per_run=60,
        concurrency_limit=4,
        max_request_bytes=8192,
        max_terminal_record_bytes=8192,
        max_wire_response_bytes=1_048_576,
        max_output_tokens_per_attempt=128,
        reservation_tokens_per_request=512,
        initial_attempts=1,
        transient_retries_ceiling=2,
        repair_attempts_ceiling=1,
        attempt_timeout_seconds_ceiling=30,
        aggregate_deadline_seconds=180,
        aggregate_output_token_ceiling=0
        if mode in {"rules", "mock"}
        else funded_response_limit * 512,
        reservations=allocate_city_cognition_reservations_v2(
            checked, mode=mode, funded_response_limit=funded_response_limit
        ),
    )


def build_city_rules_result_v2(request: CityCognitionRequestV2) -> CityCognitionResultV2:
    """Return the exact frozen neutral signal for a validated causal request."""
    request = revalidate_model(request, CityCognitionRequestV2, label="city cognition request")
    return CityCognitionResultV2(
        schema_version=2,
        request_id=request.request_id,
        affect="neutral",
        relevance="medium",
        credibility="medium",
        salience="medium",
        safety_flags=(),
        disclosure=request.causal_identity.disclosure,
    )


def build_city_mock_result_v2(
    request: CityCognitionRequestV2,
    *,
    policy: CityCognitionMockPolicyV1 = CITY_COGNITION_DEFAULT_MOCK_POLICY_V1,
) -> CityCognitionResultV2:
    """Map only the causal request ID through the frozen four digest bytes."""
    request = revalidate_model(request, CityCognitionRequestV2, label="city cognition request")
    policy = revalidate_model(policy, CityCognitionMockPolicyV1, label="city cognition mock policy")
    if policy.fingerprint != _CITY_COGNITION_MOCK_POLICY_V1_SHA256:
        raise ValueError("mock policy must match its historical canonical document")
    digest = sha256((policy.digest_prefix + request.request_id).encode("utf-8")).digest()
    return CityCognitionResultV2(
        schema_version=2,
        request_id=request.request_id,
        affect=policy.affect_order[digest[0] % len(policy.affect_order)],
        relevance=policy.relevance_order[digest[1] % len(policy.relevance_order)],
        credibility=policy.credibility_order[digest[2] % len(policy.credibility_order)],
        salience=policy.salience_order[digest[3] % len(policy.salience_order)],
        safety_flags=(),
        disclosure=request.causal_identity.disclosure,
    )


def build_city_fallback_result_v2(
    request: CityCognitionRequestV2, *, reason: CityCognitionRuntimeFallbackReasonV2
) -> CityCognitionResultV2:
    if reason not in get_args(CityCognitionRuntimeFallbackReasonV2):
        raise ValueError("fallback requires an exact runtime reason; budget-skipped is separate")
    return build_city_rules_result_v2(request)


def build_city_budget_skipped_result_v2(
    request: CityCognitionRequestV2,
) -> CityCognitionResultV2:
    return build_city_rules_result_v2(request)


def apply_city_cognition_influence_v1(
    baseline_sentiment_delta: float,
    baseline_recall_delta: float,
    *,
    source: CityCognitionAppliedSourceV2,
    result: CityCognitionResultV2 | None,
    policy: CityCognitionInfluencePolicyV1 = CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1,
) -> CityCognitionAppliedVectorV2:
    """Apply the frozen arithmetic to one complete, validated signal vector."""
    policy = revalidate_model(
        policy, CityCognitionInfluencePolicyV1, label="city cognition influence policy"
    )
    if policy.fingerprint != _CITY_COGNITION_INFLUENCE_POLICY_V1_SHA256:
        raise ValueError("influence policy must match its historical canonical document")
    for value, low, high in (
        (baseline_sentiment_delta, -0.2, 0.2),
        (baseline_recall_delta, 0.0, 0.3),
    ):
        if type(value) not in (int, float) or not low <= value <= high or not isfinite(value):
            raise ValueError("baseline deltas must be finite bounded numbers, never booleans")
    status: CityCognitionNeutralizationStatusV2 = "none"
    if source == "unselected":
        if result is not None:
            raise ValueError("unselected responses cannot carry a result")
        status = "unselected"
    else:
        if source not in get_args(CityCognitionSourceV2):
            raise ValueError("unsupported applied source")
        if result is None:
            raise ValueError("selected responses require a result")
        result = revalidate_model(result, CityCognitionResultV2, label="city cognition result")
        categories = (result.affect, result.relevance, result.credibility, result.salience)
        if source in {"rules", "fallback", "budget-skipped"}:
            if categories != ("neutral", "medium", "medium", "medium") or result.safety_flags:
                raise ValueError("identity outcomes require the exact neutral result")
            status = source
        elif source == "mock":
            prefix, orders = _CITY_COGNITION_MOCK_VECTOR_V1
            digest = sha256((prefix + result.request_id).encode("utf-8")).digest()
            if (
                categories != tuple(order[digest[i] % len(order)] for i, order in enumerate(orders))
                or result.safety_flags
            ):
                raise ValueError("mock source requires its deterministic result")
        if result.safety_flags:
            status = "safety"
        elif "uncertain" in categories:
            status = "uncertain"
    sentiment, recall = baseline_sentiment_delta, baseline_recall_delta
    # Exact neutral signals are identities, including the baseline's zero signs.
    if (
        status == "none"
        and result is not None
        and (result.affect, result.relevance, result.credibility, result.salience)
        != ("neutral", "medium", "medium", "medium")
    ):
        affect = {row.category: row.weight for row in policy.affect_adjustments}[result.affect]
        relevance = {row.category: row.weight for row in policy.relevance_factors}[result.relevance]
        credibility = {row.category: row.weight for row in policy.credibility_factors}[
            result.credibility
        ]
        salience = {row.category: row.weight for row in policy.salience_factors}[result.salience]
        sentiment = max(
            policy.sentiment_minimum,
            min(policy.sentiment_maximum, fsum((sentiment, affect)) * relevance * credibility),
        )
        recall = max(
            policy.recall_minimum,
            min(policy.recall_maximum, recall * relevance * credibility * salience),
        )
    return CityCognitionAppliedVectorV2(
        schema_version=2,
        source=source,
        neutralization_status=status,
        applied_sentiment_delta=sentiment,
        applied_recall_delta=recall,
    )


def build_city_cognition_terminal_record_v2(
    request: CityCognitionRequestV2,
    cognition_input: CityCognitionInputV2,
    *,
    selection_index: int,
    source: CityCognitionSourceV2,
    result: CityCognitionResultV2,
    attempt_count: int = 0,
    reason: CityCognitionFallbackReasonV2 | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> CityCognitionTerminalRecordV2:
    """Bind one terminal outcome solely to its request and frozen run authority."""
    request = revalidate_model(request, CityCognitionRequestV2, label="city cognition request")
    frozen = revalidate_model(cognition_input, CityCognitionInputV2, label="city cognition input")
    result = revalidate_model(result, CityCognitionResultV2, label="city cognition result")
    identity = request.causal_identity
    if (
        request.run_input_sha256 != frozen.run_input_sha256
        or identity.causal_contract_sha256 != frozen.causal_contract_sha256
    ):
        raise ValueError("terminal request does not match frozen run and causal contract")
    if type(selection_index) is not int or not 0 <= selection_index < len(frozen.reservations):
        raise ValueError("terminal selection index has no reservation")
    reservation = frozen.reservations[selection_index]
    if (reservation.request_id, reservation.request_record_sha256) != (
        request.request_id,
        request.request_record_sha256,
    ):
        raise ValueError("terminal request does not match its reservation")
    allowed = (
        {frozen.mode}
        if frozen.mode in {"rules", "mock"}
        else ({"provider", "cache", "fallback"} if reservation.funded else {"budget-skipped"})
    )
    if source not in allowed:
        raise ValueError("terminal source does not match the frozen reservation")
    settings, profile = frozen.effective_settings, frozen.provider_profile
    if type(attempt_count) is not int:
        raise ValueError("attempt count must be an exact integer")
    if (
        settings is not None
        and source in {"provider", "fallback"}
        and attempt_count > 1 + settings.transient_retries + settings.repair_attempts
    ):
        raise ValueError("attempt count exceeds frozen effective settings")
    baseline = identity.baseline
    contract = frozen.causal_contract
    facts = {
        field: getattr(baseline, field)
        for field in CityCognitionBaselineProposalV2.model_fields
        if field not in {"schema_version", "pre_minute_rule_state_sha256", "rule_factors"}
    }
    facts.update(
        {
            field: getattr(identity, field)
            for field in (
                "response_input_sha256",
                "scenario_sha256",
                "city_sha256",
                "baseline_sha256",
                "causal_contract_sha256",
            )
        }
    )
    facts.update(
        {
            field: getattr(contract, field)
            for field in (
                "prompt_contract_sha256",
                "result_schema_contract_sha256",
                "selection_policy_sha256",
                "fallback_policy_sha256",
                "mock_policy_sha256",
                "influence_policy_sha256",
            )
        }
    )
    facts.update(
        schema_version=2,
        selection_index=selection_index,
        request_id=request.request_id,
        request_record_sha256=request.request_record_sha256,
        run_input_sha256=request.run_input_sha256,
        source=source,
        result=result.model_dump(mode="python"),
        attempt_count=attempt_count,
        reason=reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        disclosure=contract.disclosure,
        provider_profile_id=None if profile is None else profile.profile_id,
        provider_revision_sha256=None if profile is None else profile.revision_sha256,
        provider_profile_snapshot_sha256=None if profile is None else profile.fingerprint,
        provider_protocol=None if profile is None else profile.protocol,
        provider_effective_settings_sha256=None if settings is None else settings.fingerprint,
    )
    facts["record_hash"] = sha256(canonical_json(facts).encode("utf-8")).hexdigest()
    return CityCognitionTerminalRecordV2.model_validate(facts)


def _bind_requests_to_baseline(
    requests: tuple[CityCognitionRequestV2, ...],
    evaluation: SpatialResponseEvaluation,
) -> int:
    responses = sorted(
        (r for r in evaluation.records if isinstance(r, SpatialRuleResponse)),
        key=lambda r: (r.model_minute, r.agent_id, r.campaign_id, r.event_id),
    )
    updates: dict[str, SpatialStateUpdated] = {}
    initial: dict[tuple[str, str], SpatialResponseState] = {}
    for record in evaluation.records:
        if isinstance(record, SpatialStateUpdated):
            initial.setdefault((record.agent_id, record.campaign_id), record.previous_state)
            for cause in record.caused_by_event_ids:
                updates[cause] = record
    for state in evaluation.final_states:
        initial.setdefault((state.agent_id, state.campaign_id), state)
    document = CityCognitionInitialStateDocumentV2(
        schema_version=2,
        model_id="spatial-cognition-initial-state-v2",
        states=tuple(
            CityCognitionInitialRuleStateV2(
                schema_version=2,
                agent_id=s.agent_id,
                campaign_id=s.campaign_id,
                brand_sentiment=s.brand_sentiment,
                recall_strength=s.recall_strength,
            )
            for s in initial.values()
        ),
    )
    if any(s.response_count != 0 or s.last_response_minute is not None for s in initial.values()):
        raise ValueError("baseline initial state must have no response history")
    counts: Counter[str] = Counter()
    selected: list[SpatialRuleResponse] = []
    for response in responses:
        if counts[response.agent_id] < 4 and len(selected) < 60:
            selected.append(response)
            counts[response.agent_id] += 1
    if tuple(r.causal_identity.baseline.baseline_response_id for r in requests) != tuple(
        r.event_id for r in selected
    ):
        raise ValueError("requests do not match exact canonical baseline selection")
    initial_by_key = {(s.agent_id, s.campaign_id): s for s in document.states}
    for request, response in zip(requests, selected, strict=True):
        identity = request.causal_identity
        baseline = identity.baseline
        update = updates[response.event_id]
        if any(
            getattr(identity, f) != getattr(evaluation, f)
            for f in ("response_input_sha256", "scenario_sha256", "city_sha256")
        ):
            raise ValueError("request science does not match baseline evaluation")
        if (
            identity.initial_cognition_state_sha256 != document.fingerprint
            or identity.initial_rule_state.fingerprint
            != initial_by_key[response.agent_id, response.campaign_id].fingerprint
        ):
            raise ValueError("request initial state does not match baseline evidence")
        _validate_response_assumptions(response, identity.agent, identity.campaign)
        # Activity was validated against mobility during Task 3. Here the supplied
        # request retains that fact; every fact derivable from baseline is rebound.
        expected = baseline.model_dump(mode="python")
        for field in (
            "agent_id",
            "campaign_id",
            "placement_id",
            "channel",
            "day_index",
            "model_minute",
            "millisecond_within_minute",
        ):
            expected[field] = getattr(response, field)
        expected.update(
            baseline_response_id=response.event_id,
            notice_event_id=response.caused_by,
            opportunity_id=response.opportunity_id,
            baseline_state_update_event_id=update.event_id,
            baseline_state_update_cause_count=len(update.caused_by_event_ids),
            pre_minute_rule_state_sha256=response.state_before_sha256,
            rule_factors={
                field: 2
                if field == "schema_version"
                else response.sentiment_delta
                if field == "baseline_sentiment_delta"
                else response.recall_delta
                if field == "baseline_recall_delta"
                else getattr(response, field)
                for field in CityCognitionRuleFactorsV2.model_fields
            },
        )
        if canonical_json(baseline) != canonical_json(
            CityCognitionBaselineProposalV2.model_validate(expected)
        ):
            raise ValueError("request proposal does not match complete baseline and group facts")
    return len(responses)


def validate_city_cognition_evaluation_v2(
    cognition_input: CityCognitionInputV2,
    requests: tuple[CityCognitionRequestV2, ...],
    records: tuple[CityCognitionTerminalRecordV2, ...],
    *,
    baseline_evaluation: SpatialResponseEvaluation,
) -> CityCognitionEvaluationV2:
    """Validate complete bound arrivals before normalizing their persisted order."""
    if type(records) is not tuple:
        raise TypeError("city cognition records must be a tuple")
    if len(records) > 60:
        raise ValueError("too many city cognition terminal arrivals")
    frozen = revalidate_model(cognition_input, CityCognitionInputV2, label="city cognition input")
    checked = _checked_requests(requests)
    baseline = revalidate_model(
        baseline_evaluation, SpatialResponseEvaluation, label="baseline evaluation"
    )
    eligible = _bind_requests_to_baseline(checked, baseline)
    terminals = tuple(
        revalidate_model(r, CityCognitionTerminalRecordV2, label="city cognition terminal")
        for r in records
    )
    indices = {record.selection_index for record in terminals}
    if len(indices) != len(terminals) or len({record.request_id for record in terminals}) != len(
        terminals
    ):
        raise ValueError("terminal arrivals require unique selection indices and request IDs")
    if indices != set(range(len(checked))):
        raise ValueError("terminal arrival indices must cover the complete selected requests")
    reservations = allocate_city_cognition_reservations_v2(
        checked, mode=frozen.mode, funded_response_limit=frozen.funded_response_limit
    )
    if frozen.reservations != reservations or len(terminals) != len(checked):
        raise ValueError("terminal/request/reservation evidence is incomplete")
    for record in terminals:
        index = record.selection_index
        request = checked[index]
        expected = build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=index,
            source=record.source,
            result=record.result,
            attempt_count=record.attempt_count,
            reason=record.reason,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
        )
        if canonical_json(record) != canonical_json(expected):
            raise ValueError("terminal does not match complete request and frozen input bindings")
    terminals = tuple(
        sorted(terminals, key=lambda record: (record.selection_index, record.request_id))
    )
    stream = b"".join((canonical_json(r) + "\n").encode("utf-8") for r in terminals)
    sources = Counter(r.source for r in terminals)
    reasons = Counter(r.reason for r in terminals if r.reason is not None)

    def uncertain(record: CityCognitionTerminalRecordV2) -> bool:
        result = record.result
        return "uncertain" in (result.affect, result.relevance, result.credibility, result.salience)

    funded = min(len(checked), frozen.funded_response_limit)
    contract = frozen.causal_contract
    summary = CityCognitionSummaryV2.model_validate(
        dict(
            schema_version=2,
            effect_scope=frozen.effect_scope,
            mode=frozen.mode,
            causal_contract_sha256=frozen.causal_contract_sha256,
            mock_policy_sha256=contract.mock_policy_sha256,
            influence_policy_sha256=contract.influence_policy_sha256,
            coverage=dict(
                schema_version=2,
                eligible_response_count=eligible,
                selected_request_count=len(checked),
                funded_request_count=funded,
                budget_skipped_count=len(checked) - funded,
                unselected_response_count=eligible - len(checked),
                applied_response_count=eligible,
            ),
            outcomes={"schema_version": 2}
            | {s.replace("-", "_"): sources[s] for s in get_args(CityCognitionSourceV2)},
            reasons={"schema_version": 2}
            | {r.replace("-", "_"): reasons[r] for r in get_args(CityCognitionFallbackReasonV2)},
            neutralization=dict(
                schema_version=2,
                uncertain_result_count=sum(uncertain(r) for r in terminals),
                safety_neutralized_result_count=sum(bool(r.result.safety_flags) for r in terminals),
                neutralized_response_count=eligible
                - len(checked)
                + sum(
                    r.source in {"rules", "fallback", "budget-skipped"}
                    or uncertain(r)
                    or bool(r.result.safety_flags)
                    for r in terminals
                ),
            ),
            attempt_count=sum(r.attempt_count for r in terminals),
            prompt_tokens=sum(r.prompt_tokens or 0 for r in terminals),
            completion_tokens=sum(r.completion_tokens or 0 for r in terminals),
            terminal_stream_sha256=sha256(stream).hexdigest(),
            terminal_stream_bytes=len(stream),
            terminal_record_count=len(terminals),
            disclosure=contract.disclosure,
        )
    )
    return CityCognitionEvaluationV2(
        schema_version=2,
        cognition_input_sha256=frozen.fingerprint,
        run_input_sha256=frozen.run_input_sha256,
        effect_scope=frozen.effect_scope,
        mode=frozen.mode,
        records=terminals,
        summary=summary,
    )


def city_cognition_terminal_lines_v2(evaluation: CityCognitionEvaluationV2) -> Iterator[bytes]:
    evaluation = revalidate_model(
        evaluation, CityCognitionEvaluationV2, label="city cognition evaluation"
    )
    for record in evaluation.records:
        yield (canonical_json(record) + "\n").encode("utf-8")
