from __future__ import annotations

import importlib
import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.city import CityPackDocument
from adlife.core.domain.city_places import parse_city_place_set_json
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    parse_spatial_campaign_scenario_json,
)
from adlife.core.simulation.city_mobility import CityMobility, shortest_path
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data


def _mobility(
    pack: CityPackDocument,
    *,
    days: int = 1,
    home: str = "a",
    work: str = "b",
    leisure: str = "b",
    agent_count: int = 1,
) -> CityMobility:
    if agent_count not in {1, 2}:
        raise ValueError("test fixture supports one or two agents")
    home_nodes = (home,) if agent_count == 1 else (home, "c")
    home_places = [
        {
            "place_id": f"home-place-{index}",
            "kind": "home",
            "node_id": node_id,
            "label": f"Fictional home {index}",
            "provenance": {"method": "operator-authored-fictional"},
        }
        for index, node_id in enumerate(home_nodes, start=1)
    ]
    places = parse_city_place_set_json(
        json.dumps(
            {
                "schema_version": 1,
                "city_id": pack.city_id,
                "city_sha256": pack.fingerprint,
                "name": "Forced opportunity places",
                "places": [
                    *home_places,
                    {
                        "place_id": "work-place",
                        "kind": "workplace",
                        "node_id": work,
                        "label": "Fictional work",
                        "provenance": {"method": "operator-authored-fictional"},
                    },
                    {
                        "place_id": "leisure-place",
                        "kind": "leisure",
                        "node_id": leisure,
                        "label": "Fictional leisure",
                        "provenance": {"method": "operator-authored-fictional"},
                    },
                ],
            }
        )
    )
    return CityMobility(pack, seed=42, agent_count=agent_count, days=days, places=places)


def _billboard(
    *,
    placement_id: str = "billboard-ab",
    direction: str = "forward",
    fraction: float = 0.5,
    longitude: float = 0.005,
    latitude: float = 0.0,
    orientation: float = 270.0,
    side: str = "right",
    windows: list[dict[str, int]] | None = None,
    cap: int = 3,
    max_view_distance: float = 100.0,
) -> dict[str, Any]:
    return {
        "placement_id": placement_id,
        "campaign_id": "fictional-launch",
        "channel": "roadside-billboard",
        "active_windows": windows or [{"start_minute": 0, "end_minute": 1_440}],
        "frequency_cap_per_agent_per_day": cap,
        "road_id": "ab",
        "travel_direction": direction,
        "road_fraction": fraction,
        "longitude": longitude,
        "latitude": latitude,
        "side": side,
        "orientation_degrees": orientation,
        "max_view_distance_meters": max_view_distance,
    }


def _phone(
    *,
    placement_id: str = "phone-feed",
    campaign_id: str = "fictional-launch",
    activities: list[str] | None = None,
    probability: float = 1.0,
    windows: list[dict[str, int]] | None = None,
    cap: int = 3,
) -> dict[str, Any]:
    return {
        "placement_id": placement_id,
        "campaign_id": campaign_id,
        "channel": "mobile-feed",
        "active_windows": windows or [{"start_minute": 0, "end_minute": 1_440}],
        "frequency_cap_per_agent_per_day": cap,
        "opportunity_model": "keyed-activity-minute-v1",
        "eligible_activities": activities or ["home"],
        "opportunity_probability_per_minute": probability,
    }


def _scenario(
    pack: CityPackDocument,
    placements: list[dict[str, Any]],
    *,
    days: int = 1,
) -> SpatialCampaignScenario:
    campaign_ids = sorted({str(item["campaign_id"]) for item in placements})
    return parse_spatial_campaign_scenario_json(
        json.dumps(
            {
                "schema_version": 1,
                "scenario_id": "opportunity-golden",
                "name": "Fictional opportunity golden",
                "days": days,
                "city_id": pack.city_id,
                "city_sha256": pack.fingerprint,
                "campaigns": [
                    {
                        "campaign_id": campaign_id,
                        "name": f"Campaign {index}",
                        "creative_sha256": f"{index:x}" * 64,
                    }
                    for index, campaign_id in enumerate(campaign_ids, start=1)
                ],
                "placements": placements,
            }
        )
    )


def _evaluate(mobility: CityMobility, scenario: SpatialCampaignScenario):
    module = importlib.import_module("adlife.core.simulation.spatial_opportunity")
    evaluator = getattr(module, "evaluate_spatial_opportunities", None)
    assert evaluator is not None, "spatial opportunity evaluator is not implemented"
    return evaluator(mobility, scenario)


