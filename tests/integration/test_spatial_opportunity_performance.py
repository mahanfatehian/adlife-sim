"""Maximum C2 phone denominator performance and deterministic-count regression."""

from __future__ import annotations

import contextlib
import json
import os
import time

import pytest

from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from tests.unit.city.test_city_pack import load_pack, pack_data

if os.environ.get("ADLIFE_SKIP_LONG_TESTS") == "1":
    pytest.skip("ADLIFE_SKIP_LONG_TESTS is set", allow_module_level=True)

TARGET_SECONDS = 10.0
HARD_CEILING_SECONDS = 30.0


class _SuspendedCoverage:
    def __enter__(self) -> None:
        self._collector: object | None = None
        try:
            import coverage

            instance = coverage.Coverage.current()
            if instance is not None and instance._started:
                self._collector = instance
                instance.stop()
        except Exception:
            self._collector = None

    def __exit__(self, *exc_info: object) -> None:
        if self._collector is not None:
            self._collector.start()


def _maximum_phone_scenario() -> tuple[CityMobility, object]:
    pack = load_pack(pack_data())
    mobility = CityMobility(pack, seed=42, agent_count=30, days=7)
    campaigns = [
        {
            "campaign_id": f"campaign-{index:02d}",
            "name": f"Campaign {index}",
            "creative_sha256": f"{index % 16:x}" * 64,
        }
        for index in range(20)
    ]
    placements = [
        {
            "placement_id": f"phone-{index:02d}",
            "campaign_id": f"campaign-{index:02d}",
            "channel": "mobile-feed",
            "active_windows": [{"start_minute": 0, "end_minute": 10_080}],
            "frequency_cap_per_agent_per_day": 1,
            "opportunity_model": "keyed-activity-minute-v1",
            "eligible_activities": ["home", "commute", "work", "leisure"],
            "opportunity_probability_per_minute": 0.5,
        }
        for index in range(20)
    ]
    scenario = parse_spatial_campaign_scenario_json(
        json.dumps(
            {
                "schema_version": 1,
                "scenario_id": "maximum-phone",
                "name": "Maximum fictional phone denominator",
                "days": 7,
                "city_id": pack.city_id,
                "city_sha256": pack.fingerprint,
                "campaigns": campaigns,
                "placements": placements,
            }
        )
    )
    return mobility, scenario


def test_maximum_phone_opportunity_denominator_is_bounded_and_fast() -> None:
    mobility, scenario = _maximum_phone_scenario()
    override = os.environ.get("ADLIFE_SPATIAL_PERF_CEILING_SECONDS")
    ceiling = HARD_CEILING_SECONDS
    if override:
        with contextlib.suppress(ValueError):
            ceiling = max(ceiling, float(override))

    with _SuspendedCoverage():
        started = time.perf_counter()
        result = evaluate_spatial_opportunities(mobility, scenario)
        elapsed = time.perf_counter() - started

    assert result.counts.phone_eligible_agent_minute_count == 6_048_000
    assert result.counts.phone_successful_draw_count == 3_025_584
    assert result.counts.frequency_capped_candidate_count == 3_021_384
    assert result.counts.phone_opportunity_count == 4_200
    assert len(result.opportunities) == 4_200
    print(
        f"\nmaximum spatial phone evaluation: {elapsed:.2f}s "
        f"(target <{TARGET_SECONDS}s, ceiling <{ceiling:.1f}s), "
        f"{result.counts.phone_eligible_agent_minute_count} eligible agent-minutes, "
        f"{len(result.opportunities)} retained opportunities"
    )
    assert elapsed < ceiling, (
        f"maximum spatial phone evaluation took {elapsed:.2f}s, over {ceiling:.1f}s ceiling"
    )
