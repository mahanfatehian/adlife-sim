from __future__ import annotations

import pytest
from pydantic import ValidationError

from adlife.core.experiments.spatial_comparison import (
    SpatialComparisonError,
    SpatialMetricDeltaSeries,
    compare_spatial_metrics,
)
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)
from tests.unit.experiments.test_spatial_metrics import _derive, _mixed_evidence

METRIC_NAMES = (
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "opportunity_reach",
    "impression_reach",
    "noticed_reach",
    "impression_frequency",
    "notice_rate",
)


def _variant_metrics(*, start: int = 0, end: int = 3, agent_ids=("person-001", "person-002")):
    pack = load_pack(pack_data())
    opportunities = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [
                _billboard(),
                _phone(
                    campaign_id="phone-campaign",
                    windows=[{"start_minute": start, "end_minute": end}],
                    probability=1.0,
                    cap=end - start,
                ),
            ],
        ),
    )
    attention = evaluate_spatial_attention(opportunities, seed=42)
    return derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=agent_ids,
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=1,
    )


def _values(series) -> dict[str, float]:
    return {name: getattr(series, name) for name in METRIC_NAMES}


def test_same_run_comparison_is_an_exact_aa_zero() -> None:
    metrics = _derive()

    result = compare_spatial_metrics(
        metrics,
        metrics,
        control_run_id="same-run",
        treatment_run_id="same-run",
    )

    assert result.schema_version == 1
    assert result.model_id == "spatial-metrics-comparison-v1"
    assert result.claim_scope == "synthetic-comparison-not-causal-or-observed-effect"
    assert result.classification == "matched-opportunity-structure"
    assert result.interpretation == (
        "Normalized model opportunity structure is identical; deltas remain synthetic "
        "and non-causal."
    )
    assert result.control == result.treatment == metrics
    assert result.control_run_id == result.treatment_run_id == "same-run"
    assert result.control_scenario_sha256 == result.treatment_scenario_sha256
    assert result.control_opportunity_structure_sha256 == (
        result.treatment_opportunity_structure_sha256
    )
    for series in (result.overall, *result.channels):
        assert set(_values(series).values()) == {0.0}


def test_treatment_minus_control_deltas_are_exact_and_confounded() -> None:
    control = _derive()
    treatment = _variant_metrics()

    result = compare_spatial_metrics(
        control,
        treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )

    assert result.classification == "opportunity-confounded"
    assert result.interpretation == (
        "Opportunity volume, timing, channel, agent, placement, or physical/model evidence "
        "differs; deltas are opportunity-confounded."
    )
    assert _values(result.overall) == {
        "opportunity_count": 1.0,
        "impression_count": 1.0,
        "noticed_count": 0.0,
        "opportunity_reach": 0.0,
        "impression_reach": 0.0,
        "noticed_reach": 0.0,
        "impression_frequency": 1.0,
        "notice_rate": -0.08333333333333331,
    }
    assert _values(result.channels[0]) == dict.fromkeys(METRIC_NAMES, 0.0)
    assert _values(result.channels[1]) == {
        "opportunity_count": 1.0,
        "impression_count": 1.0,
        "noticed_count": 0.0,
        "opportunity_reach": 0.0,
        "impression_reach": 0.0,
        "noticed_reach": 0.0,
        "impression_frequency": 1.0,
        "notice_rate": 0.0,
    }
    assert result.channels[0].channel == "roadside"
    assert result.channels[1].channel == "mobile"
    assert result.control == control
    assert result.treatment == treatment


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("city_sha256", "c" * 64, "city fingerprint"),
        ("trace_sha256", "c" * 64, "mobility trace"),
        ("agents_sha256", "c" * 64, "agent assignment"),
        ("seed", 43, "seed"),
        ("days", 2, "duration"),
    ],
)
def test_comparison_refuses_unmatched_provenance(
    field: str, value: str | int, message: str
) -> None:
    control = _derive()
    treatment = control.model_copy(update={field: value})

    with pytest.raises(SpatialComparisonError, match=message):
        compare_spatial_metrics(
            control,
            treatment,
            control_run_id="control",
            treatment_run_id="treatment",
        )


def test_comparison_refuses_population_mismatch() -> None:
    opportunities, attention = _mixed_evidence()
    control = _derive(opportunities, attention)
    treatment = _derive(opportunities, attention, agent_ids=("person-001",))

    with pytest.raises(SpatialComparisonError, match="population"):
        compare_spatial_metrics(
            control,
            treatment,
            control_run_id="control",
            treatment_run_id="treatment",
        )


def test_equal_counts_with_different_timing_remain_opportunity_confounded() -> None:
    control = _variant_metrics(start=0, end=2)
    treatment = _variant_metrics(start=1, end=3)

    assert control.overall.opportunity_count.value == treatment.overall.opportunity_count.value
    result = compare_spatial_metrics(
        control,
        treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )

    assert result.classification == "opportunity-confounded"
    assert result.control_opportunity_structure_sha256 != (
        result.treatment_opportunity_structure_sha256
    )


def test_comparison_models_reject_extra_nonfinite_and_tampered_deltas() -> None:
    values = {
        "channel": "overall",
        **dict.fromkeys(METRIC_NAMES, 0.0),
    }
    with pytest.raises(ValidationError, match="finite"):
        SpatialMetricDeltaSeries.model_validate(values | {"notice_rate": float("nan")})
    with pytest.raises(ValidationError, match="extra"):
        SpatialMetricDeltaSeries.model_validate(values | {"causal_effect": 1.0})

    metrics = _derive()
    result = compare_spatial_metrics(
        metrics,
        metrics,
        control_run_id="same-run",
        treatment_run_id="same-run",
    )
    with pytest.raises(ValidationError, match="deltas"):
        result.model_copy(
            update={
                "overall": result.overall.model_copy(update={"notice_rate": 0.1}),
            }
        )


def test_comparison_refuses_invalid_run_identifiers() -> None:
    metrics = _derive()
    with pytest.raises(ValidationError, match="control_run_id"):
        compare_spatial_metrics(
            metrics,
            metrics,
            control_run_id="../escape",
            treatment_run_id="safe",
        )
