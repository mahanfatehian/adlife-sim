from __future__ import annotations

import json
from copy import deepcopy

from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain import spatial_campaign
from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data
from tests.unit.city.test_spatial_campaign_contract import spatial_scenario_data


@given(
    campaign_order=st.permutations((0, 1)),
    placement_order=st.permutations((0, 1)),
    window_order=st.permutations((0, 1)),
    activity_order=st.permutations(("home", "work")),
)
def test_spatial_binding_is_invariant_to_input_order(
    campaign_order: list[int],
    placement_order: list[int],
    window_order: list[int],
    activity_order: list[str],
) -> None:
    pack = load_pack_v2(pack_v2_data())
    baseline = spatial_scenario_data()
    baseline["city_sha256"] = pack.fingerprint
    permuted = deepcopy(baseline)
    permuted["campaigns"] = [permuted["campaigns"][index] for index in campaign_order]
    permuted["placements"] = [permuted["placements"][index] for index in placement_order]
    billboard = next(
        item for item in permuted["placements"] if item["channel"] == "roadside-billboard"
    )
    phone = next(item for item in permuted["placements"] if item["channel"] == "mobile-feed")
    billboard["active_windows"] = [billboard["active_windows"][index] for index in window_order]
    phone["eligible_activities"] = list(activity_order)

    first = parse_spatial_campaign_scenario_json(json.dumps(baseline))
    second = parse_spatial_campaign_scenario_json(json.dumps(permuted))
    validate = getattr(spatial_campaign, "validate_spatial_scenario_against_city", None)
    assert validate is not None, "spatial city binding is not implemented"

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert validate(first, pack) == validate(second, pack)
