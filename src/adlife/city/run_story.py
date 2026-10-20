"""Build the path-free :class:`RunStoryV1` of one verified saved V10 run (spec 8.3).

The builder decides and the renderers translate: every status, key moment, comparison
word and sentence choice is made here once, and emitted as structured facts plus catalog
message references that the story page and the HTML report resolve the same way.

It is pure and deterministic. It reads only the verified ``StoredCityRun`` and the
packaged persona, creative and city catalogs, never a clock, a provider, a cache, the
environment or the filesystem, and it writes nothing. Formula factors are published
only when they reproduce the applied deltas exactly; otherwise they are omitted rather
than shown wrongly.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import fsum
from typing import Final, Literal, TypeAlias, cast, get_args

from adlife.city.analysis import response_metrics_for_stored_city_run
from adlife.city.catalog import load_verified_catalog_cities
from adlife.city.run_store import StoredCityRun
from adlife.city.workbench_cognition_views import (
    WorkbenchCognitionSummaryViewV2,
    build_workbench_cognition_summary_view,
)
from adlife.city.workbench_creatives import load_creative_template_catalog
from adlife.city.workbench_input import (
    NormalizedPhonePlacementDraft,
    NormalizedRoadsidePlacementDraft,
    WorkbenchPersonaDraftV2,
    WorkbenchRunInputV4,
)
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.city_cognition import CityCognitionSafetyFlag
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionAffectV2,
    CityCognitionCredibilityV2,
    CityCognitionEvaluationV2,
    CityCognitionInfluencePolicyV1,
    CityCognitionInputV2,
    CityCognitionRelevanceV2,
    CityCognitionRuntimeFallbackReasonV2,
    CityCognitionSalienceV2,
    CityCognitionTerminalRecordV2,
)
from adlife.core.domain.city_run import CityRunManifestV10
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    RoadsideBillboardPlacement,
    SpatialActiveWindow,
    SpatialActivity,
    SpatialCampaignScenario,
)
from adlife.core.domain.spatial_response import SpatialResponseInput, SpatialResponseTraits
from adlife.core.experiments.spatial_response_metrics_v2 import SpatialResponseMetricsV2
from adlife.core.simulation.city_mobility import CityMobility, CityPosition, CityRoadTraversal
from adlife.core.simulation.spatial_attention import SpatialAttentionEvaluation, SpatialImpression
from adlife.core.simulation.spatial_opportunity import (
    PhoneOpportunity,
    RoadsideOpportunity,
    SpatialOpportunityEvaluation,
)
from adlife.core.simulation.spatial_response_v2 import (
    SpatialAppliedResponseV2,
    SpatialDualStateUpdatedV2,
    SpatialResponseEvaluationV2,
)
from adlife.i18n.refs import (
    MessageRef,
    RefParam,
    clock_param,
    id_param,
    int_param,
    list_param,
    message_ref,
    num_param,
    person_param,
    text_param,
)
from adlife.reporting.run_story_model import (
    RUN_STORY_FORMAT,
    Compare,
    Effect,
    RunStoryV1,
    Status,
    StoryAi,
    StoryAiAverages,
    StoryAiState,
    StoryAnchors,
    StoryAverages,
    StoryBillboard,
    StoryBounds,
    StoryCampaign,
    StoryChannel,
    StoryCity,
    StoryCompare,
    StoryContract,
    StoryCoverage,
    StoryCreative,
    StoryDay,
    StoryDelta,
    StoryEffect,
    StoryFunnel,
    StoryHashes,
    StoryKeyMoment,
    StoryLabelCounts,
    StoryLabels,
    StoryLimits,
    StoryMap,
    StoryMath,
    StoryMoment,
    StoryNode,
    StoryPerson,
    StoryPersona,
    StoryPhone,
    StoryRange,
    StoryRoad,
    StoryRulesAverages,
    StoryRuleState,
    StoryRun,
    StoryScenario,
    StoryState,
    StoryStatuses,
    StoryTotals,
    StoryTraitChip,
    StoryUsage,
    StoryWindow,
    TemplateKey,
    TraitLevel,
    TraitName,
)

MAX_STORY_MOMENTS_PER_PERSON: Final = 40
MAX_STORY_BYTES: Final = 4_194_304
PER_PERSON_REQUESTS: Final = 4
PER_RUN_REQUESTS: Final = 60

# The three packaged fictional towns that have catalog names; a test pins this tuple to
# the packaged catalog so a new town cannot ship without its translated name.
PACKAGED_CITY_IDS: Final = (
    "fictional-grid-v2",
    "fictional-harbor-loop-v2",
    "fictional-river-junction-v2",
)
_TEMPLATE_KEYS: Final[Mapping[str, TemplateKey]] = {
    "persona-001": "lumo",
    "persona-002": "veyra",
    "persona-003": "orilo",
    "persona-004": "nuvin",
}
_TEMPLATE_COLOURS: Final[Mapping[TemplateKey, int]] = {
    "lumo": 0,
    "veyra": 1,
    "orilo": 2,
    "nuvin": 3,
}
_TRAIT_ORDER: Final[tuple[TraitName, ...]] = (
    "advertising_skepticism",
    "price_sensitivity",
    "novelty_seeking",
    "mobile_recall_encoding",
    "roadside_recall_encoding",
    "impulsivity",
)
_TRAIT_CHIP_DISTANCE: Final = 0.15 - 1e-9
_SAME_EFFECT: Final = 0.005
_SLIGHT_COMPARE: Final = 0.03
_FORMULA_TOLERANCE: Final = 1e-9
_ANSWER_SOURCES: Final = frozenset({"provider", "cache", "mock"})
_PROVIDER_MODES: Final = frozenset({"local", "remote"})
_SOURCE_STATUS: Final[Mapping[str, Status]] = {
    "provider": "answered",
    "cache": "reused",
    "mock": "practice",
    "rules": "rules",
    "fallback": "failed",
    "budget-skipped": "over_limit",
    "unselected": "not_chosen",
}
_AI_STATUSES: Final[frozenset[Status]] = frozenset(
    {"answered", "reused", "practice", "unsure", "warning", "failed"}
)
_FIRST_OF_STATUSES: Final[tuple[Status, ...]] = ("over_limit", "not_chosen", "not_noticed")
_RULES_KEY_NOTICED: Final = 20

_Where: TypeAlias = str


class RunStoryUnavailable(ValueError):
    """The run is not a complete schema-v10 bounded-cognition run; it has no story."""


class RunStoryTooLarge(ValueError):
    """The story's canonical JSON exceeds :data:`MAX_STORY_BYTES`."""


