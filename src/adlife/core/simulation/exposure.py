from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.campaign import (
    Campaign,
    PhonePlacement,
    Placement,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import DomainModel, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.engine import UnboundRun, canonical_sha256, stable_event_id
from adlife.core.simulation.movement import Snapshot
from adlife.core.simulation.policies import clamp, notice_probability
from adlife.core.simulation.rng import RandomOracle

_PLACEMENT_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


def _exact_json_tree_equal(left: object, right: object) -> bool:
    if isinstance(left, Mapping):
        if not isinstance(right, Mapping):
            return False
        if frozenset(left) != frozenset(right):
            return False
        return all(_exact_json_tree_equal(left[key], right[key]) for key in left)
    if isinstance(right, Mapping):
        return False

    if isinstance(left, (list, tuple)):
        if not isinstance(right, (list, tuple)) or len(left) != len(right):
            return False
        return all(
            _exact_json_tree_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(right, (list, tuple)):
        return False

    return type(left) is type(right) and left == right


class ExposureOpportunity(DomainModel):
    schema_version: Literal[1] = 1
    profile: PersonProfile
    state: ConsumerState
    campaign: Campaign
    placement: Placement
    placement_identity: str = Field(pattern=_PLACEMENT_DIGEST_PATTERN)
    sampling_identity: str = Field(pattern=_PLACEMENT_DIGEST_PATTERN)
    simulated_minute: int = Field(ge=0)
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    event_sequence_start: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.profile.agent_id != self.state.agent_id:
            raise ValueError("opportunity profile and state must identify the same agent")
        if self.placement not in self.campaign.placements:
            raise ValueError("opportunity placement must belong to its campaign")
        if self.placement_identity != _placement_identity(self.placement):
            raise ValueError("placement_identity must match the normalized placement")
        if self.sampling_identity != _sampling_identity(self.placement):
            raise ValueError("sampling_identity must match the semantic placement")
        if (
            not self.campaign.start_minute <= self.simulated_minute < self.campaign.end_minute
            or not _window_is_active(self.placement, self.simulated_minute)
            or not _channel_is_eligible(self.state, self.placement)
            or self.state.exposure_count(self.campaign_id, self.channel)
            >= self.placement.frequency_cap
        ):
            raise ValueError("opportunity must represent an eligible placement")
        return self

    @property
    def agent_id(self) -> str:
        return self.profile.agent_id

    @property
    def campaign_id(self) -> str:
        return self.campaign.campaign_id

    @property
    def channel(self) -> str:
        return self.placement.channel


class AttentionDecision(DomainModel):
    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal[1] = 1
    opportunity: ExposureOpportunity
    notice_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    notice_probability: float = Field(ge=0, le=1)
    random_draw: float = Field(ge=0, lt=1)
    noticed: bool
    events: tuple[DomainEvent, DomainEvent, DomainEvent]

    @field_validator("opportunity", mode="after")
    @classmethod
    def revalidate_opportunity(cls, value: ExposureOpportunity) -> ExposureOpportunity:
        return revalidate_model(value, ExposureOpportunity, label="opportunity")

    @field_validator("events", mode="after")
    @classmethod
    def revalidate_events(
        cls,
        value: tuple[DomainEvent, DomainEvent, DomainEvent],
    ) -> tuple[DomainEvent, DomainEvent, DomainEvent]:
        first, second, third = value
        return (
            revalidate_model(first, DomainEvent, label="attention event"),
            revalidate_model(second, DomainEvent, label="attention event"),
            revalidate_model(third, DomainEvent, label="attention event"),
        )

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if not isfinite(self.notice_probability) or not isfinite(self.random_draw):
            raise ValueError("attention probability and draw must be finite")
        if self.noticed != (self.random_draw < self.notice_probability):
            raise ValueError("noticed must equal random_draw < notice_probability")
        expected_probability = clamp(
            self.notice_scale
            * notice_probability(
                self.opportunity.profile,
                self.opportunity.state,
                self.opportunity.campaign,
                self.opportunity.placement,
            ),
            0.0,
            1.0,
        )
        if self.notice_probability != expected_probability:
            raise ValueError("notice_probability must match the attention policy")

        eligible, impression, _terminal = self.events
        expected_types = (
            EventType.CAMPAIGN_ELIGIBLE,
            EventType.CAMPAIGN_IMPRESSION,
            EventType.CAMPAIGN_NOTICED if self.noticed else EventType.CAMPAIGN_IGNORED,
        )
        if tuple(event.event_type for event in self.events) != expected_types:
            raise ValueError("attention events must form the expected ordered event chain")

        expected_sequences = tuple(
            self.opportunity.event_sequence_start + offset for offset in range(3)
        )
        if tuple(event.sequence for event in self.events) != expected_sequences:
            raise ValueError("attention event sequences must be contiguous and preallocated")
        expected_ids = tuple(
            stable_event_id(self.opportunity.run_id, sequence) for sequence in expected_sequences
        )
        if tuple(event.event_id for event in self.events) != expected_ids:
            raise ValueError("attention event IDs must match run and sequence")

        for event in self.events:
            if (
                event.run_id != self.opportunity.run_id
                or event.simulated_minute != self.opportunity.simulated_minute
                or event.agent_id != self.opportunity.agent_id
                or event.campaign_id != self.opportunity.campaign_id
                or event.channel != self.opportunity.channel
                or event.source != EventSource.RULE
                or event.model_id is not None
                or event.prompt_hash is not None
            ):
                raise ValueError("attention event metadata must match its opportunity")

        delivery = (
            "rendered" if isinstance(self.opportunity.placement, PhonePlacement) else "crossed"
        )
        expected_payloads = (
            dict(_eligibility_payload(self.opportunity)),
            {
                "delivery": delivery,
                "placement_id": self.opportunity.placement_identity,
            },
            {
                "notice_probability": self.notice_probability,
                "placement_id": self.opportunity.placement_identity,
                "random_draw": self.random_draw,
            },
        )
        if any(
            not _exact_json_tree_equal(event.payload, expected)
            for event, expected in zip(self.events, expected_payloads, strict=True)
        ):
            raise ValueError("attention event payloads must match the decision")

        expected_causes = ((), (eligible.event_id,), (impression.event_id,))
        if tuple(event.caused_by_event_ids for event in self.events) != expected_causes:
            raise ValueError("attention event causal links must form the expected chain")
        return self


class ExposureCursor(DomainModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    simulated_minute: int = Field(ge=0)
    next_event_sequence: int = Field(ge=0)


class ExposureBatch(DomainModel):
    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal[1] = 1
    start_cursor: ExposureCursor
    preceding_events: tuple[DomainEvent, ...]
    opportunities: tuple[ExposureOpportunity, ...]
    next_cursor: ExposureCursor

    @field_validator("start_cursor", "next_cursor", mode="after")
    @classmethod
    def revalidate_cursor(cls, value: ExposureCursor) -> ExposureCursor:
        return revalidate_model(value, ExposureCursor, label="exposure cursor")

    @field_validator("preceding_events", mode="after")
    @classmethod
    def revalidate_preceding_events(
        cls,
        value: tuple[DomainEvent, ...],
    ) -> tuple[DomainEvent, ...]:
        return tuple(
            revalidate_model(event, DomainEvent, label="preceding movement event")
            for event in value
        )

    @field_validator("opportunities", mode="after")
    @classmethod
    def revalidate_opportunities(
        cls,
        value: tuple[ExposureOpportunity, ...],
    ) -> tuple[ExposureOpportunity, ...]:
        return tuple(
            revalidate_model(item, ExposureOpportunity, label="exposure opportunity")
            for item in value
        )

    @model_validator(mode="after")
    def validate_allocation(self) -> Self:
        if (
            self.next_cursor.run_id != self.start_cursor.run_id
            or self.next_cursor.simulated_minute != self.start_cursor.simulated_minute
        ):
            raise ValueError("exposure cursors must identify one run and minute")

        movement_types = {EventType.ACTIVITY_CHANGED, EventType.LOCATION_CHANGED}
        for offset, event in enumerate(self.preceding_events):
            sequence = self.start_cursor.next_event_sequence + offset
            if event.sequence != sequence or event.event_id != stable_event_id(
                self.start_cursor.run_id,
                sequence,
            ):
                raise ValueError("preceding movement events must be contiguous and stable")
            if (
                event.run_id != self.start_cursor.run_id
                or event.simulated_minute != self.start_cursor.simulated_minute
                or event.event_type not in movement_types
                or event.source != EventSource.RULE
                or event.campaign_id is not None
                or event.channel is not None
                or event.model_id is not None
                or event.prompt_hash is not None
                or event.caused_by_event_ids
            ):
                raise ValueError("preceding events must be canonical movement events")

        exposure_start = self.start_cursor.next_event_sequence + len(self.preceding_events)
        campaign_definitions: dict[str, str] = {}
        semantic_keys: set[tuple[str, str, str, int]] = set()
        for opportunity in self.opportunities:
            campaign_identity = _campaign_identity(opportunity.campaign)
            previous_identity = campaign_definitions.setdefault(
                opportunity.campaign_id,
                campaign_identity,
            )
            if previous_identity != campaign_identity:
                raise ValueError("one campaign_id has more than one campaign definition")
            semantic_key = (
                opportunity.agent_id,
                opportunity.campaign_id,
                opportunity.sampling_identity,
                opportunity.simulated_minute,
            )
            if semantic_key in semantic_keys:
                raise ValueError("exposure batch contains a duplicate semantic opportunity")
            semantic_keys.add(semantic_key)

        expected_order = tuple(sorted(self.opportunities, key=_opportunity_sort_key))
        if self.opportunities != expected_order:
            raise ValueError("exposure opportunities must use canonical stage order")
        for index, opportunity in enumerate(self.opportunities):
            if (
                opportunity.run_id != self.start_cursor.run_id
                or opportunity.simulated_minute != self.start_cursor.simulated_minute
                or opportunity.event_sequence_start != exposure_start + index * 3
            ):
                raise ValueError("exposure opportunity allocation must match the stage cursor")

        expected_next = exposure_start + len(self.opportunities) * 3
        if self.next_cursor.next_event_sequence != expected_next:
            raise ValueError("next exposure cursor must follow the complete batch")
        return self


def _normalized_windows(placement: Placement) -> tuple[Mapping[str, int], ...]:
    return tuple(
        {
            "start_minute_of_day": window.start_minute_of_day,
            "end_minute_of_day": window.end_minute_of_day,
        }
        for window in sorted(
            placement.active_windows,
            key=lambda window: (window.start_minute_of_day, window.end_minute_of_day),
        )
    )


def _placement_identity(placement: Placement) -> str:
    normalized: dict[str, object] = {
        field_name: getattr(placement, field_name) for field_name in type(placement).model_fields
    }
    normalized["active_windows"] = _normalized_windows(placement)
    return canonical_sha256(normalized)


def _campaign_identity(campaign: Campaign) -> str:
    normalized: dict[str, object] = {
        field_name: getattr(campaign, field_name)
        for field_name in type(campaign).model_fields
        if field_name != "placements"
    }
    normalized["placements"] = tuple(
        sorted(_placement_identity(placement) for placement in campaign.placements)
    )
    return canonical_sha256(normalized)


def _sampling_identity(placement: Placement) -> str:
    normalized: dict[str, object] = {
        "channel": placement.channel,
        "active_windows": _normalized_windows(placement),
    }
    if isinstance(placement, PhonePlacement):
        normalized["zone"] = placement.zone
    else:
        normalized["route_id"] = placement.route_id
    return canonical_sha256(normalized)


def _opportunity_sort_key(
    opportunity: ExposureOpportunity,
) -> tuple[str, str, str, str]:
    return (
        opportunity.agent_id,
        opportunity.campaign_id,
        opportunity.placement_identity,
        opportunity.sampling_identity,
    )


def _window_is_active(placement: Placement, minute: int) -> bool:
    minute_of_day = minute % 1440
    return any(
        window.start_minute_of_day <= minute_of_day < window.end_minute_of_day
        for window in placement.active_windows
    )


def _channel_is_eligible(
    state: ConsumerState,
    placement: Placement,
) -> bool:
    if isinstance(placement, PhonePlacement):
        return state.activity == "phone-check" and state.location == placement.zone
    return state.activity == "commute" and state.current_route_id == placement.route_id


def _revalidate_snapshot(snapshot: Snapshot) -> Snapshot:
    if not isinstance(snapshot, Snapshot):
        raise TypeError("snapshot must be a Snapshot")
    if not isinstance(snapshot.agents, Mapping):
        raise TypeError("snapshot agents must be a mapping")
    agents: dict[str, tuple[PersonProfile, ConsumerState]] = {}
    for agent_id, pair in snapshot.agents.items():
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError("snapshot agent values must be profile/state tuples")
        profile, state = pair
        agents[agent_id] = (
            revalidate_model(profile, PersonProfile, label="snapshot profile"),
            revalidate_model(state, ConsumerState, label="snapshot state"),
        )
    return Snapshot(
        agents=agents,
        simulated_minute=snapshot.simulated_minute,
        run_id=snapshot.run_id,
        next_event_sequence=snapshot.next_event_sequence,
        version=snapshot.version,
    )


def _movement_event_key(event: DomainEvent) -> tuple[int, str]:
    if event.agent_id is None:
        raise ValueError("movement event must identify an agent")
    ranks = {
        EventType.ACTIVITY_CHANGED: 0,
        EventType.LOCATION_CHANGED: 1,
    }
    try:
        return ranks[event.event_type], event.agent_id
    except KeyError as error:
        raise ValueError("movement event has an unsupported event type") from error


def _require_payload_keys(
    payload: Mapping[str, object],
    expected: frozenset[str],
) -> None:
    if frozenset(payload) != expected:
        raise ValueError("movement event payload is incomplete or contains unknown fields")


def _project_movement_snapshot(
    snapshot: Snapshot,
    events: tuple[DomainEvent, ...],
) -> Snapshot:
    keys = tuple(_movement_event_key(event) for event in events)
    if keys != tuple(sorted(keys)):
        raise ValueError("movement events are not in canonical order by agent and minute")
    if len(keys) != len(set(keys)):
        raise ValueError("movement events contain a duplicate agent update")

    states = {agent_id: state for agent_id, (_, state) in snapshot.agents.items()}
    for offset, event in enumerate(events):
        expected_sequence = snapshot.next_event_sequence + offset
        if event.sequence != expected_sequence or event.event_id != stable_event_id(
            snapshot.run_id or "",
            expected_sequence,
        ):
            raise ValueError("movement events must have contiguous stable sequences")
        if (
            event.run_id != snapshot.run_id
            or event.simulated_minute != snapshot.simulated_minute
            or event.source != EventSource.RULE
            or event.campaign_id is not None
            or event.channel is not None
            or event.model_id is not None
            or event.prompt_hash is not None
            or event.caused_by_event_ids
        ):
            raise ValueError("movement event metadata does not match the pre-tick snapshot")
        agent_id = event.agent_id
        if agent_id is None or agent_id not in states:
            raise ValueError("movement event agent is missing from the pre-tick snapshot")
        state = states[agent_id]
        payload = event.payload

        if event.event_type == EventType.ACTIVITY_CHANGED:
            _require_payload_keys(payload, frozenset({"from_activity", "to_activity"}))
            from_activity = payload["from_activity"]
            to_activity = payload["to_activity"]
            if type(from_activity) is not str or type(to_activity) is not str:
                raise ValueError("movement activity payload values must be strings")
            if from_activity != state.activity:
                raise ValueError("movement from_activity contradicts projected state")
            if to_activity == from_activity:
                raise ValueError("movement activity event must change activity")
            try:
                states[agent_id] = state.model_copy(update={"activity": to_activity})
            except ValueError as error:
                raise ValueError("movement to_activity is invalid") from error
            continue

        _require_payload_keys(
            payload,
            frozenset(
                {
                    "from_zone",
                    "to_zone",
                    "route_id",
                    "from_route_id",
                    "to_route_id",
                }
            ),
        )
        from_zone = payload["from_zone"]
        to_zone = payload["to_zone"]
        route_id = payload["route_id"]
        from_route_id = payload["from_route_id"]
        to_route_id = payload["to_route_id"]
        if type(from_zone) is not str or type(to_zone) is not str:
            raise ValueError("movement zone payload values must be strings")
        if any(
            value is not None and type(value) is not str
            for value in (route_id, from_route_id, to_route_id)
        ):
            raise ValueError("movement route payload values must be strings or null")
        if route_id != to_route_id:
            raise ValueError("movement route_id must match to_route_id")
        if from_zone != state.location or from_route_id != state.current_route_id:
            raise ValueError("movement origin contradicts projected state")
        if to_zone == from_zone and to_route_id == from_route_id:
            raise ValueError("movement location event must change zone or route")
        try:
            states[agent_id] = state.model_copy(
                update={
                    "location": to_zone,
                    "current_route_id": to_route_id,
                }
            )
        except ValueError as error:
            raise ValueError("movement destination is invalid") from error

    return Snapshot(
        agents={
            agent_id: (profile, states[agent_id])
            for agent_id, (profile, _) in snapshot.agents.items()
        },
        simulated_minute=snapshot.simulated_minute,
        run_id=snapshot.run_id,
        next_event_sequence=snapshot.next_event_sequence + len(events),
        version=snapshot.version,
    )


def eligible_placements(
    snapshot: Snapshot,
    campaign: Campaign,
    minute: int,
) -> tuple[ExposureOpportunity, ...]:
    """Return one campaign's preallocated convenience opportunities.

    Persisted runs allocate every campaign together with allocate_exposure_batch.
    """
    snapshot = _revalidate_snapshot(snapshot)
    campaign = revalidate_model(campaign, Campaign, label="campaign")
    sampling_identities = tuple(_sampling_identity(placement) for placement in campaign.placements)
    if len(sampling_identities) != len(set(sampling_identities)):
        raise ValueError(
            "placements in one campaign must have a distinct semantic sampling identity"
        )
    if type(minute) is not int or minute < 0:
        raise ValueError("minute must be a nonnegative integer")
    if snapshot.run_id is None:
        raise UnboundRun("snapshot event stream is unbound; call bind_run before exposure")
    if not campaign.start_minute <= minute < campaign.end_minute:
        return ()

    placements = tuple(
        sorted(
            (
                (_sampling_identity(placement), _placement_identity(placement), placement)
                for placement in campaign.placements
            ),
            key=lambda item: (item[0], item[1]),
        )
    )
    candidates: list[tuple[str, PersonProfile, ConsumerState, str, str, Placement]] = []
    projected_counts: dict[tuple[str, str], int] = {}
    for agent_id, (profile, state) in snapshot.agents.items():
        for sampling_identity, placement_identity, placement in placements:
            if not _window_is_active(placement, minute):
                continue
            if not _channel_is_eligible(state, placement):
                continue
            count_key = (agent_id, placement.channel)
            projected_count = projected_counts.get(
                count_key,
                state.exposure_count(campaign.campaign_id, placement.channel),
            )
            if projected_count >= placement.frequency_cap:
                continue
            candidates.append(
                (
                    agent_id,
                    profile,
                    state,
                    sampling_identity,
                    placement_identity,
                    placement,
                )
            )
            projected_counts[count_key] = projected_count + 1

    opportunities = []
    for index, (
        _,
        profile,
        state,
        sampling_identity,
        placement_identity,
        placement,
    ) in enumerate(sorted(candidates, key=lambda item: (item[0], item[4], item[3]))):
        opportunities.append(
            ExposureOpportunity(
                profile=profile,
                state=state,
                campaign=campaign,
                placement=placement,
                placement_identity=placement_identity,
                sampling_identity=sampling_identity,
                simulated_minute=minute,
                run_id=snapshot.run_id,
                event_sequence_start=snapshot.next_event_sequence + index * 3,
            )
        )
    return tuple(opportunities)


def allocate_exposure_batch(
    snapshot: Snapshot,
    campaigns: Sequence[Campaign],
    minute: int,
    *,
    preceding_events: Sequence[DomainEvent] = (),
) -> ExposureBatch:
    """Project canonical movement from a pre-tick snapshot, then allocate exposure.

    The start cursor belongs to the supplied pre-tick snapshot. The commit pipeline can
    pass its planned movement events directly; opportunities contain the projected
    post-movement
    state and begin after every movement sequence.
    """
    snapshot = _revalidate_snapshot(snapshot)
    if snapshot.run_id is None:
        raise UnboundRun("snapshot event stream is unbound; call bind_run before exposure")
    if type(minute) is not int or minute < 0:
        raise ValueError("minute must be a nonnegative integer")
    if minute != snapshot.simulated_minute:
        raise ValueError("exposure stage minute must match snapshot simulated_minute")
    if isinstance(campaigns, (str, bytes)) or not isinstance(campaigns, Sequence):
        raise TypeError("campaigns must be a sequence of Campaign values")
    checked_campaigns = tuple(
        revalidate_model(campaign, Campaign, label="campaign") for campaign in campaigns
    )
    campaign_ids = tuple(campaign.campaign_id for campaign in checked_campaigns)
    if len(campaign_ids) != len(set(campaign_ids)):
        raise ValueError("exposure batch campaign IDs must be unique")

    if isinstance(preceding_events, (str, bytes)) or not isinstance(
        preceding_events,
        Sequence,
    ):
        raise TypeError("preceding_events must be a sequence of DomainEvent values")
    checked_preceding = tuple(
        sorted(
            (
                revalidate_model(event, DomainEvent, label="preceding movement event")
                for event in preceding_events
            ),
            key=lambda event: (event.sequence, event.event_id),
        )
    )
    projected_snapshot = _project_movement_snapshot(snapshot, checked_preceding)

    candidates = tuple(
        opportunity
        for campaign in sorted(checked_campaigns, key=lambda item: item.campaign_id)
        for opportunity in eligible_placements(projected_snapshot, campaign, minute)
    )
    ordered = tuple(sorted(candidates, key=_opportunity_sort_key))
    exposure_start = snapshot.next_event_sequence + len(checked_preceding)
    opportunities = tuple(
        opportunity.model_copy(update={"event_sequence_start": exposure_start + index * 3})
        for index, opportunity in enumerate(ordered)
    )
    next_sequence = exposure_start + len(opportunities) * 3
    return ExposureBatch(
        start_cursor=ExposureCursor(
            run_id=snapshot.run_id,
            simulated_minute=minute,
            next_event_sequence=snapshot.next_event_sequence,
        ),
        preceding_events=checked_preceding,
        opportunities=opportunities,
        next_cursor=ExposureCursor(
            run_id=snapshot.run_id,
            simulated_minute=minute,
            next_event_sequence=next_sequence,
        ),
    )


def _eligibility_payload(opportunity: ExposureOpportunity) -> Mapping[str, object]:
    placement = opportunity.placement
    payload: dict[str, object] = {
        "frequency_cap": placement.frequency_cap,
        "placement_id": opportunity.placement_identity,
        "prior_exposures": opportunity.state.exposure_count(
            opportunity.campaign_id,
            opportunity.channel,
        ),
        "visibility": placement.visibility,
    }
    if isinstance(placement, PhonePlacement):
        payload["zone"] = placement.zone
    else:
        payload["route_id"] = placement.route_id
    return payload


def _event(
    opportunity: ExposureOpportunity,
    *,
    offset: int,
    event_type: EventType,
    payload: Mapping[str, object],
    caused_by_event_ids: tuple[str, ...] = (),
) -> DomainEvent:
    sequence = opportunity.event_sequence_start + offset
    return DomainEvent(
        event_id=stable_event_id(opportunity.run_id, sequence),
        run_id=opportunity.run_id,
        simulated_minute=opportunity.simulated_minute,
        sequence=sequence,
        event_type=event_type,
        agent_id=opportunity.agent_id,
        campaign_id=opportunity.campaign_id,
        channel=opportunity.channel,
        payload=payload,
        source=EventSource.RULE,
        caused_by_event_ids=caused_by_event_ids,
    )


def decide_attention(
    opportunity: ExposureOpportunity,
    oracle: RandomOracle,
    *,
    notice_scale: float = 1.0,
) -> AttentionDecision:
    opportunity = revalidate_model(
        opportunity,
        ExposureOpportunity,
        label="opportunity",
    )
    if not isinstance(oracle, RandomOracle):
        raise TypeError("oracle must be a RandomOracle")

    probability = clamp(
        notice_scale
        * notice_probability(
            opportunity.profile,
            opportunity.state,
            opportunity.campaign,
            opportunity.placement,
        ),
        0.0,
        1.0,
    )
    namespace = (
        f"campaign-attention:{opportunity.campaign_id}:{opportunity.channel}:"
        f"{opportunity.sampling_identity}"
    )
    draw = oracle.uniform(
        namespace,
        opportunity.agent_id,
        opportunity.simulated_minute,
        0,
    )
    if not isinstance(draw, float) or not isfinite(draw) or not 0.0 <= draw < 1.0:
        raise ValueError("RandomOracle draw must be a finite float in [0, 1)")
    noticed = draw < probability

    eligible = _event(
        opportunity,
        offset=0,
        event_type=EventType.CAMPAIGN_ELIGIBLE,
        payload=_eligibility_payload(opportunity),
    )
    delivery = "rendered" if isinstance(opportunity.placement, PhonePlacement) else "crossed"
    impression = _event(
        opportunity,
        offset=1,
        event_type=EventType.CAMPAIGN_IMPRESSION,
        payload={
            "delivery": delivery,
            "placement_id": opportunity.placement_identity,
        },
        caused_by_event_ids=(eligible.event_id,),
    )
    terminal = _event(
        opportunity,
        offset=2,
        event_type=(EventType.CAMPAIGN_NOTICED if noticed else EventType.CAMPAIGN_IGNORED),
        payload={
            "notice_probability": probability,
            "placement_id": opportunity.placement_identity,
            "random_draw": draw,
        },
        caused_by_event_ids=(impression.event_id,),
    )
    return AttentionDecision(
        opportunity=opportunity,
        notice_scale=notice_scale,
        notice_probability=probability,
        random_draw=draw,
        noticed=noticed,
        events=(eligible, impression, terminal),
    )


def decide_attention_batch(
    batch: ExposureBatch,
    oracle: RandomOracle,
    *,
    notice_scale: float = 1.0,
) -> tuple[AttentionDecision, ...]:
    """Evaluate a preallocated persisted exposure batch in canonical order."""
    batch = revalidate_model(batch, ExposureBatch, label="exposure batch")
    if not isinstance(oracle, RandomOracle):
        raise TypeError("oracle must be a RandomOracle")
    return tuple(
        decide_attention(opportunity, oracle, notice_scale=notice_scale)
        for opportunity in batch.opportunities
    )


__all__ = [
    "AttentionDecision",
    "ExposureBatch",
    "ExposureCursor",
    "ExposureOpportunity",
    "allocate_exposure_batch",
    "decide_attention",
    "decide_attention_batch",
    "eligible_placements",
]
