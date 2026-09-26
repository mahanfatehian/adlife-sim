from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from math import isfinite
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.person import DomainModel, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Relationship
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.decision import (
    MAX_DAILY_REINFORCEMENT,
    StateTransition,
    jaccard,
)
from adlife.core.simulation.exposure import _project_movement_snapshot
from adlife.core.simulation.memory import MINUTES_PER_DAY
from adlife.core.simulation.movement import Snapshot
from adlife.core.simulation.parameters import ModelParameters
from adlife.core.simulation.policies import clamp
from adlife.core.simulation.rng import RandomOracle

Identifier = Annotated[str, Field(min_length=1, max_length=160)]

SOCIAL_PROOF_GAIN = 0.25
"""How far one trusted message can move a receiver's social proof."""

SOCIAL_RECALL_GAIN = 0.10
"""How far one fully trusted conversation can reinforce a receiver's recall."""


class SocialIntent(DomainModel):
    """One planned word-of-mouth message across exactly one relationship edge."""

    schema_version: Literal[1] = 1
    sender_id: str = Field(pattern=r"^person-[0-9]{3}$")
    receiver_id: str = Field(pattern=r"^person-[0-9]{3}$")
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    relationship_kind: Literal["friend", "colleague", "family", "online"]
    relationship_strength: float = Field(ge=0, le=1)
    valence: float = Field(ge=-1, le=1)
    share_probability: float = Field(gt=0, le=1)
    random_draw: float = Field(ge=0, lt=1)
    simulated_minute: int = Field(ge=0)
    caused_by_event_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if self.sender_id == self.receiver_id:
            raise ValueError("an agent cannot share with itself")
        if len(self.caused_by_event_ids) != len(set(self.caused_by_event_ids)):
            raise ValueError("causal event identifiers must be unique")
        if self.random_draw >= self.share_probability:
            raise ValueError("random_draw must be below share_probability")
        return self


def _validated_events(
    noticed_events: Sequence[DomainEvent],
    snapshot: Snapshot,
) -> tuple[DomainEvent, ...]:
    if isinstance(noticed_events, (str, bytes)) or not isinstance(noticed_events, Sequence):
        raise TypeError("noticed_events must be a sequence of domain events")
    validated: list[DomainEvent] = []
    for candidate in noticed_events:
        event = revalidate_model(candidate, DomainEvent, label="noticed event")
        if event.event_type is not EventType.CAMPAIGN_NOTICED:
            raise ValueError("social planning accepts campaign.noticed events only")
        if event.agent_id is None or event.campaign_id is None:
            raise ValueError("a noticed event must identify an agent and a campaign")
        if event.run_id != snapshot.run_id or event.simulated_minute != snapshot.simulated_minute:
            raise ValueError("noticed event metadata does not match the pre-tick snapshot")
        if event.agent_id not in snapshot.agents:
            raise ValueError(f"noticed event agent {event.agent_id} is outside the snapshot")
        validated.append(event)
    return tuple(
        sorted(
            validated,
            key=lambda event: (event.agent_id or "", event.campaign_id or "", event.event_id),
        )
    )


def _validated_relationships(
    relationships: Sequence[Relationship],
    snapshot: Snapshot,
) -> tuple[Relationship, ...]:
    if isinstance(relationships, (str, bytes)) or not isinstance(relationships, Sequence):
        raise TypeError("relationships must be a sequence of Relationship values")
    seen: set[tuple[str, str]] = set()
    validated: list[Relationship] = []
    for candidate in relationships:
        relationship = revalidate_model(candidate, Relationship, label="relationship")
        if relationship.source_id == relationship.target_id:
            raise ValueError("a self-relationship cannot carry a message")
        for endpoint in (relationship.source_id, relationship.target_id):
            if endpoint not in snapshot.agents:
                raise ValueError(f"relationship endpoint {endpoint} is outside the snapshot")
        edge = (
            min(relationship.source_id, relationship.target_id),
            max(relationship.source_id, relationship.target_id),
        )
        if edge in seen:
            raise ValueError(f"duplicate relationship: {edge[0]} and {edge[1]}")
        seen.add(edge)
        validated.append(relationship)
    return tuple(sorted(validated, key=lambda item: (item.source_id, item.target_id, item.kind)))


