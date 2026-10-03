"""Pure bounded response rules for persisted synthetic spatial notices.

This module deliberately models neither purchases nor observed resident behavior.  It
turns validated ``spatial.noticed`` evidence into transparent response scores and one
atomic campaign-scoped state update per agent/campaign/minute group.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Iterator, Sequence
from hashlib import sha256
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
    SpatialCampaignScenario,
)
from adlife.core.domain.spatial_response import (
    SpatialResponseInput,
    validate_spatial_response_input,
)
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    SpatialImpression,
    SpatialNotice,
    spatial_attention_event_id,
    spatial_notice_draw,
)
from adlife.core.simulation.spatial_opportunity import (
    MAX_SPATIAL_OPPORTUNITIES,
    PhoneOpportunity,
    RoadsideOpportunity,
    SpatialOpportunity,
    SpatialOpportunityEvaluation,
    spatial_opportunity_id,
)

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_AGENT_PATTERN = r"^person-[0-9]{3}$"
_MODEL_ID: Literal["spatial-response-v1"] = "spatial-response-v1"
_CLAIM_SCOPE: Literal["synthetic-response-not-observed-behavior"] = (
    "synthetic-response-not-observed-behavior"
)
_MAX_SEED = 2**63 - 1
_MAX_AGENTS = 30
_MAX_CAMPAIGNS = 20

MAX_SPATIAL_RESPONSES = MAX_SPATIAL_OPPORTUNITIES
MAX_SPATIAL_STATE_UPDATES = MAX_SPATIAL_OPPORTUNITIES
MAX_SPATIAL_RESPONSE_RECORDS = MAX_SPATIAL_RESPONSES + MAX_SPATIAL_STATE_UPDATES
MAX_SPATIAL_RESPONSE_STREAM_BYTES = 2_147_483_648
MAX_SPATIAL_RESPONSE_STATE_BYTES = 4_194_304
MAX_SPATIAL_RESPONSE_SUMMARY_BYTES = 65_536

_Sha256: TypeAlias = Annotated[str, Field(pattern=_HASH_PATTERN)]


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


class SpatialResponseState(DomainModel):
    """Bounded campaign state for one fictional agent."""

    schema_version: Literal[1] = 1
    agent_id: str = Field(pattern=_AGENT_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    brand_sentiment: float = Field(ge=-1, le=1)
    recall_strength: float = Field(ge=0, le=1)
    purchase_intention: float = Field(ge=0, le=1)
    response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    last_response_minute: int | None = Field(default=None, ge=0, lt=10_080)

    @model_validator(mode="after")
    def coherent_progress(self) -> Self:
        if (self.response_count == 0) is not (self.last_response_minute is None):
            raise ValueError("response count and last response minute are inconsistent")
        return self


def _state_sha256(state: SpatialResponseState) -> str:
    return sha256(canonical_json(state).encode("utf-8")).hexdigest()


def _response_event_id(*, caused_by: str, response_input_sha256: str) -> str:
    identity = {
        "caused_by": caused_by,
        "event_type": "spatial.response",
        "model_id": _MODEL_ID,
        "response_input_sha256": response_input_sha256,
    }
    return sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _state_update_event_id(
    *,
    response_input_sha256: str,
    model_minute: int,
    agent_id: str,
    campaign_id: str,
    caused_by_event_ids: Sequence[str],
) -> str:
    identity = {
        "agent_id": agent_id,
        "campaign_id": campaign_id,
        "caused_by_event_ids": list(caused_by_event_ids),
        "event_type": "spatial.state-updated",
        "model_id": _MODEL_ID,
        "model_minute": model_minute,
        "response_input_sha256": response_input_sha256,
    }
    return sha256(canonical_json(identity).encode("utf-8")).hexdigest()


class SpatialRuleResponse(DomainModel):
    """Auditable bounded scores caused by exactly one validated notice."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-v1"] = _MODEL_ID
    claim_scope: Literal["synthetic-response-not-observed-behavior"] = _CLAIM_SCOPE
    event_type: Literal["spatial.response"] = "spatial.response"
    event_id: str = Field(pattern=_HASH_PATTERN)
    caused_by: str = Field(pattern=_HASH_PATTERN)
    opportunity_id: str = Field(pattern=_HASH_PATTERN)
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    placement_id: str = Field(pattern=_ID_PATTERN)
    agent_id: str = Field(pattern=_AGENT_PATTERN)
    channel: Literal["roadside-billboard", "mobile-feed"]
    day_index: int = Field(ge=0, le=6)
    model_minute: int = Field(ge=0, lt=10_080)
    millisecond_within_minute: int = Field(ge=0, lt=60_000)
    state_before_sha256: str = Field(pattern=_HASH_PATTERN)
    prior_notices_today: int = Field(ge=0, le=100)
    frequency_cap_per_agent_per_day: int = Field(ge=1, le=100)
    interest_match: float = Field(ge=0, le=1)
    relative_price: float = Field(gt=0, le=100)
    price_sensitivity: float = Field(ge=0, le=1)
    affordability: float = Field(ge=0, le=1)
    novelty_seeking: float = Field(ge=0, le=1)
    advertising_skepticism: float = Field(ge=0, le=1)
    channel_recall_encoding: float = Field(ge=0, le=1)
    impulsivity: float = Field(ge=0, le=1)
    frequency_fatigue: float = Field(ge=0, le=1)
    value_match: float = Field(ge=0, le=1)
    sentiment_delta: float = Field(ge=-0.2, le=0.2)
    recall_delta: float = Field(ge=0, le=0.3)

    @model_validator(mode="after")
    def coherent_derived_evidence(self) -> Self:
        if self.day_index != self.model_minute // 1_440:
            raise ValueError("response day does not match model minute")
        if self.prior_notices_today >= self.frequency_cap_per_agent_per_day:
            raise ValueError("response prior notice count exceeds placement daily cap")
        expected_affordability = _clamp(
            1.25 - self.relative_price * self.price_sensitivity,
            0.0,
            1.0,
        )
        expected_fatigue = min(
            1.0,
            self.prior_notices_today / self.frequency_cap_per_agent_per_day,
        )
        expected_value = (
            0.55 * self.interest_match + 0.25 * self.novelty_seeking + 0.20 * expected_affordability
        )
        expected_sentiment = _clamp(
            0.18 * expected_value - 0.12 * self.advertising_skepticism - 0.06 * expected_fatigue,
            -0.2,
            0.2,
        )
        expected_recall = _clamp(
            0.22 * self.channel_recall_encoding
            + 0.12 * self.novelty_seeking
            - 0.08 * expected_fatigue,
            0.0,
            0.3,
        )
        if (
            self.affordability != expected_affordability
            or self.frequency_fatigue != expected_fatigue
            or self.value_match != expected_value
            or self.sentiment_delta != expected_sentiment
            or self.recall_delta != expected_recall
        ):
            raise ValueError("response derived rule evidence is inconsistent")
        expected_opportunity_id = spatial_opportunity_id(
            scenario_sha256=self.scenario_sha256,
            city_sha256=self.city_sha256,
            campaign_id=self.campaign_id,
            placement_id=self.placement_id,
            agent_id=self.agent_id,
            channel=self.channel,
            at_millisecond=(self.model_minute * 60_000 + self.millisecond_within_minute),
        )
        if self.opportunity_id != expected_opportunity_id:
            raise ValueError("response opportunity causal identity is inconsistent")
        expected_impression_id = spatial_attention_event_id(
            event_type="spatial.impression",
            caused_by=self.opportunity_id,
        )
        expected_notice_id = spatial_attention_event_id(
            event_type="spatial.noticed",
            caused_by=expected_impression_id,
        )
        if self.caused_by != expected_notice_id:
            raise ValueError("response notice causal identity is inconsistent")
        expected_id = _response_event_id(
            caused_by=self.caused_by,
            response_input_sha256=self.response_input_sha256,
        )
        if self.event_id != expected_id:
            raise ValueError("response causal identity does not match event ID")
        return self


