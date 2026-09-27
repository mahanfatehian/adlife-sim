"""The error boundary: expected failures speak, defects dump, exit codes hold.

An expected failure — a refused input, a duplicate run, a misconfigured provider, a
corrupted artifact — is a normal conversation with the user: one concise diagnostic on
stderr, the documented exit code, and a clean machine-readable stdout in JSON mode.
A Python traceback is for unexpected internal defects and for explicit ``ADLIFE_DEBUG=1``
diagnostics, never for ordinary usage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app
from adlife.cli.errors import ExitCode

runner = CliRunner()


def make_project(tmp_path: Path, name: str = "study") -> Path:
    result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / name


def complete_a_run(project: Path, run_id: str) -> None:
    result = runner.invoke(app, ["run", str(project), "--run-id", run_id, "--mode", "rules"])
    assert result.exit_code == 0, result.output


def test_an_expected_input_error_never_prints_a_traceback(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(app, ["run", str(project), "--run-id", "BAD_ID", "--mode", "rules"])

    assert result.exit_code == ExitCode.INPUT_ERROR
    combined = result.output
    assert "Traceback" not in combined
    assert "error:" in combined, "the human diagnostic must still reach the user"


def test_a_duplicate_run_never_prints_a_traceback_and_exits_3(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    complete_a_run(project, "run-twice")

    second = runner.invoke(app, ["run", str(project), "--run-id", "run-twice", "--mode", "rules"])

    assert second.exit_code == 3, second.output
    assert "run-twice" in second.output
    assert "Traceback" not in second.output


def test_json_mode_keeps_the_error_document_as_the_only_stdout(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    complete_a_run(project, "run-twice")

    second = runner.invoke(
        app, ["--format", "json", "run", str(project), "--run-id", "run-twice", "--mode", "rules"]
    )

    assert second.exit_code == 3
    document = json.loads(second.stdout)
    assert document["error"]["exit_code"] == 3
    assert document["error"]["message"]
    assert "Traceback" not in second.stdout


def test_json_mode_stderr_diagnostic_is_concise_without_a_traceback(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app, ["--format", "json", "run", str(project), "--run-id", "BAD_ID", "--mode", "rules"]
    )

    assert result.exit_code == ExitCode.INPUT_ERROR
    assert json.loads(result.stdout)["error"]["type"]
    assert "Traceback" not in result.stderr
    assert "error:" in result.stderr


def test_a_corrupted_artifact_reports_without_a_traceback(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    complete_a_run(project, "run-corrupt")
    (project / "runs" / "run-corrupt" / "results.sqlite3").write_bytes(b"not a database")

    result = runner.invoke(app, ["replay", str(project), "run-corrupt"])

    assert result.exit_code == ExitCode.ARTIFACT_ERROR
    assert "Traceback" not in result.output


def test_a_provider_configuration_error_reports_without_a_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    """A remote provider without its credential exits 2 before creating a run."""
    monkeypatch.delenv("ADLIFE_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", type("NotATty", (), {"isatty": staticmethod(lambda: False)})())
    project = make_project(tmp_path)
    config = project / "adlife.yaml"
    import yaml

    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    document["provider"] = {"mode": "remote", "base_url": "https://api.openai.example/v1"}
    config.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = runner.invoke(app, ["run", str(project), "--run-id", "run-remote", "--mode", "hybrid"])

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert result.output.strip(), "the user must still learn why"
    assert not (project / "runs" / "run-remote").exists()


def test_an_unexpected_defect_still_dumps_a_traceback(tmp_path: Path, monkeypatch) -> None:
    """A genuine internal defect must stay diagnosable: traceback on, exit 1."""
    monkeypatch.setattr("sys.stdin", type("NotATty", (), {"isatty": staticmethod(lambda: False)})())
    project = make_project(tmp_path)

    def defective(*args, **kwargs):
        raise RuntimeError("unexpected engine defect")

    monkeypatch.setattr("adlife.cli.commands.validate.load_project", defective)

    result = runner.invoke(app, ["validate", str(project)])
    assert result.exit_code == 1
    assert "Traceback" in result.stderr


@pytest.mark.parametrize("fmt", ["human", "json", "jsonl"])
@pytest.mark.parametrize("kind,code", [("cache", 4), ("duplicate", 3), ("interrupt", 130)])
def test_boundary_classifies_cache_conflict_and_interrupt(tmp_path, monkeypatch, fmt, kind, code):
    from adlife.adapters.cognition.cache import CacheMiss
    from adlife.core.ports.run_store import DuplicateRun

    project = make_project(tmp_path)

    def fail(*args, **kwargs):
        if kind == "cache":
            raise CacheMiss("d" * 64)
        if kind == "duplicate":
            raise DuplicateRun("run already exists")
        raise KeyboardInterrupt

    monkeypatch.setattr("adlife.cli.commands.validate.load_project", fail)
    result = runner.invoke(app, ["--format", fmt, "validate", str(project)])
    assert result.exit_code == code
    assert "Traceback" not in result.stderr
    if fmt != "human":
        assert json.loads(result.stdout)["error"]["exit_code"] == code


def test_debug_mode_prints_the_traceback_for_an_expected_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADLIFE_DEBUG", "1")
    project = make_project(tmp_path)

    result = runner.invoke(app, ["run", str(project), "--run-id", "BAD_ID", "--mode", "rules"])

    assert result.exit_code == ExitCode.INPUT_ERROR
    assert "Traceback" in result.output, "ADLIFE_DEBUG=1 must request the diagnostic detail"


@pytest.mark.parametrize("debug", ["0", "1"])
def test_boundary_redacts_diagnostics_and_debug_tracebacks(tmp_path, monkeypatch, debug):
    project = make_project(tmp_path)
    monkeypatch.setenv("ADLIFE_DEBUG", debug)
    monkeypatch.setenv("ADLIFE_API_KEY", "opaque-test-credential")

    def fail(*args, **kwargs):
        raise RuntimeError(
            "internal transport opaque-test-credential Authorization: Bearer another-secret"
        )

    monkeypatch.setattr("adlife.cli.commands.validate.load_project", fail)
    result = runner.invoke(app, ["--format", "json", "validate", str(project)])
    assert result.exit_code == 1
    assert "opaque-test-credential" not in result.output
    assert "another-secret" not in result.output
    assert json.loads(result.stdout)["error"]["exit_code"] == 1
