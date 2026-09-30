import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.city.run_store import CityRunStore
from adlife.cli.app import app
from adlife.core.domain.serialization import canonical_json
from tests.cli.test_city_places_cli import write_city_place_inputs
from tests.unit.city.test_city_pack import load_pack, pack_data


def _pack(tmp_path: Path) -> Path:
    path = tmp_path / "pack.json"
    path.write_text(canonical_json(load_pack(pack_data())) + "\n", encoding="utf-8")
    return path


def test_city_run_creates_saved_artifact_with_clean_json(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "study",
            "--agents",
            "2",
            "--days",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["run_id"] == "study"
    assert document["frame_count"] == 1440
    assert document["position_count"] == 2880
    assert "_lines" not in document
    assert (tmp_path / "city-runs" / "study" / "run.json").is_file()


def test_city_run_refuses_duplicate_without_changing_manifest(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    args = [
        "city-run",
        str(pack),
        "--output-root",
        str(tmp_path),
        "--run-id",
        "study",
        "--agents",
        "2",
        "--days",
        "1",
    ]
    assert CliRunner().invoke(app, args).exit_code == 0
    manifest = tmp_path / "city-runs" / "study" / "run.json"
    before = manifest.read_bytes()
    duplicate = CliRunner().invoke(app, ["--format", "json", *args])
    assert duplicate.exit_code == 3
    assert json.loads(duplicate.stdout)["error"]["exit_code"] == 3
    assert manifest.read_bytes() == before
    assert "Traceback" not in duplicate.output


def test_city_run_refuses_invalid_pack_bounds_and_run_id(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    bad_pack = tmp_path / "bad.json"
    bad_pack.write_text('{"api_key":"topsecret123"', encoding="utf-8")
    for extra in (
        [str(bad_pack)],
        [str(pack), "--agents", "31"],
        [str(pack), "--days", "8"],
        [str(pack), "--run-id", "../escape"],
    ):
        result = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "city-run",
                "--output-root",
                str(tmp_path),
                "--run-id",
                "study",
                *extra,
            ],
        )
        assert result.exit_code == 2, result.output
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
        assert "topsecret123" not in result.output
        assert "Traceback" not in result.output
    assert not (tmp_path / "city-runs" / "study").exists()


def test_city_run_interrupt_exits_130_without_an_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = _pack(tmp_path)

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("adlife.cli.commands.city_run.create_city_run", interrupt)
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "study",
        ],
    )
    assert result.exit_code == 130
    assert json.loads(result.stdout)["error"]["exit_code"] == 130
    assert not (tmp_path / "city-runs" / "study").exists()


def test_city_run_can_freeze_a_verified_catalog_pack(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            "--city-id",
            "fictional-grid-v2",
            "--output-root",
            str(tmp_path),
            "--run-id",
            "catalog-study",
            "--agents",
            "2",
            "--days",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    stored = CityRunStore(tmp_path).load("catalog-study")
    assert stored.pack.city_id == "fictional-grid-v2"
    assert stored.manifest.schema_version == 2


def test_city_run_requires_exactly_one_pack_selector(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    common = [
        "city-run",
        "--output-root",
        str(tmp_path),
        "--run-id",
        "study",
        "--agents",
        "2",
        "--days",
        "1",
    ]
    neither = CliRunner().invoke(app, common)
    both = CliRunner().invoke(app, [*common, str(pack), "--city-id", "fictional-grid-v2"])
    unknown = CliRunner().invoke(app, [*common, "--city-id", "unknown-city"])
    assert neither.exit_code == both.exit_code == unknown.exit_code == 2
    assert "Traceback" not in neither.output + both.output + unknown.output
    assert not (tmp_path / "city-runs" / "study").exists()


def test_city_run_and_replay_expose_frozen_place_evidence(tmp_path: Path) -> None:
    pack, places, place_set = write_city_place_inputs(tmp_path)
    created = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--places",
            str(places),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "place-study",
            "--agents",
            "2",
            "--days",
            "7",
        ],
    )
    assert created.exit_code == 0, created.output
    document = json.loads(created.stdout)
    assert document["run_schema_version"] == 3
    assert document["place_set_sha256"] == place_set.fingerprint
    assert len(document["place_assignments_sha256"]) == 64

    stored = CityRunStore(tmp_path).load("place-study")
    assert stored.manifest.schema_version == 3
    assert (stored.directory / "inputs" / "places.json").is_file()
    replay = CliRunner().invoke(
        app,
        ["--format", "json", "city-replay", str(tmp_path), "place-study"],
    )
    assert replay.exit_code == 0, replay.output
    replayed = json.loads(replay.stdout)
    assert replayed["place_set_sha256"] == place_set.fingerprint
    assert replayed["place_assignments_sha256"] == document["place_assignments_sha256"]