def _story_keys() -> frozenset[str]:
    statuses = [status for status in get_args(Status) if status != "not_noticed"]
    return frozenset(
        {
            "common.days",
            "common.people",
            *(f"city.{city_id}" for city_id in PACKAGED_CITY_IDS),
            "story.headline.ai",
            "story.headline.nobody_noticed",
            "story.headline.noticed",
            "story.headline.people",
            "story.headline.practice",
            "story.headline.rules",
            "story.headline.shown",
            *(
                f"story.headline.compare.{measure}.{bucket}"
                for measure in ("liking", "memory")
                for bucket in get_args(Compare)
            ),
            "story.person.never_noticed",
            "story.person.never_shown",
            "story.person.noticed",
            "story.moment.missed",
            "story.moment.noticed",
            "story.moment.rules_line",
            "story.moment.with_ai_line",
            "story.moment.with_ai_same",
            *(f"story.where.mobile-feed.{activity}" for activity in get_args(SpatialActivity)),
            "story.where.roadside-billboard",
            *(f"story.ai.{status}" for status in statuses),
            *(f"flag.{flag}" for flag in get_args(CityCognitionSafetyFlag)),
            *(f"reason.{reason}" for reason in get_args(CityCognitionRuntimeFallbackReasonV2)),
        }
    )


STORY_MESSAGE_KEYS: Final[frozenset[str]] = _story_keys()


@dataclass(frozen=True, slots=True)
class _Evidence:
    manifest: CityRunManifestV10
    workbench_input: WorkbenchRunInputV4
    scenario: SpatialCampaignScenario
    opportunities: SpatialOpportunityEvaluation
    attention: SpatialAttentionEvaluation
    response_input: SpatialResponseInput
    response: SpatialResponseEvaluationV2
    cognition_input: CityCognitionInputV2
    cognition: CityCognitionEvaluationV2


@dataclass(frozen=True, slots=True)
class _Moment:
    """One opportunity classified once; positions and sentences are built only if kept."""

    opportunity: RoadsideOpportunity | PhoneOpportunity
    noticed: bool
    status: Status
    applied: SpatialAppliedResponseV2 | None
    dual: SpatialDualStateUpdatedV2 | None
    terminal: CityCognitionTerminalRecordV2 | None

    @property
    def agent_id(self) -> str:
        return self.opportunity.agent_id

    @property
    def moment_id(self) -> str:
        return self.opportunity.opportunity_id[:16]

    @property
    def time_key(self) -> tuple[int, int, str]:
        opportunity = self.opportunity
        return opportunity.model_minute, opportunity.millisecond_within_minute, self.moment_id

    @property
    def priority(self) -> int:
        if self.terminal is not None:
            return 0
        return 1 if self.noticed else 2


def _inconsistent() -> ValueError:
    return ValueError("verified run evidence is inconsistent with its story")


def _r6(value: float) -> float:
    if not math.isfinite(value):
        raise _inconsistent()
    return round(value, 6) + 0.0


def _evidence(stored: StoredCityRun) -> _Evidence:
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    manifest = stored.manifest
    if (
        type(manifest) is not CityRunManifestV10
        or type(stored.workbench_input) is not WorkbenchRunInputV4
        or type(stored.response_evaluation) is not SpatialResponseEvaluationV2
        or type(stored.cognition_input) is not CityCognitionInputV2
        or type(stored.cognition_evaluation) is not CityCognitionEvaluationV2
        or stored.spatial_scenario is None
        or stored.opportunity_evaluation is None
        or stored.attention_evaluation is None
        or stored.response_input is None
    ):
        raise RunStoryUnavailable("only complete schema-v10 runs have a story")
    return _Evidence(
        manifest=manifest,
        workbench_input=stored.workbench_input,
        scenario=stored.spatial_scenario,
        opportunities=stored.opportunity_evaluation,
        attention=stored.attention_evaluation,
        response_input=stored.response_input,
        response=stored.response_evaluation,
        cognition_input=stored.cognition_input,
        cognition=stored.cognition_evaluation,
    )


# --- classification ----------------------------------------------------------------------


def _status(applied: SpatialAppliedResponseV2) -> Status:
    if applied.neutralization_status == "uncertain":
        return "unsure"
    if applied.neutralization_status == "safety":
        return "warning"
    return _SOURCE_STATUS[applied.source]


def _classify(evidence: _Evidence) -> list[_Moment]:
    impressions: dict[str, bool] = {
        event.opportunity_id: event.noticed
        for event in evidence.attention.events
        if isinstance(event, SpatialImpression)
    }
    applied_by_opportunity: dict[str, SpatialAppliedResponseV2] = {}
    dual_by_cause: dict[str, SpatialDualStateUpdatedV2] = {}
    for record in evidence.response.records:
        if isinstance(record, SpatialAppliedResponseV2):
            applied_by_opportunity[record.baseline.opportunity_id] = record
        else:
            for cause in record.caused_by_event_ids:
                dual_by_cause[cause] = record
    terminals = {record.record_hash: record for record in evidence.cognition.records}
    moments: list[_Moment] = []
    for opportunity in evidence.opportunities.opportunities:
        noticed = impressions.get(opportunity.opportunity_id)
        if noticed is None:
            raise _inconsistent()
        if not noticed:
            moments.append(_Moment(opportunity, False, "not_noticed", None, None, None))
            continue
        applied = applied_by_opportunity.get(opportunity.opportunity_id)
        if applied is None or applied.event_id not in dual_by_cause:
            raise _inconsistent()
        terminal = None
        if applied.terminal_record_hash is not None:
            terminal = terminals.get(applied.terminal_record_hash)
            if terminal is None:
                raise _inconsistent()
        moments.append(
            _Moment(
                opportunity,
                True,
                _status(applied),
                applied,
                dual_by_cause[applied.event_id],
                terminal,
            )
        )
    moments.sort(key=_global_key)
    return moments


