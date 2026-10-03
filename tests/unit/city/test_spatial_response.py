from __future__ import annotations

import importlib
import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_response import (
    SpatialCampaignResponseInput,
    SpatialResponseInitialState,
    SpatialResponseInput,
    SpatialResponseProfile,
    SpatialResponseTraits,
    parse_spatial_response_input_json,
)
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionCounts,
    SpatialAttentionEvaluation,
    SpatialNotice,
    spatial_notice_draw,
)
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_attention import _evaluate_attention, _mixed_opportunities
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)


def _module() -> Any:
    return importlib.import_module("adlife.core.simulation.spatial_response")


def _response_input(
    scenario: Any,
    *,
    agent_ids: tuple[str, ...] = ("person-001",),
    sentiment: float = 0.1,
    recall: float = 0.1,
    intention: float = 0.2,
    traits: SpatialResponseTraits | None = None,
) -> SpatialResponseInput:
    response_traits = traits or SpatialResponseTraits(
        price_sensitivity=0.8,
        novelty_seeking=0.3,
        advertising_skepticism=0.6,
        mobile_recall_encoding=0.7,
        roadside_recall_encoding=0.4,
        impulsivity=0.2,
    )
    return SpatialResponseInput(
        city_sha256=scenario.city_sha256,
        scenario_sha256=scenario.fingerprint,
        profiles=tuple(
            SpatialResponseProfile(
                agent_id=agent_id,
                fictional=True,
                interests=frozenset({"coffee", "outdoors"}),
                traits=response_traits,
            )
            for agent_id in agent_ids
        ),
        campaigns=tuple(
            SpatialCampaignResponseInput(
                campaign_id=campaign.campaign_id,
                creative_sha256=campaign.creative_sha256,
                target_interests=frozenset({"coffee", "work"}),
                relative_price=1.25,
            )
            for campaign in scenario.campaigns
        ),
        initial_states=tuple(
            SpatialResponseInitialState(
                agent_id=agent_id,
                campaign_id=campaign.campaign_id,
                brand_sentiment=sentiment,
                recall_strength=recall,
                purchase_intention=intention,
            )
            for agent_id in agent_ids
            for campaign in scenario.campaigns
        ),
    )


def _evaluate_response(
    response_input: SpatialResponseInput,
    scenario: Any,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    *,
    agent_ids: tuple[str, ...] = ("person-001",),
) -> Any:
    return _module().evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agent_ids,
    )


def _billboard_evidence(*, attention_seed: int) -> tuple[Any, Any, Any, Any]:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_billboard()])
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=attention_seed)
    return scenario, opportunities, attention, _response_input(scenario)


def _state_hash(state: Any) -> str:
    return sha256(canonical_json(state).encode("utf-8")).hexdigest()


def _attention_event_id(*, event_type: str, caused_by: str) -> str:
    return sha256(
        canonical_json(
            {
                "caused_by": caused_by,
                "event_type": event_type,
                "model_id": "spatial-attention-v1",
            }
        ).encode("utf-8")
    ).hexdigest()


def _opportunity_id(
    *,
    scenario_sha256: str,
    city_sha256: str,
    campaign_id: str,
    placement_id: str,
    agent_id: str,
    channel: str,
    model_minute: int,
    millisecond_within_minute: int,
) -> str:
    return sha256(
        canonical_json(
            {
                "model_id": "spatial-opportunity-v1",
                "scenario_sha256": scenario_sha256,
                "city_sha256": city_sha256,
                "campaign_id": campaign_id,
                "placement_id": placement_id,
                "agent_id": agent_id,
                "channel": channel,
                "at_millisecond": (model_minute * 60_000 + millisecond_within_minute),
            }
        ).encode("utf-8")
    ).hexdigest()


