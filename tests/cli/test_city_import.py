import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.city.loader import load_city_pack
from adlife.cli.app import app
from adlife.core.simulation.city_mobility import CityMobility
from tests.unit.city.test_osm_import import extract


def _args(source: Path, output: Path) -> list[str]:
    return [
        "city-import",
        str(source),
        "--output",
        str(output),
        "--city-id",
        "tehran-pilot",
        "--name",
        "تهران",
    ]


def _v2_args(source: Path, output: Path) -> list[str]:
    return [
        *_args(source, output),
        "--schema-version",
        "2",
        "--time-zone",
        "Asia/Tehran",
        "--source-date",
        "2026-09-29",
        "--source-version",
        "local-extract-1",
    ]


def test_city_import_writes_reusable_pack_with_clean_json_result(tmp_path: Path) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    document = extract()
    document["elements"][3]["tags"]["contact:email"] = "person@example.org"
    source.write_text(json.dumps(document), encoding="utf-8")
    result = CliRunner().invoke(app, ["--format", "json", *_args(source, output)])
    assert result.exit_code == 0, result.output
    message = json.loads(result.stdout)
    assert message["city_id"] == "tehran-pilot"
    assert message["nodes"] == 3
    assert message["roads"] == 3
    assert message["dropped_nodes"] == 0
    assert message["dropped_roads"] == 0
    assert message["pack_sha256"] == load_city_pack(output).fingerprint
    assert output.read_bytes().endswith(b"\n")
    assert b"contact:email" not in output.read_bytes()
    assert b"person@example.org" not in output.read_bytes()
    assert len(CityMobility(load_city_pack(output), seed=42, agent_count=5, days=7).frame(480)) == 5


def test_city_import_refuses_existing_output_without_changing_it(tmp_path: Path) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    source.write_text(json.dumps(extract()), encoding="utf-8")
    output.write_bytes(b"leave me alone")
    result = CliRunner().invoke(app, _args(source, output))
    assert result.exit_code == 3
    assert output.read_bytes() == b"leave me alone"
    assert "Traceback" not in result.output


def test_city_import_refuses_malformed_source_without_creating_pack(tmp_path: Path) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    source.write_text('{"elements":NaN,"api_key":"topsecret123"}', encoding="utf-8")
    result = CliRunner().invoke(app, ["--format", "json", *_args(source, output)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["exit_code"] == 2
    assert "topsecret123" not in result.output
    assert "Traceback" not in result.output
    assert not output.exists()


def test_city_import_uses_explicit_largest_component_flag(tmp_path: Path) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    document = extract()
    document["elements"].extend(
        [
            {"type": "node", "id": 30, "lat": 35.1, "lon": 51.1},
            {"type": "node", "id": 31, "lat": 35.1, "lon": 51.11},
            {"type": "way", "id": 40, "nodes": [30, 31], "tags": {"highway": "residential"}},
        ]
    )
    source.write_text(json.dumps(document), encoding="utf-8")
    refused = CliRunner().invoke(app, _args(source, output))
    assert refused.exit_code == 2
    assert not output.exists()
    result = CliRunner().invoke(
        app, ["--format", "json", *_args(source, output), "--largest-component"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dropped_roads"] == 1
    assert len(load_city_pack(output).roads) == 3


def test_city_import_publication_failure_leaves_no_partial_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    source.write_text(json.dumps(extract()), encoding="utf-8")

    def denied(source_path: str | os.PathLike[str], dest_path: str | os.PathLike[str]) -> None:
        raise OSError("disk refused")

    monkeypatch.setattr(os, "link", denied)
    result = CliRunner().invoke(app, _args(source, output))
    assert result.exit_code == 2
    assert not output.exists()
    assert list(tmp_path.glob(".city.json.*.tmp")) == []
    assert "disk refused" not in result.output


def test_city_import_v2_writes_geometry_pack_and_clean_quality_json(tmp_path: Path) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city-v2.json"
    source.write_text(json.dumps(extract()), encoding="utf-8")
    result = CliRunner().invoke(app, ["--format", "json", *_v2_args(source, output)])
    assert result.exit_code == 0, result.output
    message = json.loads(result.stdout)
    pack = load_city_pack(output)
    assert pack.schema_version == 2
    assert message == {
        "city_id": "tehran-pilot",
        "nodes": 3,
        "output": str(output),
        "pack_sha256": pack.fingerprint,
        "quality": {
            "dropped_nodes": 0,
            "dropped_roads": 0,
            "eligible_ways": 2,
            "excluded_ways": 1,
            "input_nodes": 3,
            "input_ways": 3,
            "retained_nodes": 3,
            "retained_roads": 3,
        },
        "roads": 3,
        "schema_version": 2,
        "source_sha256": pack.source.source_sha256,
    }


@pytest.mark.parametrize(
    "omitted",
    ["--time-zone", "--source-date", "--source-version"],
)
def test_city_import_v2_requires_every_provenance_option(tmp_path: Path, omitted: str) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city-v2.json"
    source.write_text(json.dumps(extract()), encoding="utf-8")
    args = _v2_args(source, output)
    index = args.index(omitted)
    del args[index : index + 2]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert not output.exists()


def test_city_import_refuses_unknown_schema_and_v2_options_with_v1(tmp_path: Path) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    source.write_text(json.dumps(extract()), encoding="utf-8")
    unknown = CliRunner().invoke(app, [*_args(source, output), "--schema-version", "3"])
    assert unknown.exit_code == 2
    assert not output.exists()
    mismatched = CliRunner().invoke(app, [*_args(source, output), "--source-date", "2026-09-29"])
    assert mismatched.exit_code == 2
    assert "Traceback" not in mismatched.output
    assert not output.exists()


def test_city_import_v2_never_uses_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    source = tmp_path / "overpass.json"
    output = tmp_path / "city-v2.json"
    source.write_text(json.dumps(extract()), encoding="utf-8")

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    result = CliRunner().invoke(app, _v2_args(source, output))
    assert result.exit_code == 0, result.output
    assert output.is_file()