def _kept_moments(moments: Sequence[_Moment]) -> list[_Moment]:
    """Keep at most 40 moments: terminal answers first, then noticed, then unnoticed."""
    kept: list[_Moment] = []
    for priority in (0, 1, 2):
        for moment in moments:
            if moment.priority == priority and len(kept) < MAX_STORY_MOMENTS_PER_PERSON:
                kept.append(moment)
    return sorted(kept, key=lambda moment: moment.time_key)


def _global_key(moment: _Moment) -> tuple[int, int, str, str]:
    minute, millisecond, moment_id = moment.time_key
    return minute, millisecond, moment.agent_id, moment_id


def _key_moments(kept: Sequence[_Moment], mode: str) -> list[_Moment]:
    """Every AI moment plus the first of each telling non-AI kind, in time order."""
    ordered = sorted(kept, key=_global_key)
    if mode == "rules":
        selected = [moment for moment in ordered if moment.noticed][:_RULES_KEY_NOTICED]
        firsts: tuple[Status, ...] = ("not_noticed",)
    else:
        selected = [moment for moment in ordered if moment.status in _AI_STATUSES]
        firsts = _FIRST_OF_STATUSES
    chosen = {_global_key(moment) for moment in selected}
    for status in firsts:
        first = next((moment for moment in ordered if moment.status == status), None)
        if first is not None and _global_key(first) not in chosen:
            chosen.add(_global_key(first))
            selected.append(first)
    return sorted(selected, key=_global_key)


# --- small decisions ---------------------------------------------------------------------


def _effect(applied: float, rule: float) -> Effect:
    difference = applied - rule
    if abs(difference) < _SAME_EFFECT:
        return "same"
    return "higher" if difference > 0 else "lower"


def compare_bucket(with_ai: float, rules: float) -> Compare:
    """Word the end-state gap between the with-AI and rules-only scores."""
    difference = with_ai - rules
    magnitude = abs(difference)
    if magnitude < _SAME_EFFECT:
        return "about_same"
    if magnitude < _SLIGHT_COMPARE:
        return "slightly_higher" if difference > 0 else "slightly_lower"
    return "higher" if difference > 0 else "lower"


def trait_chips(traits: SpatialResponseTraits) -> tuple[StoryTraitChip, ...]:
    """Pick at most three clearly high or low traits, strongest first."""
    candidates: list[tuple[float, int, TraitName, TraitLevel]] = []
    for index, name in enumerate(_TRAIT_ORDER):
        value = float(getattr(traits, name))
        distance = abs(value - 0.5)
        if distance >= _TRAIT_CHIP_DISTANCE:
            # Rounding treats float noise (0.65 - 0.5 against 0.5 - 0.35) as a true tie.
            level: TraitLevel = "high" if value > 0.5 else "low"
            candidates.append((-round(distance, 9), index, name, level))
    candidates.sort()
    return tuple(StoryTraitChip(trait=name, level=level) for _, _, name, level in candidates[:3])


def _mode_value(values: Iterable[str], order: tuple[str, ...]) -> str:
    counts = Counter(values)
    return max(order, key=lambda value: (counts[value], -order.index(value)))


def _label_mode(records: Sequence[CityCognitionTerminalRecordV2]) -> StoryLabels | None:
    if not records:
        return None
    results = [record.result for record in records]
    return StoryLabels(
        affect=cast(
            CityCognitionAffectV2,
            _mode_value((r.affect for r in results), get_args(CityCognitionAffectV2)),
        ),
        relevance=cast(
            CityCognitionRelevanceV2,
            _mode_value((r.relevance for r in results), get_args(CityCognitionRelevanceV2)),
        ),
        credibility=cast(
            CityCognitionCredibilityV2,
            _mode_value((r.credibility for r in results), get_args(CityCognitionCredibilityV2)),
        ),
        salience=cast(
            CityCognitionSalienceV2,
            _mode_value((r.salience for r in results), get_args(CityCognitionSalienceV2)),
        ),
    )


def _model_param(model_id: str) -> RefParam:
    if len(model_id) <= 120 and all(" " <= character <= "~" for character in model_id):
        return id_param(model_id)
    return text_param(model_id[:240])


# --- run facts ---------------------------------------------------------------------------


