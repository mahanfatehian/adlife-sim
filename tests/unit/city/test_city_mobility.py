import pytest

from adlife.core.domain.city import CityNode, CityPackV2
from adlife.core.domain.city_places import CityPlaceSet, parse_city_place_set_json
from adlife.core.simulation.city_mobility import CityMobility, shortest_path
from adlife.core.simulation.city_trace import summarize_city_trace
from adlife.core.simulation.rng import RandomOracle
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_city_places import place_set_data


def mobility_place_set() -> CityPlaceSet:
    data = place_set_data()
    places = data["places"]
    assert isinstance(places, list)
    places.append(
        {
            "place_id": "leisure-c",
            "kind": "leisure",
            "node_id": "c",
            "label": "Copper Park",
            "provenance": {"method": "operator-authored-fictional"},
        }
    )
    return parse_city_place_set_json(__import__("json").dumps(data))


def test_directed_one_way_paths() -> None:
    data = pack_data()
    data["roads"] = [
        {
            "road_id": "ab",
            "source_node": "a",
            "target_node": "b",
            "kind": "residential",
            "one_way": True,
        },
        {
            "road_id": "bc",
            "source_node": "b",
            "target_node": "c",
            "kind": "residential",
            "one_way": True,
        },
        {
            "road_id": "ca",
            "source_node": "c",
            "target_node": "a",
            "kind": "residential",
            "one_way": True,
        },
    ]
    pack = load_pack(data)
    assert shortest_path(pack, "a", "c").road_ids == ("ab", "bc")
    assert shortest_path(pack, "c", "a").road_ids == ("ca",)


