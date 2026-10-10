from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_metrics import (
    SpatialMetricReceipt,
    SpatialMetrics,
    derive_spatial_metrics,
    spatial_opportunity_structure_sha256,
)
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
)
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)

AGENTS_SHA256 = "a" * 64
TRACE_SHA256 = "b" * 64


def _mixed_evidence():
    pack = load_pack(pack_data())
    opportunities = _evaluate(
        _mobility(pack),
        _scenario(
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
        ),
    )
    return opportunities, evaluate_spatial_attention(opportunities, seed=42)


def _derive(opportunities=None, attention=None, *, agent_ids=("person-001", "person-002")):
    if opportunities is None or attention is None:
        opportunities, attention = _mixed_evidence()
    return derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=agent_ids,
        agents_sha256=AGENTS_SHA256,
        trace_sha256=TRACE_SHA256,
        days=1,
    )


def _receipt(
    name: str,
    numerator: int,
    denominator: int,
    value: float,
    event_types: tuple[str, ...],
    artifacts: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": name,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "source_event_types": event_types,
        "source_artifacts": artifacts,
    }


def _expected_series(
    channel: str,
    *,
    opportunities: int,
    impressions: int,
    noticed: int,
    opportunity_agents: int,
    impression_agents: int,
    noticed_agents: int,
) -> dict[str, object]:
    opportunity_artifact = ("outputs/spatial-opportunities.jsonl",)
    attention_artifact = ("outputs/spatial-attention.jsonl",)
    return {
        "schema_version": 1,
        "channel": channel,
        "opportunity_count": _receipt(
            "opportunity_count",
            opportunities,
            1,
            float(opportunities),
            ("spatial.opportunity",),
            opportunity_artifact,
        ),
        "impression_count": _receipt(
            "impression_count",
            impressions,
            1,
            float(impressions),
            ("spatial.impression",),
            attention_artifact,
        ),
        "noticed_count": _receipt(
            "noticed_count",
            noticed,
            1,
            float(noticed),
            ("spatial.noticed",),
            attention_artifact,
        ),
        "opportunity_reach": _receipt(
            "opportunity_reach",
            opportunity_agents,
            2,
            opportunity_agents / 2,
            ("spatial.opportunity",),
            opportunity_artifact,
        ),
        "impression_reach": _receipt(
            "impression_reach",
            impression_agents,
            2,
            impression_agents / 2,
            ("spatial.impression",),
            attention_artifact,
        ),
        "noticed_reach": _receipt(
            "noticed_reach",
            noticed_agents,
            2,
            noticed_agents / 2,
            ("spatial.noticed",),
            attention_artifact,
        ),
        "impression_frequency": _receipt(
            "impression_frequency",
            impressions,
            impression_agents,
            impressions / impression_agents if impression_agents else 0.0,
            ("spatial.impression",),
            attention_artifact,
        ),
        "notice_rate": _receipt(
            "notice_rate",
            noticed,
            impressions,
            noticed / impressions if impressions else 0.0,
            ("spatial.impression", "spatial.noticed"),
            attention_artifact,
        ),
    }


