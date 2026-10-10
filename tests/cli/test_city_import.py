import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

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


@pytest.mark.parametrize("schema_version", [1, 2])
@pytest.mark.parametrize("failure", ["partial", "zero", "write", "flush", "fsync"])
def test_city_import_incomplete_write_or_sync_failure_never_publishes_a_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema_version: int, failure: str
) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    source_bytes = json.dumps(extract()).encode("utf-8")
    source.write_bytes(source_bytes)
    original_fdopen = os.fdopen

    def denied(*args: object) -> None:
        raise OSError("private-publication-diagnostic")

    @contextmanager
    def faulty_destination(descriptor: int, mode: str) -> Iterator[BinaryIO]:
        with original_fdopen(descriptor, mode) as destination:
            original_write = destination.write

            def write(contents: bytes) -> int:
                if failure == "partial":
                    return original_write(contents[: len(contents) // 2])
                if failure == "zero":
                    return 0
                raise OSError("private-publication-diagnostic")

            if failure in {"partial", "zero", "write"}:
                monkeypatch.setattr(destination, "write", write)
            elif failure == "flush":
                monkeypatch.setattr(destination, "flush", denied)
            yield destination

    monkeypatch.setattr(os, "fdopen", faulty_destination)
    if failure == "fsync":
        monkeypatch.setattr(os, "fsync", denied)
    args = _args(source, output) if schema_version == 1 else _v2_args(source, output)

    result = CliRunner().invoke(app, ["--format", "json", *args])

    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"]["exit_code"] == 2
    assert "private-publication-diagnostic" not in result.output
    assert "Traceback" not in result.output
    assert not output.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["overpass.json"]
    assert source.read_bytes() == source_bytes


def test_city_import_publication_conflict_preserves_the_winning_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "overpass.json"
    output = tmp_path / "city.json"
    source_bytes = json.dumps(extract()).encode("utf-8")
    source.write_bytes(source_bytes)
    original_link = os.link

    def competing_publication(
        source_path: str | os.PathLike[str], dest_path: str | os.PathLike[str]
    ) -> None:
        Path(dest_path).write_bytes(b"winning pack")
        original_link(source_path, dest_path)

    monkeypatch.setattr(os, "link", competing_publication)

    result = CliRunner().invoke(app, ["--format", "json", *_args(source, output)])

    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["error"]["exit_code"] == 3
    assert output.read_bytes() == b"winning pack"
    assert source.read_bytes() == source_bytes
    assert sorted(path.name for path in tmp_path.iterdir()) == ["city.json", "overpass.json"]
    assert "Traceback" not in result.output


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