def test_one_notice_has_exact_rule_scores_causal_ids_and_artifact_receipts() -> None:
    module = _module()
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    notice = next(event for event in attention.events if event.event_type == "spatial.noticed")

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    assert result.model_id == "spatial-response-v1"
    assert result.claim_scope == "synthetic-response-not-observed-behavior"
    assert result.response_input_sha256 == response_input.fingerprint
    assert result.attention_seed == 0
    assert result.counts.model_dump() == {
        "schema_version": 1,
        "response_count": 1,
        "state_update_count": 1,
        "roadside_response_count": 1,
        "phone_response_count": 0,
        "campaign_count": 1,
        "final_state_count": 1,
    }
    response, update = result.records
    assert response.event_type == "spatial.response"
    assert response.caused_by == notice.event_id
    assert response.opportunity_id == notice.opportunity_id
    assert response.prior_notices_today == 0
    assert response.frequency_cap_per_agent_per_day == 3
    assert response.interest_match == pytest.approx(1 / 3)
    assert response.relative_price == 1.25
    assert response.price_sensitivity == 0.8
    assert response.affordability == 0.25
    assert response.novelty_seeking == 0.3
    assert response.advertising_skepticism == 0.6
    assert response.channel_recall_encoding == 0.4
    assert response.impulsivity == 0.2
    assert response.frequency_fatigue == 0.0
    assert response.value_match == pytest.approx(0.30833333333333335)
    assert response.sentiment_delta == pytest.approx(-0.0165)
    assert response.recall_delta == pytest.approx(0.124)
    expected_response_id = sha256(
        canonical_json(
            {
                "caused_by": notice.event_id,
                "event_type": "spatial.response",
                "model_id": "spatial-response-v1",
                "response_input_sha256": response_input.fingerprint,
            }
        ).encode("utf-8")
    ).hexdigest()
    assert response.event_id == expected_response_id

    assert update.event_type == "spatial.state-updated"
    assert update.caused_by_event_ids == (response.event_id,)
    assert update.previous_state.brand_sentiment == 0.1
    assert update.previous_state.recall_strength == 0.1
    assert response.state_before_sha256 == _state_hash(update.previous_state)
    assert update.state.brand_sentiment == pytest.approx(0.0835)
    assert update.state.recall_strength == pytest.approx(0.2116)
    assert update.state.purchase_intention == pytest.approx(0.3661033333333333)
    assert update.state.response_count == 1
    assert update.state.last_response_minute == notice.model_minute
    expected_update_id = sha256(
        canonical_json(
            {
                "agent_id": "person-001",
                "campaign_id": "fictional-launch",
                "caused_by_event_ids": [response.event_id],
                "event_type": "spatial.state-updated",
                "model_id": "spatial-response-v1",
                "model_minute": notice.model_minute,
                "response_input_sha256": response_input.fingerprint,
            }
        ).encode("utf-8")
    ).hexdigest()
    assert update.event_id == expected_update_id
    assert result.final_states == (update.state,)

    lines = tuple(module.spatial_response_lines(result))
    expected_lines = tuple(
        (canonical_json(record) + "\n").encode("utf-8") for record in result.records
    )
    state_document = module.spatial_response_state_document(result)
    state_bytes = (canonical_json(state_document) + "\n").encode("utf-8")
    summary = module.summarize_spatial_response_artifact(result)
    assert lines == expected_lines
    assert state_document.states == result.final_states
    assert summary.model_dump() == {
        "schema_version": 1,
        "model_id": "spatial-response-artifact-v1",
        "claim_scope": "synthetic-response-not-observed-behavior",
        "response_model_id": "spatial-response-v1",
        "response_input_sha256": response_input.fingerprint,
        "scenario_sha256": scenario.fingerprint,
        "city_sha256": scenario.city_sha256,
        "attention_seed": 0,
        "stream_sha256": sha256(b"".join(expected_lines)).hexdigest(),
        "stream_bytes": sum(map(len, expected_lines)),
        "state_document_sha256": sha256(state_bytes).hexdigest(),
        "state_document_bytes": len(state_bytes),
        "counts": result.counts.model_dump(),
    }


def test_ignored_impression_produces_no_response_and_preserves_complete_state() -> None:
    module = _module()
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=2)
    assert attention.counts.impression_count == 1
    assert attention.counts.noticed_count == 0

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    initial = response_input.initial_states[0]
    final = result.final_states[0]
    assert result.records == ()
    assert result.counts.response_count == 0
    assert result.counts.state_update_count == 0
    assert final.model_dump() == {
        "schema_version": 1,
        "agent_id": initial.agent_id,
        "campaign_id": initial.campaign_id,
        "brand_sentiment": initial.brand_sentiment,
        "recall_strength": initial.recall_strength,
        "purchase_intention": initial.purchase_intention,
        "response_count": 0,
        "last_response_minute": None,
    }
    assert tuple(module.spatial_response_lines(result)) == ()
    assert (
        module.summarize_spatial_response_artifact(result).stream_sha256 == sha256(b"").hexdigest()
    )


