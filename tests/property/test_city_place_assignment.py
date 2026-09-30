import json

from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.city_places import parse_city_place_set_json
from adlife.core.simulation.city_mobility import CityMobility
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data


@given(st.permutations((0, 1, 2, 3, 4)))
@settings(max_examples=30)
def test_place_input_order_cannot_change_assignments(permutation: list[int]) -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    payload = places.model_dump(mode="json")
    original = payload["places"]
    assert isinstance(original, list)
    payload["places"] = [original[index] for index in permutation]
    reordered = parse_city_place_set_json(json.dumps(payload))

    first = CityMobility(pack, seed=7643, agent_count=2, days=7, places=places)
    second = CityMobility(pack, seed=7643, agent_count=2, days=7, places=reordered)
    assert first.place_assignments == second.place_assignments
    assert first.agents == second.agents
    assert first.metadata() == second.metadata()
    for minute in (0, 480, 1020, 5 * 1440 + 660, 7 * 1440 - 1):
        assert first.frame(minute) == second.frame(minute)
