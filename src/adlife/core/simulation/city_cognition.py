"""Deterministic selection and validation of noncausal city explanations."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from typing import Literal

from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
    CITY_COGNITION_DISCLOSURE,
    CITY_COGNITION_FALLBACK_POLICY_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_SHA256,
    CITY_COGNITION_RESERVATION_TOKENS,
    CITY_COGNITION_RESULT_SCHEMA_SHA256,
    CITY_COGNITION_SELECTION_POLICY_SHA256,
    MAX_CITY_COGNITION_AGGREGATE_OUTPUT_TOKENS,
    MAX_CITY_COGNITION_REQUESTS,
    MAX_CITY_COGNITION_REQUESTS_PER_AGENT,
    MAX_CITY_COGNITION_STREAM_BYTES,
    CityCognitionCommittedStateV1,
    CityCognitionEffectiveSettingsV1,
    CityCognitionEvaluationV1,
    CityCognitionFallbackPolicyV1,
    CityCognitionFallbackReason,
    CityCognitionInputV1,
    CityCognitionOutcomeCountsV1,
    CityCognitionProviderProfileSnapshotV1,
    CityCognitionReasonCountsV1,
    CityCognitionRequestV1,
    CityCognitionReservationV1,
    CityCognitionResultV1,
    CityCognitionRuleFactorsV1,
    CityCognitionRunContextV1,
    CityCognitionSource,
    CityCognitionSummaryV1,
    CityCognitionTerminalRecordV1,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_response import (
    SpatialResponseEvaluation,
    SpatialRuleResponse,
    SpatialStateUpdated,
)


def _request_sort_key(response: SpatialRuleResponse) -> tuple[object, ...]:
    return (
        response.model_minute,
        response.agent_id,
        response.campaign_id,
        response.event_id,
    )


def _city_cognition_request_sort_key(
    request: CityCognitionRequestV1,
) -> tuple[object, ...]:
    return (
        request.model_minute,
        request.agent_id,
        request.campaign_id,
        request.response_event_id,
    )


def _derive_request(
    *,
    context: CityCognitionRunContextV1,
    response: SpatialRuleResponse,
    update: SpatialStateUpdated,
    activity: Literal["home", "commute", "work", "leisure"],
) -> CityCognitionRequestV1:
    agent_by_id = {item.agent_id: item for item in context.agents}
    campaign_by_id = {item.campaign_id: item for item in context.campaigns}
    persona = agent_by_id[response.agent_id]
    campaign = campaign_by_id[response.campaign_id]
    rule_factors = CityCognitionRuleFactorsV1(
        prior_notices_today=response.prior_notices_today,
        frequency_cap_per_agent_per_day=response.frequency_cap_per_agent_per_day,
        interest_match=response.interest_match,
        relative_price=response.relative_price,
        price_sensitivity=response.price_sensitivity,
        affordability=response.affordability,
        novelty_seeking=response.novelty_seeking,
        advertising_skepticism=response.advertising_skepticism,
        channel_recall_encoding=response.channel_recall_encoding,
        impulsivity=response.impulsivity,
        frequency_fatigue=response.frequency_fatigue,
        value_match=response.value_match,
        sentiment_delta=response.sentiment_delta,
        recall_delta=response.recall_delta,
    )
    placeholder = CityCognitionRequestV1.model_construct(
        request_id=f"cog-{'0' * 64}",
        run_input_sha256=context.run_input_sha256,
        response_input_sha256=response.response_input_sha256,
        scenario_sha256=response.scenario_sha256,
        city_sha256=response.city_sha256,
        response_event_id=response.event_id,
        notice_event_id=response.caused_by,
        opportunity_id=response.opportunity_id,
        state_update_event_id=update.event_id,
        agent_id=response.agent_id,
        campaign_id=response.campaign_id,
        placement_id=response.placement_id,
        model_minute=response.model_minute,
        millisecond_within_minute=response.millisecond_within_minute,
        activity=activity,
        channel=response.channel,
        campaign=campaign,
        persona=persona,
        rule_factors=rule_factors,
        state_before_sha256=response.state_before_sha256,
        state_update_cause_count=len(update.caused_by_event_ids),
        committed_state=CityCognitionCommittedStateV1(
            agent_id=update.state.agent_id,
            campaign_id=update.state.campaign_id,
            brand_sentiment=update.state.brand_sentiment,
            recall_strength=update.state.recall_strength,
            purchase_intention=update.state.purchase_intention,
            response_count=update.state.response_count,
            last_response_minute=update.state.last_response_minute,
        ),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    request_id = f"cog-{placeholder.request_facts_sha256}"
    return CityCognitionRequestV1(
        request_id=request_id,
        run_input_sha256=context.run_input_sha256,
        response_input_sha256=response.response_input_sha256,
        scenario_sha256=response.scenario_sha256,
        city_sha256=response.city_sha256,
        response_event_id=response.event_id,
        notice_event_id=response.caused_by,
        opportunity_id=response.opportunity_id,
        state_update_event_id=update.event_id,
        agent_id=response.agent_id,
        campaign_id=response.campaign_id,
        placement_id=response.placement_id,
        model_minute=response.model_minute,
        millisecond_within_minute=response.millisecond_within_minute,
        activity=activity,
        channel=response.channel,
        campaign=campaign,
        persona=persona,
        rule_factors=rule_factors,
        state_before_sha256=response.state_before_sha256,
        state_update_cause_count=len(update.caused_by_event_ids),
        committed_state=CityCognitionCommittedStateV1(
            agent_id=update.state.agent_id,
            campaign_id=update.state.campaign_id,
            brand_sentiment=update.state.brand_sentiment,
            recall_strength=update.state.recall_strength,
            purchase_intention=update.state.purchase_intention,
            response_count=update.state.response_count,
            last_response_minute=update.state.last_response_minute,
        ),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )


def _validate_context_against_science(
    context: CityCognitionRunContextV1,
    mobility: CityMobility,
    response_evaluation: SpatialResponseEvaluation,
) -> None:
    if mobility.pack.fingerprint != context.city_sha256:
        raise ValueError("city cognition context does not match mobility city")
    if tuple(agent.agent_id for agent in mobility.agents) != tuple(
        item.agent_id for item in context.agents
    ):
        raise ValueError("city cognition context does not match mobility population")
    if (
        response_evaluation.response_input_sha256 != context.response_input_sha256
        or response_evaluation.scenario_sha256 != context.scenario_sha256
        or response_evaluation.city_sha256 != context.city_sha256
    ):
        raise ValueError("city cognition context does not match response evidence")

    expected_state_keys = {
        (agent.agent_id, campaign.campaign_id)
        for agent in context.agents
        for campaign in context.campaigns
    }
    actual_state_keys = {
        (state.agent_id, state.campaign_id) for state in response_evaluation.final_states
    }
    if actual_state_keys != expected_state_keys:
        raise ValueError("city cognition context does not cover response state identities")


def _validate_response_context(
    response: SpatialRuleResponse,
    context: CityCognitionRunContextV1,
) -> None:
    agent = next(item for item in context.agents if item.agent_id == response.agent_id)
    campaign = next(item for item in context.campaigns if item.campaign_id == response.campaign_id)
    traits = agent.traits
    if (
        traits.price_sensitivity != response.price_sensitivity
        or traits.novelty_seeking != response.novelty_seeking
        or traits.advertising_skepticism != response.advertising_skepticism
        or traits.impulsivity != response.impulsivity
        or campaign.relative_price != response.relative_price
    ):
        raise ValueError("city cognition context disagrees with deterministic rule factors")
    expected_recall = (
        traits.roadside_recall_encoding
        if response.channel == "roadside-billboard"
        else traits.mobile_recall_encoding
    )
    union = set(agent.interests) | set(campaign.target_interests)
    expected_interest = len(set(agent.interests) & set(campaign.target_interests)) / len(union)
    if (
        expected_recall != response.channel_recall_encoding
        or expected_interest != response.interest_match
    ):
        raise ValueError("city cognition context disagrees with response assumptions")


def build_city_cognition_requests(
    context: CityCognitionRunContextV1,
    mobility: CityMobility,
    response_evaluation: SpatialResponseEvaluation,
) -> tuple[CityCognitionRequestV1, ...]:
    """Select persisted noticed responses and bind each to its committed minute state."""
    context = revalidate_model(context, CityCognitionRunContextV1, label="city cognition context")
    response_evaluation = revalidate_model(
        response_evaluation,
        SpatialResponseEvaluation,
        label="spatial response evaluation",
    )
    if not isinstance(mobility, CityMobility):
        raise TypeError("mobility must be CityMobility")
    _validate_context_against_science(context, mobility, response_evaluation)

    responses = sorted(
        (
            record
            for record in response_evaluation.records
            if isinstance(record, SpatialRuleResponse)
        ),
        key=_request_sort_key,
    )
    updates_by_cause: dict[str, list[SpatialStateUpdated]] = defaultdict(list)
    for record in response_evaluation.records:
        if isinstance(record, SpatialStateUpdated):
            for cause in record.caused_by_event_ids:
                updates_by_cause[cause].append(record)

    selected: list[SpatialRuleResponse] = []
    agent_counts: Counter[str] = Counter()
    for response in responses:
        if agent_counts[response.agent_id] >= MAX_CITY_COGNITION_REQUESTS_PER_AGENT:
            continue
        selected.append(response)
        agent_counts[response.agent_id] += 1
        if len(selected) == MAX_CITY_COGNITION_REQUESTS:
            break

    requests: list[CityCognitionRequestV1] = []
    for response in selected:
        _validate_response_context(response, context)
        updates = updates_by_cause.get(response.event_id, [])
        if len(updates) != 1:
            raise ValueError("selected response must bind to exactly one committed state update")
        update = updates[0]
        if (
            update.model_minute != response.model_minute
            or update.agent_id != response.agent_id
            or update.campaign_id != response.campaign_id
            or update.previous_state.agent_id != response.agent_id
        ):
            raise ValueError("selected response state update identity is inconsistent")
        positions = {item.agent_id: item for item in mobility.frame(response.model_minute)}
        position = positions.get(response.agent_id)
        if position is None:
            raise ValueError("selected response has no mobility position")
        requests.append(
            _derive_request(
                context=context,
                response=response,
                update=update,
                activity=position.activity,
            )
        )
    return tuple(requests)


def allocate_city_cognition_reservations(
    requests: tuple[CityCognitionRequestV1, ...],
    *,
    aggregate_output_token_ceiling: int,
) -> tuple[CityCognitionReservationV1, ...]:
    """Reserve whole four-attempt budgets before any asynchronous provider work."""
    if type(aggregate_output_token_ceiling) is not int:
        raise TypeError("city cognition aggregate budget must be an integer")
    if not 0 <= aggregate_output_token_ceiling <= MAX_CITY_COGNITION_AGGREGATE_OUTPUT_TOKENS:
        raise ValueError("city cognition aggregate budget is outside its hard limit")
    if aggregate_output_token_ceiling % CITY_COGNITION_RESERVATION_TOKENS:
        raise ValueError("city cognition aggregate budget must use whole reservations")
    checked = tuple(
        revalidate_model(item, CityCognitionRequestV1, label="city cognition request")
        for item in requests
    )
    if len(checked) > MAX_CITY_COGNITION_REQUESTS:
        raise ValueError("too many city cognition requests")
    expected_order = tuple(sorted(checked, key=_city_cognition_request_sort_key))
    if checked != expected_order:
        raise ValueError("city cognition requests must be in selection order")
    if len({request.request_id for request in checked}) != len(checked) or len(
        {request.fingerprint for request in checked}
    ) != len(checked):
        raise ValueError("city cognition requests must have unique identities and hashes")
    if len({request.response_event_id for request in checked}) != len(checked):
        raise ValueError("city cognition requests must reference unique response events")
    funded_count = min(
        len(checked), aggregate_output_token_ceiling // CITY_COGNITION_RESERVATION_TOKENS
    )
    return tuple(
        CityCognitionReservationV1(
            schema_version=1,
            selection_index=selection_index,
            request_id=request.request_id,
            request_sha256=request.fingerprint,
            reserved_output_tokens=_reservation_tokens(funded=selection_index < funded_count),
        )
        for selection_index, request in enumerate(checked)
    )


def _reservation_tokens(*, funded: bool) -> Literal[0, 1536]:
    return 1_536 if funded else 0


def _result_from_frozen_template(
    request: CityCognitionRequestV1,
    *,
    interpretation: str,
    decision_factors: tuple[str, ...],
    safety_flags: tuple[str, ...],
) -> CityCognitionResultV1:
    return CityCognitionResultV1.model_validate(
        {
            "schema_version": 1,
            "request_id": request.request_id,
            "interpretation": interpretation,
            "decision_factors": decision_factors,
            "safety_flags": safety_flags,
            "disclosure": CITY_COGNITION_DISCLOSURE,
        }
    )


def build_city_rules_result(
    request: CityCognitionRequestV1,
    *,
    policy: CityCognitionFallbackPolicyV1 = CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
) -> CityCognitionResultV1:
    request = revalidate_model(request, CityCognitionRequestV1, label="city cognition request")
    policy = revalidate_model(
        policy,
        CityCognitionFallbackPolicyV1,
        label="city cognition fallback policy",
    )
    return _result_from_frozen_template(
        request,
        interpretation=policy.rules.interpretation,
        decision_factors=policy.rules.decision_factors,
        safety_flags=policy.rules.safety_flags,
    )


def build_city_mock_result(
    request: CityCognitionRequestV1,
    *,
    policy: CityCognitionFallbackPolicyV1 = CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
) -> CityCognitionResultV1:
    request = revalidate_model(request, CityCognitionRequestV1, label="city cognition request")
    policy = revalidate_model(
        policy,
        CityCognitionFallbackPolicyV1,
        label="city cognition fallback policy",
    )
    return _result_from_frozen_template(
        request,
        interpretation=policy.mock.interpretation,
        decision_factors=policy.mock.decision_factors,
        safety_flags=policy.mock.safety_flags,
    )


def build_city_fallback_result(
    request: CityCognitionRequestV1,
    *,
    reason: CityCognitionFallbackReason,
    policy: CityCognitionFallbackPolicyV1 = CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
) -> CityCognitionResultV1:
    request = revalidate_model(request, CityCognitionRequestV1, label="city cognition request")
    policy = revalidate_model(
        policy,
        CityCognitionFallbackPolicyV1,
        label="city cognition fallback policy",
    )
    if reason == "budget-skipped" or reason not in policy.runtime_reasons:
        raise ValueError("budget-skipped uses the dedicated deterministic result")
    return _result_from_frozen_template(
        request,
        interpretation=policy.fallback.interpretation,
        decision_factors=policy.fallback.decision_factors,
        safety_flags=policy.fallback.safety_flags,
    )


def build_city_budget_skipped_result(
    request: CityCognitionRequestV1,
    *,
    policy: CityCognitionFallbackPolicyV1 = CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
) -> CityCognitionResultV1:
    request = revalidate_model(request, CityCognitionRequestV1, label="city cognition request")
    policy = revalidate_model(
        policy,
        CityCognitionFallbackPolicyV1,
        label="city cognition fallback policy",
    )
    return _result_from_frozen_template(
        request,
        interpretation=policy.budget_skipped.interpretation,
        decision_factors=policy.budget_skipped.decision_factors,
        safety_flags=policy.budget_skipped.safety_flags,
    )


def _effective_settings_from_profile(
    profile: CityCognitionProviderProfileSnapshotV1,
) -> CityCognitionEffectiveSettingsV1:
    return CityCognitionEffectiveSettingsV1(
        schema_version=1,
        protocol=profile.protocol,
        temperature=profile.temperature,
        top_p=profile.top_p,
        seed_policy=profile.seed_policy,
        seed=profile.seed,
        max_output_tokens=profile.max_output_tokens,
        attempt_timeout_seconds=profile.attempt_timeout_seconds,
        transient_retries=profile.transient_retries,
        repair_attempts=profile.repair_attempts,
    )


def build_city_cognition_terminal_record(
    request: CityCognitionRequestV1,
    *,
    selection_index: int,
    source: CityCognitionSource,
    result: CityCognitionResultV1,
    attempt_count: int = 0,
    reason: CityCognitionFallbackReason | None = None,
    provider_profile: CityCognitionProviderProfileSnapshotV1 | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> CityCognitionTerminalRecordV1:
    """Bind a validated result to its exact selected request and provenance."""
    request = revalidate_model(request, CityCognitionRequestV1, label="city cognition request")
    try:
        result = revalidate_model(result, CityCognitionResultV1, label="city cognition result")
    except ValueError:
        raise ValueError("city cognition result failed validation") from None
    if provider_profile is not None:
        if not isinstance(provider_profile, CityCognitionProviderProfileSnapshotV1):
            raise TypeError(
                "city cognition provider profile must be a CityCognitionProviderProfileSnapshotV1"
            )
        try:
            provider_profile = revalidate_model(
                provider_profile,
                CityCognitionProviderProfileSnapshotV1,
                label="city cognition provider profile",
            )
        except ValueError:
            raise ValueError("city cognition provider profile failed validation") from None
    profile_id = None if provider_profile is None else provider_profile.profile_id
    revision_sha256 = None if provider_profile is None else provider_profile.revision_sha256
    profile_sha256 = None if provider_profile is None else provider_profile.fingerprint
    provider_protocol = None if provider_profile is None else provider_profile.protocol
    effective_settings_sha256 = (
        None
        if provider_profile is None
        else _effective_settings_from_profile(provider_profile).fingerprint
    )
    placeholder = CityCognitionTerminalRecordV1.model_construct(
        schema_version=1,
        selection_index=selection_index,
        request_id=request.request_id,
        request_sha256=request.fingerprint,
        run_input_sha256=request.run_input_sha256,
        response_input_sha256=request.response_input_sha256,
        scenario_sha256=request.scenario_sha256,
        city_sha256=request.city_sha256,
        response_event_id=request.response_event_id,
        notice_event_id=request.notice_event_id,
        opportunity_id=request.opportunity_id,
        state_update_event_id=request.state_update_event_id,
        agent_id=request.agent_id,
        campaign_id=request.campaign_id,
        placement_id=request.placement_id,
        model_minute=request.model_minute,
        millisecond_within_minute=request.millisecond_within_minute,
        state_update_cause_count=request.state_update_cause_count,
        source=source,
        result=result,
        attempt_count=attempt_count,
        reason=reason,
        provider_profile_id=profile_id,
        provider_revision_sha256=revision_sha256,
        provider_profile_snapshot_sha256=profile_sha256,
        provider_protocol=provider_protocol,
        provider_effective_settings_sha256=effective_settings_sha256,
        prompt_contract_sha256=CITY_COGNITION_PROMPT_CONTRACT_SHA256,
        result_schema_contract_sha256=CITY_COGNITION_RESULT_SCHEMA_SHA256,
        selection_policy_sha256=CITY_COGNITION_SELECTION_POLICY_SHA256,
        fallback_policy_sha256=CITY_COGNITION_FALLBACK_POLICY_SHA256,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        disclosure=CITY_COGNITION_DISCLOSURE,
        record_hash="0" * 64,
    )
    return CityCognitionTerminalRecordV1(
        schema_version=1,
        selection_index=selection_index,
        request_id=request.request_id,
        request_sha256=request.fingerprint,
        run_input_sha256=request.run_input_sha256,
        response_input_sha256=request.response_input_sha256,
        scenario_sha256=request.scenario_sha256,
        city_sha256=request.city_sha256,
        response_event_id=request.response_event_id,
        notice_event_id=request.notice_event_id,
        opportunity_id=request.opportunity_id,
        state_update_event_id=request.state_update_event_id,
        agent_id=request.agent_id,
        campaign_id=request.campaign_id,
        placement_id=request.placement_id,
        model_minute=request.model_minute,
        millisecond_within_minute=request.millisecond_within_minute,
        state_update_cause_count=request.state_update_cause_count,
        source=source,
        result=result,
        attempt_count=attempt_count,
        reason=reason,
        provider_profile_id=profile_id,
        provider_revision_sha256=revision_sha256,
        provider_profile_snapshot_sha256=profile_sha256,
        provider_protocol=provider_protocol,
        provider_effective_settings_sha256=effective_settings_sha256,
        prompt_contract_sha256=CITY_COGNITION_PROMPT_CONTRACT_SHA256,
        result_schema_contract_sha256=CITY_COGNITION_RESULT_SCHEMA_SHA256,
        selection_policy_sha256=CITY_COGNITION_SELECTION_POLICY_SHA256,
        fallback_policy_sha256=CITY_COGNITION_FALLBACK_POLICY_SHA256,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        disclosure=CITY_COGNITION_DISCLOSURE,
        record_hash=placeholder.record_facts_sha256,
    )


def _expected_result(
    record: CityCognitionTerminalRecordV1,
    request: CityCognitionRequestV1,
    policy: CityCognitionFallbackPolicyV1,
) -> CityCognitionResultV1 | None:
    if record.source == "rules":
        return build_city_rules_result(request, policy=policy)
    if record.source == "mock":
        return build_city_mock_result(request, policy=policy)
    if record.source == "fallback":
        assert record.reason is not None
        return build_city_fallback_result(request, reason=record.reason, policy=policy)
    if record.source == "budget-skipped":
        return build_city_budget_skipped_result(request, policy=policy)
    return None


def validate_city_cognition_evaluation(
    cognition_input: CityCognitionInputV1,
    requests: tuple[CityCognitionRequestV1, ...],
    records: tuple[CityCognitionTerminalRecordV1, ...],
) -> CityCognitionEvaluationV1:
    """Cross-bind a complete ordered terminal stream to requests and frozen policy."""
    cognition_input = revalidate_model(
        cognition_input, CityCognitionInputV1, label="city cognition input"
    )
    checked_requests = tuple(
        revalidate_model(item, CityCognitionRequestV1, label="city cognition request")
        for item in requests
    )
    checked_records = tuple(
        revalidate_model(item, CityCognitionTerminalRecordV1, label="city cognition record")
        for item in records
    )
    if len(checked_requests) != len(checked_records):
        raise ValueError("city cognition terminal stream is incomplete")
    if any(item.run_input_sha256 != cognition_input.run_input_sha256 for item in checked_requests):
        raise ValueError("city cognition requests do not match frozen run input")
    scientific_provenance = {
        (
            item.run_input_sha256,
            item.response_input_sha256,
            item.scenario_sha256,
            item.city_sha256,
        )
        for item in checked_requests
    }
    if len(scientific_provenance) > 1:
        raise ValueError("city cognition requests contain mixed scientific provenance")

    persona_by_agent: dict[str, object] = {}
    persona_template_by_id: dict[str, str] = {}
    campaign_by_id: dict[str, object] = {}
    update_by_minute_identity: dict[tuple[int, str, str], tuple[object, ...]] = {}
    update_by_event: dict[str, tuple[object, ...]] = {}
    selections_by_minute_identity: Counter[tuple[int, str, str]] = Counter()
    selections_by_update_event: Counter[str] = Counter()
    cause_count_by_minute_identity: dict[tuple[int, str, str], int] = {}
    cause_count_by_update_event: dict[str, int] = {}
    for request in checked_requests:
        existing_persona = persona_by_agent.setdefault(request.agent_id, request.persona)
        if existing_persona != request.persona:
            raise ValueError("city cognition agent has inconsistent persona snapshots")
        persona_template = canonical_json(
            request.persona.model_dump(mode="json", exclude={"agent_id"})
        )
        existing_template = persona_template_by_id.setdefault(
            request.persona.persona_id,
            persona_template,
        )
        if existing_template != persona_template:
            raise ValueError("city cognition persona id has inconsistent persona templates")
        existing_campaign = campaign_by_id.setdefault(request.campaign_id, request.campaign)
        if existing_campaign != request.campaign:
            raise ValueError("city cognition campaign has inconsistent campaign snapshots")
        state_json = canonical_json(request.committed_state)
        update_binding = (
            request.state_update_event_id,
            request.state_before_sha256,
            request.state_update_cause_count,
            state_json,
        )
        minute_identity = (request.model_minute, request.agent_id, request.campaign_id)
        existing_update = update_by_minute_identity.setdefault(minute_identity, update_binding)
        if existing_update != update_binding:
            raise ValueError("city cognition requests disagree on a shared state update")
        selections_by_minute_identity[minute_identity] += 1
        cause_count_by_minute_identity[minute_identity] = request.state_update_cause_count
        event_binding = (
            request.model_minute,
            request.agent_id,
            request.campaign_id,
            request.state_before_sha256,
            request.state_update_cause_count,
            state_json,
        )
        existing_event = update_by_event.setdefault(request.state_update_event_id, event_binding)
        if existing_event != event_binding:
            raise ValueError("city cognition requests disagree on a shared state update event")
        selections_by_update_event[request.state_update_event_id] += 1
        cause_count_by_update_event[request.state_update_event_id] = (
            request.state_update_cause_count
        )
    if any(
        count > cause_count_by_minute_identity[key]
        for key, count in selections_by_minute_identity.items()
    ) or any(
        count > cause_count_by_update_event[key]
        for key, count in selections_by_update_event.items()
    ):
        raise ValueError(
            "city cognition selected responses exceed the shared state update cause count"
        )
    expected_order = tuple(
        sorted(
            checked_requests,
            key=lambda item: (
                item.model_minute,
                item.agent_id,
                item.campaign_id,
                item.response_event_id,
            ),
        )
    )
    if checked_requests != expected_order:
        raise ValueError("city cognition requests are not in deterministic selection order")
    if max(Counter(item.agent_id for item in checked_requests).values(), default=0) > 4:
        raise ValueError("city cognition per-agent selection limit was exceeded")
    expected_reservations = allocate_city_cognition_reservations(
        checked_requests,
        aggregate_output_token_ceiling=cognition_input.aggregate_output_token_ceiling,
    )
    if cognition_input.reservations != expected_reservations:
        raise ValueError("city cognition reservations do not match selected requests")

    reservation_by_request = {item.request_id: item for item in expected_reservations}
    for selection_index, (request, record) in enumerate(
        zip(checked_requests, checked_records, strict=True)
    ):
        if (
            record.selection_index != selection_index
            or record.request_id != request.request_id
            or record.request_sha256 != request.fingerprint
            or record.run_input_sha256 != request.run_input_sha256
            or record.response_input_sha256 != request.response_input_sha256
            or record.scenario_sha256 != request.scenario_sha256
            or record.city_sha256 != request.city_sha256
            or record.response_event_id != request.response_event_id
            or record.notice_event_id != request.notice_event_id
            or record.opportunity_id != request.opportunity_id
            or record.state_update_event_id != request.state_update_event_id
            or record.agent_id != request.agent_id
            or record.campaign_id != request.campaign_id
            or record.placement_id != request.placement_id
            or record.model_minute != request.model_minute
            or record.millisecond_within_minute != request.millisecond_within_minute
            or record.state_update_cause_count != request.state_update_cause_count
            or record.prompt_contract_sha256 != cognition_input.prompt_contract_sha256
            or record.result_schema_contract_sha256 != cognition_input.result_schema_contract_sha256
            or record.selection_policy_sha256 != cognition_input.selection_policy_sha256
            or record.fallback_policy_sha256 != cognition_input.fallback_policy_sha256
        ):
            raise ValueError("city cognition terminal record is cross-bound")
        expected = _expected_result(record, request, cognition_input.fallback_policy)
        if expected is not None and record.result != expected:
            raise ValueError("deterministic city cognition result does not match policy")

        if cognition_input.mode == "rules" and record.source != "rules":
            raise ValueError("rules cognition input requires rules terminal records")
        if cognition_input.mode == "mock" and record.source != "mock":
            raise ValueError("mock cognition input requires mock terminal records")
        if cognition_input.mode in {"local", "remote"}:
            if record.source not in {"provider", "cache", "fallback", "budget-skipped"}:
                raise ValueError("provider cognition input has an invalid terminal source")
            profile = cognition_input.provider_profile
            assert profile is not None
            if (
                record.provider_profile_id != profile.profile_id
                or record.provider_revision_sha256 != profile.revision_sha256
                or record.provider_profile_snapshot_sha256 != profile.fingerprint
                or record.provider_protocol != profile.protocol
                or cognition_input.effective_settings is None
                or record.provider_effective_settings_sha256
                != cognition_input.effective_settings.fingerprint
            ):
                raise ValueError("terminal record does not match frozen provider profile")
            settings = cognition_input.effective_settings
            assert settings is not None
            maximum_attempts = (
                cognition_input.initial_attempts
                + settings.transient_retries
                + settings.repair_attempts
            )
            if (
                record.source in {"provider", "fallback"}
                and record.attempt_count > maximum_attempts
            ):
                raise ValueError("terminal record exceeds the frozen provider attempt limit")
            funded = reservation_by_request[request.request_id].reserved_output_tokens != 0
            if funded == (record.source == "budget-skipped"):
                raise ValueError("terminal record does not match deterministic budget winner")

    stream = b"".join((canonical_json(record) + "\n").encode("utf-8") for record in checked_records)
    if len(stream) > MAX_CITY_COGNITION_STREAM_BYTES:
        raise ValueError("city cognition terminal stream exceeds its size limit")
    source_counts = Counter(item.source for item in checked_records)
    reason_counts = Counter(item.reason for item in checked_records if item.reason is not None)
    summary = CityCognitionSummaryV1(
        schema_version=1,
        selected_request_count=len(checked_requests),
        outcome_counts=CityCognitionOutcomeCountsV1(
            schema_version=1,
            rules=source_counts["rules"],
            mock=source_counts["mock"],
            provider=source_counts["provider"],
            cache=source_counts["cache"],
            fallback=source_counts["fallback"],
            budget_skipped=source_counts["budget-skipped"],
        ),
        reason_counts=CityCognitionReasonCountsV1(
            schema_version=1,
            endpoint_resolution_refused=reason_counts["endpoint-resolution-refused"],
            authentication_refused=reason_counts["authentication-refused"],
            timeout_exhausted=reason_counts["timeout-exhausted"],
            rate_limit_exhausted=reason_counts["rate-limit-exhausted"],
            server_error_exhausted=reason_counts["server-error-exhausted"],
            response_too_large=reason_counts["response-too-large"],
            protocol_incompatible=reason_counts["protocol-incompatible"],
            invalid_result=reason_counts["invalid-result"],
            budget_skipped=reason_counts["budget-skipped"],
        ),
        attempt_count=sum(item.attempt_count for item in checked_records),
        prompt_tokens=sum(item.prompt_tokens or 0 for item in checked_records),
        completion_tokens=sum(item.completion_tokens or 0 for item in checked_records),
        terminal_stream_sha256=sha256(stream).hexdigest(),
        terminal_stream_bytes=len(stream),
        terminal_record_count=len(checked_records),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    return CityCognitionEvaluationV1(
        cognition_input_sha256=cognition_input.fingerprint,
        run_input_sha256=cognition_input.run_input_sha256,
        mode=cognition_input.mode,
        records=checked_records,
        summary=summary,
    )


__all__ = [
    "allocate_city_cognition_reservations",
    "build_city_budget_skipped_result",
    "build_city_cognition_requests",
    "build_city_cognition_terminal_record",
    "build_city_fallback_result",
    "build_city_mock_result",
    "build_city_rules_result",
    "validate_city_cognition_evaluation",
]
