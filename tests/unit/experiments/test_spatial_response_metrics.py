from __future__ import annotations

import math
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_response import (
    SpatialResponseInput,
    SpatialResponseTraits,
    parse_spatial_response_input_json,
)
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseMetrics,
    derive_spatial_response_metrics,
    spatial_response_assumption_structure_sha256,
)
from adlife.core.simulation.spatial_attention import SpatialAttentionEvaluation
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from adlife.core.simulation.spatial_response import (
    SpatialResponseEvaluation,
    SpatialResponseState,
    evaluate_spatial_responses,
    summarize_spatial_response_artifact,
)
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_attention import _evaluate_attention
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)
from tests.unit.city.test_spatial_response import _response_input

AGENTS_SHA256 = "a" * 64
TRACE_SHA256 = "b" * 64
RESPONSE_ARTIFACT = ("outputs/spatial-responses.jsonl",)
STATE_ARTIFACTS = (
    "inputs/spatial-response.json",
    "outputs/response-state.json",
)


def _attention_metrics(
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    *,
    agent_ids: tuple[str, ...],
) -> SpatialMetrics:
    return derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=agent_ids,
        agents_sha256=AGENTS_SHA256,
        trace_sha256=TRACE_SHA256,
        days=1,
        source_run_schema_version=6,
    )


def _derive(
    response_input: SpatialResponseInput,
    response: SpatialResponseEvaluation,
    *,
    scenario: Any,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    agent_ids: tuple[str, ...] = ("person-001",),
    attention_metrics: SpatialMetrics | None = None,
) -> SpatialResponseMetrics:
    metrics = attention_metrics or _attention_metrics(
        opportunities,
        attention,
        agent_ids=agent_ids,
    )
    return derive_spatial_response_metrics(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        agent_ids=agent_ids,
        attention_metrics=metrics,
    )


def _billboard_bundle(
    *, attention_seed: int, response_input: SpatialResponseInput | None = None
) -> tuple[
    Any,
    SpatialOpportunityEvaluation,
    SpatialAttentionEvaluation,
    SpatialResponseInput,
    SpatialResponseEvaluation,
    SpatialMetrics,
]:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_billboard()])
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=attention_seed)
    actual_input = response_input or _response_input(scenario)
    response = evaluate_spatial_responses(
        actual_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )
    return (
        scenario,
        opportunities,
        attention,
        actual_input,
        response,
        _attention_metrics(opportunities, attention, agent_ids=("person-001",)),
    )


def _event_receipt(
    name: str,
    numerator: int | float,
    denominator: int,
    value: float,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": name,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "source_event_types": ("spatial.response",),
        "source_artifacts": RESPONSE_ARTIFACT,
    }


def _state_receipt(
    name: str,
    *,
    initial_total: float,
    final_total: float,
    denominator: int,
) -> dict[str, object]:
    change_total = final_total - initial_total
    return {
        "schema_version": 1,
        "name": name,
        "initial_total": initial_total,
        "final_total": final_total,
        "change_total": change_total,
        "denominator": denominator,
        "initial_mean": initial_total / denominator,
        "final_mean": final_total / denominator,
        "mean_change": change_total / denominator,
        "source_artifacts": STATE_ARTIFACTS,
    }


def _replace_input_document(
    response_input: SpatialResponseInput,
    mutate: Any,
) -> SpatialResponseInput:
    document = deepcopy(response_input.model_dump(mode="json"))
    mutate(document)
    return parse_spatial_response_input_json(canonical_json(document))


