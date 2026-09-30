from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from adlife.city.catalog import select_catalog_city
from adlife.cli.app import app
from adlife.core.domain.city import CityPackDocument
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    parse_spatial_campaign_scenario_json,
)
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data
from tests.unit.city.test_spatial_campaign_contract import spatial_scenario_data

runner = CliRunner()


def _scenario_for(pack: CityPackDocument, *, catalog: bool = False) -> SpatialCampaignScenario:
    data = spatial_scenario_data()
    data["city_id"] = pack.city_id
    data["city_sha256"] = pack.fingerprint
    billboard: dict[str, Any] = data["placements"][0]
    if catalog:
        billboard.update(
            road_id="north-west",
            longitude=0.02,
            latitude=0.06,
        )
    return parse_spatial_campaign_scenario_json(json.dumps(data))


def _write_local_inputs(tmp_path: Path) -> tuple[Path, Path, SpatialCampaignScenario]:
    pack = load_pack_v2(pack_v2_data())
    scenario = _scenario_for(pack)
    pack_path = tmp_path / "city.json"
    scenario_path = tmp_path / "spatial-campaign.json"
    pack_path.write_text(canonical_json(pack) + "\n", encoding="utf-8")
    scenario_path.write_text(canonical_json(scenario) + "\n", encoding="utf-8")
    return pack_path, scenario_path, scenario


def test_city_campaign_validate_has_clean_human_and_json_results(tmp_path: Path) -> None:
    pack, scenario_path, scenario = _write_local_inputs(tmp_path)
    human = runner.invoke(app, ["city-campaign", "validate", str(scenario_path), str(pack)])
    assert human.exit_code == 0, human.output
    assert "valid spatial campaign scenario" in human.stdout
    assert scenario.fingerprint in human.stdout

    machine = runner.invoke(
        app,
        [
            "--format",
            "json",
            "city-campaign",
            "validate",
            str(scenario_path),
            str(pack),
        ],
    )
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout) == {
        "billboard_count": 1,
        "campaign_count": 2,
        "city_id": "sample-city-v2",
        "city_sha256": scenario.city_sha256,
        "max_billboard_binding_error_meters": 0.0,
        "phone_count": 1,
        "placement_count": 2,
        "scenario_id": "fictional-launch",
        "scenario_sha256": scenario.fingerprint,
        "valid": True,
    }


def test_city_campaign_validate_catalog_selection_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = select_catalog_city("fictional-grid-v2")
    scenario = _scenario_for(pack, catalog=True)
    path = tmp_path / "spatial-campaign.json"
    path.write_text(canonical_json(scenario) + "\n", encoding="utf-8")

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "city-campaign",
            "validate",
            str(path),
            "--city-id",
            "fictional-grid-v2",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["city_id"] == "fictional-grid-v2"
    assert document["scenario_sha256"] == scenario.fingerprint


def test_city_campaign_validate_refuses_selectors_and_bad_binding_cleanly(
    tmp_path: Path,
) -> None:
    pack, scenario_path, scenario = _write_local_inputs(tmp_path)
    common = ["--format", "json", "city-campaign", "validate", str(scenario_path)]
    for arguments in (common, [*common, str(pack), "--city-id", "fictional-grid-v2"]):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 2, result.output
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
        assert "Traceback" not in result.output

    wrong = scenario.model_copy(update={"city_sha256": "f" * 64})
    scenario_path.write_text(canonical_json(wrong) + "\n", encoding="utf-8")
    result = runner.invoke(app, [*common, str(pack)])
    assert result.exit_code == 2, result.output
    assert "city fingerprint" in result.stderr
    assert "Traceback" not in result.output


def test_city_campaign_help_describes_validation_only_offline_boundary() -> None:
    result = runner.invoke(app, ["city-campaign", "--help"])
    assert result.exit_code == 0, result.output
    assert "validate" in result.stdout
    assert "network" in result.stdout.lower()
    assert "run" not in result.stdout.lower().split("commands", maxsplit=1)[-1]
