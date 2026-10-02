from __future__ import annotations

import importlib
import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)


def _attention_module() -> Any:
    return importlib.import_module("adlife.core.simulation.spatial_attention")


def _evaluate_attention(opportunities: object, *, seed: int = 42) -> Any:
    evaluator = getattr(_attention_module(), "evaluate_spatial_attention", None)
    assert evaluator is not None, "spatial attention evaluator is not implemented"
    return evaluator(opportunities, seed=seed)


def _mixed_opportunities() -> object:
    pack = load_pack(pack_data())
    return _evaluate(
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


def test_attention_events_have_exact_keyed_draws_causes_and_order() -> None:
    opportunities = _mixed_opportunities()

    result = _evaluate_attention(opportunities)

    assert result.schema_version == 1
    assert result.model_id == "spatial-attention-v1"
    assert result.claim_scope == "synthetic-attention-not-observed-behavior"
    assert result.seed == 42
    assert result.scenario_sha256 == opportunities.scenario_sha256
    assert result.city_sha256 == opportunities.city_sha256
    assert result.notice_probability == 0.5
    assert result.counts.model_dump() == {
        "schema_version": 1,
        "opportunity_count": 3,
        "impression_count": 3,
        "noticed_count": 1,
        "roadside_impression_count": 1,
        "roadside_noticed_count": 1,
        "phone_impression_count": 2,
        "phone_noticed_count": 0,
    }
    assert [event.event_type for event in result.events] == [
        "spatial.impression",
        "spatial.impression",
        "spatial.impression",
        "spatial.noticed",
    ]
    assert [event.notice_draw for event in result.events] == [
        0.5000315267597767,
        0.7649605060930738,
        0.21973056624584208,
        0.21973056624584208,
    ]
    assert [event.event_id for event in result.events] == [
        "6f7145aaf1473fb5c13840e2423a13a462bae332fccf85739a0771a06e19088a",
        "88ca5461c3f71334a3a7e1390ee0228c14406e7496813946181454417f662020",
        "a09aeb6506501d1d0db99c64f8e394e764bc84d80a6ef06e9467e3a4c1f7d674",
        "e72a40ab62fb579cb677736cea27a13ae762445ea068c13beff62d8090dfd5c7",
    ]
    for opportunity, impression in zip(opportunities.opportunities, result.events[:3], strict=True):
        assert impression.event_type == "spatial.impression"
        assert impression.caused_by == opportunity.opportunity_id
        assert impression.opportunity_id == opportunity.opportunity_id
        assert impression.noticed is (impression.notice_draw < 0.5)
    notice = result.events[-1]
    assert notice.caused_by == result.events[-2].event_id
    assert notice.opportunity_id == result.events[-2].opportunity_id
    assert notice.notice_draw == result.events[-2].notice_draw
    assert _evaluate_attention(opportunities) == result


def test_attention_draw_excludes_campaign_identity_copy_and_scenario_hash() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    baseline_scenario = _scenario(
        pack,
        [_phone(windows=[{"start_minute": 0, "end_minute": 2}], probability=1.0, cap=2)],
    )
    changed = deepcopy(baseline_scenario.model_dump(mode="json"))
    changed["scenario_id"] = "changed-scenario"
    changed["name"] = "Different fictional scenario copy"
    changed_campaign = changed["campaigns"][0]
    changed_campaign["campaign_id"] = "changed-campaign"
    changed_campaign["name"] = "Completely different fictional creative"
    changed_campaign["creative_sha256"] = "f" * 64
    changed["placements"][0]["campaign_id"] = "changed-campaign"
    changed_scenario = parse_spatial_campaign_scenario_json(json.dumps(changed))

    baseline_opportunities = _evaluate(mobility, baseline_scenario)
    changed_opportunities = _evaluate(mobility, changed_scenario)
    baseline = _evaluate_attention(baseline_opportunities)
    variant = _evaluate_attention(changed_opportunities)

    assert baseline.scenario_sha256 != variant.scenario_sha256
    assert [item.opportunity_id for item in baseline_opportunities.opportunities] != [
        item.opportunity_id for item in changed_opportunities.opportunities
    ]
    assert [
        (event.notice_draw, event.noticed)
        for event in baseline.events
        if event.event_type == "spatial.impression"
    ] == [
        (event.notice_draw, event.noticed)
        for event in variant.events
        if event.event_type == "spatial.impression"
    ]


def test_attention_contract_rejects_invalid_seed_events_and_causal_chains() -> None:
    module = _attention_module()
    opportunities = _mixed_opportunities()
    result = _evaluate_attention(opportunities)
    impression = result.events[0]

    with pytest.raises(TypeError, match="integer"):
        _evaluate_attention(opportunities, seed=True)
    with pytest.raises(ValueError, match="between"):
        _evaluate_attention(opportunities, seed=-1)
    with pytest.raises(ValidationError, match="extra"):
        type(impression).model_validate(impression.model_dump() | {"provider": "remote"})
    with pytest.raises(ValidationError, match="finite"):
        type(impression).model_validate(impression.model_dump() | {"notice_draw": float("nan")})
    with pytest.raises(ValidationError, match="event_type"):
        type(impression).model_validate(
            impression.model_dump() | {"event_type": "spatial.purchase"}
        )
    with pytest.raises(ValidationError, match="causal"):
        type(result).model_validate(
            result.model_dump()
            | {
                "events": (
                    *result.events[:-1],
                    result.events[-1].model_copy(update={"caused_by": "0" * 64}),
                )
            }
        )
    with pytest.raises(ValidationError, match="records do not match"):
        type(result).model_validate(result.model_dump() | {"events": result.events[:-1]})
    with pytest.raises(ValidationError, match="frozen"):
        impression.__setattr__("noticed", True)

    counts = result.counts.model_dump()
    counts.update(
        opportunity_count=520_801,
        impression_count=520_801,
        noticed_count=520_800,
        roadside_impression_count=0,
        roadside_noticed_count=0,
        phone_impression_count=520_801,
        phone_noticed_count=520_800,
    )
    with pytest.raises(ValidationError, match="opportunity_count"):
        module.SpatialAttentionCounts.model_validate(counts)


def test_attention_artifact_lines_and_summary_bind_exact_canonical_bytes() -> None:
    module = _attention_module()
    result = _evaluate_attention(_mixed_opportunities())

    lines = tuple(module.spatial_attention_lines(result))
    expected = tuple((canonical_json(event) + "\n").encode("utf-8") for event in result.events)
    summary = module.summarize_spatial_attention_artifact(result)

    assert lines == expected
    assert summary.model_dump() == {
        "schema_version": 1,
        "model_id": "spatial-attention-artifact-v1",
        "claim_scope": "synthetic-attention-not-observed-behavior",
        "attention_model_id": "spatial-attention-v1",
        "scenario_sha256": result.scenario_sha256,
        "city_sha256": result.city_sha256,
        "notice_probability": 0.5,
        "stream_sha256": sha256(b"".join(expected)).hexdigest(),
        "stream_bytes": sum(map(len, expected)),
        "counts": result.counts.model_dump(),
    }
    with pytest.raises(ValidationError, match="frozen"):
        summary.__setattr__("stream_bytes", 0)


def test_empty_attention_artifact_uses_zero_byte_hash() -> None:
    module = _attention_module()
    pack = load_pack(pack_data())
    opportunities = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [_phone(windows=[{"start_minute": 0, "end_minute": 1}], probability=0.0)],
        ),
    )

    result = _evaluate_attention(opportunities)
    summary = module.summarize_spatial_attention_artifact(result)

    assert result.events == ()
    assert result.counts.model_dump() == {
        "schema_version": 1,
        "opportunity_count": 0,
        "impression_count": 0,
        "noticed_count": 0,
        "roadside_impression_count": 0,
        "roadside_noticed_count": 0,
        "phone_impression_count": 0,
        "phone_noticed_count": 0,
    }
    assert tuple(module.spatial_attention_lines(result)) == ()
    assert summary.stream_bytes == 0
    assert summary.stream_sha256 == sha256(b"").hexdigest()


def test_attention_artifact_enforces_stream_byte_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _attention_module()
    result = _evaluate_attention(_mixed_opportunities())

    assert module.MAX_SPATIAL_ATTENTION_EVENTS == 1_041_600
    assert module.MAX_SPATIAL_ATTENTION_STREAM_BYTES == 1_073_741_824
    monkeypatch.setattr(module, "MAX_SPATIAL_ATTENTION_STREAM_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        module.summarize_spatial_attention_artifact(result)