def test_one_notice_response_metrics_have_exact_provenance_values_and_receipts() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )
    summary = summarize_spatial_response_artifact(response)

    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=attention_metrics,
    )

    assert set(result.model_dump()) == {
        "schema_version",
        "model_id",
        "claim_scope",
        "source_run_schema_version",
        "opportunity_model_id",
        "attention_model_id",
        "response_model_id",
        "scenario_sha256",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "opportunity_structure_sha256",
        "response_input_sha256",
        "response_assumption_structure_sha256",
        "response_stream_sha256",
        "response_state_sha256",
        "seed",
        "population_size",
        "days",
        "campaign_count",
        "overall",
        "channels",
        "campaigns",
    }
    assert result.schema_version == 1
    assert result.model_id == "spatial-response-metrics-v1"
    assert result.claim_scope == "synthetic-response-metrics-not-observed-outcomes"
    assert result.source_run_schema_version == 6
    assert result.opportunity_model_id == "spatial-opportunity-v1"
    assert result.attention_model_id == "spatial-attention-v1"
    assert result.response_model_id == "spatial-response-v1"
    assert result.scenario_sha256 == (
        "18a56d60fb9c627dec150011b6399c873ef2d6c26c2d285f43e705f10e26d9a4"
    )
    assert result.city_sha256 == (
        "178f2cae93f8d144b014510b35e7e7b3a517b2a82522d6d2f10aadcafcde43a3"
    )
    assert result.agents_sha256 == AGENTS_SHA256
    assert result.trace_sha256 == TRACE_SHA256
    assert result.opportunity_structure_sha256 == (
        "85d6dad66872494508404c90a9175514fd839610dccaff4c6fc4875a6812befa"
    )
    assert result.response_input_sha256 == (
        "02e75eb61cc96fb221c74f1ebda6bad60e1b45da59769cbcd5e17e187674fd1a"
    )
    assert result.response_assumption_structure_sha256 == (
        "bc7682df53947474186e6033b7a074dd5aec63c8297ff55965d2247e1c07264a"
    )
    assert result.response_stream_sha256 == (
        "941fdff99513a43ee3e5d729710a37dbafdb55f70e653a5cc14c37cf69816333"
    )
    assert result.response_state_sha256 == (
        "f2f4117a2dc05659b8117a256bb1be58dbe33d92e8cf10152bcdd0d3ac717bc6"
    )
    assert result.response_stream_sha256 == summary.stream_sha256
    assert result.response_state_sha256 == summary.state_document_sha256
    assert (result.seed, result.population_size, result.days, result.campaign_count) == (
        0,
        1,
        1,
        1,
    )

    assert result.overall.response_count.model_dump() == _event_receipt("response_count", 1, 1, 1.0)
    assert result.overall.response_reach.model_dump() == _event_receipt("response_reach", 1, 1, 1.0)
    assert result.overall.response_frequency.model_dump() == _event_receipt(
        "response_frequency", 1, 1, 1.0
    )
    assert result.overall.mean_rule_sentiment_delta.model_dump() == _event_receipt(
        "mean_rule_sentiment_delta",
        -0.016499999999999994,
        1,
        -0.016499999999999994,
    )
    assert result.overall.mean_rule_recall_delta.model_dump() == _event_receipt(
        "mean_rule_recall_delta", 0.124, 1, 0.124
    )
    assert type(result.overall.response_count.numerator) is int
    assert type(result.overall.mean_rule_sentiment_delta.numerator) is float
    assert type(result.overall.response_count.denominator) is int
    assert result.overall.brand_sentiment.model_dump() == _state_receipt(
        "brand_sentiment",
        initial_total=0.1,
        final_total=0.08350000000000002,
        denominator=1,
    )
    assert result.overall.recall_strength.model_dump() == _state_receipt(
        "recall_strength",
        initial_total=0.1,
        final_total=0.2116,
        denominator=1,
    )
    assert result.overall.purchase_intention_proxy.model_dump() == _state_receipt(
        "purchase_intention_proxy",
        initial_total=0.2,
        final_total=0.36610333333333334,
        denominator=1,
    )

    assert tuple(series.channel for series in result.channels) == ("roadside", "mobile")
    assert result.channels[0].response_count == result.overall.response_count
    assert result.channels[1].response_count.model_dump() == _event_receipt(
        "response_count", 0, 1, 0.0
    )
    assert tuple(series.campaign_id for series in result.campaigns) == ("fictional-launch",)
    assert result.campaigns[0].response_count == result.overall.response_count
    assert result.campaigns[0].brand_sentiment == result.overall.brand_sentiment


def test_zero_responses_keep_finite_positive_zero_receipts_and_nonzero_state() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=2)
    )
    assert attention.counts.noticed_count == response.counts.response_count == 0

    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=attention_metrics,
    )

    assert result.overall.response_count.model_dump() == _event_receipt("response_count", 0, 1, 0.0)
    assert result.overall.response_reach.model_dump() == _event_receipt("response_reach", 0, 1, 0.0)
    for name in (
        "response_frequency",
        "mean_rule_sentiment_delta",
        "mean_rule_recall_delta",
    ):
        receipt = getattr(result.overall, name)
        assert receipt.model_dump() == _event_receipt(name, 0, 0, 0.0)
        assert math.copysign(1.0, receipt.value) == 1.0

    for series in (result.overall, *result.campaigns):
        for name in ("brand_sentiment", "recall_strength", "purchase_intention_proxy"):
            receipt = getattr(series, name)
            assert receipt.initial_total == receipt.final_total
            assert receipt.change_total == receipt.mean_change == 0.0
            assert math.copysign(1.0, receipt.change_total) == 1.0
            assert math.copysign(1.0, receipt.mean_change) == 1.0
    assert result.overall.brand_sentiment.model_dump() == _state_receipt(
        "brand_sentiment", initial_total=0.1, final_total=0.1, denominator=1
    )
    assert result.overall.recall_strength.model_dump() == _state_receipt(
        "recall_strength", initial_total=0.1, final_total=0.1, denominator=1
    )
    assert result.overall.purchase_intention_proxy.model_dump() == _state_receipt(
        "purchase_intention_proxy", initial_total=0.2, final_total=0.2, denominator=1
    )


