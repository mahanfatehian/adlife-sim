from __future__ import annotations

import math
import os
import subprocess
import sys
from functools import lru_cache
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain.spatial_response import SpatialResponseTraits
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_attention import _evaluate_attention
from tests.unit.city.test_spatial_opportunity import _billboard, _evaluate, _mobility, _scenario
from tests.unit.city.test_spatial_response import _evaluate_response, _response_input


@lru_cache(maxsize=1)
def _one_notice() -> tuple[Any, Any, Any]:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_billboard()])
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=0)
    return scenario, opportunities, attention


_unit_float = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=32,
)


@given(
    price_sensitivity=_unit_float,
    novelty_seeking=_unit_float,
    advertising_skepticism=_unit_float,
    mobile_recall_encoding=_unit_float,
    roadside_recall_encoding=_unit_float,
    impulsivity=_unit_float,
    sentiment=st.floats(
        min_value=-1.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
        width=32,
    ),
    recall=_unit_float,
    intention=_unit_float,
)
def test_rule_outputs_and_committed_state_are_always_finite_and_bounded(
    price_sensitivity: float,
    novelty_seeking: float,
    advertising_skepticism: float,
    mobile_recall_encoding: float,
    roadside_recall_encoding: float,
    impulsivity: float,
    sentiment: float,
    recall: float,
    intention: float,
) -> None:
    scenario, opportunities, attention = _one_notice()
    response_input = _response_input(
        scenario,
        sentiment=sentiment,
        recall=recall,
        intention=intention,
        traits=SpatialResponseTraits(
            price_sensitivity=price_sensitivity,
            novelty_seeking=novelty_seeking,
            advertising_skepticism=advertising_skepticism,
            mobile_recall_encoding=mobile_recall_encoding,
            roadside_recall_encoding=roadside_recall_encoding,
            impulsivity=impulsivity,
        ),
    )

    result = _evaluate_response(response_input, scenario, opportunities, attention)

    response, update = result.records
    assert -0.2 <= response.sentiment_delta <= 0.2
    assert 0 <= response.recall_delta <= 0.3
    assert 0 <= response.interest_match <= 1
    assert 0 <= response.affordability <= 1
    assert 0 <= response.frequency_fatigue <= 1
    assert 0 <= response.value_match <= 1
    assert -1 <= update.state.brand_sentiment <= 1
    assert 0 <= update.state.recall_strength <= 1
    assert 0 <= update.state.purchase_intention <= 1
    assert all(
        math.isfinite(value)
        for value in (
            response.sentiment_delta,
            response.recall_delta,
            response.interest_match,
            response.affordability,
            response.frequency_fatigue,
            response.value_match,
            update.state.brand_sentiment,
            update.state.recall_strength,
            update.state.purchase_intention,
        )
    )


def test_response_artifact_bytes_are_stable_across_python_hash_seeds() -> None:
    script = (
        "from hashlib import sha256; "
        "from adlife.core.domain.serialization import canonical_json; "
        "from adlife.core.simulation.spatial_attention import evaluate_spatial_attention; "
        "from adlife.core.simulation.spatial_response import "
        "evaluate_spatial_responses, spatial_response_lines, "
        "spatial_response_state_document; "
        "from tests.unit.city.test_city_pack import load_pack, pack_data; "
        "from tests.unit.city.test_spatial_opportunity import "
        "_billboard, _evaluate, _mobility, _scenario; "
        "from tests.unit.city.test_spatial_response import _response_input; "
        "pack=load_pack(pack_data()); "
        "scenario=_scenario(pack,["
        "_billboard(placement_id='a-later',fraction=.5,longitude=.005,side='left'),"
        "_billboard(placement_id='z-earlier',fraction=.4,longitude=.004)]); "
        "opportunities=_evaluate(_mobility(pack),scenario); "
        "attention=evaluate_spatial_attention(opportunities,seed=5); "
        "evaluation=evaluate_spatial_responses("
        "_response_input(scenario),scenario,opportunities,attention,"
        "agent_ids=('person-001',)); "
        "payload=b''.join(spatial_response_lines(evaluation)) + "
        "(canonical_json(spatial_response_state_document(evaluation))+'\\n').encode('utf-8'); "
        "print(sha256(payload).hexdigest())"
    )
    digests = []
    for hash_seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        digests.append(completed.stdout.strip())

    assert digests[0] == digests[1]