def test_forward_roadside_opportunity_is_typed_auditable_and_stable() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    scenario = _scenario(pack, [_billboard()])

    result = _evaluate(mobility, scenario)

    assert result.schema_version == 1
    assert result.model_id == "spatial-opportunity-v1"
    assert result.scenario_sha256 == scenario.fingerprint
    assert result.city_sha256 == pack.fingerprint
    assert result.counts.model_dump() == {
        "schema_version": 1,
        "roadside_matching_traversal_count": 1,
        "roadside_active_crossing_count": 1,
        "roadside_proximity_passage_count": 1,
        "roadside_approximately_visible_count": 1,
        "phone_eligible_agent_minute_count": 0,
        "phone_successful_draw_count": 0,
        "frequency_capped_candidate_count": 0,
        "roadside_opportunity_count": 1,
        "phone_opportunity_count": 0,
        "opportunity_count": 1,
    }
    (event,) = result.opportunities
    assert event.model_dump(exclude={"opportunity_id"}) == {
        "schema_version": 1,
        "model_id": "spatial-opportunity-v1",
        "claim_scope": "synthetic-opportunity-not-impression",
        "scenario_sha256": scenario.fingerprint,
        "city_sha256": pack.fingerprint,
        "campaign_id": "fictional-launch",
        "placement_id": "billboard-ab",
        "agent_id": "person-001",
        "channel": "roadside-billboard",
        "day_index": 0,
        "model_minute": 481,
        "millisecond_within_minute": event.millisecond_within_minute,
        "ordinal_for_agent_placement_day": 1,
        "basis": "directional-road-passage-v1",
        "road_id": "ab",
        "travel_direction": "forward",
        "road_fraction": 0.5,
        "side": "right",
        "minimum_distance_meters": pytest.approx(0.0, abs=1e-9),
        "approach_distance_meters": pytest.approx(100.0),
        "view_angle_degrees": pytest.approx(0.0, abs=1e-6),
    }
    at_millisecond = event.model_minute * 60_000 + event.millisecond_within_minute
    identity = {
        "model_id": "spatial-opportunity-v1",
        "scenario_sha256": scenario.fingerprint,
        "city_sha256": pack.fingerprint,
        "campaign_id": event.campaign_id,
        "placement_id": event.placement_id,
        "agent_id": event.agent_id,
        "channel": event.channel,
        "at_millisecond": at_millisecond,
    }
    assert event.opportunity_id == sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    assert _evaluate(mobility, scenario) == result
    with pytest.raises(ValidationError, match="extra"):
        type(event).model_validate(event.model_dump() | {"unexpected": True})
    with pytest.raises(ValidationError, match="finite"):
        type(event).model_validate(event.model_dump() | {"view_angle_degrees": float("nan")})
    with pytest.raises(ValidationError, match="frozen"):
        event.__setattr__("model_minute", 0)


def test_roadside_stage_counts_distinguish_time_and_facing_refusals() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)

    inactive = _evaluate(
        mobility,
        _scenario(
            pack,
            [_billboard(windows=[{"start_minute": 600, "end_minute": 700}])],
        ),
    )
    assert inactive.counts.roadside_matching_traversal_count == 1
    assert inactive.counts.roadside_active_crossing_count == 0
    assert inactive.counts.roadside_proximity_passage_count == 0
    assert inactive.counts.roadside_approximately_visible_count == 0
    assert inactive.opportunities == ()

    away = _evaluate(mobility, _scenario(pack, [_billboard(orientation=90.0)]))
    assert away.counts.roadside_matching_traversal_count == 1
    assert away.counts.roadside_active_crossing_count == 1
    assert away.counts.roadside_proximity_passage_count == 1
    assert away.counts.roadside_approximately_visible_count == 0
    assert away.opportunities == ()


def test_approximate_facing_threshold_is_inclusive_at_ninety_degrees() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)

    at_limit = _evaluate(mobility, _scenario(pack, [_billboard(orientation=0.0)]))
    beyond_limit = _evaluate(mobility, _scenario(pack, [_billboard(orientation=0.001)]))

    (event,) = at_limit.opportunities
    assert event.view_angle_degrees == pytest.approx(90.0)
    assert beyond_limit.counts.roadside_proximity_passage_count == 1
    assert beyond_limit.counts.roadside_approximately_visible_count == 0
    assert beyond_limit.opportunities == ()


