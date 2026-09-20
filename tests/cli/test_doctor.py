"""`adlife doctor --offline`: the pre-flight report, pinned to its documented checks.

Doctor is the first command a new user runs, so its output is pinned in both modes and
its offline guarantee is absolute: with --offline the suite must complete without any
network capability at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def test_offline_doctor_reports_every_check(tmp_path: Path) -> None:
    project = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["--format", "json", "doctor", "--offline", str(project)])
    assert result.exit_code == 0, result.output

    document = json.loads(result.stdout)
    names = {check["name"] for check in document["checks"]}
    assert {
        "package-version",
        "python-version",
        "resources",
        "writable",
        "project",
        "sqlite-json1",
        "utf-8",
        "providers",
        "offline",
    } <= names
    assert all(check["ok"] for check in document["checks"])


def test_doctor_reports_a_broken_project_without_crashing(tmp_path: Path) -> None:
    project = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0
    (project / "adlife.yaml").write_text("simulation: [unclosed", encoding="utf-8")

    result = runner.invoke(app, ["--format", "json", "doctor", "--offline", str(project)])
    assert result.exit_code == 0, result.output

    document = json.loads(result.stdout)
    project_check = next(check for check in document["checks"] if check["name"] == "project")
    assert project_check["ok"] is False


def test_human_doctor_respects_no_color(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("NO_COLOR", "1")  # type: ignore[attr-defined]

    result = runner.invoke(app, ["doctor", "--offline"])
    assert result.exit_code == 0, result.output
    assert "\x1b[" not in result.stdout


def test_doctor_human_mode_names_the_checks(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--offline"])
    assert result.exit_code == 0, result.output
    assert "package" in result.stdout.lower()
    assert "sqlite" in result.stdout.lower()
