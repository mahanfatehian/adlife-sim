"""The run story is a pure, verified, path-free projection of one saved V10 run.

Every fixture run is built by the real pure pipeline (``run_story_fixtures``) and loaded
back through ``CityRunStore``; each check compares the story with an independent oracle
read straight from the stored evidence, the verified summaries or the mobility model.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.city import run_story as run_story_module
from adlife.city.analysis import response_metrics_for_stored_city_run
from adlife.city.catalog import load_verified_catalog_cities
from adlife.city.run_store import StoredCityRun
from adlife.city.run_story import (
    MAX_STORY_MOMENTS_PER_PERSON,
    PACKAGED_CITY_IDS,
    STORY_MESSAGE_KEYS,
    RunStoryTooLarge,
    RunStoryUnavailable,
    build_run_story,
    compare_bucket,
    formula_math,
    raw_day_tracks,
    road_geometry,
    story_json_bytes,
    trait_chips,
)
from adlife.city.workbench_cognition_views import build_workbench_cognition_summary_view
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_runs import WorkbenchRunRepository
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1,
    CITY_COGNITION_PROMPT_CONTRACT_V2_TEXT,
    CITY_COGNITION_PROMPT_CONTRACT_V3_TEXT,
)
from adlife.core.simulation.spatial_attention import SpatialImpression
from adlife.core.simulation.spatial_response_v2 import (
    SpatialAppliedResponseV2,
    SpatialDualStateUpdatedV2,
)
from adlife.i18n import load_catalog
from adlife.i18n.refs import RefContext, person_param
from adlife.reporting.run_story_model import RunStoryV1, StoryLabels, story_ref_context
from tests.unit.city.run_story_fixtures import (
    DEFAULT_PROVIDER_BASE_URL,
    DEFAULT_PROVIDER_MODEL,
    HOSTILE_BACKGROUND,
    HOSTILE_PERSONA_NAME,
    StoryRunName,
    saved_story_run,
)

V10_NAMES: tuple[StoryRunName, ...] = (
    "rules",
    "mock",
    "provider",
    "cache",
    "fallback",
    "empty",
    "mixed",
    "dense",
    "default-mock",
    "default-provider",
    "persona-provider",
    "persona-fallback",
    "persona-mock",
    "persona-rules",
    "hostile",
)
SOURCE_STATUS = {
    "provider": "answered",
    "cache": "reused",
    "mock": "practice",
    "rules": "rules",
    "fallback": "failed",
    "budget-skipped": "over_limit",
    "unselected": "not_chosen",
}


class _Saved:
    def __init__(self, root: Path, stored: StoredCityRun) -> None:
        self.root = root
        self.stored = stored
        self.story = build_run_story(stored)


@pytest.fixture(scope="module")
def saved(tmp_path_factory: pytest.TempPathFactory) -> Callable[[StoryRunName], _Saved]:
    cache: dict[str, _Saved] = {}

    def build(name: StoryRunName) -> _Saved:
        if name not in cache:
            root = tmp_path_factory.mktemp(f"story-{name}") / "workspace"
            cache[name] = _Saved(root, saved_story_run(root, name))
        return cache[name]

    return build


def _story_json(story: RunStoryV1) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(story_json_bytes(story))
    return document


def _refs(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("key"), str):
            yield value
        for child in value.values():
            yield from _refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child)


def _r6(value: float) -> float:
    return round(value, 6) + 0.0


def _oracle_statuses(stored: StoredCityRun) -> dict[str, str]:
    """Status per opportunity, derived independently from the stored evidence."""
    assert stored.attention_evaluation is not None and stored.response_evaluation is not None
    noticed = {
        event.opportunity_id: event.noticed
        for event in stored.attention_evaluation.events
        if isinstance(event, SpatialImpression)
    }
    statuses = {opportunity_id: "not_noticed" for opportunity_id, seen in noticed.items()}
    for record in stored.response_evaluation.records:
        if isinstance(record, SpatialAppliedResponseV2):
            if record.neutralization_status == "uncertain":
                status = "unsure"
            elif record.neutralization_status == "safety":
                status = "warning"
            else:
                status = SOURCE_STATUS[record.source]
            statuses[record.baseline.opportunity_id] = status
    assert {key for key, seen in noticed.items() if seen} == {
        key for key, status in statuses.items() if status != "not_noticed"
    }
    return statuses


def _moments(story: RunStoryV1) -> Iterator[tuple[str, Any]]:
    for person in story.people:
        for moment in person.moments:
            yield person.agent_id, moment


# --- statuses, funnel and coverage --------------------------------------------------------


@pytest.mark.parametrize("name", V10_NAMES)
def test_status_mapping_matches_the_stored_applied_evidence(saved, name) -> None:
    run = saved(name)
    expected = _oracle_statuses(run.stored)
    seen = 0
    for _agent_id, moment in _moments(run.story):
        matches = [key for key in expected if key.startswith(moment.moment_id)]
        assert len(matches) == 1
        assert moment.status == expected[matches[0]]
        assert moment.noticed == (moment.status != "not_noticed")
        seen += 1
    omitted = sum(person.moments_omitted for person in run.story.people)
    assert seen + omitted == len(expected)
    statuses = run.story.ai.statuses
    assert statuses.total == run.story.ai.coverage.noticed == run.story.funnel.noticed
    assert Counter(status for status in expected.values() if status != "not_noticed") == {
        status: count for status, count in statuses.model_dump().items() if count
    }


def test_the_mixed_run_shows_every_status(saved) -> None:
    story = saved("mixed").story
    assert {moment.status for _, moment in _moments(story)} == {
        "answered",
        "reused",
        "unsure",
        "warning",
        "failed",
        "over_limit",
        "not_chosen",
        "not_noticed",
    }
    for _, moment in _moments(story):
        if moment.status == "warning":
            assert moment.safety_flags and moment.labels is not None
            assert moment.ai_clause is not None
            flags = moment.ai_clause.model_dump(mode="json")["params"]["flags"]["list"]
            assert [item["key"] for item in flags] == [
                f"flag.{flag}" for flag in moment.safety_flags
            ]
        if moment.status == "failed":
            assert moment.reason == "timeout-exhausted" and moment.labels is None
            assert moment.ai_clause is not None
            assert moment.ai_clause.model_dump(mode="json") == {
                "key": "story.ai.failed",
                "params": {"reason": {"key": "reason.timeout-exhausted"}},
            }
        if moment.status == "over_limit":
            assert moment.ai_clause is not None
            assert moment.ai_clause.model_dump(mode="json") == {
                "key": "story.ai.over_limit",
                "params": {"limit": {"int": 10}},
            }
        if moment.status == "not_chosen":
            assert moment.ai_clause is not None
            assert moment.ai_clause.model_dump(mode="json") == _not_chosen_clause(saved("mixed"))
    assert story.ai.fallback_reasons == {"timeout-exhausted": story.ai.statuses.failed}


def _not_chosen_clause(run: _Saved) -> dict[str, Any]:
    """The not-chosen wording the run's own frozen selection policy calls for."""
    frozen = run.stored.cognition_input
    if frozen.causal_contract.selection_policy_version == 2:
        return {
            "key": "story.ai.not_chosen",
            "params": {
                "per_person": {"int": frozen.selected_requests_per_agent},
                "per_run": {"int": frozen.selected_requests_per_run},
            },
        }
    return {
        "key": "story.ai.not_chosen_spread",
        "params": {"limit": {"int": frozen.funded_response_limit}},
    }


