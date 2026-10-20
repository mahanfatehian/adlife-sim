"""``RunStoryV1``: one verified saved V10 run as path-free story data.

The guided story page (JavaScript) and the HTML run report (Jinja) render this one
document (format ``adlife-run-story-v1``). The builder in :mod:`adlife.city.run_story`
decides what happened; this document carries structured facts plus catalog message
references (:class:`~adlife.i18n.refs.MessageRef`), never prose, so both renderers
translate the same decisions and cannot drift apart.

Every float is finite and rounded to six decimals, seeds are strings, and saved user or
data text (persona, scenario, campaign and product names, model IDs) stays verbatim
data for the renderers to isolate. Nothing here names a filesystem path, a provider
address, a credential slot, a prompt, or descriptive persona context.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import pairwise
from typing import Annotated, Any, Final, Literal, Self, TypeAlias, get_args

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from adlife.core.domain.city import RoadKind, TravelDirection
from adlife.core.domain.city_cognition import CityCognitionSafetyFlag
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionAffectV2,
    CityCognitionCredibilityV2,
    CityCognitionModeV2,
    CityCognitionRelevanceV2,
    CityCognitionRuntimeFallbackReasonV2,
    CityCognitionSalienceV2,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.spatial_campaign import BillboardSide, SpatialActivity
from adlife.i18n.refs import MessageRef, PersonLabel, RefContext

RUN_STORY_FORMAT: Final = "adlife-run-story-v1"
STORY_MOMENTS_PER_PERSON: Final = 40

Status: TypeAlias = Literal[
    "answered",
    "reused",
    "practice",
    "rules",
    "unsure",
    "warning",
    "failed",
    "over_limit",
    "not_chosen",
    "not_noticed",
]
Compare: TypeAlias = Literal["lower", "slightly_lower", "about_same", "slightly_higher", "higher"]
Effect: TypeAlias = Literal["higher", "lower", "same"]
StoryChannel: TypeAlias = Literal["mobile-feed", "roadside-billboard"]
TemplateKey: TypeAlias = Literal["lumo", "veyra", "orilo", "nuvin"]
LabelGroup: TypeAlias = Literal["ai", "practice"]
TraitName: TypeAlias = Literal[
    "advertising_skepticism",
    "price_sensitivity",
    "novelty_seeking",
    "mobile_recall_encoding",
    "roadside_recall_encoding",
    "impulsivity",
]
TraitLevel: TypeAlias = Literal["high", "low"]

# Statuses whose noticed moment carries a terminal answer with labels.
LABELLED_STATUSES: Final[frozenset[str]] = frozenset(
    {"answered", "reused", "practice", "unsure", "warning"}
)
# Statuses whose applied effect is the fixed formula over the AI labels.
MATH_STATUSES: Final[frozenset[str]] = frozenset({"answered", "reused", "practice"})


def _six_decimals(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("story numbers must be finite")
    # ``+ 0.0`` turns a rounded negative zero into zero, so JSON never shows -0.0.
    return round(value, 6) + 0.0


StoryNumber: TypeAlias = Annotated[float, AfterValidator(_six_decimals)]
_Hash: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_AgentId: TypeAlias = Annotated[str, Field(pattern=r"^person-[0-9]{3}$")]
_Identifier: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]
_RunId: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
_ProfileId: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
_PersonaId: TypeAlias = Annotated[str, Field(pattern=r"^persona-[0-9]{3}$")]
_MomentId: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
_Count: TypeAlias = Annotated[int, Field(ge=0, le=520_800)]
_Requests: TypeAlias = Annotated[int, Field(ge=0, le=60)]
_Name: TypeAlias = Annotated[str, Field(min_length=1, max_length=120)]
_ShortText: TypeAlias = Annotated[str, Field(min_length=1, max_length=80)]
_Point: TypeAlias = tuple[StoryNumber, StoryNumber]
TrackPoint: TypeAlias = tuple[StoryNumber, StoryNumber, StoryNumber]


class _StoryModel(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)


def _exact_counts(values: Mapping[Any, int], literal: object, *, label: str) -> dict[Any, int]:
    order = get_args(literal)
    if set(values) != set(order):
        raise ValueError(f"{label} counts must name every category exactly once")
    return {category: values[category] for category in order}


# --- run -------------------------------------------------------------------------------


class StoryCity(_StoryModel):
    city_id: _Identifier
    name: _Name
    catalog_key: _Identifier | None


class StoryScenario(_StoryModel):
    scenario_id: _Identifier
    name: _Name


class StoryCampaign(_StoryModel):
    campaign_id: _Identifier
    name: _Name
    target_interests: tuple[_ShortText, ...] = Field(min_length=1, max_length=12)
    relative_price: StoryNumber = Field(gt=0, le=100)


class StoryCreative(_StoryModel):
    template_id: _Identifier
    catalog_key: _Identifier | None
    product_name: _ShortText
    product_category: _ShortText
    message: Annotated[str, Field(min_length=1, max_length=240)]
    call_to_action: _Name


class StoryWindow(_StoryModel):
    """One half-open active window as a 1-based day and minutes of that day."""

    day: int = Field(ge=1, le=7)
    start_minute: int = Field(ge=0, le=1_439)
    end_minute: int = Field(ge=1, le=1_440)

    @model_validator(mode="after")
    def forward_window(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("story window end must be after its start")
        return self


class StoryPhone(_StoryModel):
    windows: tuple[StoryWindow, ...] = Field(min_length=1, max_length=14)
    cap: int = Field(ge=1, le=100)
    eligible_activities: tuple[SpatialActivity, ...] = Field(min_length=1, max_length=4)
    chance_per_minute: StoryNumber = Field(ge=0, le=1)


class StoryBillboard(_StoryModel):
    road_id: _Identifier
    side: BillboardSide
    travel_direction: TravelDirection
    road_fraction: StoryNumber = Field(gt=0, lt=1)
    cap: int = Field(ge=1, le=100)
    windows: tuple[StoryWindow, ...] = Field(min_length=1, max_length=14)
    position: _Point


class StoryHashes(_StoryModel):
    workbench_input_sha256: _Hash
    cognition_input_sha256: _Hash
    causal_contract_sha256: _Hash


class StoryRun(_StoryModel):
    run_id: _RunId
    seed: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,18})$")
    days: int = Field(ge=1, le=7)
    people_count: int = Field(ge=1, le=30)
    city: StoryCity
    scenario: StoryScenario
    campaign: StoryCampaign
    creative: StoryCreative
    channels: tuple[StoryChannel, ...] = Field(min_length=1, max_length=2)
    phone: StoryPhone | None
    billboard: StoryBillboard | None
    hashes: StoryHashes

    @model_validator(mode="after")
    def coherent_channels(self) -> Self:
        expected: list[StoryChannel] = []
        if self.phone is not None:
            expected.append("mobile-feed")
        if self.billboard is not None:
            expected.append("roadside-billboard")
        if self.channels != tuple(expected):
            raise ValueError("story channels must list exactly the configured placements")
        return self


# --- AI facts --------------------------------------------------------------------------


class StoryCoverage(_StoryModel):
    noticed: _Count
    chosen: _Requests
    sent: _Requests
    over_limit: _Requests
    not_chosen: _Count


class StoryStatuses(_StoryModel):
    answered: _Requests
    reused: _Requests
    practice: _Requests
    rules: _Requests
    unsure: _Requests
    warning: _Requests
    failed: _Requests
    over_limit: _Requests
    not_chosen: _Count

    @property
    def total(self) -> int:
        return (
            self.answered
            + self.reused
            + self.practice
            + self.rules
            + self.unsure
            + self.warning
            + self.failed
            + self.over_limit
            + self.not_chosen
        )


class StoryUsage(_StoryModel):
    attempts: int = Field(ge=0, le=240)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)


class StoryContract(_StoryModel):
    prompt_contract_id: Literal["city-cognition-signal-prompt-v2"]
    prompt_contract_sha256: _Hash
    result_schema_id: Literal["city-cognition-signal-result-v2"]
    influence_policy_id: Literal["city-cognition-influence-v1"]
    influence_policy_sha256: _Hash


class StoryAi(_StoryModel):
    mode: CityCognitionModeV2
    model_id: _Name | None
    provider_profile_id: _ProfileId | None
    provider_revision_sha256: _Hash | None
    thinking_off: bool
    funded_limit: Literal[10, 20, 40, 60]
    per_person: Literal[4]
    per_run: Literal[60]
    coverage: StoryCoverage
    statuses: StoryStatuses
    fallback_reasons: dict[CityCognitionRuntimeFallbackReasonV2, Annotated[int, Field(ge=1, le=60)]]
    usage_this_run: StoryUsage
    reused_answers: _Requests
    contract: StoryContract

    @model_validator(mode="after")
    def coherent_ai(self) -> Self:
        provider = (self.model_id, self.provider_profile_id, self.provider_revision_sha256)
        if self.mode in {"rules", "mock"}:
            if any(value is not None for value in provider) or self.thinking_off:
                raise ValueError("offline story AI facts cannot name a provider")
            if self.coverage.sent or self.funded_limit != 60:
                raise ValueError("offline story AI facts cannot send questions")
        elif any(value is None for value in provider):
            raise ValueError("provider story AI facts must name the model and revision")
        if self.statuses.total != self.coverage.noticed:
            raise ValueError("story statuses must account for every noticed moment")
        if (
            self.statuses.over_limit != self.coverage.over_limit
            or self.statuses.not_chosen != self.coverage.not_chosen
            or self.coverage.chosen + self.coverage.not_chosen != self.coverage.noticed
        ):
            raise ValueError("story statuses and coverage disagree")
        if sum(self.fallback_reasons.values()) != self.statuses.failed:
            raise ValueError("story fallback reasons must account for every failed answer")
        return self


class StoryFunnel(_StoryModel):
    opportunities: _Count
    shown: _Count
    noticed: _Count
    chosen: _Requests
    sent: _Requests
    answered: _Requests
    used: _Requests


class StoryLabelCounts(_StoryModel):
    """How often each label value occurs across the run's AI (or practice) answers."""

    group: LabelGroup
    answers: int = Field(ge=1, le=60)
    affect: dict[CityCognitionAffectV2, _Requests]
    relevance: dict[CityCognitionRelevanceV2, _Requests]
    credibility: dict[CityCognitionCredibilityV2, _Requests]
    salience: dict[CityCognitionSalienceV2, _Requests]
    safety_flags: dict[CityCognitionSafetyFlag, _Requests]

    @model_validator(mode="after")
    def complete_counts(self) -> Self:
        # Categories are stored in their fixed order, whatever order the input used.
        for name, literal in (
            ("affect", CityCognitionAffectV2),
            ("relevance", CityCognitionRelevanceV2),
            ("credibility", CityCognitionCredibilityV2),
            ("salience", CityCognitionSalienceV2),
            ("safety_flags", CityCognitionSafetyFlag),
        ):
            ordered = _exact_counts(getattr(self, name), literal, label=name)
            if name != "safety_flags" and sum(ordered.values()) != self.answers:
                raise ValueError(f"{name} counts must sum to the answer count")
            if name == "safety_flags" and any(value > self.answers for value in ordered.values()):
                raise ValueError("a safety flag cannot occur more often than answers")
            object.__setattr__(self, name, ordered)
        return self