def test_no_response_campaign_is_retained_and_overall_state_weights_all_campaigns() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                campaign_id="phone-campaign",
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=2,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=42)
    response_input = _response_input(scenario)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )

    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
    )

    assert tuple(series.campaign_id for series in result.campaigns) == (
        "fictional-launch",
        "phone-campaign",
    )
    changed, untouched = result.campaigns
    assert changed.response_count.numerator == 1
    assert untouched.response_count.model_dump() == _event_receipt("response_count", 0, 1, 0.0)
    assert untouched.response_frequency.denominator == 0
    assert untouched.brand_sentiment.model_dump() == _state_receipt(
        "brand_sentiment", initial_total=0.1, final_total=0.1, denominator=1
    )
    assert untouched.recall_strength.model_dump() == _state_receipt(
        "recall_strength", initial_total=0.1, final_total=0.1, denominator=1
    )
    assert untouched.purchase_intention_proxy.model_dump() == _state_receipt(
        "purchase_intention_proxy", initial_total=0.2, final_total=0.2, denominator=1
    )

    # State aggregates use the complete agent/campaign Cartesian product. They are not
    # weighted by responses, so the untouched campaign remains in every denominator.
    assert result.overall.brand_sentiment.model_dump() == _state_receipt(
        "brand_sentiment",
        initial_total=0.2,
        final_total=0.18350000000000002,
        denominator=2,
    )
    assert result.overall.brand_sentiment.final_mean == pytest.approx(
        (changed.brand_sentiment.final_mean + untouched.brand_sentiment.final_mean) / 2
    )
    assert result.overall.purchase_intention_proxy.denominator == 2


def test_multi_agent_campaign_state_uses_complete_cartesian_denominators() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                campaign_id="phone-campaign",
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=2,
            ),
        ],
    )
    agent_ids = ("person-001", "person-002")
    opportunities = _evaluate(_mobility(pack, agent_count=2), scenario)
    attention = _evaluate_attention(opportunities, seed=42)
    response_input = _response_input(scenario, agent_ids=agent_ids)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agent_ids,
    )

    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        agent_ids=agent_ids,
    )

    assert result.population_size == 2
    assert result.campaign_count == 2
    assert result.overall.response_reach.denominator == 2
    assert result.overall.brand_sentiment.denominator == 4
    assert result.overall.brand_sentiment.initial_total == 0.4
    assert [series.brand_sentiment.denominator for series in result.campaigns] == [2, 2]
    assert [series.response_reach.denominator for series in result.campaigns] == [2, 2]
    assert result.overall.brand_sentiment.final_total == math.fsum(
        series.brand_sentiment.final_total for series in result.campaigns
    )

    document = result.model_dump(mode="python")
    reversed_campaigns = deepcopy(document)
    reversed_campaigns["campaigns"] = tuple(reversed(reversed_campaigns["campaigns"]))
    swapped_channels = deepcopy(document)
    swapped_channels["channels"] = tuple(reversed(swapped_channels["channels"]))
    wrong_campaign_count = deepcopy(document)
    wrong_campaign_count["campaign_count"] = 1
    for invalid in (reversed_campaigns, swapped_channels, wrong_campaign_count):
        with pytest.raises(ValidationError):
            SpatialResponseMetrics.model_validate(invalid)