def test_reverse_direction_uses_return_crossing_and_reverse_approach() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    scenario = _scenario(
        pack,
        [
            _billboard(
                direction="backward",
                orientation=90.0,
                windows=[{"start_minute": 1_020, "end_minute": 1_030}],
            )
        ],
    )

    result = _evaluate(mobility, scenario)

    (event,) = result.opportunities
    assert event.travel_direction == "backward"
    assert event.model_minute == 1_021
    assert event.approach_distance_meters == pytest.approx(100.0)
    assert event.view_angle_degrees == pytest.approx(0.0, abs=1e-6)


def test_multiple_crossings_in_one_minute_keep_continuous_time_order() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
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

    result = _evaluate(mobility, scenario)

    assert result.counts.roadside_matching_traversal_count == 2
    assert result.counts.roadside_approximately_visible_count == 2
    assert [event.model_minute for event in result.opportunities] == [481, 481]
    assert [event.placement_id for event in result.opportunities] == ["z-earlier", "a-later"]
    assert result.opportunities[0].millisecond_within_minute < (
        result.opportunities[1].millisecond_within_minute
    )


def test_crossing_at_window_start_is_active_and_at_end_is_inactive() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    duration = shortest_path(pack, "a", "b").duration_minutes
    fraction = 1 / duration
    longitude = 0.01 * fraction

    active = _evaluate(
        mobility,
        _scenario(
            pack,
            [
                _billboard(
                    fraction=fraction,
                    longitude=longitude,
                    windows=[{"start_minute": 481, "end_minute": 482}],
                )
            ],
        ),
    )
    inactive = _evaluate(
        mobility,
        _scenario(
            pack,
            [
                _billboard(
                    fraction=fraction,
                    longitude=longitude,
                    windows=[{"start_minute": 480, "end_minute": 481}],
                )
            ],
        ),
    )

    (event,) = active.opportunities
    assert (event.model_minute, event.millisecond_within_minute) == (481, 0)
    assert inactive.counts.roadside_matching_traversal_count == 1
    assert inactive.counts.roadside_active_crossing_count == 0
    assert inactive.opportunities == ()


def test_curved_shape_point_uses_the_actual_approach_polyline() -> None:
    pack = load_pack_v2(pack_v2_data())
    mobility = _mobility(pack)
    scenario = _scenario(
        pack,
        [
            _billboard(
                longitude=0.005,
                latitude=0.004,
                orientation=231.34,
                max_view_distance=10.0,
            )
        ],
    )

    result = _evaluate(mobility, scenario)

    (event,) = result.opportunities
    assert event.approach_distance_meters == pytest.approx(10.0)
    assert event.view_angle_degrees < 0.1


def test_evaluation_refuses_duration_mismatch_and_does_not_mutate_inputs() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    scenario = _scenario(pack, [_billboard()])
    scenario_before = deepcopy(scenario.model_dump(mode="json"))
    frame_before = mobility.frame_document(481)

    _evaluate(mobility, scenario)

    assert scenario.model_dump(mode="json") == scenario_before
    assert mobility.frame_document(481) == frame_before

    mismatch = _scenario(pack, [_billboard()], days=2)
    with pytest.raises(ValueError, match="duration"):
        _evaluate(mobility, mismatch)


def test_phone_probability_one_records_all_pre_cap_candidates_and_exact_draws() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 5}],
                probability=1.0,
                cap=2,
            )
        ],
    )

    result = _evaluate(mobility, scenario)

    assert result.counts.model_dump() == {
        "schema_version": 1,
        "roadside_matching_traversal_count": 0,
        "roadside_active_crossing_count": 0,
        "roadside_proximity_passage_count": 0,
        "roadside_approximately_visible_count": 0,
        "phone_eligible_agent_minute_count": 5,
        "phone_successful_draw_count": 5,
        "frequency_capped_candidate_count": 3,
        "roadside_opportunity_count": 0,
        "phone_opportunity_count": 2,
        "opportunity_count": 2,
    }
    assert [event.model_minute for event in result.opportunities] == [0, 1]
    assert [event.ordinal_for_agent_placement_day for event in result.opportunities] == [1, 2]
    for event in result.opportunities:
        assert event.channel == "mobile-feed"
        assert event.claim_scope == "synthetic-opportunity-not-impression"
        assert event.basis == "keyed-activity-minute-v1"
        assert event.activity == "home"
        assert event.millisecond_within_minute == 0
        assert event.opportunity_probability_per_minute == 1.0
        stream_material = "42|spatial-phone-opportunity-v1:fictional-launch:phone-feed|person-001|0"
        stream_key = int.from_bytes(sha256(stream_material.encode("utf-8")).digest()[:8], "big")
        mask = (1 << 64) - 1
        mixed = (stream_key + event.model_minute * 0x9E3779B97F4A7C15) & mask
        mixed = ((mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9) & mask
        mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & mask
        mixed ^= mixed >> 31
        expected = (mixed >> 11) / (1 << 53)
        assert event.eligibility_draw == expected


def test_phone_probability_zero_keeps_eligible_denominator_without_events() -> None:
    pack = load_pack(pack_data())
    result = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [
                _phone(
                    windows=[{"start_minute": 0, "end_minute": 5}],
                    probability=0.0,
                )
            ],
        ),
    )

    assert result.counts.phone_eligible_agent_minute_count == 5
    assert result.counts.phone_successful_draw_count == 0
    assert result.counts.phone_opportunity_count == 0
    assert result.opportunities == ()