class StoryRange(_StoryModel):
    start: StoryNumber
    end: StoryNumber


class StoryRulesAverages(_StoryModel):
    liking: StoryRange
    memory: StoryRange
    purchase_interest: StoryRange


class StoryAiAverages(_StoryModel):
    liking: StoryRange
    memory: StoryRange


class StoryCompare(_StoryModel):
    liking: Compare
    memory: Compare


class StoryAverages(_StoryModel):
    rules: StoryRulesAverages
    with_ai: StoryAiAverages
    compare: StoryCompare


# --- map -------------------------------------------------------------------------------


class StoryBounds(_StoryModel):
    west: StoryNumber = Field(ge=-180, le=180)
    south: StoryNumber = Field(ge=-90, le=90)
    east: StoryNumber = Field(ge=-180, le=180)
    north: StoryNumber = Field(ge=-90, le=90)


class StoryNode(_StoryModel):
    node_id: _Identifier
    longitude: StoryNumber
    latitude: StoryNumber


class StoryRoad(_StoryModel):
    road_id: _Identifier
    kind: RoadKind
    points: tuple[_Point, ...] = Field(min_length=2, max_length=66)


class StoryMap(_StoryModel):
    bounds: StoryBounds
    nodes: tuple[StoryNode, ...] = Field(min_length=2, max_length=10_000)
    roads: tuple[StoryRoad, ...] = Field(min_length=1, max_length=20_000)