class SpatialStateUpdated(DomainModel):
    """One atomic state transition caused by a canonical minute group."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-v1"] = _MODEL_ID
    claim_scope: Literal["synthetic-response-not-observed-behavior"] = _CLAIM_SCOPE
    event_type: Literal["spatial.state-updated"] = "spatial.state-updated"
    event_id: str = Field(pattern=_HASH_PATTERN)
    caused_by_event_ids: tuple[_Sha256, ...] = Field(
        min_length=1,
        max_length=MAX_SPATIAL_RESPONSES,
    )
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    agent_id: str = Field(pattern=_AGENT_PATTERN)
    day_index: int = Field(ge=0, le=6)
    model_minute: int = Field(ge=0, lt=10_080)
    previous_state: SpatialResponseState
    state: SpatialResponseState

    @model_validator(mode="after")
    def coherent_transition_identity(self) -> Self:
        if self.day_index != self.model_minute // 1_440:
            raise ValueError("state update day does not match model minute")
        if len(self.caused_by_event_ids) != len(set(self.caused_by_event_ids)):
            raise ValueError("state update causes must be unique")
        expected_key = (self.agent_id, self.campaign_id)
        if (self.previous_state.agent_id, self.previous_state.campaign_id) != expected_key or (
            self.state.agent_id,
            self.state.campaign_id,
        ) != expected_key:
            raise ValueError("state update identity does not match its states")
        if self.state.response_count != (
            self.previous_state.response_count + len(self.caused_by_event_ids)
        ):
            raise ValueError("state update response count does not match its causes")
        if self.state.last_response_minute != self.model_minute:
            raise ValueError("state update last response minute is inconsistent")
        if (
            self.previous_state.last_response_minute is not None
            and self.previous_state.last_response_minute >= self.model_minute
        ):
            raise ValueError("state update must follow the previous response minute")
        expected_id = _state_update_event_id(
            response_input_sha256=self.response_input_sha256,
            model_minute=self.model_minute,
            agent_id=self.agent_id,
            campaign_id=self.campaign_id,
            caused_by_event_ids=self.caused_by_event_ids,
        )
        if self.event_id != expected_id:
            raise ValueError("state update causal identity does not match event ID")
        return self


SpatialResponseRecord: TypeAlias = Annotated[
    SpatialRuleResponse | SpatialStateUpdated,
    Field(discriminator="event_type"),
]


class SpatialResponseCounts(DomainModel):
    """Exact response and state totals for one evaluation."""

    schema_version: Literal[1] = 1
    response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    state_update_count: int = Field(ge=0, le=MAX_SPATIAL_STATE_UPDATES)
    roadside_response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    phone_response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    campaign_count: int = Field(ge=1, le=20)
    final_state_count: int = Field(ge=1, le=600)

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        if self.response_count != (self.roadside_response_count + self.phone_response_count):
            raise ValueError("response channel counts do not match total")
        if self.response_count == 0 and self.state_update_count != 0:
            raise ValueError("state updates require responses")
        if self.response_count > 0 and not (1 <= self.state_update_count <= self.response_count):
            raise ValueError("state update count does not match response total")
        return self


def _record_sort_key(record: SpatialResponseRecord) -> tuple[object, ...]:
    if isinstance(record, SpatialRuleResponse):
        return (
            record.model_minute,
            record.agent_id,
            record.campaign_id,
            0,
            record.millisecond_within_minute,
            record.placement_id,
            record.event_id,
        )
    return (
        record.model_minute,
        record.agent_id,
        record.campaign_id,
        1,
        0,
        "",
        record.event_id,
    )


def _updated_state(
    previous: SpatialResponseState,
    responses: Sequence[SpatialRuleResponse],
    *,
    model_minute: int,
) -> SpatialResponseState:
    if not responses:
        raise ValueError("a state update requires at least one response")
    value_match = responses[0].value_match
    impulsivity = responses[0].impulsivity
    if any(
        response.value_match != value_match or response.impulsivity != impulsivity
        for response in responses
    ):
        raise ValueError("state transition responses disagree on campaign profile inputs")
    sentiment_after = _clamp(
        math.fsum((previous.brand_sentiment, *(item.sentiment_delta for item in responses))),
        -1.0,
        1.0,
    )
    retained_headroom = math.prod(sorted(1.0 - item.recall_delta for item in responses))
    recall_after = _clamp(
        1.0 - (1.0 - previous.recall_strength) * retained_headroom,
        0.0,
        1.0,
    )
    intention_after = _clamp(
        0.40 * ((sentiment_after + 1.0) / 2.0)
        + 0.25 * value_match
        + 0.20 * recall_after
        + 0.15 * impulsivity,
        0.0,
        1.0,
    )
    return SpatialResponseState(
        agent_id=previous.agent_id,
        campaign_id=previous.campaign_id,
        brand_sentiment=sentiment_after,
        recall_strength=recall_after,
        purchase_intention=intention_after,
        response_count=previous.response_count + len(responses),
        last_response_minute=model_minute,
    )


class SpatialResponseEvaluation(DomainModel):
    """Canonical response evidence and complete final campaign state."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-v1"] = _MODEL_ID
    claim_scope: Literal["synthetic-response-not-observed-behavior"] = _CLAIM_SCOPE
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    attention_seed: int = Field(ge=0, le=_MAX_SEED)
    counts: SpatialResponseCounts
    records: tuple[SpatialResponseRecord, ...] = Field(max_length=MAX_SPATIAL_RESPONSE_RECORDS)
    final_states: tuple[SpatialResponseState, ...] = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def coherent_evaluation(self) -> Self:
        responses = tuple(
            record for record in self.records if isinstance(record, SpatialRuleResponse)
        )
        updates = tuple(
            record for record in self.records if isinstance(record, SpatialStateUpdated)
        )
        if len(self.records) != self.counts.response_count + self.counts.state_update_count:
            raise ValueError("response records do not match counts")
        if len(responses) != self.counts.response_count or len(updates) != (
            self.counts.state_update_count
        ):
            raise ValueError("response record kinds do not match counts")
        if len({record.event_id for record in self.records}) != len(self.records):
            raise ValueError("response record identifiers must be unique")
        if any(
            record.response_input_sha256 != self.response_input_sha256
            or record.scenario_sha256 != self.scenario_sha256
            or record.city_sha256 != self.city_sha256
            for record in self.records
        ):
            raise ValueError("response evidence does not match evaluation fingerprints")
        keys = tuple(_record_sort_key(record) for record in self.records)
        if keys != tuple(sorted(keys)):
            raise ValueError("response records must be in canonical order")

        state_keys = tuple((state.agent_id, state.campaign_id) for state in self.final_states)
        if state_keys != tuple(sorted(state_keys)) or len(state_keys) != len(set(state_keys)):
            raise ValueError("final response states must be unique and canonical")
        agent_ids = {state.agent_id for state in self.final_states}
        campaign_ids = {state.campaign_id for state in self.final_states}
        if len(agent_ids) > _MAX_AGENTS:
            raise ValueError("response evaluation cannot contain more than 30 agents")
        if len(campaign_ids) > _MAX_CAMPAIGNS:
            raise ValueError("response evaluation cannot contain more than 20 campaigns")
        expected_state_keys = {
            (agent_id, campaign_id) for agent_id in agent_ids for campaign_id in campaign_ids
        }
        if set(state_keys) != expected_state_keys:
            raise ValueError("final response states must cover every agent and campaign")
        if self.counts.campaign_count != len(campaign_ids) or (
            self.counts.final_state_count != len(self.final_states)
        ):
            raise ValueError("final response states do not match counts")
        if sum(state.response_count for state in self.final_states) != (self.counts.response_count):
            raise ValueError("final state response counts do not match response total")
        if self.counts.roadside_response_count != sum(
            response.channel == "roadside-billboard" for response in responses
        ) or self.counts.phone_response_count != sum(
            response.channel == "mobile-feed" for response in responses
        ):
            raise ValueError("response channels do not match counts")

        response_groups: dict[tuple[int, str, str], list[SpatialRuleResponse]] = defaultdict(list)
        for response in responses:
            notice_draw = spatial_notice_draw(
                agent_id=response.agent_id,
                at_millisecond=(
                    response.model_minute * 60_000 + response.millisecond_within_minute
                ),
                channel=response.channel,
                placement_id=response.placement_id,
                seed=self.attention_seed,
            )
            if notice_draw >= 0.5:
                raise ValueError("response does not have a passing keyed attention draw")
            response_groups[
                (response.model_minute, response.agent_id, response.campaign_id)
            ].append(response)
        update_by_group = {
            (update.model_minute, update.agent_id, update.campaign_id): update for update in updates
        }
        if len(update_by_group) != len(updates) or set(update_by_group) != set(response_groups):
            raise ValueError("each response minute group must have exactly one state update")

        latest: dict[tuple[str, str], SpatialResponseState] = {}
        prior_daily_notices: dict[tuple[int, str, str], int] = defaultdict(int)
        placement_contracts: dict[str, tuple[str, str, int]] = {}
        for group_key in sorted(response_groups):
            group = tuple(sorted(response_groups[group_key], key=_record_sort_key))
            update = update_by_group[group_key]
            group_increments: dict[tuple[int, str, str], int] = defaultdict(int)
            for response in group:
                placement_contract = (
                    response.campaign_id,
                    response.channel,
                    response.frequency_cap_per_agent_per_day,
                )
                known_contract = placement_contracts.setdefault(
                    response.placement_id,
                    placement_contract,
                )
                if known_contract != placement_contract:
                    raise ValueError("response placement contract is inconsistent")
                fatigue_key = (
                    response.day_index,
                    response.agent_id,
                    response.placement_id,
                )
                if response.prior_notices_today != prior_daily_notices[fatigue_key]:
                    raise ValueError("response fatigue history is inconsistent")
                group_increments[fatigue_key] += 1
            for fatigue_key, increment in group_increments.items():
                placement_id = fatigue_key[2]
                cap = placement_contracts[placement_id][2]
                if prior_daily_notices[fatigue_key] + increment > cap:
                    raise ValueError("response notice history exceeds placement daily cap")
            cause_ids = tuple(response.event_id for response in group)
            if update.caused_by_event_ids != cause_ids:
                raise ValueError("state update causes do not match response order")
            previous_hash = _state_sha256(update.previous_state)
            if any(response.state_before_sha256 != previous_hash for response in group):
                raise ValueError("responses do not share their minute state snapshot")
            state_key = (update.agent_id, update.campaign_id)
            prior = latest.get(state_key)
            if prior is not None and update.previous_state != prior:
                raise ValueError("state update chain is discontinuous")
            expected = _updated_state(
                update.previous_state,
                group,
                model_minute=update.model_minute,
            )
            if update.state != expected:
                raise ValueError("state transition does not match response rules")
            latest[state_key] = update.state
            for fatigue_key, increment in group_increments.items():
                prior_daily_notices[fatigue_key] += increment

        final_by_key = {(state.agent_id, state.campaign_id): state for state in self.final_states}
        if not set(latest) <= set(final_by_key):
            raise ValueError("response record keys are absent from final states")
        if any(final_by_key[key] != state for key, state in latest.items()):
            raise ValueError("final response state does not match committed updates")
        return self