def _validated_share_signals(
    share_signals: Mapping[str, float] | None,
    events: tuple[DomainEvent, ...],
) -> dict[str, float]:
    """Validate the cognition share probability supplied for each noticed event."""
    if share_signals is None:
        return {}
    if not isinstance(share_signals, Mapping):
        raise TypeError("share_signals must map a noticed event id to a share probability")
    known = {event.event_id for event in events}
    validated: dict[str, float] = {}
    for event_id in sorted(share_signals):
        if not isinstance(event_id, str) or event_id not in known:
            raise ValueError(f"share signal {event_id!r} has no matching noticed event")
        value = share_signals[event_id]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("a share signal must be a finite number")
        numeric = float(value)
        if not isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ValueError("a share signal must be within [0, 1]")
        validated[event_id] = numeric
    return validated


def day_index(simulated_minute: int) -> int:
    """The simulated day a minute belongs to; the scope of the one-edge bound."""
    if type(simulated_minute) is not int or simulated_minute < 0:
        raise ValueError("simulated_minute must be a nonnegative integer")
    return simulated_minute // MINUTES_PER_DAY


def _edge_key(
    campaign_id: str,
    first: str,
    second: str,
    simulated_minute: int,
) -> tuple[int, str, str, str]:
    return (day_index(simulated_minute), campaign_id, min(first, second), max(first, second))


def social_edge_key(intent: SocialIntent) -> tuple[int, str, str, str]:
    """The simulated day, campaign and unordered agent pair one message traverses.

    The day is part of the key, so an accumulator a caller carries across ticks bounds a
    message to one edge per simulated day rather than per tick, and yesterday's entries
    are inert today whether or not the caller cleared them.
    """
    intent = revalidate_model(intent, SocialIntent, label="social intent")
    return _edge_key(
        intent.campaign_id,
        intent.sender_id,
        intent.receiver_id,
        intent.simulated_minute,
    )


def social_message_key(intent: SocialIntent) -> tuple[int, str, str]:
    """The simulated day, campaign and sender whose one message an intent advances."""
    intent = revalidate_model(intent, SocialIntent, label="social intent")
    return (day_index(intent.simulated_minute), intent.campaign_id, intent.sender_id)


def _validated_carried_messages(
    carried_messages: Collection[tuple[int, str, str]],
) -> set[tuple[int, str, str]]:
    """Normalize the messages that already advanced, each scoped to its simulated day."""
    if isinstance(carried_messages, (str, bytes)) or not isinstance(carried_messages, Collection):
        raise TypeError("carried_messages must be a collection of day, campaign and sender")
    normalized: set[tuple[int, str, str]] = set()
    for candidate in carried_messages:
        if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
            raise TypeError("a carried message must be a day, campaign and sender triple")
        entry = tuple(candidate)
        if len(entry) != 3:
            raise ValueError("a carried message must name a day, a campaign and a sender")
        day, campaign_id, sender_id = entry
        if type(day) is not int or day < 0:
            raise ValueError("a carried message must name a nonnegative simulated day")
        if not isinstance(campaign_id, str) or not campaign_id:
            raise ValueError("a carried message must name a campaign and a sender")
        if not isinstance(sender_id, str) or not sender_id:
            raise ValueError("a carried message must name a campaign and a sender")
        normalized.add((day, campaign_id, sender_id))
    return normalized


def _validated_traversed_edges(
    traversed_edges: Collection[tuple[int, str, str, str]],
) -> set[tuple[int, str, str, str]]:
    """Normalize the edges a campaign message crossed, each scoped to its simulated day."""
    if isinstance(traversed_edges, (str, bytes)) or not isinstance(traversed_edges, Collection):
        raise TypeError("traversed_edges must be a collection of day, campaign and agent entries")
    normalized: set[tuple[int, str, str, str]] = set()
    for candidate in traversed_edges:
        if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
            raise TypeError("a traversed edge must be a day, campaign and agent quadruple")
        entry = tuple(candidate)
        if len(entry) != 4:
            raise ValueError("a traversed edge must name a day, a campaign and two agents")
        day, campaign_id, first, second = entry
        if type(day) is not int or day < 0:
            raise ValueError("a traversed edge must name a nonnegative simulated day")
        if not isinstance(campaign_id, str) or not campaign_id:
            raise ValueError("a traversed edge must name a campaign and two agents")
        if not isinstance(first, str) or not first:
            raise ValueError("a traversed edge must name a campaign and two agents")
        if not isinstance(second, str) or not second:
            raise ValueError("a traversed edge must name a campaign and two agents")
        if first == second:
            raise ValueError("a traversed edge must name two different agents")
        normalized.add((day, campaign_id, min(first, second), max(first, second)))
    return normalized


