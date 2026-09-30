from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.city.catalog import select_catalog_city
from adlife.cli.app import app
from adlife.core.domain.city import CityPackDocument
from adlife.core.domain.city_places import CityPlaceSet, parse_city_place_set_json
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data


def _places_for(pack: CityPackDocument) -> CityPlaceSet:
    nodes = [node.node_id for node in pack.nodes]
    document = {
        "schema_version": 1,
        "city_id": pack.city_id,
        "city_sha256": pack.fingerprint,
        "name": "Operator-authored fictional places",
        "places": [
            {
                "place_id": "home-1",
                "kind": "home",
                "node_id": nodes[0],
                "label": "Fictional Home One",
                "provenance": {"method": "operator-authored-fictional"},
            },
            {
                "place_id": "home-2",
                "kind": "home",
                "node_id": nodes[1],
                "label": "Fictional Home Two",
                "provenance": {"method": "operator-authored-fictional"},
            },
            {
                "place_id": "work-1",
                "kind": "workplace",
                "node_id": nodes[2],
                "label": "Fictional Workplace",
                "provenance": {"method": "operator-authored-fictional"},
            },
            {
                "place_id": "leisure-1",
                "kind": "leisure",
                "node_id": nodes[2],
                "label": "Fictional Leisure Point",
                "provenance": {"method": "operator-authored-fictional"},
            },
        ],
    }
    return parse_city_place_set_json(json.dumps(document))


def write_city_place_inputs(tmp_path: Path) -> tuple[Path, Path, CityPlaceSet]:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    pack_path = tmp_path / "city.json"
    places_path = tmp_path / "places.json"
    pack_path.write_text(canonical_json(pack) + "\n", encoding="utf-8")
    places_path.write_text(canonical_json(places) + "\n", encoding="utf-8")
    return pack_path, places_path, places


def test_city_places_validate_has_clean_human_and_json_results(tmp_path: Path) -> None:
    pack, places, model = write_city_place_inputs(tmp_path)
    human = CliRunner().invoke(
        app,
        ["city-places", "validate", str(places), str(pack), "--agents", "2"],
    )
    assert human.exit_code == 0, human.output
    assert "valid synthetic place set" in human.stdout
    assert model.fingerprint in human.stdout

    machine = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-places",
            "validate",
            str(places),
            str(pack),
            "--agents",
            "2",
        ],
    )
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout) == {
        "agent_count": 2,
        "assignment_count": 2,
        "city_id": "sample-city-v2",
        "city_sha256": model.city_sha256,
        "place_count": 5,
        "place_set_sha256": model.fingerprint,
        "valid": True,
    }


def test_city_places_validate_catalog_selection_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = select_catalog_city("fictional-grid-v2")
    places = _places_for(pack)
    path = tmp_path / "places.json"
    path.write_text(canonical_json(places) + "\n", encoding="utf-8")

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-places",
            "validate",
            str(path),
            "--city-id",
            "fictional-grid-v2",
            "--agents",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["city_id"] == "fictional-grid-v2"


def test_city_places_validate_refuses_selectors_binding_and_capacity_cleanly(
    tmp_path: Path,
) -> None:
    pack, places, _ = write_city_place_inputs(tmp_path)
    common = ["--format", "json", "city-places", "validate", str(places)]
    cases = [
        common,
        [*common, str(pack), "--city-id", "fictional-grid-v2"],
        [*common, str(pack), "--agents", "3"],
    ]
    for arguments in cases:
        result = CliRunner().invoke(app, arguments)
        assert result.exit_code == 2, result.output
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
        assert "Traceback" not in result.output

    wrong = mobility_place_set().model_copy(update={"city_sha256": "f" * 64})
    places.write_text(canonical_json(wrong) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, [*common, str(pack), "--agents", "2"])
    assert result.exit_code == 2
    assert "city fingerprint" in result.stderr
    assert "Traceback" not in result.output