class SpatialResponseStateDocument(DomainModel):
    """Complete canonical final-state artifact for a response evaluation."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-state-v1"] = "spatial-response-state-v1"
    claim_scope: Literal["synthetic-response-not-observed-behavior"] = _CLAIM_SCOPE
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    states: tuple[SpatialResponseState, ...] = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def canonical_states(self) -> Self:
        keys = tuple((state.agent_id, state.campaign_id) for state in self.states)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("response state document must be unique and canonical")
        agents = {state.agent_id for state in self.states}
        campaigns = {state.campaign_id for state in self.states}
        if len(agents) > _MAX_AGENTS:
            raise ValueError("response state document cannot contain more than 30 agents")
        if len(campaigns) > _MAX_CAMPAIGNS:
            raise ValueError("response state document cannot contain more than 20 campaigns")
        if set(keys) != {
            (agent_id, campaign_id) for agent_id in agents for campaign_id in campaigns
        }:
            raise ValueError("response state document must be a complete Cartesian product")
        return self


class SpatialResponseArtifactSummary(DomainModel):
    """Exact hashes and counts for canonical response and final-state artifacts."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-artifact-v1"] = "spatial-response-artifact-v1"
    claim_scope: Literal["synthetic-response-not-observed-behavior"] = _CLAIM_SCOPE
    response_model_id: Literal["spatial-response-v1"] = _MODEL_ID
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    attention_seed: int = Field(ge=0, le=_MAX_SEED)
    stream_sha256: str = Field(pattern=_HASH_PATTERN)
    stream_bytes: int = Field(ge=0, le=MAX_SPATIAL_RESPONSE_STREAM_BYTES)
    state_document_sha256: str = Field(pattern=_HASH_PATTERN)
    state_document_bytes: int = Field(ge=1, le=MAX_SPATIAL_RESPONSE_STATE_BYTES)
    counts: SpatialResponseCounts


