from __future__ import annotations

import json
import socket

import pytest
from typer.testing import CliRunner

from adlife.city.catalog import CityCatalogError, load_city_catalog
from adlife.cli.app import app


def expected_entry() -> dict[str, object]:
    return load_city_catalog().entries[0].model_dump(mode="json")


def test_city_catalog_help_lists_local_read_only_commands() -> None:
    result = CliRunner().invoke(app, ["city-catalog", "--help"])
    assert result.exit_code == 0, result.output
    assert "list" in result.output
    assert "show" in result.output
    assert "no download" in result.output.lower()


def test_city_catalog_list_has_stable_human_json_and_jsonl_output() -> None:
    human = CliRunner().invoke(app, ["city-catalog", "list"])
    assert human.exit_code == 0, human.output
    assert "fictional-grid-v2" in human.stdout
    assert "fictional-fixture" in human.stdout

    json_result = CliRunner().invoke(app, ["--format", "json", "city-catalog", "list"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.stdout) == {"cities": [expected_entry()]}

    jsonl = CliRunner().invoke(app, ["--format", "jsonl", "city-catalog", "list"])
    assert jsonl.exit_code == 0, jsonl.output
    assert [json.loads(line) for line in jsonl.stdout.splitlines()] == [expected_entry()]


def test_city_catalog_search_is_casefolded_and_no_match_is_clean() -> None:
    matched = CliRunner().invoke(
        app, ["--format", "json", "city-catalog", "list", "--search", "GRID V2"]
    )
    assert json.loads(matched.stdout) == {"cities": [expected_entry()]}
    missing = CliRunner().invoke(
        app, ["--format", "json", "city-catalog", "list", "--search", "not-present"]
    )
    assert missing.exit_code == 0
    assert json.loads(missing.stdout) == {"cities": []}


def test_city_catalog_show_exposes_qualification_and_unknown_is_input_error() -> None:
    shown = CliRunner().invoke(
        app, ["--format", "json", "city-catalog", "show", "fictional-grid-v2"]
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout) == expected_entry()
    unknown = CliRunner().invoke(app, ["--format", "json", "city-catalog", "show", "unknown-city"])
    assert unknown.exit_code == 2
    assert json.loads(unknown.stdout)["error"]["exit_code"] == 2
    assert "Traceback" not in unknown.output


def test_city_catalog_corruption_is_artifact_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def corrupt() -> object:
        raise CityCatalogError("city catalog failed integrity validation")

    monkeypatch.setattr("adlife.cli.commands.city_catalog.load_city_catalog", corrupt)
    result = CliRunner().invoke(app, ["--format", "json", "city-catalog", "list"])
    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"]["exit_code"] == 4
    assert "Traceback" not in result.output


def test_city_catalog_cli_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    result = CliRunner().invoke(app, ["--format", "json", "city-catalog", "list"])
    assert result.exit_code == 0, result.output