def _day_windows(windows: Sequence[SpatialActiveWindow]) -> tuple[StoryWindow, ...]:
    result = []
    for window in windows:
        day_start = (window.start_minute // 1_440) * 1_440
        result.append(
            StoryWindow(
                day=window.start_minute // 1_440 + 1,
                start_minute=window.start_minute - day_start,
                end_minute=window.end_minute - day_start,
            )
        )
    return tuple(result)


def _phone(placement: NormalizedPhonePlacementDraft | None) -> StoryPhone | None:
    if placement is None:
        return None
    return StoryPhone(
        windows=_day_windows(placement.active_windows),
        cap=placement.frequency_cap_per_agent_per_day,
        eligible_activities=placement.eligible_activities,
        chance_per_minute=placement.opportunity_probability_per_minute,
    )


def _billboard(
    placement: NormalizedRoadsidePlacementDraft | None,
    scenario: SpatialCampaignScenario,
) -> StoryBillboard | None:
    if placement is None:
        return None
    frozen = next(
        (
            item
            for item in scenario.placements
            if isinstance(item, RoadsideBillboardPlacement)
            and item.placement_id == placement.placement_id
        ),
        None,
    )
    if frozen is None:
        raise _inconsistent()
    return StoryBillboard(
        road_id=placement.road_id,
        side=placement.side,
        travel_direction=placement.travel_direction,
        road_fraction=placement.road_fraction,
        cap=placement.frequency_cap_per_agent_per_day,
        windows=_day_windows(placement.active_windows),
        position=(frozen.longitude, frozen.latitude),
    )


def _run_facts(stored: StoredCityRun, evidence: _Evidence) -> StoryRun:
    draft = evidence.workbench_input.draft
    scenario = draft.scenario
    campaign = scenario.campaign
    creative = evidence.workbench_input.creative_template
    pack = stored.pack
    packaged_names = {item.entry.city_id: item.pack.name for item in load_verified_catalog_cities()}
    city_key = (
        pack.city_id
        if pack.city_id in PACKAGED_CITY_IDS and packaged_names.get(pack.city_id) == pack.name
        else None
    )
    packaged_creative = next(
        (
            template
            for template in load_creative_template_catalog().templates
            if template.template_id == creative.template_id
        ),
        None,
    )
    creative_key = (
        creative.template_id
        if packaged_creative is not None
        and canonical_json(packaged_creative) == canonical_json(creative)
        else None
    )
    channels: list[StoryChannel] = []
    if scenario.phone is not None:
        channels.append("mobile-feed")
    if scenario.roadside is not None:
        channels.append("roadside-billboard")
    manifest = evidence.manifest
    return StoryRun(
        run_id=manifest.run_id,
        seed=str(manifest.seed),
        days=manifest.days,
        people_count=manifest.agent_count,
        city=StoryCity(city_id=pack.city_id, name=pack.name, catalog_key=city_key),
        scenario=StoryScenario(scenario_id=scenario.scenario_id, name=scenario.name),
        campaign=StoryCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            target_interests=tuple(sorted(campaign.target_interests)),
            relative_price=campaign.relative_price,
        ),
        creative=StoryCreative(
            template_id=creative.template_id,
            catalog_key=creative_key,
            product_name=creative.product_name,
            product_category=creative.product_category,
            message=creative.message,
            call_to_action=creative.call_to_action,
        ),
        channels=tuple(channels),
        phone=_phone(scenario.phone),
        billboard=_billboard(scenario.roadside, evidence.scenario),
        hashes=StoryHashes(
            workbench_input_sha256=manifest.workbench_input_sha256,
            cognition_input_sha256=manifest.cognition.cognition_input_sha256,
            causal_contract_sha256=evidence.cognition_input.causal_contract_sha256,
        ),
    )


# --- map and tracks ----------------------------------------------------------------------


def _length_meters(start: tuple[float, float], end: tuple[float, float]) -> float:
    """The same haversine as ``city_mobility._length_meters``, on (longitude, latitude)."""
    lat1, lat2 = math.radians(start[1]), math.radians(end[1])
    delta_lat = lat2 - lat1
    delta_lon = math.radians(end[0] - start[0])
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 12_742_000.0 * math.asin(min(1.0, math.sqrt(value)))


def road_geometry(stored: StoredCityRun) -> dict[str, tuple[tuple[float, float], ...]]:
    """Each road's points from source to target node, shape points included."""
    pack = stored.pack
    nodes = {node.node_id: (node.longitude, node.latitude) for node in pack.nodes}
    geometry: dict[str, tuple[tuple[float, float], ...]] = {}
    if isinstance(pack, CityPackV2):
        for road in pack.roads:
            shape = tuple((point.longitude, point.latitude) for point in road.shape)
            geometry[road.road_id] = (nodes[road.source_node], *shape, nodes[road.target_node])
    else:
        for legacy in pack.roads:
            geometry[legacy.road_id] = (nodes[legacy.source_node], nodes[legacy.target_node])
    return geometry


def _story_map(
    stored: StoredCityRun, geometry: Mapping[str, Sequence[tuple[float, float]]]
) -> StoryMap:
    pack = stored.pack
    if isinstance(pack, CityPackV2):
        bounds = StoryBounds(
            west=pack.bounds.west,
            south=pack.bounds.south,
            east=pack.bounds.east,
            north=pack.bounds.north,
        )
    else:
        bounds = StoryBounds(
            west=min(node.longitude for node in pack.nodes),
            south=min(node.latitude for node in pack.nodes),
            east=max(node.longitude for node in pack.nodes),
            north=max(node.latitude for node in pack.nodes),
        )
    return StoryMap(
        bounds=bounds,
        nodes=tuple(
            StoryNode(node_id=node.node_id, longitude=node.longitude, latitude=node.latitude)
            for node in pack.nodes
        ),
        roads=tuple(
            StoryRoad(road_id=road.road_id, kind=road.kind, points=tuple(geometry[road.road_id]))
            for road in pack.roads
        ),
    )


RawTrack: TypeAlias = list[tuple[float, float, float]]


def raw_leg_track(
    traversals: Sequence[CityRoadTraversal],
    geometry: Mapping[str, Sequence[tuple[float, float]]],
) -> RawTrack:
    """Return one leg as unrounded ``(minute, longitude, latitude)`` points.

    A road's vertex times are proportional to haversine distance, exactly as
    ``CityMobility`` interpolates a position within one road at constant speed.
    """
    track: RawTrack = []
    for traversal in sorted(traversals, key=lambda item: item.road_sequence):
        points = list(geometry[traversal.road_id])
        if traversal.travel_direction == "backward":
            points.reverse()
        lengths = [_length_meters(start, end) for start, end in pairwise(points)]
        total = sum(lengths)
        if total <= 0:
            raise _inconsistent()
        duration = traversal.end_minute - traversal.start_minute
        travelled = 0.0
        for index, (longitude, latitude) in enumerate(points):
            if index:
                travelled += lengths[index - 1]
            minute = (
                traversal.end_minute
                if index == len(points) - 1
                else traversal.start_minute + duration * travelled / total
            )
            if index == 0 and track and (track[-1][1], track[-1][2]) == (longitude, latitude):
                continue  # The joint vertex is the previous road's last point.
            track.append((minute, longitude, latitude))
    if len(track) < 2:
        raise _inconsistent()
    return track