def test_saturation_keeps_planned_rule_delta_separate_from_committed_state_change() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_billboard()])
    base = _response_input(
        scenario,
        sentiment=1.0,
        recall=1.0,
        intention=1.0,
        traits=SpatialResponseTraits(
            price_sensitivity=0.0,
            novelty_seeking=1.0,
            advertising_skepticism=0.0,
            mobile_recall_encoding=1.0,
            roadside_recall_encoding=1.0,
            impulsivity=1.0,
        ),
    )
    response_input = _replace_input_document(
        base,
        lambda document: document["profiles"][0].__setitem__("interests", ["coffee", "work"]),
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=0)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )

    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
    )

    assert result.overall.mean_rule_sentiment_delta.value == 0.18
    assert result.overall.mean_rule_recall_delta.value == 0.3
    assert result.overall.brand_sentiment.final_mean == 1.0
    assert result.overall.brand_sentiment.mean_change == 0.0
    assert result.overall.recall_strength.final_mean == 1.0
    assert result.overall.recall_strength.mean_change == 0.0
    assert result.overall.purchase_intention_proxy.final_mean == 1.0
    assert result.overall.purchase_intention_proxy.mean_change == 0.0


def test_mixed_channel_same_minute_has_event_series_but_no_channel_state_attribution() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                activities=["commute"],
                windows=[{"start_minute": 481, "end_minute": 482}],
                probability=1.0,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=4)
    response_input = _response_input(scenario)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )
    assert response.counts.roadside_response_count == 1
    assert response.counts.phone_response_count == 1
    assert response.counts.state_update_count == 1

    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
    )

    roadside, mobile = result.channels
    assert (roadside.response_count.numerator, mobile.response_count.numerator) == (1, 1)
    assert roadside.mean_rule_recall_delta.value == 0.124
    assert mobile.mean_rule_recall_delta.value == 0.19
    assert result.overall.mean_rule_recall_delta.value == pytest.approx((0.124 + 0.19) / 2)
    assert result.overall.recall_strength.mean_change == pytest.approx(
        1.0 - (1.0 - 0.1) * (1.0 - 0.124) * (1.0 - 0.19) - 0.1
    )
    assert result.overall.recall_strength.mean_change != pytest.approx(0.124 + 0.19)
    forbidden = {
        "brand_sentiment",
        "recall_strength",
        "purchase_intention",
        "purchase_intention_proxy",
        "state",
    }
    for channel in result.channels:
        assert forbidden.isdisjoint(channel.model_dump())


def test_metrics_models_are_strict_frozen_finite_bounded_and_receipt_coherent() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )
    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=attention_metrics,
    )
    with pytest.raises(ValidationError, match="frozen"):
        result.__setattr__("days", 2)

    document = result.model_dump(mode="python")
    with pytest.raises(ValidationError, match="extra"):
        SpatialResponseMetrics.model_validate(document | {"purchase_probability": 0.5})

    invalid_documents: list[dict[str, Any]] = []
    nonfinite = deepcopy(document)
    nonfinite["overall"]["mean_rule_recall_delta"]["value"] = float("nan")
    invalid_documents.append(nonfinite)
    wrong_quotient = deepcopy(document)
    wrong_quotient["overall"]["response_reach"]["value"] = 0.5
    invalid_documents.append(wrong_quotient)
    bool_count = deepcopy(document)
    bool_count["overall"]["response_count"]["numerator"] = True
    invalid_documents.append(bool_count)
    wrong_source = deepcopy(document)
    wrong_source["overall"]["response_count"]["source_artifacts"] = (
        "outputs/spatial-attention.jsonl",
    )
    invalid_documents.append(wrong_source)
    wrong_state = deepcopy(document)
    wrong_state["overall"]["brand_sentiment"]["mean_change"] = 0.5
    invalid_documents.append(wrong_state)
    channel_state = deepcopy(document)
    channel_state["channels"][0]["brand_sentiment"] = document["overall"]["brand_sentiment"]
    invalid_documents.append(channel_state)

    for invalid in invalid_documents:
        with pytest.raises(ValidationError):
            SpatialResponseMetrics.model_validate(invalid)


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "model_id",
        "claim_scope",
        "source_run_schema_version",
        "opportunity_model_id",
        "attention_model_id",
        "response_model_id",
    ],
)
def test_metrics_refuse_public_identity_tampering(field: str) -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )
    result = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=attention_metrics,
    )
    document = result.model_dump(mode="python")
    document[field] = 7 if field in {"schema_version", "source_run_schema_version"} else "other"

    with pytest.raises(ValidationError):
        SpatialResponseMetrics.model_validate(document)