def test_equal_travel_time_uses_stable_road_id_tie_break() -> None:
    data = pack_data()
    data["nodes"] = [
        {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
        {"node_id": "b", "longitude": 0.01, "latitude": 0.01},
        {"node_id": "c", "longitude": 0.01, "latitude": -0.01},
        {"node_id": "d", "longitude": 0.02, "latitude": 0.0},
    ]
    data["roads"] = [
        {"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "residential"},
        {"road_id": "ac", "source_node": "a", "target_node": "c", "kind": "residential"},
        {"road_id": "bd", "source_node": "b", "target_node": "d", "kind": "residential"},
        {"road_id": "cd", "source_node": "c", "target_node": "d", "kind": "residential"},
    ]
    assert shortest_path(load_pack(data), "a", "d").road_ids == ("ab", "bd")


def test_v2_curved_road_length_direction_and_geometry_are_core_owned() -> None:
    pack = load_pack_v2(pack_v2_data())
    assert isinstance(pack, CityPackV2)
    forward = shortest_path(pack, "a", "b")
    backward = shortest_path(pack, "b", "a")
    expected_forward = ((0.0, 0.0), (0.005, 0.004), (0.01, 0.0))
    assert forward.road_ids == ("ab",)
    assert forward.directions == ("forward",)
    assert forward.geometry == (expected_forward,)
    assert forward.distance_meters > 1_400
    assert backward.road_ids == ("ab",)
    assert backward.directions == ("backward",)
    assert backward.geometry == (tuple(reversed(expected_forward)),)
    assert backward.distance_meters == pytest.approx(forward.distance_meters)


def test_v2_prohibited_reverse_direction_is_not_added_to_graph() -> None:
    data = pack_v2_data()
    roads = data["roads"]
    assert isinstance(roads, list) and isinstance(roads[0], dict)
    roads[0]["directions"] = ["forward"]
    pack = load_pack_v2(data)
    assert isinstance(pack, CityPackV2)
    assert shortest_path(pack, "a", "b").road_ids == ("ab",)
    assert shortest_path(pack, "b", "a").road_ids != ("ab",)


def test_v2_agent_position_follows_curve_and_route_document_exposes_geometry() -> None:
    pack = load_pack_v2(pack_v2_data())
    assert isinstance(pack, CityPackV2)
    simulation = next(
        candidate
        for seed in range(100)
        if (candidate := CityMobility(pack, seed=seed, agent_count=1, days=1)).agents[0].home_node
        == "a"
        and candidate.agents[0].work_node == "b"
    )
    position = simulation.frame(481)[0]
    route = simulation.route_document("person-001", 481)
    assert position.road_id == "ab"
    assert position.latitude > 0
    assert route["directions"] == ["forward"]
    assert route["geometry"] == [
        [
            {"longitude": 0.0, "latitude": 0.0},
            {"longitude": 0.005, "latitude": 0.004},
            {"longitude": 0.01, "latitude": 0.0},
        ]
    ]
    assert simulation.metadata()["model"] == "illustrative-road-mobility-v2"


def test_v1_trace_digest_stays_compatible_after_v2_routing_support() -> None:
    summary = summarize_city_trace(
        CityMobility(load_pack(pack_data()), seed=42, agent_count=2, days=1)
    )
    assert (
        summary.trace_sha256 == "2d7996c311c694f20adc244f4fada1170f355bbd1c8ea50ef804ab0b743d5583"
    )
    assert (
        summary.agents_sha256 == "f563085e0ac5f8cdb32598b72f6b809b7608a2741e2a25c907b1dc771bc9695c"
    )


def test_route_endpoints_are_validated() -> None:
    pack = load_pack(pack_data())
    assert shortest_path(pack, "a", "a").road_ids == ()
    with pytest.raises(ValueError, match="not a city node"):
        shortest_path(pack, "a", "missing")


def test_same_seed_and_permuted_pack_make_identical_trace() -> None:
    data = pack_data()
    nodes, roads = data["nodes"], data["roads"]
    assert isinstance(nodes, list) and isinstance(roads, list)
    permuted = {**data, "nodes": list(reversed(nodes)), "roads": list(reversed(roads))}
    first = CityMobility(load_pack(data), seed=42, agent_count=8, days=2)
    second = CityMobility(load_pack(permuted), seed=42, agent_count=8, days=2)
    assert first.metadata() == second.metadata()
    for minute in (0, 480, 481, 540, 1020, 1021, 1439, 1440, 1910):
        assert first.frame(minute) == second.frame(minute)


def test_positions_remain_on_the_road_or_at_nodes() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=7, agent_count=12, days=1)
    roads = {road.road_id: road for road in simulation.pack.roads}
    nodes = {node.node_id: node for node in simulation.pack.nodes}
    for minute in range(0, 1440):
        for position in simulation.frame(minute):
            assert -90 <= position.latitude <= 90
            assert -180 <= position.longitude <= 180
            if position.road_id is not None:
                road = roads[position.road_id]
                start, end = nodes[road.source_node], nodes[road.target_node]
                assert (
                    min(start.latitude, end.latitude) - 1e-12
                    <= position.latitude
                    <= max(start.latitude, end.latitude) + 1e-12
                )
                assert (
                    min(start.longitude, end.longitude) - 1e-12
                    <= position.longitude
                    <= max(start.longitude, end.longitude) + 1e-12
                )


def test_weekend_has_leisure_not_work() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=7, agent_count=3, days=7)
    saturday = 5 * 1440
    assert all(position.activity != "work" for position in simulation.frame(saturday + 13 * 60))
    assert any(position.activity == "leisure" for position in simulation.frame(saturday + 13 * 60))


@pytest.mark.parametrize("agent_count,days", [(0, 1), (251, 1), (1, 0), (1, 32)])
def test_configuration_bounds(agent_count: int, days: int) -> None:
    with pytest.raises(ValueError):
        CityMobility(load_pack(pack_data()), seed=1, agent_count=agent_count, days=days)


def test_out_of_range_minute_refused() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=1, agent_count=1, days=1)
    with pytest.raises(ValueError):
        simulation.frame(1440)
    with pytest.raises(ValueError):
        simulation.frame(True)


def test_boolean_seed_is_not_an_integer_configuration() -> None:
    with pytest.raises(ValueError, match="seed"):
        CityMobility(load_pack(pack_data()), seed=True, agent_count=1, days=1)