def raw_day_tracks(
    mobility: CityMobility,
    geometry: Mapping[str, Sequence[tuple[float, float]]],
) -> dict[str, list[tuple[int, Literal["work", "leisure"], RawTrack, RawTrack]]]:
    """Unrounded outbound and return tracks of every agent for every simulated day."""
    tracks: dict[str, list[tuple[int, Literal["work", "leisure"], RawTrack, RawTrack]]] = (
        defaultdict(list)
    )
    for day_index in range(mobility.days):
        legs: dict[tuple[str, str], list[CityRoadTraversal]] = defaultdict(list)
        for traversal in mobility.road_traversals(day_index):
            legs[traversal.agent_id, traversal.leg].append(traversal)
        destination: Literal["work", "leisure"] = "leisure" if day_index % 7 in {5, 6} else "work"
        for agent in mobility.agents:
            tracks[agent.agent_id].append(
                (
                    day_index,
                    destination,
                    raw_leg_track(legs[agent.agent_id, "outbound"], geometry),
                    raw_leg_track(legs[agent.agent_id, "return"], geometry),
                )
            )
    return tracks


def _story_days(
    days: Sequence[tuple[int, Literal["work", "leisure"], RawTrack, RawTrack]],
) -> tuple[StoryDay, ...]:
    return tuple(
        StoryDay(
            day_index=day_index,
            destination=destination,
            outbound=tuple(outbound),
            inbound=tuple(inbound),
        )
        for day_index, destination, outbound, inbound in days
    )


# --- personas ----------------------------------------------------------------------------


def _template_key(
    persona: WorkbenchPersonaDraftV2, templates: Sequence[WorkbenchPersonaDraftV2]
) -> TemplateKey | None:
    facts = persona.model_dump(mode="json", exclude={"persona_id", "count"})
    for template in templates:
        if template.model_dump(mode="json", exclude={"persona_id", "count"}) == facts:
            return _TEMPLATE_KEYS.get(template.persona_id)
    return None


def _personas(evidence: _Evidence) -> tuple[StoryPersona, ...]:
    population = evidence.workbench_input.draft.population
    templates = load_persona_template_catalog().templates
    agents: dict[str, list[str]] = defaultdict(list)
    for assignment in population.assignments:
        agents[assignment.persona_id].append(assignment.agent_id)
    personas: list[StoryPersona] = []
    others = 0
    for persona in population.personas:
        key = _template_key(persona, templates)
        if key is None:
            colour = 4 + others
            others += 1
        else:
            colour = _TEMPLATE_COLOURS[key]
        personas.append(
            StoryPersona(
                persona_id=persona.persona_id,
                template_key=key,
                display_name=persona.display_name,
                count=persona.count,
                interests=tuple(sorted(persona.interests)),
                trait_chips=trait_chips(persona.traits),
                color_index=colour,
                agent_ids=tuple(sorted(agents[persona.persona_id])),
            )
        )
    return tuple(personas)


# --- moments -----------------------------------------------------------------------------


def _rule_state(
    brand_sentiment: float, recall_strength: float, purchase_intention: float
) -> StoryRuleState:
    return StoryRuleState(
        liking=brand_sentiment, memory=recall_strength, purchase_interest=purchase_intention
    )


def _states(dual: SpatialDualStateUpdatedV2) -> tuple[StoryState, StoryState]:
    previous_rule, rule = dual.previous_rule_state, dual.rule_state
    previous_ai, ai = dual.previous_cognition_state, dual.cognition_state
    before = StoryState(
        rules=_rule_state(
            previous_rule.brand_sentiment,
            previous_rule.recall_strength,
            previous_rule.purchase_intention,
        ),
        with_ai=StoryAiState(
            liking=previous_ai.synthetic_sentiment, memory=previous_ai.synthetic_recall
        ),
    )
    after = StoryState(
        rules=_rule_state(rule.brand_sentiment, rule.recall_strength, rule.purchase_intention),
        with_ai=StoryAiState(liking=ai.synthetic_sentiment, memory=ai.synthetic_recall),
    )
    return before, after


def formula_math(
    labels: StoryLabels,
    rule_delta: tuple[float, float],
    applied_delta: tuple[float, float],
    policy: CityCognitionInfluencePolicyV1,
) -> StoryMath | None:
    """Return the formula's factors, or None when they do not reproduce the applied deltas."""
    tables = (
        ({row.category: row.weight for row in policy.affect_adjustments}, labels.affect),
        ({row.category: row.weight for row in policy.relevance_factors}, labels.relevance),
        ({row.category: row.weight for row in policy.credibility_factors}, labels.credibility),
        ({row.category: row.weight for row in policy.salience_factors}, labels.salience),
    )
    factors = [table.get(category) for table, category in tables]
    if any(factor is None for factor in factors):
        return None
    affect, relevance, credibility, salience = cast(list[float], factors)
    rule_sentiment, rule_recall = rule_delta
    # The same operation order as the frozen influence policy implementation.
    liking_raw = fsum((rule_sentiment, affect)) * relevance * credibility
    memory_raw = rule_recall * relevance * credibility * salience
    liking = max(policy.sentiment_minimum, min(policy.sentiment_maximum, liking_raw))
    memory = max(policy.recall_minimum, min(policy.recall_maximum, memory_raw))
    if (
        abs(liking - applied_delta[0]) > _FORMULA_TOLERANCE
        or abs(memory - applied_delta[1]) > _FORMULA_TOLERANCE
    ):
        return None
    return StoryMath(
        affect_adjustment=affect,
        relevance_factor=relevance,
        credibility_factor=credibility,
        salience_factor=salience,
        liking_raw=liking_raw,
        memory_raw=memory_raw,
        liking_limit=policy.sentiment_maximum,
        memory_min=policy.recall_minimum,
        memory_max=policy.recall_maximum,
    )


def _where(moment: _Moment, activity: SpatialActivity) -> _Where:
    if moment.opportunity.channel == "roadside-billboard":
        return "story.where.roadside-billboard"
    return f"story.where.mobile-feed.{activity}"


def _ai_clause(moment: _Moment, flags: Sequence[str], funded_limit: int) -> MessageRef:
    status = moment.status
    if status == "warning":
        return message_ref(
            "story.ai.warning", flags=list_param([message_ref(f"flag.{flag}") for flag in flags])
        )
    if status == "failed":
        assert moment.terminal is not None and moment.terminal.reason is not None
        return message_ref(
            "story.ai.failed", reason=message_ref(f"reason.{moment.terminal.reason}")
        )
    if status == "over_limit":
        return message_ref("story.ai.over_limit", limit=int_param(funded_limit))
    if status == "not_chosen":
        return message_ref(
            "story.ai.not_chosen",
            per_person=int_param(PER_PERSON_REQUESTS),
            per_run=int_param(PER_RUN_REQUESTS),
        )
    return message_ref(f"story.ai.{status}")