def _shared_opportunity_evidence(
    opportunity: SpatialOpportunity,
    impression: SpatialImpression,
) -> bool:
    return (
        opportunity.opportunity_id,
        opportunity.scenario_sha256,
        opportunity.city_sha256,
        opportunity.campaign_id,
        opportunity.placement_id,
        opportunity.agent_id,
        opportunity.channel,
        opportunity.day_index,
        opportunity.model_minute,
        opportunity.millisecond_within_minute,
    ) == (
        impression.opportunity_id,
        impression.scenario_sha256,
        impression.city_sha256,
        impression.campaign_id,
        impression.placement_id,
        impression.agent_id,
        impression.channel,
        impression.day_index,
        impression.model_minute,
        impression.millisecond_within_minute,
    )


def _validate_evaluator_inputs(
    response_input: SpatialResponseInput,
    scenario: SpatialCampaignScenario,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    *,
    agent_ids: Collection[str],
) -> None:
    validate_spatial_response_input(response_input, scenario, agent_ids=agent_ids)
    if opportunities.scenario_sha256 != scenario.fingerprint:
        raise ValueError("spatial opportunity scenario does not match response scenario")
    if opportunities.city_sha256 != scenario.city_sha256:
        raise ValueError("spatial opportunity city does not match response scenario")
    if attention.scenario_sha256 != opportunities.scenario_sha256:
        raise ValueError("spatial attention scenario does not match opportunities")
    if attention.city_sha256 != opportunities.city_sha256:
        raise ValueError("spatial attention city does not match opportunities")

    agents = set(agent_ids)
    placement_by_id = {placement.placement_id: placement for placement in scenario.placements}
    opportunity_by_id = {
        opportunity.opportunity_id: opportunity for opportunity in opportunities.opportunities
    }
    ordinal_counts: dict[tuple[int, str, str], int] = defaultdict(int)
    for opportunity in opportunities.opportunities:
        placement = placement_by_id.get(opportunity.placement_id)
        if placement is None:
            raise ValueError("spatial opportunity references an unknown placement")
        if (
            opportunity.agent_id not in agents
            or opportunity.campaign_id != placement.campaign_id
            or opportunity.channel != placement.channel
        ):
            raise ValueError("spatial opportunity identity does not match response inputs")
        if opportunity.model_minute >= scenario.days * 1_440:
            raise ValueError("spatial opportunity exceeds response scenario duration")
        at_millisecond = opportunity.model_minute * 60_000 + opportunity.millisecond_within_minute
        if not any(
            window.start_minute * 60_000 <= at_millisecond < window.end_minute * 60_000
            for window in placement.active_windows
        ):
            raise ValueError("spatial opportunity is outside its placement active window")
        ordinal_key = (
            opportunity.day_index,
            opportunity.agent_id,
            opportunity.placement_id,
        )
        expected_ordinal = ordinal_counts[ordinal_key] + 1
        if (
            opportunity.ordinal_for_agent_placement_day != expected_ordinal
            or expected_ordinal > placement.frequency_cap_per_agent_per_day
        ):
            raise ValueError("spatial opportunity ordinal or daily cap is inconsistent")
        ordinal_counts[ordinal_key] = expected_ordinal

        if isinstance(opportunity, RoadsideOpportunity):
            if not isinstance(placement, RoadsideBillboardPlacement) or (
                opportunity.road_id != placement.road_id
                or opportunity.travel_direction != placement.travel_direction
                or opportunity.road_fraction != placement.road_fraction
                or opportunity.side != placement.side
                or (
                    opportunity.approach_distance_meters > placement.max_view_distance_meters
                    and not math.isclose(
                        opportunity.approach_distance_meters,
                        placement.max_view_distance_meters,
                        rel_tol=1e-12,
                        abs_tol=1e-9,
                    )
                )
            ):
                raise ValueError("spatial opportunity roadside placement evidence is inconsistent")
        elif isinstance(opportunity, PhoneOpportunity):
            if not isinstance(placement, PhoneOpportunityPlacement) or (
                opportunity.millisecond_within_minute != 0
                or opportunity.activity not in placement.eligible_activities
                or opportunity.opportunity_probability_per_minute
                != placement.opportunity_probability_per_minute
                or opportunity.eligibility_draw >= placement.opportunity_probability_per_minute
            ):
                raise ValueError("spatial opportunity phone placement evidence is inconsistent")
        else:
            raise TypeError("unsupported spatial opportunity record")

    impressions = tuple(event for event in attention.events if isinstance(event, SpatialImpression))
    if {item.opportunity_id for item in impressions} != set(opportunity_by_id):
        raise ValueError("spatial attention opportunities do not match opportunity evidence")
    if any(
        not _shared_opportunity_evidence(
            opportunity_by_id[impression.opportunity_id],
            impression,
        )
        for impression in impressions
    ):
        raise ValueError("spatial attention evidence does not match its opportunity")


