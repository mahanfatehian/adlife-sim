from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy

from adlife.core.experiments.spatial_response_metrics import (
    derive_spatial_response_metrics,
    spatial_response_assumption_structure_sha256,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.spatial_response import parse_spatial_response_input_json
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from adlife.core.simulation.spatial_response import evaluate_spatial_responses
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _mobility,
    _phone,
    _scenario,
)
from tests.unit.city.test_spatial_response import _response_input


@settings(max_examples=12, deadline=None)
@given(
    seed=st.sampled_from((0, 1, 2, 4, 10, 42)),
    reverse_profiles=st.booleans(),
    reverse_campaigns=st.booleans(),
    reverse_states=st.booleans(),
    reverse_interests=st.booleans(),
    reverse_agents=st.booleans(),
)
def test_response_metrics_are_invariant_to_every_accepted_input_permutation(
    seed: int,
    reverse_profiles: bool,
    reverse_campaigns: bool,
    reverse_states: bool,
    reverse_interests: bool,
    reverse_agents: bool,
) -> None:
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
    agents = ("person-001", "person-002")
    mobility = _mobility(pack, agent_count=2)
    opportunities = evaluate_spatial_opportunities(mobility, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=seed)
    baseline_input = _response_input(scenario, agent_ids=agents)
    baseline_response = evaluate_spatial_responses(
        baseline_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agents,
    )
    baseline_attention_metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=agents,
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=1,
        source_run_schema_version=6,
    )
    expected = derive_spatial_response_metrics(
        baseline_input,
        baseline_response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        agent_ids=agents,
        attention_metrics=baseline_attention_metrics,
    )

    document = deepcopy(baseline_input.model_dump(mode="json"))
    if reverse_profiles:
        document["profiles"].reverse()
    if reverse_campaigns:
        document["campaigns"].reverse()
    if reverse_states:
        document["initial_states"].reverse()
    if reverse_interests:
        for profile in document["profiles"]:
            profile["interests"].reverse()
        for campaign in document["campaigns"]:
            campaign["target_interests"].reverse()
    permuted_input = parse_spatial_response_input_json(json.dumps(document))
    supplied_agents = tuple(reversed(agents)) if reverse_agents else agents
    actual_response = evaluate_spatial_responses(
        permuted_input,
        scenario,
        opportunities,
        attention,
        agent_ids=supplied_agents,
    )
    actual_attention_metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=supplied_agents,
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=1,
        source_run_schema_version=6,
    )
    actual = derive_spatial_response_metrics(
        permuted_input,
        actual_response,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        agent_ids=supplied_agents,
        attention_metrics=actual_attention_metrics,
    )

    assert actual == expected
    assert spatial_response_assumption_structure_sha256(permuted_input, scenario) == (
        spatial_response_assumption_structure_sha256(baseline_input, scenario)
    )


def test_response_metrics_bytes_do_not_depend_on_python_hash_seed() -> None:
    script = """
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import derive_spatial_response_metrics
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from adlife.core.simulation.spatial_response import evaluate_spatial_responses
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _billboard, _mobility, _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input

pack = load_pack(pack_data())
scenario = _scenario(pack, [
    _billboard(),
    _phone(
        campaign_id='phone-campaign',
        windows=[{'start_minute': 0, 'end_minute': 2}],
        probability=1.0,
        cap=2,
    ),
])
agent_ids = ('person-001', 'person-002')
opportunities = evaluate_spatial_opportunities(_mobility(pack, agent_count=2), scenario)
attention = evaluate_spatial_attention(opportunities, seed=42)
response_input = _response_input(scenario, agent_ids=agent_ids)
response = evaluate_spatial_responses(
    response_input, scenario, opportunities, attention, agent_ids=agent_ids
)
attention_metrics = derive_spatial_metrics(
    opportunities,
    attention,
    agent_ids=agent_ids,
    agents_sha256='a' * 64,
    trace_sha256='b' * 64,
    days=1,
    source_run_schema_version=6,
)
result = derive_spatial_response_metrics(
    response_input,
    response,
    scenario=scenario,
    opportunities=opportunities,
    attention=attention,
    agent_ids=agent_ids,
    attention_metrics=attention_metrics,
)
print(canonical_json(result))
"""
    outputs = []
    for hash_seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert completed.stderr == ""
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    document = json.loads(outputs[0])
    assert document["model_id"] == "spatial-response-metrics-v1"
    assert [series["campaign_id"] for series in document["campaigns"]] == [
        "fictional-launch",
        "phone-campaign",
    ]
