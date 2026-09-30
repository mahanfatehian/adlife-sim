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
) -> CityMobility:
    places = parse_city_place_set_json(
        json.dumps(
            {
                "schema_version": 1,
                "city_id": pack.city_id,
                "city_sha256": pack.fingerprint,
                "name": "Forced opportunity places",
                "places": [
                    {
                        "place_id": "home-place",
                        "kind": "home",
                        "node_id": home,
                        "label": "Fictional home",
                        "provenance": {"method": "operator-authored-fictional"},
                    },
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
    return CityMobility(pack, seed=42, agent_count=1, days=days, places=places)


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


def _scenario(
    pack: CityPackDocument,
    placements: list[dict[str, Any]],
    *,
    days: int = 1,
) -> SpatialCampaignScenario:
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
                        "campaign_id": "fictional-launch",
                        "name": "Fictional launch",
                        "creative_sha256": "a" * 64,
                    }
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