def test_same_minute_notices_share_snapshot_and_commit_one_commutative_update() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(
                placement_id="a-later",
                fraction=0.5,
                longitude=0.005,
                side="left",
            ),
            _billboard(
                placement_id="z-earlier",
                fraction=0.4,
                longitude=0.004,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=5)
    response_input = _response_input(scenario)

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    responses = tuple(
        record for record in result.records if record.event_type == "spatial.response"
    )
    updates = tuple(
        record for record in result.records if record.event_type == "spatial.state-updated"
    )
    assert len(responses) == 2
    assert len(updates) == 1
    assert [record.placement_id for record in responses] == ["z-earlier", "a-later"]
    assert {record.state_before_sha256 for record in responses} == {
        _state_hash(updates[0].previous_state)
    }
    assert [record.prior_notices_today for record in responses] == [0, 0]
    assert updates[0].caused_by_event_ids == tuple(record.event_id for record in responses)
    assert updates[0].state.brand_sentiment == pytest.approx(0.067)
    assert updates[0].state.recall_strength == pytest.approx(1 - (1 - 0.1) * (1 - 0.124) ** 2)
    assert updates[0].state.response_count == 2


def test_later_minute_reads_committed_state_and_prior_notice_fatigue() -> None:
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

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    responses = tuple(
        record for record in result.records if record.event_type == "spatial.response"
    )
    updates = tuple(
        record for record in result.records if record.event_type == "spatial.state-updated"
    )
    assert [record.model_minute for record in responses] == [0, 1]
    assert [record.prior_notices_today for record in responses] == [0, 1]
    assert [record.frequency_fatigue for record in responses] == [0.0, 0.5]
    assert [record.sentiment_delta for record in responses] == pytest.approx([-0.0165, -0.0465])
    assert [record.recall_delta for record in responses] == pytest.approx([0.19, 0.15])
    assert len(updates) == 2
    assert responses[0].state_before_sha256 == _state_hash(updates[0].previous_state)
    assert responses[1].state_before_sha256 == _state_hash(updates[0].state)
    assert updates[1].previous_state == updates[0].state
    assert updates[1].state.response_count == 2


def test_campaign_state_isolated_when_only_other_campaign_is_noticed() -> None:
    opportunities = _mixed_opportunities()
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
    attention = _evaluate_attention(opportunities, seed=42)
    response_input = _response_input(scenario)

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    states = {state.campaign_id: state for state in result.final_states}
    assert result.counts.response_count == 1
    assert states["fictional-launch"].response_count == 1
    assert states["phone-campaign"].model_dump(exclude={"schema_version"}) == {
        "agent_id": "person-001",
        "campaign_id": "phone-campaign",
        "brand_sentiment": 0.1,
        "recall_strength": 0.1,
        "purchase_intention": 0.2,
        "response_count": 0,
        "last_response_minute": None,
    }


def test_response_contract_refuses_tampering_and_forbidden_state_controls() -> None:
    module = _module()
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    result = _evaluate_response(response_input, scenario, opportunities, attention)
    response, update = result.records

    for field in (
        "provider",
        "purchase_probability",
        "purchase_event",
        "budget_delta",
        "movement",
        "purchase_reason",
    ):
        with pytest.raises(ValidationError, match="extra"):
            type(response).model_validate(response.model_dump() | {field: "forbidden"})
    forged_cause = "0" * 64
    forged_response_id = sha256(
        canonical_json(
            {
                "caused_by": forged_cause,
                "event_type": "spatial.response",
                "model_id": "spatial-response-v1",
                "response_input_sha256": response_input.fingerprint,
            }
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValidationError, match="notice causal identity"):
        type(response).model_validate(
            response.model_dump()
            | {
                "caused_by": forged_cause,
                "event_id": forged_response_id,
            }
        )
    with pytest.raises(ValidationError, match="opportunity causal identity"):
        type(response).model_validate(response.model_dump() | {"opportunity_id": "0" * 64})
    invalid_cause = "not-a-sha256"
    invalid_update_id = sha256(
        canonical_json(
            {
                "agent_id": update.agent_id,
                "campaign_id": update.campaign_id,
                "caused_by_event_ids": [invalid_cause],
                "event_type": "spatial.state-updated",
                "model_id": "spatial-response-v1",
                "model_minute": update.model_minute,
                "response_input_sha256": response_input.fingerprint,
            }
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValidationError, match="caused_by_event_ids"):
        type(update).model_validate(
            update.model_dump()
            | {
                "event_id": invalid_update_id,
                "caused_by_event_ids": (invalid_cause,),
            }
        )
    with pytest.raises(ValidationError, match="state transition"):
        type(result).model_validate(
            result.model_dump()
            | {
                "records": (
                    response,
                    update.model_copy(
                        update={
                            "state": update.state.model_copy(update={"purchase_intention": 0.99})
                        }
                    ),
                )
            }
        )
    with pytest.raises(ValueError, match="agent"):
        _evaluate_response(
            response_input,
            scenario,
            opportunities,
            attention,
            agent_ids=("person-000",),
        )
    monkey_scenario = scenario.model_copy(update={"name": "Different fictional copy"})
    with pytest.raises(ValueError, match="scenario"):
        _evaluate_response(
            response_input,
            monkey_scenario,
            opportunities,
            attention,
        )
    assert module.MAX_SPATIAL_RESPONSE_RECORDS == 1_041_600
    assert module.MAX_SPATIAL_RESPONSE_STREAM_BYTES == 2_147_483_648
    assert module.MAX_SPATIAL_RESPONSE_SUMMARY_BYTES == 65_536


def test_response_artifact_enforces_stream_and_state_document_byte_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    result = _evaluate_response(response_input, scenario, opportunities, attention)

    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES", 1)
    with pytest.raises(ValueError, match="stream exceeds"):
        module.summarize_spatial_response_artifact(result)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES", 2_147_483_648)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STATE_BYTES", 1)
    with pytest.raises(ValueError, match="state document exceeds"):
        module.summarize_spatial_response_artifact(result)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STATE_BYTES", 4_194_304)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_SUMMARY_BYTES", 1)
    with pytest.raises(ValueError, match="summary exceeds"):
        module.summarize_spatial_response_artifact(result)


