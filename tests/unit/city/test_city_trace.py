"""A saved city run fingerprints every rendered core minute, not sampled frames."""

from dataclasses import asdict
from hashlib import sha256

from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace
from tests.unit.city.test_city_pack import load_pack, pack_data


def test_full_minute_trace_matches_the_documented_canonical_stream() -> None:
    mobility = CityMobility(load_pack(pack_data()), seed=42, agent_count=2, days=1)
    expected = sha256()
    for minute in range(1440):
        expected.update((canonical_json(mobility.frame_document(minute)) + "\n").encode("utf-8"))

    summary = summarize_city_trace(mobility)

    assert summary.frame_count == 1440
    assert summary.position_count == 2880
    assert summary.trace_sha256 == expected.hexdigest()
    assert (
        summary.agents_sha256
        == sha256(
            canonical_json({"agents": [asdict(agent) for agent in mobility.agents]}).encode("utf-8")
        ).hexdigest()
    )


def test_pack_input_order_does_not_change_trace_or_assignments() -> None:
    data = pack_data()
    nodes, roads = data["nodes"], data["roads"]
    assert isinstance(nodes, list) and isinstance(roads, list)
    permuted = {**data, "nodes": list(reversed(nodes)), "roads": list(reversed(roads))}
    original = CityMobility(load_pack(data), seed=42, agent_count=2, days=1)
    reordered = CityMobility(load_pack(permuted), seed=42, agent_count=2, days=1)

    assert summarize_city_trace(original) == summarize_city_trace(reordered)


def test_changing_seed_changes_frozen_assignments_or_trace() -> None:
    pack = load_pack(pack_data())
    first = summarize_city_trace(CityMobility(pack, seed=42, agent_count=3, days=1))
    second = summarize_city_trace(CityMobility(pack, seed=43, agent_count=3, days=1))

    assert (first.agents_sha256, first.trace_sha256) != (
        second.agents_sha256,
        second.trace_sha256,
    )


def test_changed_road_speed_changes_the_full_trace() -> None:
    data = pack_data()
    roads = data["roads"]
    assert isinstance(roads, list)
    roads[0] = {**roads[0], "kind": "motorway"}
    original = CityMobility(load_pack(pack_data()), seed=42, agent_count=3, days=1)
    changed = CityMobility(load_pack(data), seed=42, agent_count=3, days=1)

    assert summarize_city_trace(original).trace_sha256 != summarize_city_trace(changed).trace_sha256
