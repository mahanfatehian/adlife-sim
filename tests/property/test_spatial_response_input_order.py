from __future__ import annotations

import json
from copy import deepcopy

from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain.spatial_response import parse_spatial_response_input_json
from tests.unit.city.test_spatial_response_contract import spatial_response_data


@given(
    profile_order=st.permutations((0, 1)),
    campaign_order=st.permutations((0, 1)),
    state_order=st.permutations((0, 1, 2, 3)),
    first_interests=st.permutations((0, 1)),
    second_interests=st.permutations((0, 1)),
    first_targets=st.permutations((0, 1)),
    second_targets=st.permutations((0, 1)),
)
def test_response_input_fingerprint_is_invariant_to_document_order(
    profile_order: list[int],
    campaign_order: list[int],
    state_order: list[int],
    first_interests: list[int],
    second_interests: list[int],
    first_targets: list[int],
    second_targets: list[int],
) -> None:
    baseline = spatial_response_data()
    permuted = deepcopy(baseline)
    permuted["profiles"] = [permuted["profiles"][index] for index in profile_order]
    permuted["campaigns"] = [permuted["campaigns"][index] for index in campaign_order]
    permuted["initial_states"] = [permuted["initial_states"][index] for index in state_order]
    profile_by_id = {item["agent_id"]: item for item in permuted["profiles"]}
    campaign_by_id = {item["campaign_id"]: item for item in permuted["campaigns"]}
    for agent_id, order in (
        ("person-000", first_interests),
        ("person-001", second_interests),
    ):
        values = profile_by_id[agent_id]["interests"]
        profile_by_id[agent_id]["interests"] = [values[index] for index in order]
    for campaign_id, order in (
        ("coffee-launch", first_targets),
        ("snack-launch", second_targets),
    ):
        values = campaign_by_id[campaign_id]["target_interests"]
        campaign_by_id[campaign_id]["target_interests"] = [values[index] for index in order]

    first = parse_spatial_response_input_json(json.dumps(baseline))
    second = parse_spatial_response_input_json(json.dumps(permuted))

    assert first == second
    assert first.fingerprint == second.fingerprint