def test_response_boundaries_revalidate_bypass_constructed_models() -> None:
    module = _module()
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    result = _evaluate_response(response_input, scenario, opportunities, attention)
    response, update = result.records
    poisoned_response = type(response).model_construct(
        **(response.model_dump() | {"sentiment_delta": float("nan")})
    )
    poisoned_evaluation = type(result).model_construct(
        **(
            result.model_dump()
            | {
                "records": (poisoned_response, update),
                "final_states": result.final_states,
            }
        )
    )

    with pytest.raises(ValueError, match="spatial response evaluation"):
        tuple(module.spatial_response_lines(poisoned_evaluation))
    with pytest.raises(ValueError, match="spatial response evaluation"):
        module.spatial_response_state_document(poisoned_evaluation)
    with pytest.raises(ValueError, match="spatial response evaluation"):
        module.summarize_spatial_response_artifact(poisoned_evaluation)

    stream = module.spatial_response_lines(result)
    next(stream)
    valid_state = result.final_states[0]
    equality_poisoned_state = type(valid_state).model_construct(
        **(valid_state.model_dump() | {"response_count": True})
    )
    equality_poisoned_evaluation = type(result).model_construct(
        **(
            result.model_dump()
            | {
                "counts": result.counts,
                "records": result.records,
                "final_states": (equality_poisoned_state,),
            }
        )
    )
    assert equality_poisoned_evaluation == result
    try:
        with pytest.raises(ValidationError, match="integer"):
            module.spatial_response_state_document(equality_poisoned_evaluation)
        with pytest.raises(ValidationError, match="integer"):
            module.summarize_spatial_response_artifact(equality_poisoned_evaluation)
    finally:
        stream.close()

    traits = response_input.profiles[0].traits.model_construct(
        **(response_input.profiles[0].traits.model_dump() | {"price_sensitivity": float("nan")})
    )
    profile = response_input.profiles[0].model_construct(
        **(
            response_input.profiles[0].model_dump()
            | {
                "interests": response_input.profiles[0].interests,
                "traits": traits,
            }
        )
    )
    poisoned_input = type(response_input).model_construct(
        **(
            response_input.model_dump()
            | {
                "profiles": (profile,),
                "campaigns": response_input.campaigns,
                "initial_states": response_input.initial_states,
            }
        )
    )
    with pytest.raises(ValueError, match="spatial response input"):
        _evaluate_response(poisoned_input, scenario, opportunities, attention)

    reordered_attention = type(attention).model_construct(
        **(
            attention.model_dump()
            | {
                "counts": attention.counts,
                "events": tuple(reversed(attention.events)),
            }
        )
    )
    with pytest.raises(ValidationError, match="canonical order"):
        _evaluate_response(
            response_input,
            scenario,
            opportunities,
            reordered_attention,
        )


