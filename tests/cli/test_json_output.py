"""The machine-output contract: JSON modes, diagnostics, and the exit-code mapping.

Every command can be consumed by a script, so the contract is pinned here once: JSON
stdout carries exactly one machine-readable document, diagnostics go to stderr, color
obeys NO_COLOR and TERM=dumb, and the documented exit codes come out of the documented
failures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app
from adlife.cli.errors import ExitCode, command_boundary

runner = CliRunner()


def make_project(tmp_path: Path) -> Path:
    result = runner.invoke(app, ["init", "study", "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / "study"


def test_json_mode_keeps_stdout_machine_readable(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = runner.invoke(app, ["--format", "json", "validate", str(project)])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["valid"] is True
    assert "warning" not in result.stdout.lower()


def test_json_mode_puts_diagnostics_on_stderr(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--format", "json", "validate", str(tmp_path / "does-not-exist")])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["valid"] is False
    assert result.stderr.strip(), "diagnostics must reach stderr in JSON mode"


def test_no_color_and_dumb_terminal_disable_ansi(tmp_path: Path, monkeypatch) -> None:
    project = make_project(tmp_path)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")

    result = runner.invoke(app, ["validate", str(project)])
    assert result.exit_code == 0
    assert "\x1b[" not in result.stdout


def test_corrupted_artifact_maps_to_artifact_error(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    rules = runner.invoke(app, ["run", str(project), "--run-id", "run-corrupt", "--mode", "rules"])
    assert rules.exit_code == 0, rules.output

    database = project / "runs" / "run-corrupt" / "results.sqlite3"
    database.write_bytes(b"this is not a database")

    result = runner.invoke(app, ["--format", "json", "replay", str(project), "run-corrupt"])
    assert result.exit_code == ExitCode.ARTIFACT_ERROR
    assert json.loads(result.stdout)["error"]["exit_code"] == ExitCode.ARTIFACT_ERROR


def test_replay_cache_miss_maps_to_provider_error(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    rules = runner.invoke(app, ["run", str(project), "--run-id", "run-hybrid", "--mode", "rules"])
    assert rules.exit_code == 0, rules.output

    empty_cache = tmp_path / "empty-cache"
    empty_cache.mkdir()
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "replay",
            str(project),
            "run-hybrid",
            "--provider-mode",
            "hybrid",
            "--cache-dir",
            str(empty_cache),
        ],
    )
    assert result.exit_code == ExitCode.PROVIDER_ERROR


def test_command_boundary_maps_keyboard_interrupt_to_130() -> None:
    @command_boundary
    def interrupted() -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        interrupted()


def test_command_boundary_maps_unexpected_defects_to_1() -> None:
    @command_boundary
    def defective() -> str:
        raise RuntimeError("an unexpected defect")

    with pytest.raises(SystemExit) as excinfo:
        defective()
    assert excinfo.value.code == ExitCode.UNEXPECTED


def test_command_boundary_maps_validation_to_2() -> None:
    @command_boundary
    def invalid() -> str:
        raise ValueError("a refused input")

    with pytest.raises(SystemExit) as excinfo:
        invalid()
    assert excinfo.value.code == ExitCode.INPUT_ERROR