def test_derivation_revalidates_attention_metric_and_response_artifact_anchors() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )

    tampered_metric_documents = [
        attention_metrics.model_dump(mode="python") | {"seed": 2},
        attention_metrics.model_dump(mode="python") | {"scenario_sha256": "f" * 64},
        attention_metrics.model_dump(mode="python") | {"city_sha256": "f" * 64},
        attention_metrics.model_dump(mode="python") | {"opportunity_structure_sha256": "f" * 64},
    ]
    for document in tampered_metric_documents:
        tampered = SpatialMetrics.model_validate(document)
        with pytest.raises(ValueError, match="attention metrics"):
            _derive(
                response_input,
                response,
                scenario=scenario,
                opportunities=opportunities,
                attention=attention,
                attention_metrics=tampered,
            )

    v5_metrics = attention_metrics.model_copy(update={"source_run_schema_version": 5})
    with pytest.raises(ValueError, match=r"schema.*6"):
        _derive(
            response_input,
            response,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=v5_metrics,
        )

    constructed = SpatialResponseEvaluation.model_construct(
        **(response.model_dump(mode="python") | {"attention_seed": 2})
    )
    with pytest.raises((ValidationError, ValueError), match=r"seed|attention"):
        _derive(
            response_input,
            constructed,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=attention_metrics,
        )


def test_derivation_requires_exact_scenario_opportunity_attention_input_and_population() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )
    changed_scenario = scenario.model_copy(update={"name": "Different scenario identity"})
    changed_input = _replace_input_document(
        response_input,
        lambda document: document["initial_states"][0].__setitem__("brand_sentiment", 0.2),
    )
    different_opportunities = _evaluate(
        _mobility(load_pack(pack_data())),
        _scenario(load_pack(pack_data()), [_billboard(fraction=0.4, longitude=0.004)]),
    )
    different_attention = _evaluate_attention(opportunities, seed=2)

    calls = (
        {
            "response_input": response_input,
            "scenario": changed_scenario,
            "opportunities": opportunities,
            "attention": attention,
            "agent_ids": ("person-001",),
        },
        {
            "response_input": response_input,
            "scenario": scenario,
            "opportunities": different_opportunities,
            "attention": attention,
            "agent_ids": ("person-001",),
        },
        {
            "response_input": response_input,
            "scenario": scenario,
            "opportunities": opportunities,
            "attention": different_attention,
            "agent_ids": ("person-001",),
        },
        {
            "response_input": changed_input,
            "scenario": scenario,
            "opportunities": opportunities,
            "attention": attention,
            "agent_ids": ("person-001",),
        },
        {
            "response_input": response_input,
            "scenario": scenario,
            "opportunities": opportunities,
            "attention": attention,
            "agent_ids": ("person-002",),
        },
    )
    for case in calls:
        with pytest.raises((ValidationError, ValueError)):
            _derive(
                case["response_input"],
                response,
                scenario=case["scenario"],
                opportunities=case["opportunities"],
                attention=case["attention"],
                agent_ids=case["agent_ids"],
                attention_metrics=attention_metrics,
            )


def test_derivation_rejects_notice_response_count_and_channel_mismatches() -> None:
    scenario, opportunities, attention, response_input, _, attention_metrics = _billboard_bundle(
        attention_seed=0
    )
    _, _, _, _, empty_response, _ = _billboard_bundle(attention_seed=2)
    forged_empty = SpatialResponseEvaluation.model_validate(
        empty_response.model_dump(mode="python") | {"attention_seed": 0}
    )

    with pytest.raises(ValueError, match=r"notice|response"):
        _derive(
            response_input,
            forged_empty,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=attention_metrics,
        )

    valid_response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )
    counts = valid_response.counts.model_dump(mode="python")
    counts["roadside_response_count"] = 0
    counts["phone_response_count"] = 1
    wrong_channel = SpatialResponseEvaluation.model_construct(
        **(valid_response.model_dump(mode="python") | {"counts": counts})
    )
    with pytest.raises((ValidationError, ValueError), match="channel"):
        _derive(
            response_input,
            wrong_channel,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=attention_metrics,
        )