def _projected_snapshot(
    snapshot: Snapshot,
    movement_events: Sequence[DomainEvent] | None,
) -> Snapshot:
    """Project the movement of this tick, the frame the noticed events belong to.

    Exposure allocation runs against the post-movement state, so a noticed event at this
    minute already reflects the move. Reusing the exposure stage's canonical projection
    keeps social
    contact in the same frame instead of one movement behind it.
    """
    if movement_events is None:
        return snapshot
    if isinstance(movement_events, (str, bytes)) or not isinstance(movement_events, Sequence):
        raise TypeError("movement_events must be a sequence of domain events")
    checked = tuple(
        sorted(
            (
                revalidate_model(event, DomainEvent, label="movement event")
                for event in movement_events
            ),
            key=lambda event: (event.sequence, event.event_id),
        )
    )
    return _project_movement_snapshot(snapshot, checked)


def _has_contact_opportunity(
    sender_state: ConsumerState,
    receiver_state: ConsumerState,
    relationship: Relationship,
) -> bool:
    if sender_state.activity == "sleep" or receiver_state.activity == "sleep":
        return False
    if sender_state.location == receiver_state.location:
        return True
    return relationship.kind == "online"


def plan_social_shares(
    snapshot: Snapshot,
    noticed_events: Sequence[DomainEvent],
    oracle: RandomOracle,
    *,
    relationships: Sequence[Relationship] = (),
    share_signals: Mapping[str, float] | None = None,
    movement_events: Sequence[DomainEvent] | None = None,
    traversed_edges: Collection[tuple[int, str, str, str]] | None = None,
    carried_messages: Collection[tuple[int, str, str]] | None = None,
    social_enabled: bool = True,
    parameters: ModelParameters | None = None,
) -> tuple[SocialIntent, ...]:
    """Plan at most one edge per message, and at most one message per simulated day.

    The receiver of a message is drawn from the sender's relationship graph with a keyed
    :class:`RandomOracle` draw over the neighbors this tick can reach and this day has not
    already carried the message to. Selecting the lowest agent identifier instead would
    concentrate every indirect impression on the same few agents.

    The documented share probability combines the cognition result with edge trust, and
    applies each documented factor exactly once. The cognition result already carries the
    sender's novelty seeking and the sentiment magnitude of its reaction - in rule mode it
    is ``abs(sentiment_delta) * social_susceptibility * novelty_seeking`` - so those
    factors are never multiplied in a second time here; edge trust is the relationship
    strength, and it is the only factor this composition adds.

    ``share_signals`` carries the cognition result of a noticed event, keyed by its event
    id: the rule response share probability, or the provider value in LLM mode. It is
    required for every noticed event once a relationship edge is offered, because an
    assumed cognition result would silently maximize sharing.
    ``movement_events`` carries the canonical movement events of this tick, because the
    noticed events were allocated against the projected post-movement state; without them
    contact is decided one movement behind the impression that triggered it.
    ``traversed_edges`` carries the edges a campaign message already crossed earlier in
    the same simulated day and ``carried_messages`` the messages that already advanced, so
    a caller holds the documented one-edge-per-day bound across the ticks of one day.
    Build both with :func:`social_edge_key` and :func:`social_message_key`: each entry
    names the simulated day it belongs to, so the bound is day-scoped by construction and
    an entry from another day can neither block today nor outlive its day.
    All three are required once a relationship edge is offered: an omitted accumulator
    fails open, letting one message hop the same edge once per tick all day, and an
    omitted movement frame silently vetoes or misroutes every transition-tick share.
    """
    if not isinstance(snapshot, Snapshot):
        raise TypeError("snapshot must be a Snapshot")
    if not isinstance(oracle, RandomOracle):
        raise TypeError("oracle must be a RandomOracle")
    if not isinstance(social_enabled, bool):
        raise TypeError("social_enabled must be a bool")
    events = _validated_events(noticed_events, snapshot)
    edges = _validated_relationships(relationships, snapshot)
    signals = _validated_share_signals(share_signals, events)
    carried = _validated_traversed_edges(() if traversed_edges is None else traversed_edges)
    advanced = _validated_carried_messages(() if carried_messages is None else carried_messages)
    contact_snapshot = _projected_snapshot(snapshot, movement_events)
    if not social_enabled:
        return ()
    if edges:
        unsignalled = tuple(event.event_id for event in events if event.event_id not in signals)
        if unsignalled:
            raise ValueError(
                f"noticed event {unsignalled[0]} has no cognition share signal; "
                "share_signals must carry the cognition result of every noticed event"
            )
        if movement_events is None:
            raise ValueError(
                "movement_events must carry the canonical movement events of this tick; "
                "pass () when the tick moved nobody"
            )
        if traversed_edges is None:
            raise ValueError(
                "traversed_edges must carry the edges this simulated day already crossed; "
                "pass () at the first tick of the day"
            )
        if carried_messages is None:
            raise ValueError(
                "carried_messages must carry the messages this simulated day already "
                "advanced; pass () at the first tick of the day"
            )

    neighbors: dict[str, list[tuple[str, Relationship]]] = {}
    for relationship in edges:
        neighbors.setdefault(relationship.source_id, []).append(
            (relationship.target_id, relationship)
        )
        neighbors.setdefault(relationship.target_id, []).append(
            (relationship.source_id, relationship)
        )

    candidates: list[SocialIntent] = []
    for event in events:
        sender_id = event.agent_id
        campaign_id = event.campaign_id
        if sender_id is None or campaign_id is None:  # pragma: no cover - validated above
            raise ValueError("a noticed event must identify an agent and a campaign")
        _, sender_state = contact_snapshot.agents[sender_id]
        eligible: list[tuple[str, Relationship]] = []
        for receiver_id, relationship in sorted(
            neighbors.get(sender_id, ()),
            key=lambda item: item[0],
        ):
            _, receiver_state = contact_snapshot.agents[receiver_id]
            if not _has_contact_opportunity(sender_state, receiver_state, relationship):
                continue
            if _edge_key(campaign_id, sender_id, receiver_id, event.simulated_minute) in carried:
                continue
            eligible.append((receiver_id, relationship))
        if not eligible:
            continue
        choice = oracle.uniform(
            f"social-receiver:{campaign_id}",
            sender_id,
            event.simulated_minute,
            0,
        )
        if not isinstance(choice, float) or not isfinite(choice) or not 0.0 <= choice < 1.0:
            raise ValueError("RandomOracle draw must be a finite float in [0, 1)")
        weight = parameters.social_similarity_weight if parameters is not None else 0.0
        if weight > 0.0:
            receiver_id, relationship = _similarity_weighted_choice(
                contact_snapshot,
                sender_id,
                eligible,
                choice,
                weight,
            )
        else:
            receiver_id, relationship = eligible[
                min(int(choice * len(eligible)), len(eligible) - 1)
            ]
        cognition = signals[event.event_id]
        share_scale = parameters.share_probability_scale if parameters is not None else 1.0
        probability = clamp(cognition * share_scale * relationship.strength, 0.0, 1.0)
        if probability <= 0.0:
            continue
        draw = oracle.uniform(
            f"social-share:{campaign_id}:{receiver_id}",
            sender_id,
            event.simulated_minute,
            0,
        )
        if not isinstance(draw, float) or not isfinite(draw) or not 0.0 <= draw < 1.0:
            raise ValueError("RandomOracle draw must be a finite float in [0, 1)")
        if draw >= probability:
            continue
        candidates.append(
            SocialIntent(
                sender_id=sender_id,
                receiver_id=receiver_id,
                campaign_id=campaign_id,
                relationship_kind=relationship.kind,
                relationship_strength=relationship.strength,
                valence=clamp(sender_state.brand_sentiment, -1.0, 1.0),
                share_probability=probability,
                random_draw=draw,
                simulated_minute=event.simulated_minute,
                caused_by_event_ids=(event.event_id,),
            )
        )

    accepted: list[SocialIntent] = []
    traversed: set[tuple[int, str, str, str]] = set(carried)
    hopped: set[tuple[int, str, str]] = set(advanced)
    for intent in sorted(
        candidates,
        key=lambda item: (item.sender_id, item.receiver_id, item.campaign_id),
    ):
        message_key = social_message_key(intent)
        if message_key in hopped:
            continue
        edge_key = social_edge_key(intent)
        if edge_key in traversed:
            continue
        hopped.add(message_key)
        traversed.add(edge_key)
        accepted.append(intent)
    return tuple(accepted)