def test_maximum_pilot_window_and_population_are_sampleable() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=9, agent_count=250, days=31)
    final_frame = simulation.frame(31 * 1440 - 1)
    assert len(final_frame) == 250
    assert final_frame[0].agent_id == "person-001"
    assert final_frame[-1].agent_id == "person-250"


def test_geographic_labels_support_southern_western_hemispheres() -> None:
    data = pack_data()
    nodes = data["nodes"]
    assert isinstance(nodes, list)
    for node in nodes:
        assert isinstance(node, dict)
        node["longitude"] = float(node["longitude"]) - 74
        node["latitude"] = float(node["latitude"]) - 35
    simulation = CityMobility(load_pack(data), seed=1, agent_count=1, days=1)
    frame = simulation.frame_document(0)
    positions = frame["positions"]
    assert isinstance(positions, list)
    assert "° S / " in positions[0]["coordinate_label"]
    assert positions[0]["coordinate_label"].endswith("° W")


def test_trip_that_cannot_finish_before_midnight_is_refused() -> None:
    data = pack_data()
    data["nodes"] = [
        {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
        {"node_id": "b", "longitude": 0.0, "latitude": 2.0},
    ]
    data["roads"] = [
        {"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "residential"}
    ]
    with pytest.raises(ValueError, match="trip cannot finish before midnight"):
        CityMobility(load_pack(data), seed=1, agent_count=1, days=1)


def test_outbound_arriving_after_fixed_return_time_does_not_skip_return_travel() -> None:
    data = pack_data()
    data["nodes"] = [
        {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
        {"node_id": "b", "longitude": 0.0, "latitude": 1.5},
    ]
    data["roads"] = [
        {
            "road_id": "slow",
            "source_node": "a",
            "target_node": "b",
            "kind": "residential",
            "one_way": True,
        },
        {
            "road_id": "fast",
            "source_node": "b",
            "target_node": "a",
            "kind": "motorway",
            "one_way": True,
        },
    ]
    pack = load_pack(data)
    candidate = next(
        seed
        for seed in range(100)
        if RandomOracle(seed).uniform("city-home", "person-001", 0, 0) < 0.5
    )
    simulation = CityMobility(pack, seed=candidate, agent_count=1, days=1)
    assert simulation.agents[0].home_node == "a"
    arrival = 480 + shortest_path(pack, "a", "b").duration_minutes
    before = simulation.frame(int(arrival) - 1)[0]
    after = simulation.frame(int(arrival) + 1)[0]
    assert abs(after.latitude - before.latitude) < 0.02
    assert after.road_id == "fast"


def test_mobility_builds_weighted_road_graph_once_for_all_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adlife.core.simulation.city_mobility as mobility

    data = pack_data()
    data["nodes"] = [
        {"node_id": f"n-{index:03d}", "longitude": index * 0.0001, "latitude": 0.0}
        for index in range(100)
    ]
    data["roads"] = [
        {
            "road_id": f"r-{index:03d}",
            "source_node": f"n-{index:03d}",
            "target_node": f"n-{index + 1:03d}",
            "kind": "residential",
        }
        for index in range(99)
    ]
    calls = 0
    original = mobility._length_meters

    def counted(start: CityNode, end: CityNode) -> float:
        nonlocal calls
        calls += 1
        return original(start, end)

    monkeypatch.setattr(mobility, "_length_meters", counted)
    simulation = CityMobility(load_pack(data), seed=42, agent_count=20, days=1)
    assert len(simulation.agents) == 20
    assert calls <= 99


def test_selected_agent_route_uses_core_directed_paths_for_each_day() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=1, days=7)
    agent = simulation.agents[0]
    outbound = simulation.frame_document(8 * 60, selected_agent_id=agent.agent_id)["route"]
    inbound = simulation.frame_document(18 * 60, selected_agent_id=agent.agent_id)["route"]
    weekend = simulation.frame_document(5 * 1440 + 12 * 60, selected_agent_id=agent.agent_id)[
        "route"
    ]
    assert outbound["direction"] == "outbound"
    assert outbound["node_ids"][0] == agent.home_node
    assert outbound["node_ids"][-1] == agent.work_node
    assert inbound["direction"] == "return"
    assert inbound["node_ids"][0] == agent.work_node
    assert inbound["node_ids"][-1] == agent.home_node
    assert weekend["node_ids"][-1] == agent.leisure_node
    with pytest.raises(ValueError, match="unknown city agent"):
        simulation.frame_document(0, selected_agent_id="person-999")


def test_place_set_binding_and_node_references_are_refused_before_frames() -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    with pytest.raises(ValueError, match="city identifier"):
        CityMobility(
            pack,
            seed=42,
            agent_count=2,
            days=1,
            places=places.model_copy(update={"city_id": "another-city"}),
        )
    with pytest.raises(ValueError, match="city fingerprint"):
        CityMobility(
            pack,
            seed=42,
            agent_count=2,
            days=1,
            places=places.model_copy(update={"city_sha256": "f" * 64}),
        )
    changed = places.places[0].model_copy(update={"node_id": "missing"})
    with pytest.raises(ValueError, match="unknown city node"):
        CityMobility(
            pack,
            seed=42,
            agent_count=2,
            days=1,
            places=places.model_copy(update={"places": (changed, *places.places[1:])}),
        )


def test_place_assignment_requires_capacity_and_non_home_destinations() -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    with pytest.raises(ValueError, match="distinct home"):
        CityMobility(pack, seed=42, agent_count=3, days=1, places=places)

    work = next(place for place in places.places if place.kind == "workplace")
    work_at_a = work.model_copy(update={"node_id": "a"})
    constrained = places.model_copy(
        update={
            "places": tuple(
                work_at_a if place.place_id == work.place_id else place for place in places.places
            )
        }
    )
    with pytest.raises(ValueError, match="workplace different from home"):
        CityMobility(pack, seed=42, agent_count=2, days=1, places=constrained)


def test_place_assignments_name_the_exact_routed_nodes() -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    simulation = CityMobility(pack, seed=42, agent_count=2, days=7, places=places)
    by_id = {place.place_id: place for place in places.places}

    assert simulation.places == places
    assert len(simulation.place_assignments) == 2
    assert len({assignment.home_place_id for assignment in simulation.place_assignments}) == 2
    for agent, assignment in zip(simulation.agents, simulation.place_assignments, strict=True):
        assert assignment.agent_id == agent.agent_id
        assert by_id[assignment.home_place_id].node_id == agent.home_node
        assert by_id[assignment.work_place_id].node_id == agent.work_node
        assert by_id[assignment.leisure_place_id].node_id == agent.leisure_node
        assert assignment.home_node == agent.home_node
        assert assignment.work_node == agent.work_node
        assert assignment.leisure_node == agent.leisure_node
        weekday = simulation.route_document(agent.agent_id, 8 * 60)
        weekend = simulation.route_document(agent.agent_id, 5 * 1440 + 11 * 60)
        assert weekday["node_ids"] == list(
            shortest_path(pack, agent.home_node, agent.work_node).node_ids
        )
        assert weekend["node_ids"] == list(
            shortest_path(pack, agent.home_node, agent.leisure_node).node_ids
        )

    assert simulation.place_assignment_document() == {
        "assignments": [
            {
                "agent_id": assignment.agent_id,
                "home_place_id": assignment.home_place_id,
                "work_place_id": assignment.work_place_id,
                "leisure_place_id": assignment.leisure_place_id,
                "home_node": assignment.home_node,
                "work_node": assignment.work_node,
                "leisure_node": assignment.leisure_node,
            }
            for assignment in simulation.place_assignments
        ]
    }
    assert simulation.metadata()["model"] == "illustrative-road-mobility-v3"
    assert simulation.metadata()["place_set_sha256"] == places.fingerprint


def test_legacy_mobility_exposes_no_place_assignments_and_keeps_v2_identity() -> None:
    simulation = CityMobility(load_pack_v2(pack_v2_data()), seed=42, agent_count=2, days=1)
    assert simulation.places is None
    assert simulation.place_assignments == ()
    assert simulation.place_assignment_document() == {"assignments": []}
    assert simulation.metadata()["model"] == "illustrative-road-mobility-v2"