def test_derivation_rejects_a_coherent_forged_first_state_anchor() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )
    rule, update = response.records
    previous = SpatialResponseState(
        agent_id="person-001",
        campaign_id="fictional-launch",
        brand_sentiment=0.2,
        recall_strength=0.2,
        purchase_intention=0.3,
        response_count=0,
        last_response_minute=None,
    )
    changed_rule = rule.model_copy(
        update={"state_before_sha256": sha256(canonical_json(previous).encode("utf-8")).hexdigest()}
    )
    sentiment = previous.brand_sentiment + changed_rule.sentiment_delta
    recall = 1.0 - (1.0 - previous.recall_strength) * (1.0 - changed_rule.recall_delta)
    intention = (
        0.40 * ((sentiment + 1.0) / 2.0)
        + 0.25 * changed_rule.value_match
        + 0.20 * recall
        + 0.15 * changed_rule.impulsivity
    )
    state = SpatialResponseState(
        agent_id="person-001",
        campaign_id="fictional-launch",
        brand_sentiment=sentiment,
        recall_strength=recall,
        purchase_intention=intention,
        response_count=1,
        last_response_minute=changed_rule.model_minute,
    )
    changed_update = update.model_copy(update={"previous_state": previous, "state": state})
    forged = SpatialResponseEvaluation.model_validate(
        response.model_dump(mode="python")
        | {"records": (changed_rule, changed_update), "final_states": (state,)}
    )

    with pytest.raises(ValueError, match=r"initial|anchor|response evidence"):
        _derive(
            response_input,
            forged,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=attention_metrics,
        )


def test_derivation_rejects_a_coherent_forged_untouched_final_state() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=2)
    )
    untouched = response.final_states[0].model_copy(update={"brand_sentiment": 0.9})
    forged = SpatialResponseEvaluation.model_validate(
        response.model_dump(mode="python") | {"final_states": (untouched,)}
    )

    with pytest.raises(ValueError, match=r"untouched|initial|response evidence"):
        _derive(
            response_input,
            forged,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=attention_metrics,
        )


def test_derivation_rejects_valid_looking_response_rules_not_owned_by_the_input() -> None:
    scenario, opportunities, attention, response_input, response, attention_metrics = (
        _billboard_bundle(attention_seed=0)
    )
    rule, update = response.records
    affordability = 1.25 - rule.relative_price * 0.4
    value_match = 0.55 * rule.interest_match + 0.25 * rule.novelty_seeking + 0.20 * affordability
    sentiment_delta = (
        0.18 * value_match - 0.12 * rule.advertising_skepticism - 0.06 * rule.frequency_fatigue
    )
    changed_rule = rule.model_copy(
        update={
            "price_sensitivity": 0.4,
            "affordability": affordability,
            "value_match": value_match,
            "sentiment_delta": sentiment_delta,
        }
    )
    sentiment = update.previous_state.brand_sentiment + changed_rule.sentiment_delta
    recall = update.state.recall_strength
    state = update.state.model_copy(
        update={
            "brand_sentiment": sentiment,
            "purchase_intention": (
                0.40 * ((sentiment + 1.0) / 2.0)
                + 0.25 * changed_rule.value_match
                + 0.20 * recall
                + 0.15 * changed_rule.impulsivity
            ),
        }
    )
    changed_update = update.model_copy(update={"state": state})
    forged = SpatialResponseEvaluation.model_validate(
        response.model_dump(mode="python")
        | {"records": (changed_rule, changed_update), "final_states": (state,)}
    )

    with pytest.raises(ValueError, match=r"assumption|response evidence"):
        _derive(
            response_input,
            forged,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
            attention_metrics=attention_metrics,
        )


def test_derivation_rejects_noncanonical_constructed_response_records() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=2,
            )
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=10)
    response_input = _response_input(scenario)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )
    forged = SpatialResponseEvaluation.model_construct(
        **(response.model_dump(mode="python") | {"records": tuple(reversed(response.records))})
    )

    with pytest.raises(ValidationError, match="canonical"):
        _derive(
            response_input,
            forged,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
        )


def test_assumption_structure_hash_has_exact_projection_and_excludes_creative_bindings() -> None:
    scenario, _, _, response_input, _, _ = _billboard_bundle(attention_seed=0)
    expected_document = {
        "model_id": "spatial-response-assumption-structure-v1",
        "profiles": [
            {
                "agent_id": "person-001",
                "fictional": True,
                "interests": ["coffee", "outdoors"],
                "traits": {
                    "price_sensitivity": 0.8,
                    "novelty_seeking": 0.3,
                    "advertising_skepticism": 0.6,
                    "mobile_recall_encoding": 0.7,
                    "roadside_recall_encoding": 0.4,
                    "impulsivity": 0.2,
                },
            }
        ],
        "campaigns": [
            {
                "campaign_id": "fictional-launch",
                "target_interests": ["coffee", "work"],
                "relative_price": 1.25,
            }
        ],
        "placements": [
            {
                "placement_id": "billboard-ab",
                "campaign_id": "fictional-launch",
                "channel": "roadside-billboard",
                "frequency_cap_per_agent_per_day": 3,
            }
        ],
        "initial_states": [
            {
                "agent_id": "person-001",
                "campaign_id": "fictional-launch",
                "brand_sentiment": 0.1,
                "recall_strength": 0.1,
                "purchase_intention": 0.2,
            }
        ],
    }
    expected = sha256(canonical_json(expected_document).encode("utf-8")).hexdigest()
    assert expected == "bc7682df53947474186e6033b7a074dd5aec63c8297ff55965d2247e1c07264a"
    assert spatial_response_assumption_structure_sha256(response_input, scenario) == expected

    changed_campaign = scenario.campaigns[0].model_copy(
        update={"name": "Different fictional creative", "creative_sha256": "f" * 64}
    )
    changed_creative_scenario = scenario.model_copy(
        update={
            "name": "Different fictional scenario copy",
            "campaigns": (changed_campaign,),
        }
    )
    changed_city_scenario = scenario.model_copy(update={"city_sha256": "e" * 64})
    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_creative_scenario),
            changed_creative_scenario,
        )
        == expected
    )
    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_city_scenario),
            changed_city_scenario,
        )
        == expected
    )