def _state_line(key: str, before: tuple[float, float], after: tuple[float, float]) -> MessageRef:
    return message_ref(
        key,
        liking_before=num_param(before[0], 2),
        liking_after=num_param(after[0], 2),
        memory_before=num_param(before[1], 2),
        memory_after=num_param(after[1], 2),
    )


def _story_moment(
    moment: _Moment,
    position: CityPosition,
    *,
    product: str,
    funded_limit: int,
    policy: CityCognitionInfluencePolicyV1,
) -> StoryMoment:
    opportunity = moment.opportunity
    activity: SpatialActivity = position.activity
    sentence = message_ref(
        "story.moment.noticed" if moment.noticed else "story.moment.missed",
        time=clock_param(opportunity.model_minute),
        name=person_param(opportunity.agent_id),
        product=text_param(product),
        where=message_ref(_where(moment, activity)),
    )
    if not moment.noticed:
        return StoryMoment(
            moment_id=moment.moment_id,
            minute=opportunity.model_minute,
            millisecond=opportunity.millisecond_within_minute,
            day_index=opportunity.day_index,
            channel=opportunity.channel,
            activity=activity,
            position=(position.longitude, position.latitude),
            noticed=False,
            status=moment.status,
            labels=None,
            safety_flags=(),
            reason=None,
            before=None,
            after=None,
            rule_delta=None,
            applied_delta=None,
            effect=None,
            math=None,
            sentence=sentence,
            ai_clause=None,
            rules_line=None,
            with_ai_line=None,
        )
    applied, dual, terminal = moment.applied, moment.dual, moment.terminal
    if applied is None or dual is None:
        raise _inconsistent()
    labels: StoryLabels | None = None
    flags: tuple[CityCognitionSafetyFlag, ...] = ()
    if terminal is not None and terminal.source in _ANSWER_SOURCES:
        result = terminal.result
        labels = StoryLabels(
            affect=result.affect,
            relevance=result.relevance,
            credibility=result.credibility,
            salience=result.salience,
        )
        flags = tuple(result.safety_flags)
    reason: CityCognitionRuntimeFallbackReasonV2 | None = None
    if moment.status == "failed":
        if terminal is None or terminal.reason is None or terminal.reason == "budget-skipped":
            raise _inconsistent()
        reason = terminal.reason
    before, after = _states(dual)
    rule = (_r6(applied.baseline.sentiment_delta), _r6(applied.baseline.recall_delta))
    exact_applied = (applied.applied_sentiment_delta, applied.applied_recall_delta)
    shown_applied = (_r6(exact_applied[0]), _r6(exact_applied[1]))
    effect = StoryEffect(
        liking=_effect(shown_applied[0], rule[0]), memory=_effect(shown_applied[1], rule[1])
    )
    math_facts = None
    if labels is not None and moment.status in {"answered", "reused", "practice"}:
        math_facts = formula_math(
            labels,
            (applied.baseline.sentiment_delta, applied.baseline.recall_delta),
            exact_applied,
            policy,
        )
    rules_line = _state_line(
        "story.moment.rules_line",
        (before.rules.liking, before.rules.memory),
        (after.rules.liking, after.rules.memory),
    )
    # The values always show: earlier AI moments can leave the two tracks apart even when this
    # moment changed both the same way.
    with_ai_line = _state_line(
        "story.moment.with_ai_same"
        if effect.liking == "same" and effect.memory == "same"
        else "story.moment.with_ai_line",
        (before.with_ai.liking, before.with_ai.memory),
        (after.with_ai.liking, after.with_ai.memory),
    )
    return StoryMoment(
        moment_id=moment.moment_id,
        minute=opportunity.model_minute,
        millisecond=opportunity.millisecond_within_minute,
        day_index=opportunity.day_index,
        channel=opportunity.channel,
        activity=activity,
        position=(position.longitude, position.latitude),
        noticed=True,
        status=moment.status,
        labels=labels,
        safety_flags=flags,
        reason=reason,
        before=before,
        after=after,
        rule_delta=StoryDelta(liking=rule[0], memory=rule[1]),
        applied_delta=StoryDelta(liking=shown_applied[0], memory=shown_applied[1]),
        effect=effect,
        math=math_facts,
        sentence=sentence,
        ai_clause=_ai_clause(moment, flags, funded_limit),
        rules_line=rules_line,
        with_ai_line=with_ai_line,
    )


# --- people ------------------------------------------------------------------------------


def _totals(moments: Sequence[_Moment], *, provider_mode: bool) -> StoryTotals:
    statuses = Counter(moment.status for moment in moments)
    sent = sum(
        moment.terminal is not None and moment.terminal.source in {"provider", "cache", "fallback"}
        for moment in moments
    )
    return StoryTotals(
        shown=len(moments),
        noticed=sum(moment.noticed for moment in moments),
        chosen=sum(moment.terminal is not None for moment in moments),
        sent=sent if provider_mode else 0,
        used=statuses["answered"] + statuses["reused"] + statuses["practice"],
        set_aside=statuses["unsure"] + statuses["warning"],
        failed=statuses["failed"],
        over_limit=statuses["over_limit"],
        not_chosen=statuses["not_chosen"],
    )


def _person_summary(totals: StoryTotals) -> MessageRef:
    if totals.shown == 0:
        return message_ref("story.person.never_shown")
    if totals.noticed == 0:
        return message_ref("story.person.never_noticed", count=totals.shown)
    return message_ref(
        "story.person.noticed",
        count=totals.shown,
        noticed=int_param(totals.noticed),
        sent=int_param(totals.sent),
    )


def _start_state(initial: tuple[float, float, float]) -> StoryState:
    liking, memory, purchase = initial
    return StoryState(
        rules=_rule_state(liking, memory, purchase),
        with_ai=StoryAiState(liking=liking, memory=memory),
    )


