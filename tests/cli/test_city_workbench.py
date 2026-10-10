"""The local workbench command is explicit, loopback-only, and never opens a browser."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
from fastapi import FastAPI
from typer.testing import CliRunner

from adlife.cli.app import app


def test_city_workbench_is_registered_with_required_workspace_and_honest_help() -> None:
    root_help = CliRunner().invoke(app, ["--help"])
    command_help = CliRunner().invoke(app, ["city-workbench", "--help"])

    assert root_help.exit_code == 0
    assert "city-workbench" in root_help.output
    assert command_help.exit_code == 0
    assert "--workspace" in command_help.output
    assert "[required]" in command_help.output
    assert "--port" in command_help.output
    assert "validation" in command_help.output.lower()
    assert "provider" not in command_help.output.lower()


def test_city_workbench_requires_workspace_and_bounds_port_before_server_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))

    missing = CliRunner().invoke(app, ["city-workbench"])
    low = CliRunner().invoke(
        app,
        ["city-workbench", "--workspace", str(tmp_path / "low"), "--port", "0"],
    )
    high = CliRunner().invoke(
        app,
        ["city-workbench", "--workspace", str(tmp_path / "high"), "--port", "65536"],
    )

    assert missing.exit_code == 2
    assert low.exit_code == 2
    assert high.exit_code == 2
    assert calls == []


def test_city_workbench_prepares_app_and_binds_one_hidden_safe_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    calls: list[tuple[FastAPI, dict[str, object]]] = []

    def fake_run(application: FastAPI, **options: object) -> None:
        calls.append((application, options))

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(
        app,
        ["city-workbench", "--workspace", str(workspace), "--port", "8877"],
    )

    assert result.exit_code == 0, result.output
    assert workspace.is_dir()
    assert len(calls) == 1
    application, options = calls[0]
    assert isinstance(application, FastAPI)
    assert options == {
        "host": "127.0.0.1",
        "port": 8877,
        "workers": 1,
        "reload": False,
        "log_level": "warning",
        "access_log": False,
        "server_header": False,
    }
    assert result.output.count("http://127.0.0.1:8877") == 1
    assert "open" not in result.output.lower()


def test_city_workbench_refuses_machine_output_before_creating_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "must-not-exist"
    calls: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-workbench",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 2
    assert '"exit_code": 2' in result.stdout
    assert "interactive" in result.stdout
    assert calls == []
    assert not workspace.exists()


def test_city_workbench_refuses_unsafe_workspace_before_app_or_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("adlife.cli.commands.city_workbench")
    calls: list[object] = []

    def refuse_workspace(_path: Path) -> None:
        raise module.UnsafeWorkbenchWorkspace("secret C:\\private\\workspace")

    monkeypatch.setattr(module, "prepare_workbench_workspace", refuse_workspace)
    monkeypatch.setattr(
        module, "create_city_workbench_app", lambda *_args, **_kwargs: calls.append(1)
    )
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(2))

    result = CliRunner().invoke(
        app,
        ["city-workbench", "--workspace", str(tmp_path / "workspace")],
    )

    assert result.exit_code == 2
    assert calls == []
    assert "workspace is unsafe or unavailable" in result.output
    assert "C:\\private" not in result.output
    assert "http://127.0.0.1" not in result.output
    assert "Traceback" not in result.output


def test_city_workbench_interrupt_exits_130_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("uvicorn.run", interrupt)
    result = CliRunner().invoke(
        app,
        ["city-workbench", "--workspace", str(tmp_path / "workspace")],
    )

    assert result.exit_code == 130
    assert "Traceback" not in result.output


def test_city_workbench_has_no_browser_launch_path() -> None:
    module = importlib.import_module("adlife.cli.commands.city_workbench")
    source = inspect.getsource(module)

    assert "webbrowser" not in source
    assert "startfile" not in source
    assert "Start-Process" not in source