def test_evaluator_refuses_forged_notice_and_opportunity_causal_ids() -> None:
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    impression, notice = attention.events
    forged_opportunity_id = "0" * 64
    forged_impression_id = _attention_event_id(
        event_type="spatial.impression",
        caused_by=forged_opportunity_id,
    )
    forged_notice_id = _attention_event_id(
        event_type="spatial.noticed",
        caused_by=forged_impression_id,
    )
    forged_impression = impression.model_copy(
        update={
            "event_id": forged_impression_id,
            "caused_by": forged_opportunity_id,
            "opportunity_id": forged_opportunity_id,
        }
    )
    forged_notice = notice.model_copy(
        update={
            "event_id": forged_notice_id,
            "caused_by": forged_impression_id,
            "opportunity_id": forged_opportunity_id,
        }
    )
    forged_attention = type(attention).model_validate(
        attention.model_dump()
        | {
            "events": (forged_impression, forged_notice),
        }
    )

    with pytest.raises(ValueError, match="opportunities do not match"):
        _evaluate_response(
            response_input,
            scenario,
            opportunities,
            forged_attention,
        )

    forged_opportunity = type(opportunities.opportunities[0]).model_construct(
        **(opportunities.opportunities[0].model_dump() | {"opportunity_id": forged_opportunity_id})
    )
    forged_opportunities = type(opportunities).model_construct(
        **(
            opportunities.model_dump()
            | {
                "counts": opportunities.counts,
                "opportunities": (forged_opportunity,),
            }
        )
    )
    with pytest.raises(ValidationError, match="opportunity causal identity"):
        _evaluate_response(
            response_input,
            scenario,
            forged_opportunities,
            forged_attention,
        )


def test_evaluator_refuses_a_forged_passing_attention_draw() -> None:
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=2)
    impression = attention.events[0]
    changed_impression = type(impression).model_construct(
        **(
            impression.model_dump()
            | {
                "notice_draw": 0.1,
                "noticed": True,
            }
        )
    )
    notice_id = _attention_event_id(
        event_type="spatial.noticed",
        caused_by=impression.event_id,
    )
    forged_notice = SpatialNotice.model_validate(
        impression.model_dump(exclude={"noticed"})
        | {
            "event_type": "spatial.noticed",
            "event_id": notice_id,
            "caused_by": impression.event_id,
            "notice_draw": 0.1,
        }
    )
    counts = SpatialAttentionCounts(
        opportunity_count=1,
        impression_count=1,
        noticed_count=1,
        roadside_impression_count=1,
        roadside_noticed_count=1,
        phone_impression_count=0,
        phone_noticed_count=0,
    )
    forged_attention = type(attention).model_construct(
        **(
            attention.model_dump()
            | {
                "counts": counts,
                "events": (changed_impression, forged_notice),
            }
        )
    )

    with pytest.raises(ValidationError, match="keyed draw"):
        _evaluate_response(
            response_input,
            scenario,
            opportunities,
            forged_attention,
        )


def test_evaluator_refuses_opportunities_outside_scenario_placement_contract() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(
                windows=[{"start_minute": 480, "end_minute": 482}],
            )
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    original = opportunities.opportunities[0]
    forged_id = _opportunity_id(
        scenario_sha256=original.scenario_sha256,
        city_sha256=original.city_sha256,
        campaign_id=original.campaign_id,
        placement_id=original.placement_id,
        agent_id=original.agent_id,
        channel=original.channel,
        model_minute=0,
        millisecond_within_minute=12_345,
    )
    outside_window = original.model_copy(
        update={
            "opportunity_id": forged_id,
            "model_minute": 0,
            "millisecond_within_minute": 12_345,
        }
    )
    changed_opportunities = opportunities.model_copy(update={"opportunities": (outside_window,)})
    changed_attention = _evaluate_attention(changed_opportunities, seed=0)

    with pytest.raises(ValueError, match="active window"):
        _evaluate_response(
            _response_input(scenario),
            scenario,
            changed_opportunities,
            changed_attention,
        )

    wrong_road = original.model_copy(update={"road_fraction": 0.4})
    wrong_road_opportunities = opportunities.model_copy(update={"opportunities": (wrong_road,)})
    with pytest.raises(ValueError, match="roadside placement evidence"):
        _evaluate_response(
            _response_input(scenario),
            scenario,
            wrong_road_opportunities,
            _evaluate_attention(wrong_road_opportunities, seed=0),
        )

    too_far = original.model_copy(update={"approach_distance_meters": 101.0})
    too_far_opportunities = opportunities.model_copy(update={"opportunities": (too_far,)})
    with pytest.raises(ValueError, match="roadside placement evidence"):
        _evaluate_response(
            _response_input(scenario),
            scenario,
            too_far_opportunities,
            _evaluate_attention(too_far_opportunities, seed=0),
        )

    offset_scenario = _scenario(
        pack,
        [
            _billboard(
                latitude=0.000005,
                max_view_distance=0.1,
            )
        ],
    )
    offset_opportunities = _evaluate(_mobility(pack), offset_scenario)
    assert offset_opportunities.opportunities[0].minimum_distance_meters > 0.1
    offset_attention = _evaluate_attention(offset_opportunities, seed=0)
    assert (
        _evaluate_response(
            _response_input(offset_scenario),
            offset_scenario,
            offset_opportunities,
            offset_attention,
        ).counts.response_count
        == 1
    )


