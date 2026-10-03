from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseMetrics,
    derive_spatial_response_metrics,
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
from tests.unit.city.test_spatial_response import _evaluate_response, _response_input

AGENTS_SHA256 = "a" * 64
TRACE_SHA256 = "b" * 64
AGENT_IDS = ("person-001", "person-002")


def _response_metric_pair(
    *,
    phone_campaign_id: str = "phone-campaign",
    phone_end_minute: int = 2,
    phone_cap: int = 2,
    initial_sentiment: float = 0.1,
) -> tuple[Any, Any]:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                campaign_id=phone_campaign_id,
                windows=[{"start_minute": 0, "end_minute": phone_end_minute}],
                probability=1.0,
                cap=phone_cap,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack, agent_count=2), scenario)
    attention = _evaluate_attention(opportunities, seed=42)
    response_input = _response_input(
        scenario,
        agent_ids=AGENT_IDS,
        sentiment=initial_sentiment,
    )
    response = _evaluate_response(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=AGENT_IDS,
    )
    attention_metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=AGENT_IDS,
        agents_sha256=AGENTS_SHA256,
        trace_sha256=TRACE_SHA256,
        days=1,
        source_run_schema_version=6,
    )
    response_metrics = derive_spatial_response_metrics(
        response_input,
        response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=attention_metrics,
        agent_ids=AGENT_IDS,
    )
    return attention_metrics, response_metrics


def _response_metrics(
    *,
    phone_campaign_id: str = "phone-campaign",
    phone_end_minute: int = 2,
    phone_cap: int = 2,
    initial_sentiment: float = 0.1,
) -> Any:
    return _response_metric_pair(
        phone_campaign_id=phone_campaign_id,
        phone_end_minute=phone_end_minute,
        phone_cap=phone_cap,
        initial_sentiment=initial_sentiment,
    )[1]


def test_valid_campaign_regrouping_does_not_reject_response_metrics() -> None:
    result = _response_metrics(initial_sentiment=0.0, phone_end_minute=1)

    overall = result.overall.purchase_intention_proxy.final_total
    grouped = math.fsum(
        campaign.purchase_intention_proxy.final_total for campaign in result.campaigns
    )
    assert overall == 1.1040866666666669
    assert grouped == 1.1040866666666667
    assert overall - grouped == math.ulp(overall)


def test_response_metrics_still_refuse_material_campaign_total_tampering() -> None:
    result = _response_metrics(initial_sentiment=-0.5, phone_end_minute=1)
    document = result.model_dump(mode="python")
    receipt = document["overall"]["purchase_intention_proxy"]
    receipt["final_total"] += 1e-12
    receipt["change_total"] = receipt["final_total"] - receipt["initial_total"]
    receipt["final_mean"] = receipt["final_total"] / receipt["denominator"]
    receipt["mean_change"] = receipt["change_total"] / receipt["denominator"]

    with pytest.raises(ValidationError, match="campaign state totals"):
        SpatialResponseMetrics.model_validate(document)


def test_response_metrics_refuse_subnormal_tampering_of_exact_zero_total() -> None:
    result = _response_metrics(initial_sentiment=0.0, phone_end_minute=1)
    document = result.model_dump(mode="python")
    receipt = document["overall"]["brand_sentiment"]
    receipt["initial_total"] = math.ulp(0.0)
    receipt["change_total"] = receipt["final_total"] - receipt["initial_total"]
    receipt["initial_mean"] = receipt["initial_total"] / receipt["denominator"]
    receipt["mean_change"] = receipt["change_total"] / receipt["denominator"]

    with pytest.raises(ValidationError, match="campaign state totals"):
        SpatialResponseMetrics.model_validate(document)


def _number_leaves(
    value: object,
    *,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], float]]:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key != "schema_version":
                yield from _number_leaves(item, path=(*path, str(key)))
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            yield from _number_leaves(item, path=(*path, str(index)))
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        yield path, float(value)


def _delta_values(comparison: Any) -> dict[tuple[str, ...], float]:
    return dict(
        _number_leaves(
            {
                "overall": comparison.overall,
                "channels": comparison.channels,
                "campaigns": comparison.campaigns,
            }
        )
    )


def test_same_response_metrics_are_an_exact_aa_zero() -> None:
    from adlife.core.experiments.spatial_response_comparison import (
        compare_spatial_response_metrics,
    )

    metrics = _response_metrics()

    result = compare_spatial_response_metrics(
        metrics,
        metrics,
        control_run_id="same-run",
        treatment_run_id="same-run",
    )

    assert result.schema_version == 1
    assert result.model_id == "spatial-response-metrics-comparison-v1"
    assert result.claim_scope == "synthetic-response-comparison-not-causal-or-observed-effect"
    assert result.opportunity_classification == "matched-opportunity-structure"
    assert result.response_assumption_classification == "matched-response-assumptions"
    assert result.control == result.treatment == metrics
    deltas = _delta_values(result)
    assert deltas
    assert set(deltas.values()) == {0.0}


def test_swapping_response_metric_arms_exactly_negates_every_scalar_delta() -> None:
    from adlife.core.experiments.spatial_response_comparison import (
        compare_spatial_response_metrics,
    )

    control = _response_metrics()
    treatment = _response_metrics(initial_sentiment=0.25)

    forward = compare_spatial_response_metrics(
        control,
        treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )
    reverse = compare_spatial_response_metrics(
        treatment,
        control,
        control_run_id="treatment",
        treatment_run_id="control",
    )

    forward_deltas = _delta_values(forward)
    reverse_deltas = _delta_values(reverse)
    assert any(value != 0.0 for value in forward_deltas.values())
    assert reverse_deltas.keys() == forward_deltas.keys()
    assert reverse_deltas == {path: -value for path, value in forward_deltas.items()}


def test_response_comparison_refuses_a_different_campaign_set() -> None:
    from adlife.core.experiments.spatial_response_comparison import (
        SpatialResponseComparisonError,
        compare_spatial_response_metrics,
    )

    control = _response_metrics()
    treatment = _response_metrics(phone_campaign_id="alternate-campaign")

    with pytest.raises(SpatialResponseComparisonError, match="campaign"):
        compare_spatial_response_metrics(
            control,
            treatment,
            control_run_id="control",
            treatment_run_id="treatment",
        )


def test_opportunity_and_response_assumption_classifications_are_independent() -> None:
    from adlife.core.experiments.spatial_response_comparison import (
        compare_spatial_response_metrics,
    )

    control = _response_metrics()
    changed_opportunities = _response_metrics(phone_end_minute=1)
    changed_assumptions = _response_metrics(phone_cap=3)

    opportunity_only = compare_spatial_response_metrics(
        control,
        changed_opportunities,
        control_run_id="control",
        treatment_run_id="changed-opportunities",
    )
    assumptions_only = compare_spatial_response_metrics(
        control,
        changed_assumptions,
        control_run_id="control",
        treatment_run_id="changed-assumptions",
    )

    assert opportunity_only.opportunity_classification == "opportunity-confounded"
    assert opportunity_only.response_assumption_classification == ("matched-response-assumptions")
    assert assumptions_only.opportunity_classification == "matched-opportunity-structure"
    assert assumptions_only.response_assumption_classification == ("response-assumption-confounded")
