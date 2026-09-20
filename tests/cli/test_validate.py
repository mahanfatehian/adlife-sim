"""`adlife validate`: the gate a project passes before anything runs it.

Validation is the user's first feedback loop, so it is pinned in both modes: a valid
project exits zero quietly, and every broken input exits 2 with an error object that
names the file and the defect without echoing the file's contents.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def test_valid_project_exits_zero_and_reports_valid(tmp_path: Path) -> None:
    root = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["valid"] is True


def test_broken_yaml_exits_two_with_an_error_object(tmp_path: Path) -> None:
    root = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0
    (root / "adlife.yaml").write_text("simulation: [unclosed", encoding="utf-8")

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 2
    document = json.loads(result.stdout)
    assert document["valid"] is False
    assert document["errors"]
    assert "adlife.yaml" in document["errors"][0]


def test_missing_required_keys_exits_two(tmp_path: Path) -> None:
    root = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0
    (root / "adlife.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 2
    document = json.loads(result.stdout)
    assert document["valid"] is False


def test_broken_campaign_exits_two(tmp_path: Path) -> None:
    root = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0
    campaign = root / "campaigns" / "demo-phone.yaml"
    document = campaign.read_text(encoding="utf-8").replace("amount: 850.0", "amount: -5.0")
    campaign.write_text(document, encoding="utf-8")

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 2
    document = json.loads(result.stdout)
    assert document["valid"] is False
    assert any("demo-phone" in error for error in document["errors"])


def test_persian_project_name_validates(tmp_path: Path) -> None:
    name = "مطالعه"
    root = tmp_path / name
    assert runner.invoke(app, ["init", name, "--parent", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["valid"] is True


def test_missing_project_directory_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--format", "json", "validate", str(tmp_path / "does-not-exist")])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["valid"] is False


def test_human_mode_reports_errors_concisely(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "does-not-exist")])
    assert result.exit_code == 2
    assert "does-not-exist" in result.stdout