def test_phone_activity_and_half_open_windows_define_eligible_minutes() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    scenario = _scenario(
        pack,
        [
            _phone(
                activities=["commute"],
                windows=[{"start_minute": 479, "end_minute": 485}],
                probability=1.0,
                cap=10,
            )
        ],
    )
    expected_minutes = [
        minute for minute in range(479, 485) if mobility.frame(minute)[0].activity == "commute"
    ]

    result = _evaluate(mobility, scenario)

    assert expected_minutes == [480, 481, 482, 483]
    assert result.counts.phone_eligible_agent_minute_count == len(expected_minutes)
    assert [event.model_minute for event in result.opportunities] == expected_minutes


def test_phone_probability_variants_reuse_common_random_draw() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    window = [{"start_minute": 0, "end_minute": 1}]
    certain = _evaluate(
        mobility,
        _scenario(pack, [_phone(windows=window, probability=1.0, cap=1)]),
    )
    (certain_event,) = certain.opportunities
    changed_threshold = (certain_event.eligibility_draw + 1) / 2

    variant = _evaluate(
        mobility,
        _scenario(
            pack,
            [_phone(windows=window, probability=changed_threshold, cap=1)],
        ),
    )

    (variant_event,) = variant.opportunities
    assert variant_event.eligibility_draw == certain_event.eligibility_draw
    assert variant_event.opportunity_probability_per_minute == changed_threshold
    assert variant_event.opportunity_id != certain_event.opportunity_id


def test_phone_caps_are_scoped_by_placement_agent_and_day() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack, days=2, agent_count=2)
    scenario = _scenario(
        pack,
        [
            _phone(
                placement_id="phone-a",
                campaign_id="campaign-a",
                windows=[
                    {"start_minute": 0, "end_minute": 2},
                    {"start_minute": 1_440, "end_minute": 1_442},
                ],
                cap=1,
            ),
            _phone(
                placement_id="phone-b",
                campaign_id="campaign-b",
                windows=[
                    {"start_minute": 0, "end_minute": 2},
                    {"start_minute": 1_440, "end_minute": 1_442},
                ],
                cap=1,
            ),
        ],
        days=2,
    )

    result = _evaluate(mobility, scenario)

    assert result.counts.phone_eligible_agent_minute_count == 16
    assert result.counts.phone_successful_draw_count == 16
    assert result.counts.frequency_capped_candidate_count == 8
    assert result.counts.phone_opportunity_count == 8
    assert {
        (event.placement_id, event.agent_id, event.day_index, event.model_minute)
        for event in result.opportunities
    } == {
        (placement, agent, day, day * 1_440)
        for placement in ("phone-a", "phone-b")
        for agent in ("person-001", "person-002")
        for day in (0, 1)
    }
    assert all(event.ordinal_for_agent_placement_day == 1 for event in result.opportunities)


def test_mixed_channels_share_one_canonical_continuous_time_order() -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    scenario = _scenario(
        pack,
        [
            _billboard(windows=[{"start_minute": 481, "end_minute": 482}]),
            _phone(
                campaign_id="phone-campaign",
                activities=["commute"],
                windows=[{"start_minute": 481, "end_minute": 482}],
                cap=1,
            ),
        ],
    )

    result = _evaluate(mobility, scenario)

    assert result.counts.roadside_opportunity_count == 1
    assert result.counts.phone_opportunity_count == 1
    assert [event.channel for event in result.opportunities] == [
        "mobile-feed",
        "roadside-billboard",
    ]
    assert [event.model_minute for event in result.opportunities] == [481, 481]
    assert result.opportunities[0].millisecond_within_minute == 0
    assert result.opportunities[1].millisecond_within_minute > 0


