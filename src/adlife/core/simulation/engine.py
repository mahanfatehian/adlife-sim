from __future__ import annotations

import json
import re
import warnings
from collections.abc import Mapping
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import mesa  # type: ignore[import-untyped]  # Mesa does not publish a py.typed marker.
from pydantic import BaseModel, TypeAdapter

from adlife.core.domain.campaign import Campaign
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.results import SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState, ExposureCount, Memory
from adlife.core.ports.cognition import MAX_RELEVANT_MEMORIES, CognitionAnswer, CognitionRequest
from adlife.core.ports.run_store import RunCheckpoint
from adlife.core.simulation.clock import SimClock
from adlife.core.simulation.decision import (
    apply_purchase,
    apply_response,
    compose_response,
    evaluate_rule_response,
    purchase_proxy,
)
from adlife.core.simulation.memory import decay_memories, encode_memory, purchase_proxy_memory
from adlife.core.simulation.rng import RandomOracle

if TYPE_CHECKING:
    from adlife.core.simulation.agent import ConsumerAgent
    from adlife.core.simulation.exposure import AttentionDecision
    from adlife.core.simulation.movement import Snapshot, TickOutcome, TickPlan

_JSON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)
_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _json_fallback(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonicalize_dump(source: object, dumped: object) -> object:
    if isinstance(source, BaseModel):
        if not isinstance(dumped, Mapping):
            raise TypeError("Pydantic model did not produce a JSON object")
        fields = type(source).model_fields
        return {
            key: _canonicalize_dump(getattr(source, key), item) if key in fields else item
            for key, item in dumped.items()
        }

    if isinstance(source, Mapping):
        if not isinstance(dumped, Mapping):
            raise TypeError("mapping did not produce a JSON object")
        canonical: dict[str, object] = {}
        for key, item in source.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            canonical[key] = _canonicalize_dump(item, dumped[key])
        return canonical

    if isinstance(source, (set, frozenset)):
        if not isinstance(dumped, list):
            raise TypeError("set did not produce a JSON array")
        items = [
            _canonicalize_dump(item, dumped_item)
            for item, dumped_item in zip(source, dumped, strict=True)
        ]
        return sorted(items, key=_canonical_json)

    if isinstance(source, (list, tuple)):
        if not isinstance(dumped, list):
            raise TypeError("sequence did not produce a JSON array")
        return [
            _canonicalize_dump(item, dumped_item)
            for item, dumped_item in zip(source, dumped, strict=True)
        ]

    return dumped


def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str:
    if isinstance(value, BaseModel):
        dumped: object = value.model_dump(mode="json")
    else:
        dumped = _JSON_ADAPTER.dump_python(value, mode="json", fallback=_json_fallback)
    canonical = _canonical_json(_canonicalize_dump(value, dumped)).encode("utf-8")
    return sha256(canonical).hexdigest()


def stable_event_id(run_id: str, sequence: int) -> str:
    return f"{run_id}:event-{sequence:08d}"


_AGE_BANDS: tuple[tuple[int, int, str], ...] = (
    (18, 24, "18-24"),
    (25, 34, "25-34"),
    (35, 44, "35-44"),
    (45, 54, "45-54"),
    (55, 65, "55-65"),
)
"""The age bands a minimized persona may carry. Ages outside 18-65 cannot occur."""

MAX_MEMORY_SUMMARY_CHARS = 280
"""The same bound ``Memory.summary`` carries; a prompt reuses the domain bound."""

_SOURCE_BY_PROVIDER_KIND: Mapping[str, EventSource] = {
    "rule": EventSource.RULE,
    "mock": EventSource.MOCK,
    "local": EventSource.LOCAL_LLM,
    "remote": EventSource.REMOTE_LLM,
    "replay": EventSource.REPLAY,
    "fallback": EventSource.FALLBACK,
}


def _age_band(age: int) -> str:
    for lower, upper, band in _AGE_BANDS:
        if lower <= age <= upper:
            return band
    raise ValueError(f"age {age} is outside the documented 18-65 band")


def _persona_projection(profile: PersonProfile) -> Mapping[str, object]:
    """Minimize one persona to what a cognition prompt may carry.

    ``display_name``, income, home and routine zones stay out of the prompt: they are
    either identifying (a display name is) or irrelevant to the reaction a prompt asks
    for. ``interests`` is sorted because a prompt array preserves element order, and a
    frozenset has none of its own.
    """
    return {
        "age_band": _age_band(profile.age),
        "household_type": profile.household_type,
        "interests": sorted(profile.interests),
        "occupation": profile.occupation,
    }


def _campaign_projection(campaign: Campaign) -> Mapping[str, object]:
    """Minimize one campaign to the five documented prompt fields."""
    return {
        "call_to_action": campaign.call_to_action,
        "campaign_id": campaign.campaign_id,
        "message": campaign.message,
        "product_category": campaign.product_category,
        "product_name": campaign.product_name,
    }


def _memory_rank(memory: Memory) -> tuple[float, int, str]:
    """Strongest first, then most recent, then identifier - memory's own stable rank."""
    return (-memory.salience, -memory.created_minute, memory.memory_id)


def _relevant_memories(state: ConsumerState) -> tuple[str, ...]:
    return tuple(
        memory.summary[:MAX_MEMORY_SUMMARY_CHARS]
        for memory in sorted(state.memories, key=_memory_rank)[:MAX_RELEVANT_MEMORIES]
    )


def _changed_fields(previous: ConsumerState, current: ConsumerState) -> tuple[str, ...]:
    return tuple(
        field
        for field in type(previous).model_fields
        if getattr(previous, field) != getattr(current, field)
    )


class MissingCognitionAnswer(RuntimeError):
    """Raised when a planned cognition request has no resolved answer.

    The cognition service guarantees a valid result for EVERY request - provider
    failures resolve into rule fallbacks, never into absences. A missing answer is
    therefore a defect between the planner and the resolver, and the commit refuses it
    before anything changes rather than inventing an answer nobody computed.
    """


class StaleTickPlan(RuntimeError):
    """Raised when model state no longer matches a planned snapshot."""


class UnboundRun(RuntimeError):
    """Raised when a commit has no persistent run identity and sequence."""


class RunAlreadyBound(RuntimeError):
    """Raised when replacing an existing event-stream binding is attempted."""


class AdLifeModel(mesa.Model):  # type: ignore[misc]
    def __init__(
        self,
        *,
        scenario: Scenario,
        seed: int,
        run_id: str | None = None,
    ) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="The use of the `seed` keyword argument is deprecated",
                category=FutureWarning,
            )
            super().__init__(seed=seed)

        from adlife.core.simulation.agent import ConsumerAgent

        self._domain_scenario = scenario
        self._seed = seed
        self.clock = SimClock(scenario.days * 1440, scenario.tick_minutes)
        self.oracle = RandomOracle(seed)
        self._run_id: str | None = None
        self._next_event_sequence = 0
        self._snapshot_version = 0
        # The social layer's day-scoped accumulators: the edges a campaign message has
        # already crossed and the messages that have already advanced, both keyed by
        # simulated day (see social.social_edge_key). They are reset, never saved: a
        # checkpoint is only taken at a day boundary, where both are empty by
        # construction, which is what makes a day-boundary resume exact.
        self._traversed_edges: tuple[tuple[int, str, str, str], ...] = ()
        self._carried_messages: tuple[tuple[int, str, str], ...] = ()
        states_by_id = {state.agent_id: state for state in scenario.initial_states}
        self.agent_by_id: dict[str, ConsumerAgent] = {}
        for profile in sorted(scenario.population, key=lambda item: item.agent_id):
            agent = ConsumerAgent(
                self,
                profile=profile,
                state=states_by_id[profile.agent_id],
                oracle=self.oracle,
            )
            self.agent_by_id[profile.agent_id] = agent
        if run_id is not None:
            self._bind_run(run_id, next_event_sequence=0, stale_existing_plans=False)

    def bind_run(self, run_id: str, *, next_event_sequence: int) -> None:
        """Bind the one persistent event stream before the first commit."""
        self._bind_run(
            run_id,
            next_event_sequence=next_event_sequence,
            stale_existing_plans=True,
        )

    def _bind_run(
        self,
        run_id: str,
        *,
        next_event_sequence: int,
        stale_existing_plans: bool,
    ) -> None:
        if self._run_id is not None:
            raise RunAlreadyBound("model event stream is already bound")
        if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ValueError("run_id must be a lowercase slug of at most 40 characters")
        if type(next_event_sequence) is not int or next_event_sequence < 0:
            raise ValueError("next_event_sequence must be a nonnegative integer")

        self._run_id = run_id
        self._next_event_sequence = next_event_sequence
        if stale_existing_plans:
            self._snapshot_version += 1

    def start_events(self, run_id: str) -> tuple[DomainEvent, ...]:
        """Bind the run and mint its single ``run.started`` event at sequence zero.

        Every later event's causality is anchored to a stream that begins with one
        recorded start, so a replay can refuse a run that has none. Calling it twice is
        a :class:`RunAlreadyBound`, because a run starts once.
        """
        self._bind_run(run_id, next_event_sequence=0, stale_existing_plans=False)
        assert self._run_id is not None
        sequence = self._next_event_sequence
        self._next_event_sequence = sequence + 1
        event = DomainEvent(
            event_id=stable_event_id(self._run_id, sequence),
            run_id=self._run_id,
            simulated_minute=self.clock.current_minute,
            sequence=sequence,
            event_type=EventType.RUN_STARTED,
            payload={
                "scenario_id": self._domain_scenario.scenario_id,
                "seed": self._seed,
            },
            source=EventSource.RULE,
        )
        return (event,)

    def snapshot(self) -> Snapshot:
        from adlife.core.simulation.movement import Snapshot

        return Snapshot(
            agents={
                agent_id: (agent.profile, agent.state)
                for agent_id, agent in self.agent_by_id.items()
            },
            simulated_minute=self.clock.current_minute,
            run_id=self._run_id,
            next_event_sequence=self._next_event_sequence,
            version=self._snapshot_version,
        )

    def _cognition_request(self, decision: AttentionDecision) -> CognitionRequest:
        """Minimize one attention decision into the cognition prompt it asks for.

        The request identifier IS the stable identifier of the ``campaign.noticed`` event
        that caused it, which is what makes an answer addressable by cause. The prompt is
        built from the opportunity's projected post-movement state, the same state the
        commit-stage rule formula reads, so a rules run and a hybrid run are asked the
        same question about the same state.
        """
        from adlife.core.simulation.exposure import AttentionDecision

        if not isinstance(decision, AttentionDecision):
            raise TypeError("decision must be an AttentionDecision")
        opportunity = decision.opportunity
        terminal = decision.events[2]
        state = opportunity.state
        return CognitionRequest(
            request_id=terminal.event_id,
            run_id=opportunity.run_id,
            simulated_minute=opportunity.simulated_minute,
            agent_id=opportunity.agent_id,
            fictional_persona=_persona_projection(opportunity.profile),
            activity=state.activity,
            mood=state.mood,
            relevant_memories=_relevant_memories(state),
            campaign=_campaign_projection(opportunity.campaign),
            channel=opportunity.placement.channel,
            exposure_count=state.exposure_count(opportunity.campaign_id, opportunity.channel) + 1,
            creative_sha256=opportunity.campaign.asset_sha256,
        )

    def plan_tick(self) -> TickPlan:
        from adlife.core.simulation.exposure import (
            allocate_exposure_batch,
            decide_attention_batch,
        )
        from adlife.core.simulation.movement import TickPlan, _resolve_movement

        snapshot = self.snapshot()
        intents = tuple(
            self.agent_by_id[agent_id].plan(snapshot) for agent_id in sorted(self.agent_by_id)
        )
        # Movement is resolved at plan time so exposure can project the post-movement
        # state; an unbound model has no stream to address events with, so it plans a
        # movement-only tick and the commit refuses it before anything is written.
        movement_events: tuple[DomainEvent, ...] = ()
        decisions: tuple[AttentionDecision, ...] = ()
        requests: tuple[CognitionRequest, ...] = ()
        if snapshot.run_id is not None:
            from adlife.core.simulation.movement import _resolve_movement

            movement_events = _resolve_movement(
                intents,
                self._domain_scenario.world,
                snapshot,
            ).events
            campaigns = self._domain_scenario.campaigns
            if campaigns:
                from adlife.core.simulation.exposure import (
                    allocate_exposure_batch,
                    decide_attention_batch,
                )

                batch = allocate_exposure_batch(
                    snapshot,
                    campaigns,
                    self.clock.current_minute,
                    preceding_events=movement_events,
                )
                decisions = decide_attention_batch(batch, self.oracle)
                requests = tuple(
                    self._cognition_request(decision) for decision in decisions if decision.noticed
                )
        return TickPlan(
            snapshot=snapshot,
            intents=intents,
            movement_events=movement_events,
            attention=decisions,
            requests=requests,
        )

    def commit_tick(
        self,
        plan: TickPlan,
        cognition: Mapping[str, CognitionAnswer],
    ) -> TickOutcome:
        """Commit one tick through the nine documented stages, atomically.

        Stage order is: movement; advertising eligibility and attention (planned); the
        resolved cognition answers; the rule-bounded state change with its memory;
        social propagation; the purchase proxy; and, on a day boundary, the daily
        reflection - closed by ``run.completed`` on the run's last tick. Nothing is
        applied to an agent or to the sequence counter until every stage has minted, so
        a refusal anywhere leaves the tick entirely uncommitted.
        """
        from adlife.core.simulation.movement import InvalidTickPlan, TickPlan

        if self._run_id is None:
            raise UnboundRun("model event stream is unbound; call bind_run before commit_tick")
        if not isinstance(plan, TickPlan):
            raise TypeError("plan must be a TickPlan")
        if not isinstance(cognition, Mapping):
            raise TypeError("cognition must map request identifiers to CognitionAnswer values")
        checked_plan = TickPlan(snapshot=plan.snapshot, intents=plan.intents)
        if tuple(checked_plan.snapshot.agents) != tuple(self.agent_by_id):
            raise InvalidTickPlan("plan agent IDs must exactly match model agent IDs")
        if checked_plan.snapshot.fingerprint != self.snapshot().fingerprint:
            raise StaleTickPlan("state changed between plan and commit")
        return self._commit_in_stable_order(plan, cognition)

    def _commit_in_stable_order(
        self,
        plan: TickPlan,
        cognition: Mapping[str, CognitionAnswer],
    ) -> TickOutcome:
        from adlife.core.simulation.movement import (
            InvalidTickPlan,
            Snapshot,
            TickOutcome,
            _resolve_movement,
        )
        from adlife.core.simulation.social import (
            apply_social_intent,
            plan_social_shares,
            social_edge_key,
            social_message_key,
        )

        snapshot = plan.snapshot
        minute = self.clock.current_minute
        if snapshot.simulated_minute != minute:
            raise InvalidTickPlan("plan snapshot minute must match the model clock")
        resolution = _resolve_movement(
            plan.intents,
            self._domain_scenario.world,
            snapshot,
        )
        if plan.movement_events and tuple(plan.movement_events) != resolution.events:
            raise InvalidTickPlan("planned movement events do not match a fresh resolution")

        # STAGE 4 first, before stage 1's first byte is written: every planned request
        # must already have its resolved answer, so a defect between planner and resolver
        # refuses the tick instead of leaving a half-committed one.
        answers: dict[str, CognitionAnswer] = {}
        for request in plan.requests:
            answer = cognition.get(request.request_id)
            if not isinstance(answer, CognitionAnswer):
                raise MissingCognitionAnswer(
                    f"cognition request {request.request_id} has no resolved answer"
                )
            answers[request.request_id] = answer

        # STAGE 1: the projected post-movement states, applied to nothing yet - the
        # working copy every later stage reads and writes until the tick commits whole.
        working: dict[str, ConsumerState] = {}
        for movement_transition in resolution.transitions:
            agent = self.agent_by_id[movement_transition.agent_id]
            working[movement_transition.agent_id] = agent.state.model_copy(
                update={
                    "location": movement_transition.to_zone,
                    "activity": movement_transition.to_activity,
                    "current_route_id": movement_transition.to_route_id,
                }
            )

        run_id = self._run_id
        assert run_id is not None
        events: list[DomainEvent] = list(resolution.events)
        sequence = snapshot.next_event_sequence + len(events)
        agent_cause: dict[str, str] = {}

        def mint(
            event_type: EventType,
            payload: Mapping[str, object],
            *,
            agent_id: str | None = None,
            campaign_id: str | None = None,
            channel: str | None = None,
            caused_by: tuple[str, ...] = (),
            source: EventSource = EventSource.RULE,
            model_id: str | None = None,
        ) -> DomainEvent:
            nonlocal sequence
            event = DomainEvent(
                event_id=stable_event_id(run_id, sequence),
                run_id=run_id,
                simulated_minute=minute,
                sequence=sequence,
                event_type=event_type,
                agent_id=agent_id,
                campaign_id=campaign_id,
                channel=channel,
                payload=payload,
                source=source,
                model_id=model_id,
                caused_by_event_ids=caused_by,
            )
            sequence += 1
            events.append(event)
            if agent_id is not None:
                agent_cause[agent_id] = event.event_id
            return event

        # STAGES 2 and 3: the planned eligibility, impression and attention events, plus
        # the exposure count each delivery spends - the same projection the planner used
        # to bound the frequency cap.
        for decision in plan.attention:
            events.extend(decision.events)
            opportunity = decision.opportunity
            counts = list(working[opportunity.agent_id].exposure_counts)
            replaced = False
            for index, entry in enumerate(counts):
                if (
                    entry.campaign_id == opportunity.campaign_id
                    and entry.channel == opportunity.channel
                ):
                    counts[index] = entry.model_copy(update={"count": entry.count + 1})
                    replaced = True
                    break
            if not replaced:
                counts.append(
                    ExposureCount(
                        campaign_id=opportunity.campaign_id,
                        channel=opportunity.placement.channel,
                        count=1,
                    )
                )
            working[opportunity.agent_id] = working[opportunity.agent_id].model_copy(
                update={"exposure_counts": tuple(counts)}
            )
        sequence = snapshot.next_event_sequence + len(events)

        # STAGES 4 to 6: the resolved answers, the rule-bounded state change and the
        # episodic memory each noticed advertisement leaves behind. The rule baseline is
        # read from the opportunity's own projected state - the same state the request
        # was built from - so a rules run composes to exactly the formula's answer.
        noticed_events: list[DomainEvent] = []
        noticed_by_agent_campaign: dict[tuple[str, str], str] = {}
        for decision in sorted(
            plan.attention,
            key=lambda item: (item.opportunity.agent_id, item.opportunity.event_sequence_start),
        ):
            terminal = decision.events[2]
            if terminal.event_type is not EventType.CAMPAIGN_NOTICED:
                continue
            noticed_events.append(terminal)
            opportunity = decision.opportunity
            agent_id = opportunity.agent_id
            noticed_by_agent_campaign[(agent_id, opportunity.campaign_id)] = terminal.event_id
            answer = answers[terminal.event_id]
            result = answer.result
            usage = answer.usage
            fallback = usage.provider_kind == "fallback"
            baseline = evaluate_rule_response(
                opportunity.profile,
                opportunity.state,
                opportunity.campaign,
                opportunity.placement,
            )
            response = compose_response(baseline, result)
            answer_event = mint(
                EventType.COGNITION_FALLBACK if fallback else EventType.COGNITION_COMPLETED,
                payload={
                    "emotion": result.emotion,
                    "valence": result.valence,
                    "relevance": result.relevance,
                    "credibility": result.credibility,
                    "sentiment_delta": response.sentiment_delta,
                    "recall_delta": response.recall_delta,
                    "purchase_intention": response.purchase_intention,
                    "share_probability": result.share_probability,
                    "rule_modifier": result.rule_modifier,
                    "memory_summary": result.memory_summary,
                    **(
                        {"fallback_reason": usage.fallback_reason}
                        if fallback and usage.fallback_reason is not None
                        else {}
                    ),
                },
                agent_id=agent_id,
                campaign_id=opportunity.campaign_id,
                channel=opportunity.channel,
                caused_by=(terminal.event_id,),
                source=_SOURCE_BY_PROVIDER_KIND.get(usage.provider_kind, EventSource.RULE),
                model_id=usage.model_id,
            )
            transition = apply_response(working[agent_id], response, caused_by=(terminal.event_id,))
            working[agent_id] = transition.state
            mint(
                EventType.STATE_UPDATED,
                payload={
                    "reason": "cognition",
                    "changed": list(_changed_fields(transition.previous, transition.state)),
                },
                agent_id=agent_id,
                caused_by=(answer_event.event_id,),
            )
            memory = Memory(
                memory_id=f"{terminal.event_id}:memory",
                created_minute=minute,
                kind="advertising",
                summary=result.memory_summary,
                salience=result.relevance,
                campaign_id=opportunity.campaign_id,
                caused_by_event_ids=(terminal.event_id,),
            )
            working[agent_id] = encode_memory(working[agent_id], memory)
            mint(
                EventType.MEMORY_CREATED,
                payload={
                    "memory_id": memory.memory_id,
                    "kind": memory.kind,
                    "summary": memory.summary,
                    "salience": memory.salience,
                },
                agent_id=agent_id,
                campaign_id=opportunity.campaign_id,
                caused_by=(answer_event.event_id,),
            )

        # STAGE 7: word of mouth, planned against the pre-tick snapshot with the answers
        # that just landed, and the day-scoped accumulators the model carries.
        social_intents = plan_social_shares(
            snapshot,
            noticed_events,
            self.oracle,
            relationships=self._domain_scenario.relationships,
            share_signals={
                event.event_id: answers[event.event_id].result.share_probability
                for event in noticed_events
            },
            movement_events=resolution.events,
            traversed_edges=self._traversed_edges,
            carried_messages=self._carried_messages,
            social_enabled=self._domain_scenario.social_enabled,
        )
        new_edges: list[tuple[int, str, str, str]] = []
        new_messages: list[tuple[int, str, str]] = []
        for intent in social_intents:
            sender_id = intent.sender_id
            receiver_id = intent.receiver_id
            shared = mint(
                EventType.SOCIAL_SHARED,
                payload={
                    "receiver_id": receiver_id,
                    "relationship_kind": intent.relationship_kind,
                    "share_probability": intent.share_probability,
                    "random_draw": intent.random_draw,
                    "valence": intent.valence,
                },
                agent_id=sender_id,
                campaign_id=intent.campaign_id,
                caused_by=intent.caused_by_event_ids,
            )
            transition = apply_social_intent(
                self.agent_by_id[receiver_id].profile,
                working[receiver_id],
                intent,
            )
            working[receiver_id] = transition.state
            received = mint(
                EventType.SOCIAL_RECEIVED,
                payload={
                    "sender_id": sender_id,
                    "campaign_id": intent.campaign_id,
                    "changed": list(_changed_fields(transition.previous, transition.state)),
                },
                agent_id=receiver_id,
                campaign_id=intent.campaign_id,
                caused_by=(shared.event_id,),
            )
            mint(
                EventType.STATE_UPDATED,
                payload={
                    "reason": "social",
                    "changed": list(_changed_fields(transition.previous, transition.state)),
                },
                agent_id=receiver_id,
                caused_by=(received.event_id,),
            )
            new_edges.append(social_edge_key(intent))
            new_messages.append(social_message_key(intent))

        # STAGE 8: the rule-only purchase proxy. A proxy fires on the tick that noticed
        # the advertisement, and its causes name that noticed event; an intention carried
        # over from an earlier tick buys on its next exposure.
        campaigns_by_id = {
            campaign.campaign_id: campaign for campaign in self._domain_scenario.campaigns
        }
        for agent_id in sorted(working):
            state = working[agent_id]
            if not state.active_need:
                continue
            for index, campaign_id in enumerate(
                sorted(set(state.aware_campaign_ids) & set(campaigns_by_id))
            ):
                anchor = noticed_by_agent_campaign.get((agent_id, campaign_id))
                if anchor is None:
                    continue
                proxy = purchase_proxy(
                    self.agent_by_id[agent_id].profile,
                    state,
                    campaigns_by_id[campaign_id],
                    self.oracle,
                    simulated_minute=minute,
                    decision_index=index,
                )
                if not proxy.committed:
                    continue
                transition = apply_purchase(state, proxy, caused_by=(anchor,))
                working[agent_id] = transition.state
                mint(
                    EventType.STATE_UPDATED,
                    payload={
                        "reason": "purchase",
                        "changed": list(_changed_fields(transition.previous, transition.state)),
                    },
                    agent_id=agent_id,
                    campaign_id=campaign_id,
                    caused_by=(anchor,),
                )
                record = purchase_proxy_memory(
                    proxy,
                    memory_id=f"{anchor}:purchase",
                    caused_by_event_ids=(anchor,),
                )
                working[agent_id] = encode_memory(working[agent_id], record)
                mint(
                    EventType.MEMORY_CREATED,
                    payload={
                        "memory_id": record.memory_id,
                        "kind": record.kind,
                        "summary": record.summary,
                        "salience": record.salience,
                    },
                    agent_id=agent_id,
                    campaign_id=campaign_id,
                    caused_by=(anchor,),
                )

        ends_day = (minute + self._domain_scenario.tick_minutes) % 1440 == 0
        run_end = minute + self._domain_scenario.tick_minutes == self.clock._duration_minutes

        # STAGE 9: the daily reflection - the one place a day's banked reinforcement
        # reaches recall, and the boundary the social accumulators reset at.
        if ends_day:
            day_index = (minute + self._domain_scenario.tick_minutes) // 1440 - 1
            for agent_id in sorted(working):
                working[agent_id] = decay_memories(working[agent_id], day_index)
                cause = agent_cause.get(agent_id)
                mint(
                    EventType.DAY_REFLECTED,
                    payload={"day_index": day_index},
                    agent_id=agent_id,
                    caused_by=(cause,) if cause is not None else (),
                )

        if run_end:
            mint(
                EventType.RUN_COMPLETED,
                payload={"final_minute": minute + self._domain_scenario.tick_minutes},
                caused_by=(events[-1].event_id,) if events else (),
            )

        outcome = TickOutcome(
            snapshot=Snapshot(
                agents={
                    agent_id: (self.agent_by_id[agent_id].profile, working[agent_id])
                    for agent_id in self.agent_by_id
                },
                simulated_minute=minute,
                run_id=run_id,
                next_event_sequence=sequence,
                version=self._snapshot_version + 1,
            ),
            events=tuple(events),
            ends_simulated_day=ends_day,
        )
        for agent_id, state in working.items():
            self.agent_by_id[agent_id]._replace_state(state)
        self._next_event_sequence = sequence
        self._snapshot_version += 1
        self._traversed_edges = () if ends_day else tuple(self._traversed_edges) + tuple(new_edges)
        self._carried_messages = (
            () if ends_day else tuple(self._carried_messages) + tuple(new_messages)
        )
        return outcome

    def checkpoint(self) -> RunCheckpoint:
        """Take the resumable picture of the population at a simulated day boundary."""
        if self._run_id is None:
            raise UnboundRun("model event stream is unbound; call bind_run before checkpoint")
        if self.clock.current_minute % 1440 != 0:
            raise ValueError("a checkpoint must be taken at a simulated day boundary")
        return RunCheckpoint(
            run_id=self._run_id,
            simulated_minute=self.clock.current_minute,
            next_event_sequence=self._next_event_sequence,
            states=tuple(self.agent_by_id[agent_id].state for agent_id in sorted(self.agent_by_id)),
        )

    def complete(self) -> SimulationResult:
        """Name the finished run. The clock decides when a run may complete."""
        if not self.clock.finished:
            raise RuntimeError("a run completes only when its clock has finished")
        if self._run_id is None:
            raise UnboundRun("model event stream is unbound; call bind_run before complete")
        return SimulationResult(
            run_id=self._run_id,
            status="completed",
            final_minute=self.clock.current_minute,
            event_count=self._next_event_sequence,
        )

    @classmethod
    def restore(
        cls,
        *,
        scenario: Scenario,
        seed: int,
        checkpoint: RunCheckpoint,
    ) -> AdLifeModel:
        """Rebuild a model exactly where a day-boundary checkpoint left it.

        Draws are pure functions of the seed and their coordinates
        (:class:`~adlife.core.simulation.rng.RandomOracle`), the social accumulators are
        empty at a day boundary by construction, and the checkpoint carries the states
        and the event sequence - so a restored model commits byte-identical events from
        here on, with no draw replay and no hidden state.
        """
        if not isinstance(checkpoint, RunCheckpoint):
            raise TypeError("checkpoint must be a RunCheckpoint")
        model = cls(scenario=scenario, seed=seed)
        population_ids = sorted(profile.agent_id for profile in scenario.population)
        checkpoint_ids = [state.agent_id for state in checkpoint.states]
        if checkpoint_ids != population_ids:
            raise ValueError("checkpoint states do not match the scenario population")
        model._bind_run(
            checkpoint.run_id,
            next_event_sequence=checkpoint.next_event_sequence,
            stale_existing_plans=False,
        )
        for state in checkpoint.states:
            model.agent_by_id[state.agent_id]._replace_state(state)
        while model.clock.current_minute < checkpoint.simulated_minute:
            model.clock.advance()
        return model


__all__ = [
    "AdLifeModel",
    "MissingCognitionAnswer",
    "RunAlreadyBound",
    "StaleTickPlan",
    "UnboundRun",
    "canonical_sha256",
    "stable_event_id",
]