def _people(
    stored: StoredCityRun,
    evidence: _Evidence,
    moments: Sequence[_Moment],
    personas: Sequence[StoryPersona],
    geometry: Mapping[str, Sequence[tuple[float, float]]],
) -> tuple[tuple[StoryPerson, ...], list[_Moment]]:
    mobility = stored.mobility
    tracks = raw_day_tracks(mobility, geometry)
    persona_of = {
        agent_id: persona.persona_id for persona in personas for agent_id in persona.agent_ids
    }
    initial = {
        state.agent_id: (state.brand_sentiment, state.recall_strength, state.purchase_intention)
        for state in evidence.response_input.initial_states
    }
    final_rules = {state.agent_id: state for state in evidence.response.final_rule_states}
    final_ai = {state.agent_id: state for state in evidence.response.final_cognition_states}
    by_agent: dict[str, list[_Moment]] = defaultdict(list)
    for moment in moments:
        by_agent[moment.agent_id].append(moment)
    provider_mode = evidence.cognition_input.mode in _PROVIDER_MODES
    product = evidence.workbench_input.creative_template.product_name
    policy = evidence.cognition_input.causal_contract.influence_policy
    funded_limit = evidence.cognition_input.funded_response_limit
    frames: dict[int, dict[str, CityPosition]] = {}
    people: list[StoryPerson] = []
    all_kept: list[_Moment] = []
    for agent in mobility.agents:
        agent_id = agent.agent_id
        if agent_id not in persona_of or agent_id not in initial:
            raise _inconsistent()
        own = sorted(by_agent.get(agent_id, []), key=lambda moment: moment.time_key)
        kept = _kept_moments(own)
        all_kept.extend(kept)
        story_moments = []
        for moment in kept:
            minute = moment.opportunity.model_minute
            if minute not in frames:
                frames[minute] = {
                    position.agent_id: position for position in mobility.frame(minute)
                }
            story_moments.append(
                _story_moment(
                    moment,
                    frames[minute][agent_id],
                    product=product,
                    funded_limit=funded_limit,
                    policy=policy,
                )
            )
        answers = [
            moment.terminal
            for moment in own
            if moment.terminal is not None and moment.terminal.source in _ANSWER_SOURCES
        ]
        totals = _totals(own, provider_mode=provider_mode)
        rule_end, ai_end = final_rules[agent_id], final_ai[agent_id]
        end = StoryState(
            rules=_rule_state(
                rule_end.brand_sentiment, rule_end.recall_strength, rule_end.purchase_intention
            ),
            with_ai=StoryAiState(liking=ai_end.synthetic_sentiment, memory=ai_end.synthetic_recall),
        )
        people.append(
            StoryPerson(
                agent_id=agent_id,
                number=int(agent_id.removeprefix("person-")),
                persona_id=persona_of[agent_id],
                anchors=StoryAnchors(
                    home=agent.home_node, work=agent.work_node, leisure=agent.leisure_node
                ),
                days=_story_days(tracks[agent_id]),
                totals=totals,
                start=_start_state(initial[agent_id]),
                end=end,
                compare=StoryCompare(
                    liking=compare_bucket(end.with_ai.liking, end.rules.liking),
                    memory=compare_bucket(end.with_ai.memory, end.rules.memory),
                ),
                label_mode=_label_mode(answers),
                label_answers=len(answers),
                summary=_person_summary(totals),
                moments=tuple(story_moments),
                moments_omitted=len(own) - len(kept),
            )
        )
    return tuple(people), all_kept


# --- aggregates --------------------------------------------------------------------------


def _label_counts(evidence: _Evidence) -> StoryLabelCounts | None:
    answers = [record for record in evidence.cognition.records if record.source in _ANSWER_SOURCES]
    if not answers:
        return None
    results = [record.result for record in answers]
    return StoryLabelCounts(
        group="practice" if evidence.cognition_input.mode == "mock" else "ai",
        answers=len(answers),
        affect={
            value: sum(r.affect == value for r in results)
            for value in get_args(CityCognitionAffectV2)
        },
        relevance={
            value: sum(r.relevance == value for r in results)
            for value in get_args(CityCognitionRelevanceV2)
        },
        credibility={
            value: sum(r.credibility == value for r in results)
            for value in get_args(CityCognitionCredibilityV2)
        },
        salience={
            value: sum(r.salience == value for r in results)
            for value in get_args(CityCognitionSalienceV2)
        },
        safety_flags={
            flag: sum(flag in r.safety_flags for r in results)
            for flag in get_args(CityCognitionSafetyFlag)
        },
    )


def _ai_facts(stored: StoredCityRun, evidence: _Evidence, moments: Sequence[_Moment]) -> StoryAi:
    view = build_workbench_cognition_summary_view(stored)
    if not isinstance(view, WorkbenchCognitionSummaryViewV2):
        raise RunStoryUnavailable("only complete schema-v10 runs have a story")
    records = evidence.cognition.records
    coverage = view.summary.coverage
    provider_mode = view.mode in _PROVIDER_MODES
    statuses = Counter(moment.status for moment in moments if moment.noticed)
    usage = [record for record in records if record.source in {"provider", "fallback"}]
    reasons = Counter(
        cast(CityCognitionRuntimeFallbackReasonV2, record.reason)
        for record in records
        if record.source == "fallback"
    )
    profile = evidence.cognition_input.provider_profile
    return StoryAi(
        mode=view.mode,
        model_id=view.model_id,
        provider_profile_id=view.provider_profile_id,
        provider_revision_sha256=view.provider_revision_sha256,
        thinking_off=False if profile is None else profile.disable_thinking,
        funded_limit=view.funded_response_limit,
        per_person=PER_PERSON_REQUESTS,
        per_run=PER_RUN_REQUESTS,
        coverage=StoryCoverage(
            noticed=sum(moment.noticed for moment in moments),
            chosen=coverage.selected_request_count,
            sent=coverage.funded_request_count if provider_mode else 0,
            over_limit=coverage.budget_skipped_count,
            not_chosen=coverage.unselected_response_count,
        ),
        statuses=StoryStatuses(
            **{status: statuses[status] for status in get_args(Status) if status != "not_noticed"}
        ),
        fallback_reasons=dict(sorted(reasons.items())),
        usage_this_run=StoryUsage(
            attempts=sum(record.attempt_count for record in usage),
            prompt_tokens=sum(record.prompt_tokens or 0 for record in usage),
            completion_tokens=sum(record.completion_tokens or 0 for record in usage),
        ),
        reused_answers=sum(record.source == "cache" for record in records),
        contract=StoryContract(
            prompt_contract_id=view.prompt_contract_id,
            prompt_contract_sha256=view.prompt_contract_sha256,
            result_schema_id=view.result_schema_id,
            influence_policy_id=view.influence_policy_id,
            influence_policy_sha256=view.influence_policy_sha256,
        ),
    )