def test_assumption_hash_tracks_rule_relevant_placement_contract_but_not_geometry() -> None:
    pack = load_pack(pack_data())
    baseline_scenario = _scenario(pack, [_billboard()])
    baseline_input = _response_input(baseline_scenario)
    baseline = spatial_response_assumption_structure_sha256(
        baseline_input,
        baseline_scenario,
    )

    changed_cap_scenario = _scenario(pack, [_billboard(cap=4)])
    changed_channel_scenario = _scenario(
        pack,
        [
            _phone(
                placement_id="billboard-ab",
                campaign_id="fictional-launch",
                cap=3,
                windows=[{"start_minute": 0, "end_minute": 1}],
            )
        ],
    )
    changed_id_scenario = _scenario(
        pack,
        [_billboard(placement_id="other-placement")],
    )
    changed_campaign_placement = _billboard()
    changed_campaign_placement["campaign_id"] = "other-campaign"
    changed_campaign_scenario = _scenario(pack, [changed_campaign_placement])
    changed_geometry_scenario = _scenario(
        pack,
        [
            _billboard(
                fraction=0.4,
                longitude=0.004,
                orientation=260.0,
                windows=[{"start_minute": 400, "end_minute": 900}],
                max_view_distance=80.0,
            )
        ],
    )

    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_cap_scenario), changed_cap_scenario
        )
        != baseline
    )
    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_channel_scenario), changed_channel_scenario
        )
        != baseline
    )
    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_id_scenario), changed_id_scenario
        )
        != baseline
    )
    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_campaign_scenario), changed_campaign_scenario
        )
        != baseline
    )
    assert (
        spatial_response_assumption_structure_sha256(
            _response_input(changed_geometry_scenario),
            changed_geometry_scenario,
        )
        == baseline
    )


def test_response_metrics_refuse_credential_shaped_campaign_ids_without_echoing_them() -> None:
    secret_campaign_id = "sk-live-abcdefghij"
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                campaign_id=secret_campaign_id,
                windows=[{"start_minute": 0, "end_minute": 1}],
            )
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=0)
    response_input = _response_input(scenario)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=("person-001",),
    )

    with pytest.raises(ValueError) as error:
        _derive(
            response_input,
            response,
            scenario=scenario,
            opportunities=opportunities,
            attention=attention,
        )

    assert secret_campaign_id not in str(error.value)
    assert "identifier" in str(error.value).lower()

    (
        safe_scenario,
        safe_opportunities,
        safe_attention,
        safe_input,
        safe_response,
        safe_attention_metrics,
    ) = _billboard_bundle(attention_seed=0)
    safe_metrics = _derive(
        safe_input,
        safe_response,
        scenario=safe_scenario,
        opportunities=safe_opportunities,
        attention=safe_attention,
        attention_metrics=safe_attention_metrics,
    )
    document = safe_metrics.model_dump(mode="python")
    document["campaigns"][0]["campaign_id"] = secret_campaign_id
    with pytest.raises(ValidationError) as model_error:
        SpatialResponseMetrics.model_validate(document)
    assert secret_campaign_id not in str(model_error.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["profiles"][0].__setitem__("agent_id", "person-002"),
        lambda d: d["profiles"][0].__setitem__("interests", ["coffee", "travel"]),
        lambda d: d["profiles"][0]["traits"].__setitem__("price_sensitivity", 0.7),
        lambda d: d["profiles"][0]["traits"].__setitem__("novelty_seeking", 0.4),
        lambda d: d["profiles"][0]["traits"].__setitem__("advertising_skepticism", 0.5),
        lambda d: d["profiles"][0]["traits"].__setitem__("mobile_recall_encoding", 0.6),
        lambda d: d["profiles"][0]["traits"].__setitem__("roadside_recall_encoding", 0.5),
        lambda d: d["profiles"][0]["traits"].__setitem__("impulsivity", 0.3),
        lambda d: d["campaigns"][0].__setitem__("target_interests", ["coffee", "travel"]),
        lambda d: d["campaigns"][0].__setitem__("relative_price", 2.0),
        lambda d: d["initial_states"][0].__setitem__("brand_sentiment", 0.2),
        lambda d: d["initial_states"][0].__setitem__("recall_strength", 0.2),
        lambda d: d["initial_states"][0].__setitem__("purchase_intention", 0.3),
    ],
)
def test_assumption_structure_hash_is_sensitive_to_every_numeric_rule_identity(
    mutation: Any,
) -> None:
    scenario, _, _, response_input, _, _ = _billboard_bundle(attention_seed=0)
    baseline = spatial_response_assumption_structure_sha256(response_input, scenario)
    document = deepcopy(response_input.model_dump(mode="json"))
    mutation(document)
    if document["profiles"][0]["agent_id"] != document["initial_states"][0]["agent_id"]:
        document["initial_states"][0]["agent_id"] = document["profiles"][0]["agent_id"]
    changed = parse_spatial_response_input_json(canonical_json(document))

    assert spatial_response_assumption_structure_sha256(changed, scenario) != baseline