def _similarity_weighted_choice(
    contact_snapshot: Snapshot,
    sender_id: str,
    eligible: list[tuple[str, Relationship]],
    choice: float,
    weight: float,
) -> tuple[str, Relationship]:
    """Pick one receiver, mixing the keyed uniform draw with interest similarity.

    Each eligible receiver's weight is ``(1 - w) + w * similarity`` with ``w`` the run's
    ``social_similarity_weight`` and ``similarity`` the Jaccard overlap of the two
    agents' interests. At ``w = 0`` every weight is equal and the cumulative pick is the
    uniform draw; the engine only ever takes this path with a positive weight, so the
    documented default behaviour is untouched.
    """
    sender_profile = contact_snapshot.agents[sender_id][0]
    weights = tuple(
        (1.0 - weight)
        + weight
        * jaccard(sender_profile.interests, contact_snapshot.agents[receiver_id][0].interests)
        for receiver_id, _relationship in eligible
    )
    total = sum(weights)
    cumulative = 0.0
    for position, receiver_weight in enumerate(weights):
        cumulative += receiver_weight
        if choice * total < cumulative:
            return eligible[position]
    return eligible[-1]


def apply_social_intent(
    profile: PersonProfile,
    state: ConsumerState,
    intent: SocialIntent,
    *,
    parameters: ModelParameters | None = None,
) -> StateTransition:
    """Mark the receiver with social proof, reinforcement and awareness, never an exposure.

    A trusted conversation is the second reinforcement source of the documented memory
    model, so it draws on the same capped daily reinforcement a repeated exposure does
    and, like a repeated exposure, it banks that reinforcement without writing
    ``recall_strength``: the daily reflection applies the banked amount exactly once.
    """
    profile = revalidate_model(profile, PersonProfile, label="profile")
    state = revalidate_model(state, ConsumerState, label="state")
    intent = revalidate_model(intent, SocialIntent, label="social intent")
    if profile.agent_id != state.agent_id:
        raise ValueError("profile and state must identify the same agent")
    if state.agent_id != intent.receiver_id:
        raise ValueError("state must belong to the message receiver")

    proof_gain = parameters.social_proof_gain if parameters is not None else SOCIAL_PROOF_GAIN
    recall_gain = parameters.social_recall_gain if parameters is not None else SOCIAL_RECALL_GAIN
    shift = (
        intent.valence
        * intent.relationship_strength
        * profile.traits.social_susceptibility
        * proof_gain
    )
    headroom = max(0.0, MAX_DAILY_REINFORCEMENT - state.daily_reinforcement)
    projected_recall = clamp(state.recall_strength + state.daily_reinforcement, 0.0, 1.0)
    reinforcement = min(
        recall_gain
        * intent.relationship_strength
        * profile.traits.social_susceptibility
        * (1.0 - projected_recall),
        headroom,
    )
    updated = state.model_copy(
        update={
            "social_proof": clamp(state.social_proof + shift, -1.0, 1.0),
            "daily_reinforcement": clamp(
                state.daily_reinforcement + reinforcement,
                0.0,
                MAX_DAILY_REINFORCEMENT,
            ),
            "aware_campaign_ids": state.aware_campaign_ids | {intent.campaign_id},
        }
    )
    return StateTransition(
        previous=state,
        state=updated,
        caused_by_event_ids=intent.caused_by_event_ids,
    )


__all__ = [
    "SOCIAL_PROOF_GAIN",
    "SOCIAL_RECALL_GAIN",
    "SocialIntent",
    "apply_social_intent",
    "day_index",
    "plan_social_shares",
    "social_edge_key",
    "social_message_key",
]