def test_evaluator_refuses_phone_policy_and_ordinal_fabrication() -> None:
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
    first, second = opportunities.opportunities
    wrong_activity = first.model_copy(update={"activity": "commute"})
    activity_opportunities = opportunities.model_copy(
        update={"opportunities": (wrong_activity, second)}
    )
    with pytest.raises(ValueError, match="phone placement evidence"):
        _evaluate_response(
            _response_input(scenario),
            scenario,
            activity_opportunities,
            _evaluate_attention(activity_opportunities, seed=10),
        )

    wrong_ordinal = second.model_copy(update={"ordinal_for_agent_placement_day": 1})
    ordinal_opportunities = opportunities.model_copy(
        update={"opportunities": (first, wrong_ordinal)}
    )
    with pytest.raises(ValueError, match="ordinal"):
        _evaluate_response(
            _response_input(scenario),
            scenario,
            ordinal_opportunities,
            _evaluate_attention(ordinal_opportunities, seed=10),
        )


def test_response_evaluation_rederives_passing_attention_draw_from_seed() -> None:
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    result = _evaluate_response(response_input, scenario, opportunities, attention)

    with pytest.raises(ValidationError, match="keyed attention draw"):
        type(result).model_validate(result.model_dump() | {"attention_seed": 2})


def test_evaluation_refuses_unexplained_state_progress_for_untouched_campaign() -> None:
    opportunities = _mixed_opportunities()
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
    attention = _evaluate_attention(opportunities, seed=42)
    result = _evaluate_response(
        _response_input(scenario),
        scenario,
        opportunities,
        attention,
    )
    untouched = result.final_states[1]
    unexplained = untouched.model_copy(update={"response_count": 1, "last_response_minute": 0})

    with pytest.raises(ValidationError, match="final state response counts"):
        type(result).model_validate(
            result.model_dump() | {"final_states": (result.final_states[0], unexplained)}
        )


def test_evaluation_refuses_records_without_a_matching_final_state_key() -> None:
    scenario, opportunities, attention, response_input = _billboard_evidence(attention_seed=0)
    result = _evaluate_response(response_input, scenario, opportunities, attention)
    unrelated = result.final_states[0].model_copy(update={"agent_id": "person-002"})

    with pytest.raises(ValidationError, match=r"record keys.*final states"):
        type(result).model_validate(result.model_dump() | {"final_states": (unrelated,)})


def test_response_state_artifacts_enforce_population_and_campaign_ceilings() -> None:
    module = _module()
    states = tuple(
        module.SpatialResponseState(
            agent_id=f"person-{index:03d}",
            campaign_id="fictional-launch",
            brand_sentiment=0.0,
            recall_strength=0.0,
            purchase_intention=0.0,
            response_count=0,
            last_response_minute=None,
        )
        for index in range(31)
    )
    counts = module.SpatialResponseCounts(
        response_count=0,
        state_update_count=0,
        roadside_response_count=0,
        phone_response_count=0,
        campaign_count=1,
        final_state_count=31,
    )
    with pytest.raises(ValidationError, match="30 agents"):
        module.SpatialResponseEvaluation(
            response_input_sha256="a" * 64,
            scenario_sha256="b" * 64,
            city_sha256="c" * 64,
            attention_seed=0,
            counts=counts,
            records=(),
            final_states=states,
        )
    with pytest.raises(ValidationError, match="30 agents"):
        module.SpatialResponseStateDocument(
            response_input_sha256="a" * 64,
            scenario_sha256="b" * 64,
            city_sha256="c" * 64,
            states=states,
        )

    campaign_states = tuple(
        module.SpatialResponseState(
            agent_id="person-001",
            campaign_id=f"campaign-{index:02d}",
            brand_sentiment=0.0,
            recall_strength=0.0,
            purchase_intention=0.0,
            response_count=0,
            last_response_minute=None,
        )
        for index in range(21)
    )
    with pytest.raises(ValidationError, match="20 campaigns"):
        module.SpatialResponseStateDocument(
            response_input_sha256="a" * 64,
            scenario_sha256="b" * 64,
            city_sha256="c" * 64,
            states=campaign_states,
        )


