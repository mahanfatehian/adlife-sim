"""`adlife doctor --offline`: the pre-flight report, pinned to its documented checks.

Doctor is the first command a new user runs, so its output is pinned in both modes and
its offline guarantee is absolute: with --offline the suite must complete without any
network capability at all.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
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


def test_doctor_says_when_the_working_directory_is_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    real_access = os.access

    def denied(path: os.PathLike[str] | str, mode: int, *, follow_symlinks: bool = True) -> bool:
        if mode == os.W_OK and Path(path) == Path.cwd():
            return False
        return real_access(path, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "access", denied)
    result = runner.invoke(app, ["--format", "json", "doctor", "--offline"])

    assert result.exit_code == 0, result.output
    writable = next(
        item for item in json.loads(result.stdout)["checks"] if item["name"] == "writable"
    )
    assert writable["ok"] is False
    assert writable["detail"].endswith("is not writable")


def test_doctor_says_when_json1_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("adlife.cli.commands.doctor._sqlite_json1_available", lambda: False)

    result = runner.invoke(app, ["--format", "json", "doctor", "--offline"])

    assert result.exit_code == 0, result.output
    json1 = next(
        item for item in json.loads(result.stdout)["checks"] if item["name"] == "sqlite-json1"
    )
    assert json1["ok"] is False
    assert "missing" in json1["detail"]


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


def test_doctor_does_not_claim_offline_when_it_probes_a_configured_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "study"
    assert runner.invoke(app, ["init", "study", "--parent", str(tmp_path)]).exit_code == 0
    config = project / "adlife.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("mode: mock", "mode: local"),
        encoding="utf-8",
    )

    class _Response:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: _Response())
    result = runner.invoke(app, ["--format", "json", "doctor", str(project)])

    assert result.exit_code == 0, result.output
    offline = next(
        item for item in json.loads(result.stdout)["checks"] if item["name"] == "offline"
    )
    assert "no network access was required" not in offline["detail"]
    assert "probed" in offline["detail"]


def test_doctor_human_mode_names_the_checks(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--offline"])
    assert result.exit_code == 0, result.output
    assert "package" in result.stdout.lower()
    assert "sqlite" in result.stdout.lower()


@pytest.mark.parametrize(
    "url",
    [
        "https://operator:secret@example.test/v1",
        "https://example.test/v1?api_key=secret",
        "http://example.test/v1",
    ],
)
def test_doctor_refuses_unsafe_endpoint_before_network(monkeypatch, url):
    from adlife.cli.commands.doctor import _probe_provider

    calls = []

    def observe(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unsafe endpoint was contacted")

    monkeypatch.setattr("httpx.get", observe)
    ok, detail = _probe_provider(url)
    assert not ok
    assert calls == []
    assert "secret" not in detail


def test_doctor_probe_does_not_echo_a_secret_shaped_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from adlife.cli.commands.doctor import _probe_provider

    class _Response:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: _Response())
    ok, detail = _probe_provider("https://example.test/v1/sk-0123456789abcdefghijklmnopqrstuv")

    assert ok
    assert detail == "probe of https://example.test returned 200"
    assert "sk-" not in detail
