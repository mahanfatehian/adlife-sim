from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.unit.city.test_spatial_campaign_contract import spatial_scenario_data


def _loader_module():
    try:
        return importlib.import_module("adlife.city.spatial_loader")
    except ModuleNotFoundError:
        pytest.fail("bounded spatial campaign loader is not implemented")


def test_loader_reads_a_valid_local_spatial_scenario(tmp_path: Path) -> None:
    path = tmp_path / "spatial-campaign.json"
    path.write_text(json.dumps(spatial_scenario_data()), encoding="utf-8")

    loaded = _loader_module().load_spatial_campaign_scenario(path)

    assert loaded.scenario_id == "fictional-launch"
    assert len(loaded.campaigns) == 2
    assert len(loaded.placements) == 2


def test_loader_refuses_oversized_input_without_echoing_contents(tmp_path: Path) -> None:
    loader = _loader_module()
    path = tmp_path / "spatial-campaign.json"
    secret = "api_key=topsecret123"
    path.write_bytes((secret + "x" * loader.MAX_SPATIAL_CAMPAIGN_BYTES).encode())

    with pytest.raises(loader.SpatialCampaignError, match="too large") as caught:
        loader.load_spatial_campaign_scenario(path)

    assert secret not in str(caught.value)


@pytest.mark.parametrize("contents", [b"\xff", b"[]", b'{"schema_version":99}'])
def test_loader_refuses_malformed_documents_with_bounded_diagnostics(
    tmp_path: Path, contents: bytes
) -> None:
    loader = _loader_module()
    path = tmp_path / "spatial-campaign.json"
    path.write_bytes(contents)

    with pytest.raises(loader.SpatialCampaignError) as caught:
        loader.load_spatial_campaign_scenario(path)

    assert str(caught.value) in {
        "spatial campaign scenario must be UTF-8",
        "spatial campaign scenario failed schema or version validation",
    }


def test_loader_refuses_missing_file_without_exposing_path(tmp_path: Path) -> None:
    loader = _loader_module()
    missing = tmp_path / "private" / "spatial-campaign.json"

    with pytest.raises(loader.SpatialCampaignError, match="could not be read") as caught:
        loader.load_spatial_campaign_scenario(missing)

    assert str(missing) not in str(caught.value)
