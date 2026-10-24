"""Every packaged town has a guided billboard spot that real traffic passes and can see.

The guided setup page keeps one billboard placement per town in guide-setup.js. These
checks read that table and run the same draft the page would send, so a spot on a road
nobody uses, or a sign facing away from its traffic, fails here instead of in a demo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import adlife
from adlife.city.catalog import load_verified_catalog_cities
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_transport import parse_workbench_http_draft
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities

SETUP_SCRIPT = (
    Path(adlife.__file__).resolve().parent / "city" / "static" / "guide" / "guide-setup.js"
)
_SPOT = re.compile(
    r'"(?P<city>[a-z0-9-]+)": Object\.freeze\(\{\s*'
    r'road_id: "(?P<road>[a-z0-9-]+)",\s*'
    r'travel_direction: "(?P<direction>forward|backward)",\s*'
    r'side: "(?P<side>left|right)",\s*'
    r"orientation_degrees: (?P<heading>[0-9]+),\s*\}\)"
)


def _spots() -> dict[str, dict[str, Any]]:
    source = SETUP_SCRIPT.read_text(encoding="utf-8")
    table = source[source.index("const ROADSIDE = Object.freeze({") :]
    table = table[: table.index("\n});") + 4]
    return {
        match["city"]: {
            "road_id": match["road"],
            "travel_direction": match["direction"],
            "side": match["side"],
            "orientation_degrees": int(match["heading"]),
        }
        for match in _SPOT.finditer(table)
    }


def _draft(city_id: str, spot: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    templates = {
        template.persona_id: template.model_dump(mode="json")
        for template in load_persona_template_catalog().templates
    }
    personas = [{**templates[persona_id], "count": count} for persona_id, count in counts.items()]
    return {
        "schema_version": 4,
        "city_id": city_id,
        "scenario": {
            "scenario_id": "guided-device",
            "name": "Guided demo study - Lumen Pocket",
            "campaign": {
                "campaign_id": "fictional-launch",
                "name": "Lumen Pocket campaign",
                "creative_template_id": "fictional-device-launch-v1",
                "target_interests": ["commuting", "technology"],
                "relative_price": 1.25,
            },
            "phone": None,
            "roadside": {
                **spot,
                "road_fraction": 0.5,
                "max_view_distance_meters": 120,
                "frequency_cap_per_agent_per_day": 2,
                "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 1140}],
            },
        },
        "population": {"personas": personas},
        "settings": {
            "run_id": "guided-billboard-check",
            "agent_count": sum(counts.values()),
            "days": 1,
            "seed": "42",
            "response_mode": "deterministic-rules",
        },
        "cognition": {
            "effect_scope": "bounded-signals-v1",
            "mode": "mock",
            "provider_profile_id": None,
            "provider_revision_sha256": None,
            "funded_response_limit": 60,
        },
    }


def test_the_guided_billboard_table_covers_exactly_the_packaged_towns() -> None:
    spots = _spots()
    packs = {city.entry.city_id: city.pack for city in load_verified_catalog_cities()}
    assert set(spots) == set(packs)
    for city_id, spot in spots.items():
        road = next(item for item in packs[city_id].roads if item.road_id == spot["road_id"])
        assert road.directions == ("forward", "backward"), city_id
        assert road.kind in {"primary", "secondary"}, city_id
        assert 0 <= spot["orientation_degrees"] < 360


@pytest.mark.parametrize(
    "counts",
    [
        {"persona-001": 4, "persona-002": 4},
        {f"persona-{index:03d}": 3 for index in range(1, 11)},
    ],
    ids=["two-contrasting-groups", "all-personas"],
)
@pytest.mark.parametrize("city_id", sorted(_spots()))
def test_each_town_billboard_is_passed_and_seen_by_the_guided_audience(
    city_id: str, counts: dict[str, int]
) -> None:
    validated = construct_workbench_run(
        parse_workbench_http_draft(_draft(city_id, _spots()[city_id], counts))
    )
    evaluation = evaluate_spatial_opportunities(validated.mobility, validated.scenario)
    assert evaluation.counts.roadside_matching_traversal_count > 0
    assert evaluation.counts.roadside_opportunity_count > 0


@pytest.mark.parametrize("city_id", [city.entry.city_id for city in load_verified_catalog_cities()])
def test_every_packaged_town_routes_thirty_people_well_within_a_day(city_id: str) -> None:
    pack = next(
        city.pack for city in load_verified_catalog_cities() if city.entry.city_id == city_id
    )
    for seed in (42, 43, 44):
        mobility = CityMobility(pack, seed=seed, agent_count=30, days=7)
        longest = max(
            traversal.end_minute - traversal.start_minute
            for day in range(7)
            for traversal in mobility.road_traversals(day)
        )
        assert longest < 60
        trips = [
            path.duration_minutes
            for agent in mobility.agents
            for path in (
                mobility._paths[agent.home_node, agent.work_node],
                mobility._paths[agent.home_node, agent.leisure_node],
            )
        ]
        assert max(trips) < 120