@pytest.mark.parametrize("name", ["mixed", "multiday"])
def test_caps_and_wording_come_from_the_runs_frozen_cognition_input(saved, name) -> None:
    run = saved(name)
    frozen = run.stored.cognition_input
    ai = run.story.ai
    assert (ai.funded_limit, ai.per_person, ai.per_run) == (
        frozen.funded_response_limit,
        frozen.selected_requests_per_agent,
        frozen.selected_requests_per_run,
    )
    clauses = [
        moment.ai_clause.model_dump(mode="json")
        for _, moment in _moments(run.story)
        if moment.status == "not_chosen" and moment.ai_clause is not None
    ]
    assert clauses and all(clause == _not_chosen_clause(run) for clause in clauses)


def test_a_spread_selection_policy_explains_not_chosen_moments_as_spread(saved) -> None:
    # A run frozen under the spread-evenly policy (version 3: no per-person cap, the limit
    # spread over the whole run) words "not chosen" by its limit, never "only the first".
    # The facts are read from the frozen cognition input, whatever version froze it.
    spread_input = SimpleNamespace(
        funded_response_limit=250,
        selected_requests_per_agent=1_000,
        selected_requests_per_run=1_000,
        causal_contract=SimpleNamespace(selection_policy_version=3),
    )
    selection = run_story_module._selection(spread_input)  # type: ignore[arg-type]
    assert (selection.funded_limit, selection.per_person, selection.per_run) == (250, 1_000, 1_000)
    not_chosen = SimpleNamespace(status="not_chosen", terminal=None)
    clause = run_story_module._ai_clause(
        not_chosen,  # type: ignore[arg-type]
        (),
        selection,
        in_character=False,
    )
    assert clause.model_dump(mode="json") == {
        "key": "story.ai.not_chosen_spread",
        "params": {"limit": {"int": 250}},
    }
    earliest = run_story_module._selection(saved("mixed").stored.cognition_input)
    clause = run_story_module._ai_clause(
        not_chosen,  # type: ignore[arg-type]
        (),
        earliest,
        in_character=False,
    )
    assert clause.model_dump(mode="json")["key"] == (
        "story.ai.not_chosen" if earliest.policy_version == 2 else "story.ai.not_chosen_spread"
    )
    assert "story.ai.not_chosen_spread" in STORY_MESSAGE_KEYS
    assert "story.ai.not_chosen" in STORY_MESSAGE_KEYS


@pytest.mark.parametrize("name", V10_NAMES)
def test_funnel_and_coverage_reconcile_with_the_verified_summaries(saved, name) -> None:
    run = saved(name)
    story = run.story
    summary = WorkbenchRunRepository(prepare_workbench_workspace(run.root)).verified_summary(
        story.run.run_id
    )
    view = build_workbench_cognition_summary_view(run.stored)
    provider_mode = summary.cognition_mode in {"local", "remote"}
    funnel = story.funnel
    assert funnel.opportunities == summary.opportunity_count
    assert funnel.shown == summary.impression_count
    assert funnel.noticed == summary.noticed_count == summary.response_count
    assert funnel.chosen == summary.selected_request_count
    assert funnel.sent == (summary.funded_request_count if provider_mode else 0)
    assert funnel.answered == (summary.provider_count or 0) + (summary.cache_count or 0)
    statuses = story.ai.statuses
    assert funnel.used == statuses.answered + statuses.reused + statuses.practice
    coverage = story.ai.coverage
    assert coverage.over_limit == summary.budget_skipped_count == statuses.over_limit
    assert coverage.not_chosen == summary.unselected_response_count == statuses.not_chosen
    assert statuses.failed == (summary.fallback_count or 0)
    assert statuses.practice == (summary.mock_count or 0)
    assert statuses.rules == (summary.rules_count or 0)
    assert statuses.warning == (summary.safety_neutralized_result_count or 0)
    assert statuses.answered + statuses.reused + statuses.unsure + statuses.warning == (
        (summary.provider_count or 0) + (summary.cache_count or 0)
    )
    ai = story.ai
    assert (ai.mode, ai.model_id, ai.provider_profile_id, ai.provider_revision_sha256) == (
        view.mode,
        view.model_id,
        view.provider_profile_id,
        view.provider_revision_sha256,
    )
    assert ai.funded_limit == view.funded_response_limit
    assert ai.contract.model_dump() == {
        "prompt_contract_id": view.prompt_contract_id,
        "prompt_contract_sha256": view.prompt_contract_sha256,
        "result_schema_id": view.result_schema_id,
        "influence_policy_id": view.influence_policy_id,
        "influence_policy_sha256": view.influence_policy_sha256,
    }
    assert sum(person.totals.shown for person in story.people) == funnel.shown
    assert sum(person.totals.noticed for person in story.people) == funnel.noticed
    assert sum(person.totals.chosen for person in story.people) == funnel.chosen
    assert sum(person.totals.sent for person in story.people) == funnel.sent