def test_assumption_hash_and_derivation_revalidate_constructed_noncanonical_inputs() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                campaign_id="phone-campaign",
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack, agent_count=2), scenario)
    attention = _evaluate_attention(opportunities, seed=42)
    agent_ids = ("person-001", "person-002")
    response_input = _response_input(scenario, agent_ids=agent_ids)
    response = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agent_ids,
    )
    expected = _derive(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        agent_ids=agent_ids,
    )
    noncanonical = SpatialResponseInput.model_construct(
        **(
            response_input.model_dump(mode="python")
            | {
                "profiles": tuple(reversed(response_input.profiles)),
                "campaigns": tuple(reversed(response_input.campaigns)),
                "initial_states": tuple(reversed(response_input.initial_states)),
            }
        )
    )
    noncanonical_scenario = type(scenario).model_construct(
        **(
            scenario.model_dump(mode="python")
            | {
                "campaigns": tuple(reversed(scenario.campaigns)),
                "placements": tuple(reversed(scenario.placements)),
            }
        )
    )

    assert spatial_response_assumption_structure_sha256(noncanonical, scenario) == (
        expected.response_assumption_structure_sha256
    )
    assert (
        spatial_response_assumption_structure_sha256(response_input, noncanonical_scenario)
        == expected.response_assumption_structure_sha256
    )
    assert (
        _derive(
            noncanonical,
            response,
            scenario=noncanonical_scenario,
            opportunities=opportunities,
            attention=attention,
            agent_ids=tuple(reversed(agent_ids)),
        )
        == expected
    )


def test_assumption_hash_rejects_invalid_constructed_inputs_instead_of_hashing_them() -> None:
    scenario, _, _, response_input, _, _ = _billboard_bundle(attention_seed=0)
    invalid = SpatialResponseInput.model_construct(
        **(
            response_input.model_dump(mode="python")
            | {"initial_states": (response_input.initial_states[0],) * 2}
        )
    )

    with pytest.raises(ValidationError, match="duplicate"):
        spatial_response_assumption_structure_sha256(invalid, scenario)


def test_response_input_json_permutations_have_one_assumption_hash() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(campaign_id="phone-campaign", windows=[{"start_minute": 0, "end_minute": 1}]),
        ],
    )
    baseline = _response_input(
        scenario,
        agent_ids=("person-001", "person-002"),
    )
    document = baseline.model_dump(mode="json")
    document["profiles"].reverse()
    document["campaigns"].reverse()
    document["initial_states"].reverse()
    for profile in document["profiles"]:
        profile["interests"].reverse()
    for campaign in document["campaigns"]:
        campaign["target_interests"].reverse()
    permuted = parse_spatial_response_input_json(canonical_json(document))

    assert permuted == baseline
    assert spatial_response_assumption_structure_sha256(permuted, scenario) == (
        spatial_response_assumption_structure_sha256(baseline, scenario)
    )