def _notice_sort_key(notice: SpatialNotice) -> tuple[object, ...]:
    return (
        notice.millisecond_within_minute,
        notice.placement_id,
        notice.event_id,
    )


def evaluate_spatial_responses(
    response_input: SpatialResponseInput,
    scenario: SpatialCampaignScenario,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    *,
    agent_ids: Collection[str],
) -> SpatialResponseEvaluation:
    """Evaluate only validated notices and atomically commit their minute groups."""
    response_input = revalidate_model(
        response_input,
        SpatialResponseInput,
        label="spatial response input",
    )
    scenario = revalidate_model(
        scenario,
        SpatialCampaignScenario,
        label="spatial response scenario",
    )
    opportunities = revalidate_model(
        opportunities,
        SpatialOpportunityEvaluation,
        label="spatial opportunity evaluation",
    )
    attention = revalidate_model(
        attention,
        SpatialAttentionEvaluation,
        label="spatial attention evaluation",
    )
    if isinstance(agent_ids, (str, bytes)) or not isinstance(agent_ids, Collection):
        raise TypeError("agent_ids must be a collection of agent identifiers")
    _validate_evaluator_inputs(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agent_ids,
    )
    response_input_sha256 = response_input.fingerprint
    scenario_sha256 = scenario.fingerprint

    profile_by_agent = {profile.agent_id: profile for profile in response_input.profiles}
    campaign_input_by_id = {campaign.campaign_id: campaign for campaign in response_input.campaigns}
    placement_by_id = {placement.placement_id: placement for placement in scenario.placements}
    states = {
        (initial.agent_id, initial.campaign_id): SpatialResponseState(
            agent_id=initial.agent_id,
            campaign_id=initial.campaign_id,
            brand_sentiment=initial.brand_sentiment,
            recall_strength=initial.recall_strength,
            purchase_intention=initial.purchase_intention,
            response_count=0,
            last_response_minute=None,
        )
        for initial in response_input.initial_states
    }
    notice_groups: dict[tuple[int, str, str], list[SpatialNotice]] = defaultdict(list)
    for event in attention.events:
        if isinstance(event, SpatialNotice):
            notice_groups[(event.model_minute, event.agent_id, event.campaign_id)].append(event)

    records: list[SpatialResponseRecord] = []
    prior_daily_notices: dict[tuple[int, str, str], int] = defaultdict(int)
    for model_minute, agent_id, campaign_id in sorted(notice_groups):
        notices = tuple(
            sorted(
                notice_groups[(model_minute, agent_id, campaign_id)],
                key=_notice_sort_key,
            )
        )
        state_key = (agent_id, campaign_id)
        previous = states[state_key]
        state_before_sha256 = _state_sha256(previous)
        profile = profile_by_agent[agent_id]
        campaign_input = campaign_input_by_id[campaign_id]
        interest_union = profile.interests | campaign_input.target_interests
        interest_match = len(profile.interests & campaign_input.target_interests) / len(
            interest_union
        )
        affordability = _clamp(
            1.25 - campaign_input.relative_price * profile.traits.price_sensitivity,
            0.0,
            1.0,
        )
        value_match = (
            0.55 * interest_match + 0.25 * profile.traits.novelty_seeking + 0.20 * affordability
        )
        group_responses: list[SpatialRuleResponse] = []
        for notice in notices:
            placement = placement_by_id[notice.placement_id]
            notice_key = (notice.day_index, agent_id, notice.placement_id)
            prior_notices_today = prior_daily_notices[notice_key]
            fatigue = min(
                1.0,
                prior_notices_today / placement.frequency_cap_per_agent_per_day,
            )
            recall_encoding = (
                profile.traits.roadside_recall_encoding
                if notice.channel == "roadside-billboard"
                else profile.traits.mobile_recall_encoding
            )
            sentiment_delta = _clamp(
                0.18 * value_match - 0.12 * profile.traits.advertising_skepticism - 0.06 * fatigue,
                -0.2,
                0.2,
            )
            recall_delta = _clamp(
                0.22 * recall_encoding + 0.12 * profile.traits.novelty_seeking - 0.08 * fatigue,
                0.0,
                0.3,
            )
            response_id = _response_event_id(
                caused_by=notice.event_id,
                response_input_sha256=response_input_sha256,
            )
            group_responses.append(
                SpatialRuleResponse(
                    event_id=response_id,
                    caused_by=notice.event_id,
                    opportunity_id=notice.opportunity_id,
                    response_input_sha256=response_input_sha256,
                    scenario_sha256=scenario_sha256,
                    city_sha256=scenario.city_sha256,
                    campaign_id=campaign_id,
                    placement_id=notice.placement_id,
                    agent_id=agent_id,
                    channel=notice.channel,
                    day_index=notice.day_index,
                    model_minute=model_minute,
                    millisecond_within_minute=notice.millisecond_within_minute,
                    state_before_sha256=state_before_sha256,
                    prior_notices_today=prior_notices_today,
                    frequency_cap_per_agent_per_day=(placement.frequency_cap_per_agent_per_day),
                    interest_match=interest_match,
                    relative_price=campaign_input.relative_price,
                    price_sensitivity=profile.traits.price_sensitivity,
                    affordability=affordability,
                    novelty_seeking=profile.traits.novelty_seeking,
                    advertising_skepticism=profile.traits.advertising_skepticism,
                    channel_recall_encoding=recall_encoding,
                    impulsivity=profile.traits.impulsivity,
                    frequency_fatigue=fatigue,
                    value_match=value_match,
                    sentiment_delta=sentiment_delta,
                    recall_delta=recall_delta,
                )
            )

        canonical_responses = tuple(sorted(group_responses, key=_record_sort_key))
        state = _updated_state(previous, canonical_responses, model_minute=model_minute)
        cause_ids = tuple(response.event_id for response in canonical_responses)
        update = SpatialStateUpdated(
            event_id=_state_update_event_id(
                response_input_sha256=response_input_sha256,
                model_minute=model_minute,
                agent_id=agent_id,
                campaign_id=campaign_id,
                caused_by_event_ids=cause_ids,
            ),
            caused_by_event_ids=cause_ids,
            response_input_sha256=response_input_sha256,
            scenario_sha256=scenario_sha256,
            city_sha256=scenario.city_sha256,
            campaign_id=campaign_id,
            agent_id=agent_id,
            day_index=model_minute // 1_440,
            model_minute=model_minute,
            previous_state=previous,
            state=state,
        )
        records.extend(canonical_responses)
        records.append(update)
        states[state_key] = state
        for notice in notices:
            prior_daily_notices[(notice.day_index, agent_id, notice.placement_id)] += 1

    final_states = tuple(states[key] for key in sorted(states))
    response_records = tuple(
        record for record in records if isinstance(record, SpatialRuleResponse)
    )
    counts = SpatialResponseCounts(
        response_count=len(response_records),
        state_update_count=sum(isinstance(record, SpatialStateUpdated) for record in records),
        roadside_response_count=sum(
            record.channel == "roadside-billboard" for record in response_records
        ),
        phone_response_count=sum(record.channel == "mobile-feed" for record in response_records),
        campaign_count=len(response_input.campaigns),
        final_state_count=len(final_states),
    )
    return SpatialResponseEvaluation(
        response_input_sha256=response_input_sha256,
        scenario_sha256=scenario_sha256,
        city_sha256=scenario.city_sha256,
        attention_seed=attention.seed,
        counts=counts,
        records=tuple(records),
        final_states=final_states,
    )