def test_usage_counts_only_this_runs_provider_and_fallback_records(saved) -> None:
    mixed = saved("mixed")
    records = mixed.stored.cognition_evaluation.records
    used = [record for record in records if record.source in {"provider", "fallback"}]
    assert mixed.story.ai.usage_this_run.model_dump() == {
        "attempts": sum(record.attempt_count for record in used),
        "prompt_tokens": sum(record.prompt_tokens or 0 for record in used),
        "completion_tokens": sum(record.completion_tokens or 0 for record in used),
    }
    assert mixed.story.ai.reused_answers == sum(record.source == "cache" for record in records)
    cache = saved("cache").story.ai
    # Cached answers carry historical usage, which is not usage of this run.
    assert cache.usage_this_run.model_dump() == {
        "attempts": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    assert cache.reused_answers == cache.statuses.reused > 0


# --- states, averages, labels and math ----------------------------------------------------


@pytest.mark.parametrize("name", ["mixed", "default-provider", "mock", "dense"])
def test_moment_states_equal_their_dual_state_updates(saved, name) -> None:
    run = saved(name)
    response = run.stored.response_evaluation
    assert response is not None
    applied = {
        record.baseline.opportunity_id[:16]: record
        for record in response.records
        if isinstance(record, SpatialAppliedResponseV2)
    }
    duals = {
        cause: record
        for record in response.records
        if isinstance(record, SpatialDualStateUpdatedV2)
        for cause in record.caused_by_event_ids
    }
    checked = 0
    for _, moment in _moments(run.story):
        if not moment.noticed:
            assert moment.before is moment.after is moment.rules_line is None
            continue
        response_record = applied[moment.moment_id]
        dual = duals[response_record.event_id]
        assert moment.before is not None and moment.after is not None
        assert moment.before.model_dump() == {
            "rules": {
                "liking": _r6(dual.previous_rule_state.brand_sentiment),
                "memory": _r6(dual.previous_rule_state.recall_strength),
                "purchase_interest": _r6(dual.previous_rule_state.purchase_intention),
            },
            "with_ai": {
                "liking": _r6(dual.previous_cognition_state.synthetic_sentiment),
                "memory": _r6(dual.previous_cognition_state.synthetic_recall),
            },
        }
        assert moment.after.model_dump() == {
            "rules": {
                "liking": _r6(dual.rule_state.brand_sentiment),
                "memory": _r6(dual.rule_state.recall_strength),
                "purchase_interest": _r6(dual.rule_state.purchase_intention),
            },
            "with_ai": {
                "liking": _r6(dual.cognition_state.synthetic_sentiment),
                "memory": _r6(dual.cognition_state.synthetic_recall),
            },
        }
        assert moment.rule_delta is not None and moment.applied_delta is not None
        assert moment.rule_delta.liking == _r6(response_record.baseline.sentiment_delta)
        assert moment.applied_delta.memory == _r6(response_record.applied_recall_delta)
        checked += 1
    assert checked > 0


@pytest.mark.parametrize("name", ["rules", "mock", "provider", "mixed", "default-mock"])
def test_averages_equal_the_verified_response_metrics(saved, name) -> None:
    run = saved(name)
    metrics = response_metrics_for_stored_city_run(run.stored)
    rule = metrics.rule_baseline
    ai = metrics.provider_dependent_cognition
    averages = run.story.averages
    pairs = [
        (averages.rules.liking, rule.brand_sentiment),
        (averages.rules.memory, rule.recall_strength),
        (averages.rules.purchase_interest, rule.purchase_intention_proxy),
        (averages.with_ai.liking, ai.synthetic_sentiment),
        (averages.with_ai.memory, ai.synthetic_recall),
    ]
    for story_range, receipt in pairs:
        # Story floats are rounded to six decimals; within that, they are exact.
        assert abs(story_range.start - _r6(receipt.initial_mean)) <= 1e-9
        assert abs(story_range.end - _r6(receipt.final_mean)) <= 1e-9
    assert averages.compare.liking == compare_bucket(
        averages.with_ai.liking.end, averages.rules.liking.end
    )


def test_labels_count_ai_answers_and_never_neutral_fill_ins(saved) -> None:
    provider = saved("provider").story
    assert provider.labels is not None and provider.labels.group == "ai"
    # Twelve chosen, ten funded: the two over-limit neutral fill-ins are not answers.
    assert (provider.funnel.chosen, provider.labels.answers) == (12, 10)
    assert provider.labels.affect == {
        "annoyed": 0,
        "skeptical": 0,
        "neutral": 0,
        "curious": 0,
        "positive": 10,
        "uncertain": 0,
    }
    assert saved("fallback").story.labels is None
    assert saved("rules").story.labels is None
    mock = saved("mock").story
    assert mock.labels is not None and mock.labels.group == "practice"
    assert mock.labels.answers == mock.ai.statuses.practice
    mixed = saved("mixed").story
    assert mixed.labels is not None
    # Set-aside answers still count as answers; failed and over-limit ones do not.
    assert mixed.labels.answers == mixed.funnel.answered == 9
    assert mixed.labels.safety_flags == {
        "stereotype-risk": 2,
        "unsupported-inference": 2,
        "unsafe-content": 0,
    }


@pytest.mark.parametrize("name", ["mixed", "default-provider", "mock", "default-mock"])
def test_formula_factors_reproduce_the_applied_deltas_or_are_absent(saved, name) -> None:
    policy = CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1
    checked = 0
    for _, moment in _moments(saved(name).story):
        if moment.status not in {"answered", "reused", "practice"}:
            assert moment.math is None
            continue
        math_facts = moment.math
        assert math_facts is not None, "valid evidence always reproduces"
        assert moment.rule_delta is not None and moment.applied_delta is not None
        liking = max(-0.2, min(0.2, math_facts.liking_raw))
        memory = max(0.0, min(0.3, math_facts.memory_raw))
        assert abs(liking - moment.applied_delta.liking) <= 2e-6
        assert abs(memory - moment.applied_delta.memory) <= 2e-6
        assert (math_facts.liking_limit, math_facts.memory_min, math_facts.memory_max) == (
            policy.sentiment_maximum,
            policy.recall_minimum,
            policy.recall_maximum,
        )
        checked += 1
    assert checked > 0


def test_formula_math_refuses_numbers_that_do_not_reproduce() -> None:
    policy = CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1
    labels = StoryLabels(affect="positive", relevance="high", credibility="high", salience="low")
    rule = (0.05, 0.1)
    liking = (0.05 + 0.04) * 1.15 * 1.1
    memory = 0.1 * 1.15 * 1.1 * 0.75
    exact = formula_math(labels, rule, (liking, memory), policy)
    assert exact is not None
    assert (exact.affect_adjustment, exact.relevance_factor, exact.salience_factor) == (
        0.04,
        1.15,
        0.75,
    )
    assert formula_math(labels, rule, (liking + 1e-6, memory), policy) is None
    assert formula_math(labels, rule, (liking, memory - 1e-6), policy) is None
    clamped = formula_math(labels, (0.2, 0.3), (0.2, 0.3 * 1.15 * 1.1 * 0.75), policy)
    assert clamped is not None and clamped.liking_raw > 0.2


def test_effects_and_with_ai_lines_follow_the_rounded_deltas(saved) -> None:
    for _, moment in _moments(saved("mixed").story):
        if not moment.noticed:
            continue
        assert moment.effect is not None and moment.with_ai_line is not None
        assert moment.rule_delta is not None and moment.applied_delta is not None
        for measure in ("liking", "memory"):
            difference = getattr(moment.applied_delta, measure) - getattr(
                moment.rule_delta, measure
            )
            effect = getattr(moment.effect, measure)
            if abs(difference) < 0.005:
                assert effect == "same"
            else:
                assert effect == ("higher" if difference > 0 else "lower")
        same = moment.effect.liking == moment.effect.memory == "same"
        assert moment.with_ai_line.key == (
            "story.moment.with_ai_same" if same else "story.moment.with_ai_line"
        )
        if moment.status in {"over_limit", "not_chosen", "failed", "unsure", "warning"}:
            assert same


@pytest.mark.parametrize(
    ("with_ai", "rules", "expected"),
    [
        (0.1, 0.1, "about_same"),
        (0.1049, 0.1, "about_same"),
        (0.106, 0.1, "slightly_higher"),
        (0.094, 0.1, "slightly_lower"),
        (0.1299, 0.1, "slightly_higher"),
        (0.131, 0.1, "higher"),
        (0.069, 0.1, "lower"),
    ],
)
def test_compare_buckets(with_ai: float, rules: float, expected: str) -> None:
    assert compare_bucket(with_ai, rules) == expected


# --- people, personas and tracks ----------------------------------------------------------


def test_template_trait_chips_match_the_documented_expectations() -> None:
    templates = {item.persona_id: item.traits for item in load_persona_template_catalog().templates}
    expected = {
        "persona-001": [
            ("novelty_seeking", "high"),
            ("mobile_recall_encoding", "high"),
            ("advertising_skepticism", "low"),
        ],
        "persona-002": [
            ("impulsivity", "low"),
            ("price_sensitivity", "high"),
            ("advertising_skepticism", "high"),
        ],
        "persona-003": [("roadside_recall_encoding", "high"), ("impulsivity", "low")],
        "persona-004": [
            ("mobile_recall_encoding", "high"),
            ("roadside_recall_encoding", "low"),
            ("price_sensitivity", "low"),
        ],
    }
    for persona_id, chips in expected.items():
        assert [(chip.trait, chip.level) for chip in trait_chips(templates[persona_id])] == chips


def test_personas_recognize_templates_and_name_people_through_the_context(saved) -> None:
    story = saved("default-mock").story
    assert [
        (persona.persona_id, persona.template_key, persona.color_index, persona.agent_ids)
        for persona in story.personas
    ] == [
        ("persona-001", "lumo", 0, tuple(f"person-00{i}" for i in range(1, 5))),
        ("persona-002", "veyra", 1, tuple(f"person-00{i}" for i in range(5, 9))),
    ]
    context = story_ref_context(story)
    assert isinstance(context, RefContext)
    assert context.days == 1
    assert context.people["person-006"].template_key == "veyra"
    assert context.people["person-006"].number == 6
    hostile = saved("hostile").story
    persona = hostile.personas[0]
    assert (persona.template_key, persona.color_index) == (None, 10)
    assert persona.display_name == HOSTILE_PERSONA_NAME
    assert story_ref_context(hostile).people["person-001"].display_name == HOSTILE_PERSONA_NAME


def _position_at(
    days: Sequence[tuple[int, str, Sequence[Sequence[float]], Sequence[Sequence[float]]]],
    minute: float,
    home: tuple[float, float],
    destinations: dict[str, tuple[float, float]],
) -> tuple[float, float]:
    """A Python mirror of guide-map.js positionAt (spec 3.7)."""
    day_index = int(minute // 1_440)
    for index, destination, outbound, inbound in days:
        if index != day_index:
            continue
        if minute <= outbound[0][0]:
            return home
        if minute < outbound[-1][0]:
            return _interpolate(outbound, minute)
        if minute < inbound[0][0]:
            return destinations[destination]
        if minute < inbound[-1][0]:
            return _interpolate(inbound, minute)
        return home
    return home


def _interpolate(points: Sequence[Sequence[float]], minute: float) -> tuple[float, float]:
    for start, end in pairwise(points):
        if start[0] <= minute <= end[0] and end[0] > start[0]:
            fraction = (minute - start[0]) / (end[0] - start[0])
            return (
                start[1] + (end[1] - start[1]) * fraction,
                start[2] + (end[2] - start[2]) * fraction,
            )
    raise AssertionError("minute is outside the leg")


@pytest.mark.parametrize("name", ["default-mock", "rules", "dense"])
def test_position_mirror_equals_the_mobility_frame_every_minute(saved, name) -> None:
    run = saved(name)
    stored, story = run.stored, run.story
    mobility = stored.mobility
    raw = raw_day_tracks(mobility, road_geometry(stored))
    nodes = {node.node_id: (node.longitude, node.latitude) for node in stored.pack.nodes}
    for person in story.people:
        anchors = person.anchors
        home = nodes[anchors.home]
        destinations = {"work": nodes[anchors.work], "leisure": nodes[anchors.leisure]}
        published = [
            (day.day_index, day.destination, day.outbound, day.inbound) for day in person.days
        ]
        assert [(index, destination) for index, destination, _, _ in raw[person.agent_id]] == [
            (index, destination) for index, destination, _, _ in published
        ]
        for minute in range(mobility.days * 1_440):
            frame = next(
                position
                for position in mobility.frame(minute)
                if position.agent_id == person.agent_id
            )
            expected = (frame.longitude, frame.latitude)
            exact = _position_at(raw[person.agent_id], minute, home, destinations)
            assert math.dist(exact, expected) <= 1e-9, (person.agent_id, minute)
            rounded = _position_at(published, minute, home, destinations)
            assert math.dist(rounded, expected) <= 1e-6, (person.agent_id, minute)


def test_tracks_deduplicate_road_joints_and_follow_road_geometry(saved) -> None:
    run = saved("default-mock")
    geometry = road_geometry(run.stored)
    for person in run.story.people:
        for day in person.days:
            for leg in (day.outbound, day.inbound):
                coordinates = [(point[1], point[2]) for point in leg]
                assert all(a != b for a, b in pairwise(coordinates))
                vertices = {point for points in geometry.values() for point in points}
                assert set(coordinates) <= vertices


@pytest.mark.parametrize("name", ["default-mock", "mixed", "dense"])
def test_moment_positions_and_activities_come_from_the_mobility_frame(saved, name) -> None:
    run = saved(name)
    for agent_id, moment in _moments(run.story):
        frame = next(
            position
            for position in run.stored.mobility.frame(moment.minute)
            if position.agent_id == agent_id
        )
        assert moment.position == (_r6(frame.longitude), _r6(frame.latitude))
        assert moment.activity == frame.activity


def test_caps_keep_terminal_answers_then_noticed_then_unnoticed(saved) -> None:
    run = saved("dense")
    expected = _oracle_statuses(run.stored)
    by_agent: Counter[str] = Counter()
    noticed_by_agent: Counter[str] = Counter()
    assert run.stored.opportunity_evaluation is not None
    for opportunity in run.stored.opportunity_evaluation.opportunities:
        by_agent[opportunity.agent_id] += 1
        if expected[opportunity.opportunity_id] != "not_noticed":
            noticed_by_agent[opportunity.agent_id] += 1
    channels = set()
    for person in run.story.people:
        total = by_agent[person.agent_id]
        assert total > MAX_STORY_MOMENTS_PER_PERSON
        assert len(person.moments) == MAX_STORY_MOMENTS_PER_PERSON
        assert person.moments_omitted == total - MAX_STORY_MOMENTS_PER_PERSON
        kept = Counter(moment.status for moment in person.moments)
        assert kept["practice"] == person.totals.chosen == 4
        noticed_kept = sum(moment.noticed for moment in person.moments)
        assert noticed_kept == min(MAX_STORY_MOMENTS_PER_PERSON, noticed_by_agent[person.agent_id])
        channels |= {moment.channel for moment in person.moments}
        keys = [(moment.minute, moment.millisecond, moment.moment_id) for moment in person.moments]
        assert keys == sorted(keys)
    assert run.story.run.channels == ("mobile-feed", "roadside-billboard")
    assert run.story.run.billboard is not None
    assert run.story.run.billboard.road_id == "middle-west"
    assert "mobile-feed" in channels


def test_person_summaries_and_totals(saved) -> None:
    story = saved("default-provider").story
    people = {person.agent_id: person for person in story.people}
    lumo_three = people["person-003"]
    assert lumo_three.totals.noticed == 0
    assert lumo_three.summary.model_dump(mode="json") == {
        "key": "story.person.never_noticed",
        "count": lumo_three.totals.shown,
    }
    assert lumo_three.label_mode is None and lumo_three.label_answers == 0
    for person in story.people:
        if person.totals.noticed:
            assert person.summary.model_dump(mode="json") == {
                "key": "story.person.noticed",
                "count": person.totals.shown,
                "params": {
                    "noticed": {"int": person.totals.noticed},
                    "sent": {"int": person.totals.sent},
                },
            }
    empty = saved("empty").story
    assert {person.summary.key for person in empty.people} == {"story.person.never_shown"}


# --- the default study --------------------------------------------------------------------


def test_default_study_matches_the_measured_expectations(saved) -> None:
    mock = saved("default-mock").story
    assert (mock.funnel.opportunities, mock.funnel.shown, mock.funnel.noticed) == (27, 27, 13)
    assert mock.ai.coverage.chosen == 13 and mock.ai.statuses.practice == 13
    assert mock.run.city.catalog_key == "fictional-grid-v2"
    assert mock.run.creative.catalog_key == "fictional-device-launch-v1"
    assert mock.run.phone is not None
    assert mock.run.phone.windows[0].model_dump() == {
        "day": 1,
        "start_minute": 420,
        "end_minute": 1260,
    }
    assert mock.averages.rules.liking.model_dump() == {"start": -0.1, "end": -0.143969}
    assert mock.averages.rules.memory.start == 0.05
    assert mock.averages.rules.purchase_interest.start == 0.1
    provider = saved("default-provider").story
    assert provider.funnel.noticed == 13
    assert (provider.ai.coverage.sent, provider.ai.coverage.over_limit) == (10, 3)
    assert provider.ai.statuses.answered == 10 and provider.ai.statuses.over_limit == 3
    assert provider.ai.thinking_off is True
    assert provider.ai.model_id == DEFAULT_PROVIDER_MODEL
    assert provider.ai.usage_this_run.attempts == 10
    people = {person.agent_id: person for person in provider.people}
    assert people["person-003"].totals.noticed == 0
    first = next(moment for moment in people["person-006"].moments if moment.noticed)
    assert (first.minute, first.activity, first.status) == (437, "home", "answered")
    # Ten AI moments, the first over-limit moment and the first unnoticed moment.
    keyed = {(item.agent_id, item.moment_id) for item in provider.key_moments}
    by_key = {(agent_id, moment.moment_id): moment for agent_id, moment in _moments(provider)}
    assert len(provider.key_moments) == 12
    assert Counter(by_key[key].status for key in keyed) == {
        "answered": 10,
        "over_limit": 1,
        "not_noticed": 1,
    }


AI_KINDS = frozenset({"answered", "reused", "practice", "unsure", "warning", "failed"})
RUN_FIRSTS = ("over_limit", "not_chosen", "not_noticed", "failed", "unsure", "warning")


def _key_story_moments(story: RunStoryV1) -> list[tuple[str, Any]]:
    by_key = {(agent_id, moment.moment_id): moment for agent_id, moment in _moments(story)}
    return [(item.agent_id, by_key[(item.agent_id, item.moment_id)]) for item in story.key_moments]


def _per_day_budget(story: RunStoryV1) -> int:
    run_budget = 20 if story.ai.mode == "rules" else 60
    return max(3, math.ceil(run_budget / story.run.days))


@pytest.mark.parametrize(
    "name", ["mixed", "default-mock", "default-provider", "rules", "fallback", "dense", "multiday"]
)
def test_key_moments_cover_every_noticed_day_in_time_order(saved, name) -> None:
    story = saved(name).story
    kept = list(_moments(story))
    keyed = _key_story_moments(story)
    keys = [(moment.minute, moment.millisecond, agent_id) for agent_id, moment in keyed]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    budget = _per_day_budget(story)
    for day in range(story.run.days):
        day_kept = [moment for _, moment in kept if moment.day_index == day]
        day_keys = [moment for _, moment in keyed if moment.day_index == day]
        noticed = sum(moment.noticed for moment in day_kept)
        if story.ai.mode == "rules":
            # Rules only: the day's noticed moments, evenly spread, up to the day's budget.
            assert sum(moment.noticed for moment in day_keys) == min(budget, noticed)
            continue
        ai_kept = sum(moment.status in AI_KINDS for moment in day_kept)
        # AI moments come first, up to the day's budget; then rules-only noticed moments
        # fill the day up to six, so a day after the AI questions ran out is never empty.
        assert sum(moment.status in AI_KINDS for moment in day_keys) >= min(budget, ai_kept)
        assert sum(moment.noticed for moment in day_keys) >= min(6, noticed)
    # Each telling kind still opens with its first moment of the run.
    firsts = ("not_noticed",) if story.ai.mode == "rules" else RUN_FIRSTS
    for status in firsts:
        first = next(
            (
                (agent_id, moment)
                for agent_id, moment in sorted(
                    kept, key=lambda step: (step[1].minute, step[1].millisecond, step[0])
                )
                if moment.status == status
            ),
            None,
        )
        if first is not None:
            assert (first[0], first[1].moment_id) in {
                (agent_id, moment.moment_id) for agent_id, moment in keyed
            }
    assert len(keyed) <= budget * story.run.days + len(RUN_FIRSTS)
    # Unnoticed moments appear only as the first of their kind.
    assert sum(moment.status == "not_noticed" for _, moment in keyed) == min(
        1, sum(moment.status == "not_noticed" for _, moment in kept)
    )
    noticed_days = [moment.day_index for _, moment in kept if moment.noticed]
    if noticed_days:
        assert keyed[-1][1].day_index == max(noticed_days)


def test_a_multi_day_story_reaches_its_last_day(saved) -> None:
    run = saved("multiday")
    story = run.story
    assert story.run.days == 4
    keyed = _key_story_moments(story)
    noticed_days = {moment.day_index for _, moment in _moments(story) if moment.noticed}
    assert noticed_days == set(range(4))
    # Every day with noticed moments is in the replay, from the first day to the last.
    assert {moment.day_index for _, moment in keyed if moment.noticed} == noticed_days
    assert keyed[0][1].day_index == 0 and keyed[-1][1].day_index == 3
    assert len(keyed) <= _per_day_budget(story) * 4 + len(RUN_FIRSTS)
    assert story_json_bytes(build_run_story(run.stored)) == story_json_bytes(story)


def test_kept_moments_go_round_robin_over_the_days(saved) -> None:
    run = saved("multiday")
    expected = _oracle_statuses(run.stored)
    assert run.stored.opportunity_evaluation is not None
    own: dict[str, list[Any]] = {}
    for opportunity in run.stored.opportunity_evaluation.opportunities:
        own.setdefault(opportunity.agent_id, []).append(opportunity)
    checked = 0
    for person in run.story.people:
        assert person.moments_omitted > 0, "the fixture has more than 40 moments per person"
        kept_ids = {moment.moment_id for moment in person.moments}
        assert len(person.moments) == MAX_STORY_MOMENTS_PER_PERSON
        for tier in ("noticed", "not_noticed"):
            available: dict[int, list[Any]] = {}
            for opportunity in sorted(
                own[person.agent_id],
                key=lambda item: (item.model_minute, item.millisecond_within_minute),
            ):
                noticed = expected[opportunity.opportunity_id] != "not_noticed"
                if noticed == (tier == "noticed"):
                    available.setdefault(opportunity.day_index, []).append(opportunity)
            kept_per_day = {
                day: [item for item in items if item.opportunity_id[:16] in kept_ids]
                for day, items in available.items()
            }
            for day, items in available.items():
                # Within a day the earliest moments are the ones kept.
                count = len(kept_per_day[day])
                assert [item.opportunity_id for item in items[:count]] == [
                    item.opportunity_id for item in kept_per_day[day]
                ]
            for day, items in available.items():
                if len(kept_per_day[day]) < len(items):
                    # A day that still has moments left was never passed over by more
                    # than one round: no other day kept two more moments than it did.
                    assert all(
                        len(other) <= len(kept_per_day[day]) + 1 for other in kept_per_day.values()
                    )
                    checked += 1
    assert checked > 0
    # Every day of the busy run keeps some of its unnoticed moments, not only the first ones.
    for person in run.story.people:
        assert {moment.day_index for moment in person.moments if not moment.noticed} == set(
            range(4)
        )


@pytest.mark.parametrize("name", (*V10_NAMES, "multiday"))
def test_day_totals_count_every_moment_of_every_day(saved, name) -> None:
    run = saved(name)
    story = run.story
    expected = _oracle_statuses(run.stored)
    assert run.stored.opportunity_evaluation is not None
    rows = {day: Counter() for day in range(story.run.days)}
    for opportunity in run.stored.opportunity_evaluation.opportunities:
        status = expected[opportunity.opportunity_id]
        row = rows[opportunity.day_index]
        row["shown"] += 1
        row["noticed"] += status != "not_noticed"
        row["chosen"] += status not in {"not_noticed", "not_chosen"}
        row["used"] += status in {"answered", "reused", "practice"}
        row["not_chosen"] += status == "not_chosen"
    assert [day.model_dump() for day in story.day_totals] == [
        {
            "day_index": day,
            "shown": rows[day]["shown"],
            "noticed": rows[day]["noticed"],
            "chosen": rows[day]["chosen"],
            "used": rows[day]["used"],
            "not_chosen": rows[day]["not_chosen"],
        }
        for day in range(story.run.days)
    ]
    assert sum(day.shown for day in story.day_totals) == story.funnel.shown
    assert sum(day.used for day in story.day_totals) == story.funnel.used


# --- messages -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", V10_NAMES)
def test_every_emitted_key_is_declared(saved, name) -> None:
    keys = {ref["key"] for ref in _refs(_story_json(saved(name).story))}
    assert keys
    assert keys <= STORY_MESSAGE_KEYS, sorted(keys - STORY_MESSAGE_KEYS)


def test_headlines_tell_the_run_without_repeating_themselves(saved) -> None:
    def keys(name: StoryRunName) -> list[str]:
        return [ref.key for ref in saved(name).story.headline]

    assert keys("empty") == [
        "story.headline.people",
        "story.headline.shown",
        "story.headline.nobody_noticed",
    ]
    assert keys("rules") == [
        "story.headline.people",
        "story.headline.shown",
        "story.headline.noticed",
        "story.headline.rules",
    ]
    practice = keys("default-mock")
    assert practice[:4] == [
        "story.headline.people",
        "story.headline.shown",
        "story.headline.noticed",
        "story.headline.practice",
    ]
    assert practice[4].startswith("story.headline.compare.liking.")
    provider = saved("default-provider").story
    ai_line = provider.headline[3].model_dump(mode="json")
    assert ai_line == {
        "key": "story.headline.ai",
        "count": provider.funnel.answered,
        "params": {"model": {"id": DEFAULT_PROVIDER_MODEL}},
    }
    people = provider.headline[0].model_dump(mode="json")
    assert people["params"] == {
        "people": {"key": "common.people", "count": 8},
        "days": {"key": "common.days", "count": 1},
        "city": {"key": "city.fictional-grid-v2"},
    }
    memory = [ref.key for ref in provider.headline if ".compare.memory." in ref.key]
    assert len(memory) == (provider.averages.compare.memory != "about_same")


def test_a_prompt_v3_run_says_the_ai_answered_as_the_person(saved) -> None:
    story = saved("persona-provider").story
    assert story.ai.contract.prompt_contract_id == "city-cognition-persona-prompt-v3"
    assert story.headline[3].model_dump(mode="json") == {
        "key": "story.headline.ai_as_person",
        "count": story.funnel.answered,
        "params": {"model": {"id": DEFAULT_PROVIDER_MODEL}},
    }
    answered = [
        (person.agent_id, moment)
        for person in story.people
        for moment in person.moments
        if moment.status == "answered"
    ]
    assert answered
    for agent_id, moment in answered:
        assert moment.ai_clause is not None
        assert moment.ai_clause.key == "story.ai.answered_as"
        assert moment.ai_clause.params == {"name": person_param(agent_id)}

    # A run saved under prompt contract v2 keeps its wording: the AI judged the moment.
    earlier = saved("default-provider").story
    assert earlier.ai.contract.prompt_contract_id == "city-cognition-signal-prompt-v2"
    assert earlier.headline[3].key == "story.headline.ai"
    clauses = {
        moment.ai_clause.key
        for person in earlier.people
        for moment in person.moments
        if moment.status == "answered" and moment.ai_clause is not None
    }
    assert clauses == {"story.ai.answered"}


def test_a_prompt_v3_run_whose_ai_never_answered_reads_right_at_zero(saved) -> None:
    # Every funded request failed, as when the key is refused or a slow model times out:
    # the headline counts zero answers, so it must not add that each one was in character.
    story = saved("persona-fallback").story
    assert story.ai.contract.prompt_contract_id == "city-cognition-persona-prompt-v3"
    assert story.ai.statuses.failed == story.funnel.sent > 0
    assert story.funnel.answered == 0
    assert story.headline[3].model_dump(mode="json") == {
        "key": "story.headline.ai_as_person",
        "count": 0,
        "params": {"model": {"id": DEFAULT_PROVIDER_MODEL}},
    }
    context = story_ref_context(story)
    for language, each_time in (("en", "each time"), ("de", "jedes Mal")):
        catalog = load_catalog(language)
        lines = [catalog.plain(ref, context) for ref in story.headline]
        assert DEFAULT_PROVIDER_MODEL in lines[3]
        assert not [line for line in lines if each_time in line], lines


@pytest.mark.parametrize(
    ("name", "status"), [("persona-mock", "practice"), ("persona-rules", "rules")]
)
def test_practice_and_rules_runs_made_now_keep_the_general_wording(saved, name, status) -> None:
    # New runs freeze prompt contract v3 in every mode, but practice answers and rules
    # are no AI answering as the person, so these stories keep the general wording.
    story = saved(name).story
    assert story.ai.mode == ("mock" if status == "practice" else "rules")
    assert story.ai.contract.prompt_contract_id == "city-cognition-persona-prompt-v3"
    # Offline runs answer every noticed ad.
    assert story.ai.statuses.model_dump()[status] == story.funnel.noticed > 0
    keys = [ref.key for ref in story.headline]
    assert keys[3] == f"story.headline.{status}"
    assert not {"story.headline.ai", "story.headline.ai_as_person"} & set(keys)
    clauses = {
        moment.ai_clause.key for _, moment in _moments(story) if moment.ai_clause is not None
    }
    assert clauses == {f"story.ai.{status}"}


def test_a_non_ascii_model_name_is_text_not_an_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.city import run_story_fixtures as fixtures

    model = "\u0645\u062f\u0644 <b>local</b>"
    original = fixtures.story_provider_revision
    monkeypatch.setattr(
        fixtures,
        "story_provider_revision",
        lambda root: original(root, model_id=model, disable_thinking=False),
    )
    story = build_run_story(saved_story_run(tmp_path / "workspace", "default-provider"))
    line = story.headline[3].model_dump(mode="json")
    assert line["params"]["model"] == {"text": model}
    assert story.ai.model_id == model and story.ai.thinking_off is False


def test_sentences_use_typed_parameters(saved) -> None:
    story = saved("default-mock").story
    agent_id, moment = next(_moments(story))
    sentence = moment.sentence.model_dump(mode="json")
    assert sentence["key"] in {"story.moment.noticed", "story.moment.missed"}
    assert sentence["params"] == {
        "time": {"clock": moment.minute},
        "name": {"person": agent_id},
        "product": {"text": "Lumen Pocket"},
        "where": {"key": f"story.where.mobile-feed.{moment.activity}"},
    }
    for _, noticed in _moments(story):
        if noticed.noticed:
            assert noticed.rules_line is not None and noticed.before is not None
            assert noticed.after is not None
            line = noticed.rules_line.model_dump(mode="json")
            assert line == {
                "key": "story.moment.rules_line",
                "params": {
                    "liking_before": {"num": noticed.before.rules.liking, "decimals": 2},
                    "liking_after": {"num": noticed.after.rules.liking, "decimals": 2},
                    "memory_before": {"num": noticed.before.rules.memory, "decimals": 2},
                    "memory_after": {"num": noticed.after.rules.memory, "decimals": 2},
                },
            }
            break


# --- determinism, exclusions and refusals -------------------------------------------------


@pytest.mark.parametrize("name", ["mixed", "default-provider", "dense"])
def test_story_json_is_deterministic_and_round_trips(saved, name) -> None:
    run = saved(name)
    first = story_json_bytes(run.story)
    assert story_json_bytes(build_run_story(run.stored)) == first
    assert story_json_bytes(RunStoryV1.model_validate_json(first)) == first
    revalidated = RunStoryV1.model_validate(run.story.model_dump(mode="python"))
    assert story_json_bytes(revalidated) == first
    document = json.loads(first)
    assert document["format"] == "adlife-run-story-v1" and document["schema_version"] == 1
    assert isinstance(document["run"]["seed"], str)


@pytest.mark.parametrize("name", V10_NAMES)
def test_story_json_excludes_transport_secrets_paths_and_descriptive_context(saved, name) -> None:
    run = saved(name)
    text = story_json_bytes(run.story).decode("utf-8")
    for forbidden in (
        "http",
        "credential",
        "11434",
        "\\",
        DEFAULT_PROVIDER_BASE_URL,
        "Bound provider",
        "Guided demo - gapgpt",
        CITY_COGNITION_PROMPT_CONTRACT_V2_TEXT[:60],
        CITY_COGNITION_PROMPT_CONTRACT_V3_TEXT[:60],
        *(template.background[:40] for template in load_persona_template_catalog().templates),
        "example.org",
        str(run.root),
        run.root.as_posix(),
        "INTJ",
        "ENFP",
        "ISTJ",
        "Fictional designer",
        "Invented puzzle maker",
        "An invented person",
        "A wholly invented",
        HOSTILE_BACKGROUND,
        "source_url",
        "display_label",
        "credential_slot",
        "mbti",
        "occupation",
        "background",
        '"age"',
    ):
        assert forbidden not in text, forbidden


def test_hostile_text_stays_verbatim_data(saved) -> None:
    story = saved("hostile").story
    document = _story_json(story)
    assert document["personas"][0]["display_name"] == HOSTILE_PERSONA_NAME
    mixed = _story_json(saved("mixed").story)
    assert "<script>" in mixed["run"]["scenario"]["name"]
    assert "<svg" in mixed["run"]["campaign"]["name"]


def test_a_v9_run_has_no_story(saved) -> None:
    v9 = saved_story_run(saved("rules").root.parent / "v9-workspace", "v9")
    assert v9.manifest.schema_version == 9
    with pytest.raises(RunStoryUnavailable):
        build_run_story(v9)
    rules = saved("rules").stored
    for missing in ("cognition_input", "opportunity_evaluation", "workbench_input"):
        with pytest.raises(RunStoryUnavailable):
            build_run_story(replace(rules, **{missing: None}))
    with pytest.raises(TypeError):
        build_run_story(object())  # type: ignore[arg-type]


def test_an_oversized_story_is_refused(saved, monkeypatch: pytest.MonkeyPatch) -> None:
    stored = saved("mock").stored
    size = len(story_json_bytes(build_run_story(stored)))
    monkeypatch.setattr(run_story_module, "MAX_STORY_BYTES", size - 1)
    with pytest.raises(RunStoryTooLarge):
        build_run_story(stored)
    monkeypatch.setattr(run_story_module, "MAX_STORY_BYTES", size)
    assert len(story_json_bytes(build_run_story(stored))) == size


def test_packaged_city_ids_are_the_packaged_catalog() -> None:
    assert tuple(city.entry.city_id for city in load_verified_catalog_cities()) == (
        PACKAGED_CITY_IDS
    )
    for city_id in PACKAGED_CITY_IDS:
        assert f"city.{city_id}" in STORY_MESSAGE_KEYS


def test_renamed_town_and_edited_creative_keep_their_saved_text(
    saved, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = saved("mock").stored
    packaged = run_story_module.load_verified_catalog_cities()
    renamed = [
        SimpleNamespace(entry=city.entry, pack=SimpleNamespace(name="Elsewhere"))
        for city in packaged
    ]
    monkeypatch.setattr(run_story_module, "load_verified_catalog_cities", lambda: renamed)
    templates = run_story_module.load_creative_template_catalog().templates
    edited = SimpleNamespace(
        templates=tuple(
            template.model_copy(update={"message": "An edited fictional message."})
            for template in templates
        )
    )
    monkeypatch.setattr(run_story_module, "load_creative_template_catalog", lambda: edited)
    story = build_run_story(stored)
    assert story.run.city.catalog_key is None
    assert story.run.city.name == stored.pack.name
    assert story.headline[0].model_dump(mode="json")["params"]["city"] == {"text": stored.pack.name}
    assert story.run.creative.catalog_key is None
    assert story.run.creative.message == stored.workbench_input.creative_template.message
    monkeypatch.undo()
    story = build_run_story(stored)
    assert story.run.city.catalog_key == "fictional-grid-v2"
    assert story.run.creative.catalog_key == "fictional-device-launch-v1"


def _person_with_moments(document: dict[str, Any], count: int = 2) -> dict[str, Any]:
    person: dict[str, Any] = next(p for p in document["people"] if len(p["moments"]) >= count)
    return person


def _moment(document: dict[str, Any], *, noticed: bool) -> dict[str, Any]:
    moment: dict[str, Any] = next(
        m for p in document["people"] for m in p["moments"] if m["noticed"] is noticed
    )
    return moment


def _bump(mapping: dict[str, Any], key: str, by: int = 1) -> None:
    mapping[key] += by


def _unnoticed_with_math(document: dict[str, Any]) -> None:
    _moment(document, noticed=False)["math"] = _moment(document, noticed=True)["math"]


def _persona_assigned_twice(document: dict[str, Any]) -> None:
    first, second = document["personas"][:2]
    second["agent_ids"][0] = first["agent_ids"][0]


_REFUSED_STORIES: dict[str, Callable[[dict[str, Any]], None]] = {
    "format": lambda d: d.__setitem__("format", "adlife-run-story-v2"),
    "non-finite average": lambda d: d["averages"]["rules"]["liking"].__setitem__("end", 1e400),
    "offline names a model": lambda d: d["ai"].__setitem__("model_id", "fictional-model"),
    "offline sends questions": lambda d: _bump(d["ai"]["coverage"], "sent"),
    "statuses miss a moment": lambda d: _bump(d["ai"]["statuses"], "practice"),
    "fallback without failure": lambda d: d["ai"].__setitem__("fallback_reasons", {"timeout": 1}),
    "label counts": lambda d: _bump(d["labels"]["affect"], "curious"),
    "flag over answers": lambda d: d["labels"]["safety_flags"].__setitem__("stereotype-risk", 99),
    "labels without answers": lambda d: d.__setitem__("labels", None),
    "funnel": lambda d: _bump(d["funnel"], "noticed"),
    "person number": lambda d: d["people"][0].__setitem__("number", 99),
    "person days": lambda d: d["people"][0]["days"][0].__setitem__("day_index", 1),
    "people order": lambda d: d["people"].reverse(),
    "omitted moments": lambda d: _bump(d["people"][0], "moments_omitted"),
    "moment order": lambda d: _person_with_moments(d)["moments"].reverse(),
    "moment day": lambda d: _moment(d, noticed=True).__setitem__("day_index", 1),
    "unnoticed status": lambda d: _moment(d, noticed=True).__setitem__("noticed", False),
    "reason on an answer": lambda d: _moment(d, noticed=True).__setitem__("reason", "timeout"),
    "math without an answer": _unnoticed_with_math,
    "persona count": lambda d: _bump(d["personas"][0], "count"),
    "persona colour": lambda d: d["personas"][0].__setitem__("color_index", 10),
    "persona assigned twice": _persona_assigned_twice,
    "unknown key moment": lambda d: d["key_moments"][0].__setitem__("moment_id", "0" * 16),
    "key moment order": lambda d: d["key_moments"].reverse(),
    "phone window": lambda d: d["run"]["phone"]["windows"][0].__setitem__("end_minute", 420),
    "channels": lambda d: d["run"].__setitem__("channels", ["roadside-billboard"]),
    "people count": lambda d: _bump(d["run"], "people_count"),
    "per-person cap of another policy": lambda d: d["ai"].__setitem__("per_person", 1_000),
    "unknown per-person cap": lambda d: d["ai"].__setitem__("per_person", 5),
    "unknown AI limit": lambda d: d["ai"].__setitem__("funded_limit", 100),
    "offline limit below the run cap": lambda d: d["ai"].__setitem__("funded_limit", 40),
    "day totals": lambda d: _bump(d["day_totals"][0], "shown"),
    "day totals noticed": lambda d: _bump(d["day_totals"][0], "noticed"),
    "missing day totals": lambda d: d.__setitem__("day_totals", []),
    "day totals order": lambda d: d["day_totals"][0].__setitem__("day_index", 1),
}


def test_the_story_contract_accepts_both_selection_policy_value_sets(saved) -> None:
    # Selection policy 2 caps 4 per person and 60 per run; policy 3 spreads up to 1,000
    # questions over the run with no per-person cap. Offline runs fund the whole run cap.
    document = json.loads(story_json_bytes(saved("default-mock").story))
    assert (document["ai"]["per_person"], document["ai"]["per_run"]) == (4, 60)
    document["ai"].update(per_person=1_000, per_run=1_000, funded_limit=1_000)
    assert RunStoryV1.model_validate_json(json.dumps(document)).ai.per_run == 1_000
    provider = json.loads(story_json_bytes(saved("default-provider").story))
    for limit in (10, 20, 40, 60):
        provider["ai"]["funded_limit"] = limit
        RunStoryV1.model_validate_json(json.dumps(provider))
    provider["ai"].update(per_person=1_000, per_run=1_000)
    for limit in (60, 120, 250, 500, 1_000):
        provider["ai"]["funded_limit"] = limit
        RunStoryV1.model_validate_json(json.dumps(provider))
    # Request counts, attempts and per-person totals take the larger policy's maxima.
    for path, value in (
        (("ai", "usage_this_run", "attempts"), 6_000),
        (("people", 0, "totals", "chosen"), 1_000),
        (("ai", "reused_answers"), 1_000),
    ):
        changed = json.loads(json.dumps(provider))
        target: Any = changed
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value
        RunStoryV1.model_validate_json(json.dumps(changed))
        target[path[-1]] = value + 1
        with pytest.raises(ValidationError):
            RunStoryV1.model_validate_json(json.dumps(changed))


@pytest.mark.parametrize("mutation", sorted(_REFUSED_STORIES))
def test_the_story_contract_refuses_inconsistent_documents(saved, mutation: str) -> None:
    valid = story_json_bytes(saved("default-mock").story)
    assert RunStoryV1.model_validate_json(valid) == saved("default-mock").story
    document = json.loads(valid)
    _REFUSED_STORIES[mutation](document)
    with pytest.raises(ValidationError):
        RunStoryV1.model_validate_json(json.dumps(document))