def test_spatial_metrics_golden_contract_has_exact_values_and_receipts() -> None:
    opportunities, attention = _mixed_evidence()

    result = _derive(opportunities, attention)

    assert result.model_dump() == {
        "schema_version": 1,
        "model_id": "spatial-metrics-v1",
        "claim_scope": "synthetic-metrics-not-observed-outcomes",
        "source_run_schema_version": 5,
        "opportunity_model_id": "spatial-opportunity-v1",
        "attention_model_id": "spatial-attention-v1",
        "scenario_sha256": opportunities.scenario_sha256,
        "city_sha256": opportunities.city_sha256,
        "agents_sha256": AGENTS_SHA256,
        "trace_sha256": TRACE_SHA256,
        "opportunity_structure_sha256": (
            "e3633970f1f6e05061fc083b511eaf4a19e56545d222413a901ef3f00a2bfdf0"
        ),
        "seed": 42,
        "population_size": 2,
        "days": 1,
        "overall": _expected_series(
            "overall",
            opportunities=3,
            impressions=3,
            noticed=1,
            opportunity_agents=1,
            impression_agents=1,
            noticed_agents=1,
        ),
        "channels": (
            _expected_series(
                "roadside",
                opportunities=1,
                impressions=1,
                noticed=1,
                opportunity_agents=1,
                impression_agents=1,
                noticed_agents=1,
            ),
            _expected_series(
                "mobile",
                opportunities=2,
                impressions=2,
                noticed=0,
                opportunity_agents=1,
                impression_agents=1,
                noticed_agents=0,
            ),
        ),
    }
    with pytest.raises(ValidationError, match="frozen"):
        result.__setattr__("days", 2)


@pytest.mark.parametrize("version", [5, 6, 7])
def test_spatial_metrics_records_exact_supported_source_run_schema(version: int) -> None:
    opportunities, attention = _mixed_evidence()

    result = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=("person-001", "person-002"),
        agents_sha256=AGENTS_SHA256,
        trace_sha256=TRACE_SHA256,
        days=1,
        source_run_schema_version=version,
    )

    assert result.source_run_schema_version == version


@pytest.mark.parametrize("version", [True, 5.0, 6.0, "6", 4, 8])
def test_spatial_metrics_refuses_noninteger_or_unsupported_source_schema(
    version: object,
) -> None:
    document = _derive().model_dump(mode="python")
    document["source_run_schema_version"] = version

    with pytest.raises(ValidationError, match="source run schema version"):
        SpatialMetrics.model_validate(document)


def test_structure_hash_is_exact_and_excludes_campaign_identity() -> None:
    opportunities, _ = _mixed_evidence()
    expected_projection = {
        "model_id": "spatial-opportunity-structure-v1",
        "opportunities": [
            {
                "placement_id": "phone-feed",
                "channel": "mobile-feed",
                "agent_id": "person-001",
                "day_index": 0,
                "model_minute": 0,
                "millisecond_within_minute": 0,
                "activity": "home",
                "eligibility_draw": 0.8615317419496036,
                "opportunity_probability_per_minute": 1.0,
            },
            {
                "placement_id": "phone-feed",
                "channel": "mobile-feed",
                "agent_id": "person-001",
                "day_index": 0,
                "model_minute": 1,
                "millisecond_within_minute": 0,
                "activity": "home",
                "eligibility_draw": 0.7726503427850848,
                "opportunity_probability_per_minute": 1.0,
            },
            {
                "placement_id": "billboard-ab",
                "channel": "roadside-billboard",
                "agent_id": "person-001",
                "day_index": 0,
                "model_minute": 481,
                "millisecond_within_minute": 51_195,
                "road_id": "ab",
                "travel_direction": "forward",
                "road_fraction": 0.5,
                "side": "right",
                "minimum_distance_meters": 0.0,
                "approach_distance_meters": 100.0,
                "view_angle_degrees": 0.0,
            },
        ],
    }
    expected = sha256(canonical_json(expected_projection).encode("utf-8")).hexdigest()

    assert expected == "e3633970f1f6e05061fc083b511eaf4a19e56545d222413a901ef3f00a2bfdf0"
    assert spatial_opportunity_structure_sha256(opportunities) == expected