# --- personas and people ---------------------------------------------------------------


class StoryTraitChip(_StoryModel):
    trait: TraitName
    level: TraitLevel


class StoryPersona(_StoryModel):
    persona_id: _PersonaId
    template_key: TemplateKey | None
    display_name: _ShortText
    count: int = Field(ge=1, le=30)
    interests: tuple[_ShortText, ...] = Field(min_length=1, max_length=12)
    trait_chips: tuple[StoryTraitChip, ...] = Field(max_length=3)
    color_index: int = Field(ge=0, le=33)
    agent_ids: tuple[_AgentId, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def coherent_persona(self) -> Self:
        if len(self.agent_ids) != self.count or len(set(self.agent_ids)) != self.count:
            raise ValueError("a story persona must list one distinct agent per count")
        if (self.template_key is None) != (self.color_index >= 4):
            raise ValueError("template personas use colours 0-3 and others 4 onward")
        return self


class StoryAnchors(_StoryModel):
    home: _Identifier
    work: _Identifier
    leisure: _Identifier


class StoryDay(_StoryModel):
    """One day's two road legs as ``[absolute minute, longitude, latitude]`` points."""

    day_index: int = Field(ge=0, le=6)
    destination: Literal["work", "leisure"]
    outbound: tuple[TrackPoint, ...] = Field(min_length=2)
    inbound: tuple[TrackPoint, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def ordered_legs(self) -> Self:
        times = [point[0] for point in (*self.outbound, *self.inbound)]
        if any(later < earlier for earlier, later in pairwise(times)):
            raise ValueError("story track times must never decrease")
        day_start = self.day_index * 1_440
        if times[0] < day_start or times[-1] > day_start + 1_440:
            raise ValueError("story track must stay within its day")
        return self


class StoryTotals(_StoryModel):
    shown: _Count
    noticed: _Count
    chosen: Annotated[int, Field(ge=0, le=4)]
    sent: Annotated[int, Field(ge=0, le=4)]
    used: Annotated[int, Field(ge=0, le=4)]
    set_aside: Annotated[int, Field(ge=0, le=4)]
    failed: Annotated[int, Field(ge=0, le=4)]
    over_limit: Annotated[int, Field(ge=0, le=4)]
    not_chosen: _Count


class StoryRuleState(_StoryModel):
    liking: StoryNumber = Field(ge=-1, le=1)
    memory: StoryNumber = Field(ge=0, le=1)
    purchase_interest: StoryNumber = Field(ge=0, le=1)


class StoryAiState(_StoryModel):
    liking: StoryNumber = Field(ge=-1, le=1)
    memory: StoryNumber = Field(ge=0, le=1)


class StoryState(_StoryModel):
    rules: StoryRuleState
    with_ai: StoryAiState


class StoryLabels(_StoryModel):
    affect: CityCognitionAffectV2
    relevance: CityCognitionRelevanceV2
    credibility: CityCognitionCredibilityV2
    salience: CityCognitionSalienceV2


class StoryDelta(_StoryModel):
    liking: StoryNumber = Field(ge=-0.2, le=0.2)
    memory: StoryNumber = Field(ge=0, le=0.3)


class StoryEffect(_StoryModel):
    liking: Effect
    memory: Effect


class StoryMath(_StoryModel):
    """Factors of the fixed formula; present only when they reproduce the applied deltas."""

    affect_adjustment: StoryNumber
    relevance_factor: StoryNumber
    credibility_factor: StoryNumber
    salience_factor: StoryNumber
    liking_raw: StoryNumber
    memory_raw: StoryNumber
    liking_limit: StoryNumber
    memory_min: StoryNumber
    memory_max: StoryNumber


class StoryMoment(_StoryModel):
    moment_id: _MomentId
    minute: int = Field(ge=0, lt=10_080)
    millisecond: int = Field(ge=0, lt=60_000)
    day_index: int = Field(ge=0, le=6)
    channel: StoryChannel
    activity: SpatialActivity
    position: _Point
    noticed: bool
    status: Status
    labels: StoryLabels | None
    safety_flags: tuple[CityCognitionSafetyFlag, ...] = Field(max_length=3)
    reason: CityCognitionRuntimeFallbackReasonV2 | None
    before: StoryState | None
    after: StoryState | None
    rule_delta: StoryDelta | None
    applied_delta: StoryDelta | None
    effect: StoryEffect | None
    math: StoryMath | None
    sentence: MessageRef
    ai_clause: MessageRef | None
    rules_line: MessageRef | None
    with_ai_line: MessageRef | None

    @model_validator(mode="after")
    def coherent_moment(self) -> Self:
        if self.day_index != self.minute // 1_440:
            raise ValueError("story moment day does not match its minute")
        if self.noticed != (self.status != "not_noticed"):
            raise ValueError("only unnoticed moments carry the not-noticed status")
        noticed_facts = (
            self.before,
            self.after,
            self.rule_delta,
            self.applied_delta,
            self.effect,
            self.ai_clause,
            self.rules_line,
            self.with_ai_line,
        )
        if any((value is None) == self.noticed for value in noticed_facts):
            raise ValueError("state changes and AI clauses belong to noticed moments only")
        if (self.labels is not None) != (self.status in LABELLED_STATUSES):
            raise ValueError("labels belong exactly to moments with an AI or practice answer")
        if self.safety_flags and self.labels is None:
            raise ValueError("safety flags belong to labelled answers only")
        if (self.reason is not None) != (self.status == "failed"):
            raise ValueError("a reason belongs exactly to failed answers")
        if self.math is not None and self.status not in MATH_STATUSES:
            raise ValueError("formula factors belong to applied AI or practice labels only")
        return self


class StoryPerson(_StoryModel):
    agent_id: _AgentId
    number: int = Field(ge=1, le=30)
    persona_id: _PersonaId
    anchors: StoryAnchors
    days: tuple[StoryDay, ...] = Field(min_length=1, max_length=7)
    totals: StoryTotals
    start: StoryState
    end: StoryState
    compare: StoryCompare
    label_mode: StoryLabels | None
    label_answers: int = Field(ge=0, le=4)
    summary: MessageRef
    moments: tuple[StoryMoment, ...] = Field(max_length=STORY_MOMENTS_PER_PERSON)
    moments_omitted: _Count

    @model_validator(mode="after")
    def coherent_person(self) -> Self:
        if self.number != int(self.agent_id.removeprefix("person-")):
            raise ValueError("story person number must match the agent ID")
        if tuple(day.day_index for day in self.days) != tuple(range(len(self.days))):
            raise ValueError("story person days must be contiguous from day zero")
        if (self.label_mode is None) != (self.label_answers == 0):
            raise ValueError("a label mode exists exactly when the person has answers")
        keys = [(m.minute, m.millisecond, m.moment_id) for m in self.moments]
        if keys != sorted(keys) or len({m.moment_id for m in self.moments}) != len(keys):
            raise ValueError("story moments must be unique and in time order")
        if len(self.moments) + self.moments_omitted != self.totals.shown:
            raise ValueError("kept and omitted moments must account for every ad moment")
        return self


class StoryKeyMoment(_StoryModel):
    agent_id: _AgentId
    moment_id: _MomentId


class StoryLimits(_StoryModel):
    moments_per_person: Literal[40]


class RunStoryV1(_StoryModel):
    """The complete story document served by ``GET /api/runs/{run_id}/story``."""

    schema_version: Literal[1]
    format: Literal["adlife-run-story-v1"]
    run: StoryRun
    ai: StoryAi
    funnel: StoryFunnel
    labels: StoryLabelCounts | None
    averages: StoryAverages
    headline: tuple[MessageRef, ...] = Field(min_length=2, max_length=6)
    map: StoryMap
    personas: tuple[StoryPersona, ...] = Field(min_length=1, max_length=30)
    people: tuple[StoryPerson, ...] = Field(min_length=1, max_length=30)
    key_moments: tuple[StoryKeyMoment, ...] = Field(max_length=1_200)
    limits: StoryLimits

    @model_validator(mode="after")
    def coherent_story(self) -> Self:
        agent_ids = [person.agent_id for person in self.people]
        if agent_ids != sorted(set(agent_ids)) or len(agent_ids) != self.run.people_count:
            raise ValueError("story people must be unique, complete and in agent order")
        owners = {
            agent_id: persona.persona_id
            for persona in self.personas
            for agent_id in persona.agent_ids
        }
        if len(owners) != sum(persona.count for persona in self.personas) or owners != {
            person.agent_id: person.persona_id for person in self.people
        }:
            raise ValueError("story personas must assign every person exactly once")
        if len({persona.persona_id for persona in self.personas}) != len(self.personas):
            raise ValueError("story persona identifiers must be unique")
        moments = {
            (person.agent_id, moment.moment_id): moment
            for person in self.people
            for moment in person.moments
        }
        keys = []
        for item in self.key_moments:
            moment = moments.get((item.agent_id, item.moment_id))
            if moment is None:
                raise ValueError("story key moments must name kept moments")
            keys.append((moment.minute, moment.millisecond, item.agent_id, item.moment_id))
        if keys != sorted(set(keys)):
            raise ValueError("story key moments must be unique and in time order")
        if (
            self.funnel.noticed != self.ai.coverage.noticed
            or self.funnel.chosen != self.ai.coverage.chosen
            or self.funnel.sent != self.ai.coverage.sent
            or self.funnel.shown != sum(person.totals.shown for person in self.people)
            or self.funnel.noticed != sum(person.totals.noticed for person in self.people)
        ):
            raise ValueError("story funnel, coverage and people disagree")
        if (self.labels is None) != (self.funnel.answered + self.ai.statuses.practice == 0):
            raise ValueError("story label counts exist exactly when answers exist")
        return self


def story_ref_context(story: RunStoryV1) -> RefContext:
    """Return the reference context both renderers use to name the story's people."""
    personas = {persona.persona_id: persona for persona in story.personas}
    people: dict[str, PersonLabel] = {}
    for person in story.people:
        persona = personas[person.persona_id]
        people[person.agent_id] = PersonLabel(
            template_key=persona.template_key,
            display_name=persona.display_name,
            number=person.number,
        )
    return RefContext(days=story.run.days, people=people)


__all__ = [
    "LABELLED_STATUSES",
    "MATH_STATUSES",
    "RUN_STORY_FORMAT",
    "STORY_MOMENTS_PER_PERSON",
    "Compare",
    "Effect",
    "LabelGroup",
    "RunStoryV1",
    "Status",
    "StoryAi",
    "StoryAiAverages",
    "StoryAiState",
    "StoryAnchors",
    "StoryAverages",
    "StoryBillboard",
    "StoryBounds",
    "StoryCampaign",
    "StoryChannel",
    "StoryCity",
    "StoryCompare",
    "StoryContract",
    "StoryCoverage",
    "StoryCreative",
    "StoryDay",
    "StoryDelta",
    "StoryEffect",
    "StoryFunnel",
    "StoryHashes",
    "StoryKeyMoment",
    "StoryLabelCounts",
    "StoryLabels",
    "StoryLimits",
    "StoryMap",
    "StoryMath",
    "StoryMoment",
    "StoryNode",
    "StoryNumber",
    "StoryPerson",
    "StoryPersona",
    "StoryPhone",
    "StoryRange",
    "StoryRoad",
    "StoryRuleState",
    "StoryRulesAverages",
    "StoryRun",
    "StoryScenario",
    "StoryState",
    "StoryStatuses",
    "StoryTotals",
    "StoryTraitChip",
    "StoryUsage",
    "StoryWindow",
    "TemplateKey",
    "TrackPoint",
    "TraitLevel",
    "TraitName",
    "story_ref_context",
]