def test_artifact_lines_and_summary_bind_exact_canonical_bytes() -> None:
    module = importlib.import_module("adlife.core.simulation.spatial_opportunity")
    lines_for = getattr(module, "spatial_opportunity_lines", None)
    summarize = getattr(module, "summarize_spatial_opportunity_artifact", None)
    assert lines_for is not None, "canonical opportunity line iterator is not implemented"
    assert summarize is not None, "opportunity artifact summary is not implemented"
    pack = load_pack(pack_data())
    result = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [
                _phone(
                    windows=[{"start_minute": 0, "end_minute": 2}],
                    probability=1.0,
                    cap=2,
                )
            ],
        ),
    )

    lines = tuple(lines_for(result))
    expected = tuple(
        (canonical_json(opportunity) + "\n").encode("utf-8") for opportunity in result.opportunities
    )
    summary = summarize(result)

    assert lines == expected
    assert summary.model_dump() == {
        "schema_version": 1,
        "model_id": "spatial-opportunity-artifact-v1",
        "claim_scope": "synthetic-opportunity-not-impression",
        "opportunity_model_id": "spatial-opportunity-v1",
        "scenario_sha256": result.scenario_sha256,
        "city_sha256": result.city_sha256,
        "stream_sha256": sha256(b"".join(expected)).hexdigest(),
        "stream_bytes": sum(map(len, expected)),
        "counts": result.counts.model_dump(),
    }
    with pytest.raises(ValidationError, match="frozen"):
        summary.__setattr__("stream_bytes", 0)


def test_empty_artifact_uses_the_sha256_of_zero_bytes() -> None:
    module = importlib.import_module("adlife.core.simulation.spatial_opportunity")
    lines_for = getattr(module, "spatial_opportunity_lines", None)
    summarize = getattr(module, "summarize_spatial_opportunity_artifact", None)
    assert lines_for is not None, "canonical opportunity line iterator is not implemented"
    assert summarize is not None, "opportunity artifact summary is not implemented"
    pack = load_pack(pack_data())
    result = _evaluate(
        _mobility(pack),
        _scenario(
            pack,
            [
                _phone(
                    windows=[{"start_minute": 0, "end_minute": 1}],
                    probability=0.0,
                )
            ],
        ),
    )

    assert tuple(lines_for(result)) == ()
    summary = summarize(result)
    assert summary.stream_bytes == 0
    assert summary.stream_sha256 == sha256(b"").hexdigest()
    assert summary.counts.opportunity_count == 0


def test_artifact_contract_enforces_record_and_byte_ceilings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("adlife.core.simulation.spatial_opportunity")
    summary_model = getattr(module, "SpatialOpportunityArtifactSummary", None)
    summarize = getattr(module, "summarize_spatial_opportunity_artifact", None)
    assert summary_model is not None, "opportunity artifact summary is not implemented"
    assert summarize is not None, "opportunity artifact summary is not implemented"
    assert module.MAX_SPATIAL_OPPORTUNITIES == 520_800
    assert module.MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES == 536_870_912

    pack = load_pack(pack_data())
    result = _evaluate(_mobility(pack), _scenario(pack, [_billboard()]))
    counts = result.counts.model_dump()
    counts.update(
        phone_eligible_agent_minute_count=520_801,
        phone_successful_draw_count=520_801,
        roadside_matching_traversal_count=0,
        roadside_active_crossing_count=0,
        roadside_proximity_passage_count=0,
        roadside_approximately_visible_count=0,
        roadside_opportunity_count=0,
        phone_opportunity_count=520_801,
        opportunity_count=520_801,
    )
    with pytest.raises(ValidationError, match="opportunity_count"):
        type(result.counts).model_validate(counts)

    document = {
        "schema_version": 1,
        "model_id": "spatial-opportunity-artifact-v1",
        "claim_scope": "synthetic-opportunity-not-impression",
        "opportunity_model_id": "spatial-opportunity-v1",
        "scenario_sha256": result.scenario_sha256,
        "city_sha256": result.city_sha256,
        "stream_sha256": "0" * 64,
        "stream_bytes": 536_870_913,
        "counts": result.counts,
    }
    with pytest.raises(ValidationError, match="stream_bytes"):
        summary_model.model_validate(document)

    monkeypatch.setattr(module, "MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        summarize(result)