def test_empty_metrics_keep_all_zero_denominators_finite() -> None:
    pack = load_pack(pack_data())
    opportunities = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [_phone(windows=[{"start_minute": 0, "end_minute": 1}], probability=0.0)],
        ),
    )
    attention = evaluate_spatial_attention(opportunities, seed=42)

    result = _derive(opportunities, attention, agent_ids=("person-001",))

    assert (
        result.opportunity_structure_sha256
        == sha256(
            canonical_json(
                {"model_id": "spatial-opportunity-structure-v1", "opportunities": []}
            ).encode("utf-8")
        ).hexdigest()
    )
    for series in (result.overall, *result.channels):
        assert series.opportunity_count.value == 0.0
        assert series.impression_count.value == 0.0
        assert series.noticed_count.value == 0.0
        assert series.opportunity_reach.value == 0.0
        assert series.impression_reach.value == 0.0
        assert series.noticed_reach.value == 0.0
        assert series.impression_frequency.denominator == 0
        assert series.impression_frequency.value == 0.0
        assert series.notice_rate.denominator == 0
        assert series.notice_rate.value == 0.0


@pytest.mark.parametrize(
    ("agent_ids", "message"),
    [
        (("person-001", "person-001"), "duplicate"),
        (("person-002",), "absent from the declared population"),
        (("agent-001",), "agent ID"),
        ((), "at least one"),
    ],
)
def test_population_contract_rejects_duplicates_unknowns_and_invalid_ids(
    agent_ids: tuple[str, ...], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _derive(agent_ids=agent_ids)


def test_projection_revalidates_mismatched_and_tampered_evidence() -> None:
    opportunities, attention = _mixed_evidence()
    mismatched = SpatialAttentionEvaluation.model_construct(
        **(attention.model_dump(mode="python") | {"scenario_sha256": "f" * 64})
    )
    with pytest.raises(ValidationError, match="evidence does not match"):
        _derive(opportunities, mismatched)

    events = list(attention.model_dump(mode="python")["events"])
    events[-1] = events[-1] | {"caused_by": "0" * 64}
    tampered = SpatialAttentionEvaluation.model_construct(
        **(attention.model_dump(mode="python") | {"events": tuple(events)})
    )
    with pytest.raises(ValidationError, match="causal"):
        _derive(opportunities, tampered)


def test_projection_refuses_inconsistent_source_pair_and_duration() -> None:
    opportunities, attention = _mixed_evidence()
    with pytest.raises(ValueError, match="days"):
        derive_spatial_metrics(
            opportunities,
            attention,
            agent_ids=("person-001",),
            agents_sha256=AGENTS_SHA256,
            trace_sha256=TRACE_SHA256,
            days=0,
        )

    pack = load_pack(pack_data())
    different_opportunities = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [_phone(windows=[{"start_minute": 1, "end_minute": 3}], cap=2)],
        ),
    )
    with pytest.raises(ValueError, match="same scenario and city"):
        _derive(different_opportunities, attention)


def test_receipt_refuses_inconsistent_or_non_finite_values() -> None:
    valid = {
        "name": "notice_rate",
        "numerator": 1,
        "denominator": 3,
        "value": 1 / 3,
        "source_event_types": ("spatial.impression", "spatial.noticed"),
        "source_artifacts": ("outputs/spatial-attention.jsonl",),
    }
    with pytest.raises(ValidationError, match="quotient"):
        SpatialMetricReceipt.model_validate(valid | {"value": 0.5})
    with pytest.raises(ValidationError, match="finite"):
        SpatialMetricReceipt.model_validate(valid | {"value": float("nan")})
    with pytest.raises(ValidationError, match="extra"):
        SpatialMetricReceipt.model_validate(valid | {"purchase_probability": 1.0})


def test_equal_aggregate_counts_with_different_times_have_different_structure_hashes() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    first = _evaluate(
        mobility,
        _scenario(
            pack,
            [_phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2)],
        ),
    )
    second = _evaluate(
        mobility,
        _scenario(
            pack,
            [_phone(windows=[{"start_minute": 1, "end_minute": 3}], cap=2)],
        ),
    )

    assert first.counts.opportunity_count == second.counts.opportunity_count == 2
    assert spatial_opportunity_structure_sha256(first) != spatial_opportunity_structure_sha256(
        second
    )
