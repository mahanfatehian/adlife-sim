"""Deterministic causal requests and frozen funding for bounded city cognition."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from typing import Literal

from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CityCognitionBaselineProposalV2,
    CityCognitionCausalAgentV2,
    CityCognitionCausalCampaignV2,
    CityCognitionCausalContractV2,
    CityCognitionCausalRequestIdentityV2,
    CityCognitionEffectiveSettingsV2,
    CityCognitionInitialStateDocumentV2,
    CityCognitionInputV2,
    CityCognitionModeV2,
    CityCognitionRequestV2,
    CityCognitionReservationV2,
    CityCognitionRuleFactorsV2,
    CityCognitionRunContextV2,
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
        if (
            observed.brand_sentiment != declared.brand_sentiment
            or observed.recall_strength != declared.recall_strength
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
    if any(
        getattr(response, field) != getattr(traits, field)
        for field in (
            "price_sensitivity",
            "novelty_seeking",
            "advertising_skepticism",
            "impulsivity",
        )
    ) or (
        response.interest_match != interest
        or response.relative_price != campaign.relative_price
        or response.channel_recall_encoding != recall
    ):
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
        if (
            request.run_input_sha256 != context.run_input_sha256
            or identity.causal_contract_sha256 != contract.fingerprint
            or identity.initial_cognition_state_sha256 != initial_hash
            or any(
                getattr(identity, field) != getattr(context, field)
                for field in ("response_input_sha256", "scenario_sha256", "city_sha256")
            )
            or identity.agent != agents.get(baseline.agent_id)
            or identity.campaign != campaigns.get(baseline.campaign_id)
            or identity.initial_rule_state
            != initials.get((baseline.agent_id, baseline.campaign_id))
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