def test_evaluation_reconstructs_prior_notice_fatigue_chronology() -> None:
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
    result = _evaluate_response(response_input, scenario, opportunities, attention)
    first_response, first_update, second_response, second_update = result.records
    no_fatigue_sentiment = (
        0.18 * second_response.value_match - 0.12 * second_response.advertising_skepticism
    )
    no_fatigue_recall = (
        0.22 * second_response.channel_recall_encoding + 0.12 * second_response.novelty_seeking
    )
    changed_response = second_response.model_copy(
        update={
            "prior_notices_today": 0,
            "frequency_fatigue": 0.0,
            "sentiment_delta": no_fatigue_sentiment,
            "recall_delta": no_fatigue_recall,
        }
    )
    previous = first_update.state
    changed_sentiment = previous.brand_sentiment + changed_response.sentiment_delta
    changed_recall = 1 - ((1 - previous.recall_strength) * (1 - changed_response.recall_delta))
    changed_intention = (
        0.40 * ((changed_sentiment + 1) / 2)
        + 0.25 * changed_response.value_match
        + 0.20 * changed_recall
        + 0.15 * changed_response.impulsivity
    )
    changed_state = second_update.state.model_copy(
        update={
            "brand_sentiment": changed_sentiment,
            "recall_strength": changed_recall,
            "purchase_intention": changed_intention,
        }
    )
    changed_update = second_update.model_copy(update={"state": changed_state})

    with pytest.raises(ValidationError, match="fatigue history"):
        type(result).model_validate(
            result.model_dump()
            | {
                "records": (
                    first_response,
                    first_update,
                    changed_response,
                    changed_update,
                ),
                "final_states": (changed_state,),
            }
        )


def test_evaluation_refuses_same_minute_notices_beyond_the_placement_cap() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(
                placement_id="a-later",
                fraction=0.5,
                longitude=0.005,
                side="left",
            ),
            _billboard(
                placement_id="z-earlier",
                fraction=0.4,
                longitude=0.004,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=5)
    result = _evaluate_response(
        _response_input(scenario),
        scenario,
        opportunities,
        attention,
    )
    first, second, update = result.records
    shared_placement = "shared-placement"

    def with_shared_placement(response: Any) -> Any:
        opportunity_id = _opportunity_id(
            scenario_sha256=response.scenario_sha256,
            city_sha256=response.city_sha256,
            campaign_id=response.campaign_id,
            placement_id=shared_placement,
            agent_id=response.agent_id,
            channel=response.channel,
            model_minute=response.model_minute,
            millisecond_within_minute=response.millisecond_within_minute,
        )
        impression_id = _attention_event_id(
            event_type="spatial.impression",
            caused_by=opportunity_id,
        )
        notice_id = _attention_event_id(
            event_type="spatial.noticed",
            caused_by=impression_id,
        )
        event_id = sha256(
            canonical_json(
                {
                    "caused_by": notice_id,
                    "event_type": "spatial.response",
                    "model_id": "spatial-response-v1",
                    "response_input_sha256": response.response_input_sha256,
                }
            ).encode("utf-8")
        ).hexdigest()
        return response.model_copy(
            update={
                "event_id": event_id,
                "caused_by": notice_id,
                "opportunity_id": opportunity_id,
                "placement_id": shared_placement,
                "frequency_cap_per_agent_per_day": 1,
            }
        )

    changed_first = with_shared_placement(first)
    changed_second = with_shared_placement(second)
    attention_seed = next(
        seed
        for seed in range(100)
        if all(
            spatial_notice_draw(
                agent_id=response.agent_id,
                at_millisecond=(
                    response.model_minute * 60_000 + response.millisecond_within_minute
                ),
                channel=response.channel,
                placement_id=response.placement_id,
                seed=seed,
            )
            < 0.5
            for response in (changed_first, changed_second)
        )
    )
    changed_causes = (changed_first.event_id, changed_second.event_id)
    changed_update_id = sha256(
        canonical_json(
            {
                "agent_id": update.agent_id,
                "campaign_id": update.campaign_id,
                "caused_by_event_ids": list(changed_causes),
                "event_type": "spatial.state-updated",
                "model_id": "spatial-response-v1",
                "model_minute": update.model_minute,
                "response_input_sha256": update.response_input_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()
    changed_update = update.model_copy(
        update={
            "event_id": changed_update_id,
            "caused_by_event_ids": changed_causes,
        }
    )

    with pytest.raises(ValidationError, match="daily cap"):
        type(result).model_validate(
            result.model_dump()
            | {
                "attention_seed": attention_seed,
                "records": (changed_first, changed_second, changed_update),
            }
        )


def test_response_fingerprints_are_not_rehashed_per_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 20}],
                probability=1.0,
                cap=20,
            )
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=10)
    response_input = _response_input(scenario)
    calls = {"response": 0, "scenario": 0}
    response_property = type(response_input).fingerprint
    scenario_property = type(scenario).fingerprint
    assert response_property.fget is not None
    assert scenario_property.fget is not None

    def response_fingerprint(value: Any) -> str:
        calls["response"] += 1
        assert response_property.fget is not None
        return response_property.fget(value)

    def scenario_fingerprint(value: Any) -> str:
        calls["scenario"] += 1
        assert scenario_property.fget is not None
        return scenario_property.fget(value)

    monkeypatch.setattr(type(response_input), "fingerprint", property(response_fingerprint))
    monkeypatch.setattr(type(scenario), "fingerprint", property(scenario_fingerprint))

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    assert result.counts.response_count > 5
    assert calls["response"] <= 3
    assert calls["scenario"] <= 4


