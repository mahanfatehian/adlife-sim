from __future__ import annotations

import json
from copy import deepcopy

from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _billboard, _mobility, _phone, _scenario


@given(
    seed=st.integers(min_value=0, max_value=2**63 - 1),
    campaign_order=st.permutations((0, 1)),
    placement_order=st.permutations((0, 1)),
    agent_order=st.permutations(("person-001", "person-002")),
)
def test_spatial_metrics_are_invariant_to_source_and_population_order(
    seed: int,
    campaign_order: list[int],
    placement_order: list[int],
    agent_order: list[str],
) -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack, agent_count=2)
    baseline = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                campaign_id="phone-campaign",
                windows=[{"start_minute": 0, "end_minute": 2}],
                cap=2,
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
    reordered = parse_spatial_campaign_scenario_json(json.dumps(permuted))

    baseline_opportunities = evaluate_spatial_opportunities(mobility, baseline)
    reordered_opportunities = evaluate_spatial_opportunities(mobility, reordered)
    baseline_attention = evaluate_spatial_attention(baseline_opportunities, seed=seed)
    reordered_attention = evaluate_spatial_attention(reordered_opportunities, seed=seed)

    expected = derive_spatial_metrics(
        baseline_opportunities,
        baseline_attention,
        agent_ids=("person-001", "person-002"),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=1,
    )
    actual = derive_spatial_metrics(
        reordered_opportunities,
        reordered_attention,
        agent_ids=tuple(agent_order),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=1,
    )

    assert actual == expected