def _averages(stored: StoredCityRun) -> StoryAverages:
    metrics = response_metrics_for_stored_city_run(stored)
    if not isinstance(metrics, SpatialResponseMetricsV2):
        raise RunStoryUnavailable("only complete schema-v10 runs have a story")
    rule = metrics.rule_baseline
    ai = metrics.provider_dependent_cognition

    def span(initial: float, final: float) -> StoryRange:
        return StoryRange(start=initial, end=final)

    rules = StoryRulesAverages(
        liking=span(rule.brand_sentiment.initial_mean, rule.brand_sentiment.final_mean),
        memory=span(rule.recall_strength.initial_mean, rule.recall_strength.final_mean),
        purchase_interest=span(
            rule.purchase_intention_proxy.initial_mean, rule.purchase_intention_proxy.final_mean
        ),
    )
    with_ai = StoryAiAverages(
        liking=span(ai.synthetic_sentiment.initial_mean, ai.synthetic_sentiment.final_mean),
        memory=span(ai.synthetic_recall.initial_mean, ai.synthetic_recall.final_mean),
    )
    return StoryAverages(
        rules=rules,
        with_ai=with_ai,
        compare=StoryCompare(
            liking=compare_bucket(with_ai.liking.end, rules.liking.end),
            memory=compare_bucket(with_ai.memory.end, rules.memory.end),
        ),
    )


def _headline(
    run: StoryRun, ai: StoryAi, funnel: StoryFunnel, averages: StoryAverages
) -> tuple[MessageRef, ...]:
    city: RefParam = (
        message_ref(f"city.{run.city.catalog_key}")
        if run.city.catalog_key is not None
        else text_param(run.city.name)
    )
    headline = [
        message_ref(
            "story.headline.people",
            people=message_ref("common.people", count=run.people_count),
            days=message_ref("common.days", count=run.days),
            city=city,
        ),
        message_ref(
            "story.headline.shown",
            count=funnel.shown,
            product=text_param(run.creative.product_name),
        ),
    ]
    if funnel.noticed == 0:
        # Nothing changed and nothing was asked; further lines would only repeat that.
        headline.append(message_ref("story.headline.nobody_noticed"))
        return tuple(headline)
    headline.append(message_ref("story.headline.noticed", count=funnel.noticed))
    if ai.mode == "rules":
        # "With AI" equals "rules only" by construction, which this line already says.
        headline.append(message_ref("story.headline.rules"))
        return tuple(headline)
    if ai.mode == "mock":
        headline.append(message_ref("story.headline.practice", count=ai.statuses.practice))
    else:
        assert ai.model_id is not None
        headline.append(
            message_ref("story.headline.ai", count=funnel.answered, model=_model_param(ai.model_id))
        )
    compare = averages.compare
    headline.append(message_ref(f"story.headline.compare.liking.{compare.liking}"))
    if compare.memory != "about_same":
        headline.append(message_ref(f"story.headline.compare.memory.{compare.memory}"))
    return tuple(headline)


def story_json_bytes(story: RunStoryV1) -> bytes:
    """The story's canonical UTF-8 JSON bytes, exactly as the story API serves them."""
    return canonical_json(story).encode("utf-8")


def build_run_story(stored: StoredCityRun) -> RunStoryV1:
    """Build the verified, path-free story of one saved V10 run (spec 8.3)."""
    evidence = _evidence(stored)
    moments = _classify(evidence)
    personas = _personas(evidence)
    geometry = road_geometry(stored)
    people, kept = _people(stored, evidence, moments, personas, geometry)
    run = _run_facts(stored, evidence)
    ai = _ai_facts(stored, evidence, moments)
    statuses = ai.statuses
    funnel = StoryFunnel(
        opportunities=len(evidence.opportunities.opportunities),
        shown=sum(isinstance(event, SpatialImpression) for event in evidence.attention.events),
        noticed=ai.coverage.noticed,
        chosen=ai.coverage.chosen,
        sent=ai.coverage.sent,
        answered=sum(
            record.source in {"provider", "cache"} for record in evidence.cognition.records
        ),
        used=statuses.answered + statuses.reused + statuses.practice,
    )
    averages = _averages(stored)
    story = RunStoryV1(
        schema_version=1,
        format=RUN_STORY_FORMAT,
        run=run,
        ai=ai,
        funnel=funnel,
        labels=_label_counts(evidence),
        averages=averages,
        headline=_headline(run, ai, funnel, averages),
        map=_story_map(stored, geometry),
        personas=personas,
        people=people,
        key_moments=tuple(
            StoryKeyMoment(agent_id=moment.agent_id, moment_id=moment.moment_id)
            for moment in _key_moments(kept, evidence.cognition_input.mode)
        ),
        limits=StoryLimits(moments_per_person=MAX_STORY_MOMENTS_PER_PERSON),
    )
    if len(story_json_bytes(story)) > MAX_STORY_BYTES:
        raise RunStoryTooLarge("the run story exceeds its size limit")
    return story


__all__ = [
    "MAX_STORY_BYTES",
    "MAX_STORY_MOMENTS_PER_PERSON",
    "PACKAGED_CITY_IDS",
    "STORY_MESSAGE_KEYS",
    "RunStoryTooLarge",
    "RunStoryUnavailable",
    "build_run_story",
    "compare_bucket",
    "formula_math",
    "raw_day_tracks",
    "raw_leg_track",
    "road_geometry",
    "story_json_bytes",
    "trait_chips",
]