def test_daily_fatigue_resets_without_resetting_campaign_state() -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 1_439, "end_minute": 1_441}],
                probability=1.0,
                cap=1,
            )
        ],
        days=2,
    )
    opportunities = _evaluate(_mobility(pack, days=2), scenario)
    attention = _evaluate_attention(opportunities, seed=7)
    result = _evaluate_response(
        _response_input(scenario),
        scenario,
        opportunities,
        attention,
    )

    responses = tuple(
        record for record in result.records if record.event_type == "spatial.response"
    )
    updates = tuple(
        record for record in result.records if record.event_type == "spatial.state-updated"
    )
    assert [record.day_index for record in responses] == [0, 1]
    assert [record.prior_notices_today for record in responses] == [0, 0]
    assert [record.frequency_fatigue for record in responses] == [0.0, 0.0]
    assert updates[1].previous_state == updates[0].state
    assert updates[1].state.response_count == 2


def test_campaign_copy_and_creative_identity_do_not_change_numeric_projection() -> None:
    pack = load_pack(pack_data())
    baseline_scenario = _scenario(pack, [_billboard()])
    changed_campaign = baseline_scenario.campaigns[0].model_copy(
        update={
            "name": "Different fictional copy",
            "creative_sha256": "f" * 64,
        }
    )
    changed_scenario = baseline_scenario.model_copy(
        update={
            "name": "Different fictional scenario copy",
            "campaigns": (changed_campaign,),
        }
    )

    baseline_opportunities = _evaluate(_mobility(pack), baseline_scenario)
    changed_opportunities = _evaluate(_mobility(pack), changed_scenario)
    baseline_attention = _evaluate_attention(baseline_opportunities, seed=0)
    changed_attention = _evaluate_attention(changed_opportunities, seed=0)
    baseline = _evaluate_response(
        _response_input(baseline_scenario),
        baseline_scenario,
        baseline_opportunities,
        baseline_attention,
    )
    changed = _evaluate_response(
        _response_input(changed_scenario),
        changed_scenario,
        changed_opportunities,
        changed_attention,
    )

    baseline_response = baseline.records[0]
    changed_response = changed.records[0]
    numeric_fields = (
        "interest_match",
        "relative_price",
        "price_sensitivity",
        "affordability",
        "novelty_seeking",
        "advertising_skepticism",
        "channel_recall_encoding",
        "impulsivity",
        "frequency_fatigue",
        "value_match",
        "sentiment_delta",
        "recall_delta",
    )
    assert baseline_response.event_id != changed_response.event_id
    assert baseline_response.scenario_sha256 != changed_response.scenario_sha256
    assert tuple(getattr(baseline_response, name) for name in numeric_fields) == tuple(
        getattr(changed_response, name) for name in numeric_fields
    )
    assert baseline.final_states == changed.final_states


def test_agent_argument_order_cannot_change_response_bytes() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack, agent_count=2)
    scenario = _scenario(pack, [_billboard()])
    opportunities = _evaluate(mobility, scenario)
    attention = _evaluate_attention(opportunities, seed=0)
    agent_ids = tuple(agent.agent_id for agent in mobility.agents)
    response_input = _response_input(scenario, agent_ids=agent_ids)

    first = _evaluate_response(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agent_ids,
    )
    second = _evaluate_response(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=tuple(reversed(agent_ids)),
    )

    permuted_data = deepcopy(response_input.model_dump(mode="json"))
    permuted_data["profiles"].reverse()
    permuted_data["initial_states"].reverse()
    for profile in permuted_data["profiles"]:
        profile["interests"].reverse()
    permuted_input = parse_spatial_response_input_json(json.dumps(permuted_data))
    third = _evaluate_response(
        permuted_input,
        scenario,
        opportunities,
        attention,
        agent_ids=tuple(reversed(agent_ids)),
    )

    assert first == second == third
    assert (
        tuple(_module().spatial_response_lines(first))
        == tuple(_module().spatial_response_lines(second))
        == tuple(_module().spatial_response_lines(third))
    )
