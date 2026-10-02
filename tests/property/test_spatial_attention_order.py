from __future__ import annotations

import json
from copy import deepcopy

from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _billboard, _mobility, _phone, _scenario


@given(
    seed=st.integers(min_value=0, max_value=2**63 - 1),
    campaign_order=st.permutations((0, 1)),
    placement_order=st.permutations((0, 1)),
    billboard_window_order=st.permutations((0, 1)),
    phone_window_order=st.permutations((0, 1)),
    activity_order=st.permutations(("home", "commute", "work")),
)
def test_spatial_attention_is_invariant_to_every_input_collection_order(
    seed: int,
    campaign_order: list[int],
    placement_order: list[int],
    billboard_window_order: list[int],
    phone_window_order: list[int],
    activity_order: list[str],
) -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    baseline = _scenario(
        pack,
        [
            _billboard(
                windows=[
                    {"start_minute": 480, "end_minute": 490},
                    {"start_minute": 1_020, "end_minute": 1_030},
                ]
            ),
            _phone(
                campaign_id="phone-campaign",
                activities=["home", "commute", "work"],
                probability=1.0,
                windows=[
                    {"start_minute": 0, "end_minute": 3},
                    {"start_minute": 480, "end_minute": 483},
                ],
                cap=10,
            ),
        ],
    )
    payload = baseline.model_dump(mode="json")
    permuted = deepcopy(payload)
    campaigns = permuted["campaigns"]
    placements = permuted["placements"]
    assert isinstance(campaigns, list) and isinstance(placements, list)
    permuted["campaigns"] = [campaigns[index] for index in campaign_order]
    permuted["placements"] = [placements[index] for index in placement_order]
    billboard = next(item for item in placements if item["channel"] == "roadside-billboard")
    phone = next(item for item in placements if item["channel"] == "mobile-feed")
    billboard_windows = billboard["active_windows"]
    phone_windows = phone["active_windows"]
    assert isinstance(billboard_windows, list) and isinstance(phone_windows, list)
    billboard["active_windows"] = [billboard_windows[index] for index in billboard_window_order]
    phone["active_windows"] = [phone_windows[index] for index in phone_window_order]
    phone["eligible_activities"] = list(activity_order)
    reordered = parse_spatial_campaign_scenario_json(json.dumps(permuted))

    baseline_opportunities = evaluate_spatial_opportunities(mobility, baseline)
    reordered_opportunities = evaluate_spatial_opportunities(mobility, reordered)

    assert evaluate_spatial_attention(
        reordered_opportunities, seed=seed
    ) == evaluate_spatial_attention(baseline_opportunities, seed=seed)