def spatial_response_lines(evaluation: SpatialResponseEvaluation) -> Iterator[bytes]:
    """Yield canonical UTF-8 JSONL response records without joining the stream."""
    validated = revalidate_model(
        evaluation,
        SpatialResponseEvaluation,
        label="spatial response evaluation",
    )
    for record in validated.records:
        yield (canonical_json(record) + "\n").encode("utf-8")


def spatial_response_state_document(
    evaluation: SpatialResponseEvaluation,
) -> SpatialResponseStateDocument:
    """Project a complete immutable final-state document from one evaluation."""
    evaluation = revalidate_model(
        evaluation,
        SpatialResponseEvaluation,
        label="spatial response evaluation",
    )
    return SpatialResponseStateDocument(
        response_input_sha256=evaluation.response_input_sha256,
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        states=evaluation.final_states,
    )


def summarize_spatial_response_artifact(
    evaluation: SpatialResponseEvaluation,
) -> SpatialResponseArtifactSummary:
    """Hash canonical response/state bytes while enforcing their hard ceilings."""
    evaluation = revalidate_model(
        evaluation,
        SpatialResponseEvaluation,
        label="spatial response evaluation",
    )
    digest = sha256()
    stream_bytes = 0
    for line in spatial_response_lines(evaluation):
        stream_bytes += len(line)
        if stream_bytes > MAX_SPATIAL_RESPONSE_STREAM_BYTES:
            raise ValueError("spatial response stream exceeds its size limit")
        digest.update(line)

    state_document = spatial_response_state_document(evaluation)
    state_bytes = (canonical_json(state_document) + "\n").encode("utf-8")
    if len(state_bytes) > MAX_SPATIAL_RESPONSE_STATE_BYTES:
        raise ValueError("spatial response state document exceeds its size limit")
    summary = SpatialResponseArtifactSummary(
        response_input_sha256=evaluation.response_input_sha256,
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        attention_seed=evaluation.attention_seed,
        stream_sha256=digest.hexdigest(),
        stream_bytes=stream_bytes,
        state_document_sha256=sha256(state_bytes).hexdigest(),
        state_document_bytes=len(state_bytes),
        counts=evaluation.counts,
    )
    summary_bytes = (canonical_json(summary) + "\n").encode("utf-8")
    if len(summary_bytes) > MAX_SPATIAL_RESPONSE_SUMMARY_BYTES:
        raise ValueError("spatial response summary exceeds its size limit")
    return summary


__all__ = [
    "MAX_SPATIAL_RESPONSES",
    "MAX_SPATIAL_RESPONSE_RECORDS",
    "MAX_SPATIAL_RESPONSE_STATE_BYTES",
    "MAX_SPATIAL_RESPONSE_STREAM_BYTES",
    "MAX_SPATIAL_RESPONSE_SUMMARY_BYTES",
    "MAX_SPATIAL_STATE_UPDATES",
    "SpatialResponseArtifactSummary",
    "SpatialResponseCounts",
    "SpatialResponseEvaluation",
    "SpatialResponseRecord",
    "SpatialResponseState",
    "SpatialResponseStateDocument",
    "SpatialRuleResponse",
    "SpatialStateUpdated",
    "evaluate_spatial_responses",
    "spatial_response_lines",
    "spatial_response_state_document",
    "summarize_spatial_response_artifact",
]
