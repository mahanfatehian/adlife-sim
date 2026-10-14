"""The local workbench command is explicit, loopback-only, and never opens a browser."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
from click.utils import strip_ansi
from fastapi import FastAPI
from typer.testing import CliRunner

from adlife.city.provider_runtime import (
    DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
    DEFAULT_LOCAL_PROVIDER_BASE_URL,
)
from adlife.cli.app import app


def test_city_workbench_is_registered_with_required_workspace_and_honest_help() -> None:
    runner = CliRunner()
    color_environment = {"FORCE_COLOR": "1", "TERM": "xterm-256color"}
    root_help = runner.invoke(app, ["--help"], env=color_environment, color=True)
    command_help = runner.invoke(
        app,
        ["city-workbench", "--help"],
        env=color_environment,
        color=True,
    )
    root_output = strip_ansi(root_help.output)
    command_output = strip_ansi(command_help.output)
    normalized_command_output = " ".join(command_output.split()).lower()

    assert root_help.exit_code == 0
    assert "city-workbench" in root_output
    assert command_help.exit_code == 0
    assert "--workspace" in command_output
    assert "[required]" in command_output
    assert "--port" in command_output
    assert "--provider-origin" in command_output
    assert "--provider-key-env" in command_output
    assert "city -> campaign -> run -> inspect" in normalized_command_output
    assert "process-local" in normalized_command_output
    assert "bounded worker" in normalized_command_output
    assert "verified artifacts" in normalized_command_output
    assert "no authentication" in normalized_command_output
    assert "startup-approved" in normalized_command_output
    assert "provider" in normalized_command_output
    assert "oauth" in normalized_command_output
    assert "workspace city-pack upload" in normalized_command_output
    assert "rights" in normalized_command_output
    assert "unreviewed" in normalized_command_output
    assert "run jobs are not enabled" not in normalized_command_output
    assert "future verified" not in normalized_command_output


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
    runtime = application.state.provider_runtime
    assert tuple((item.approval_id, item.base_url) for item in runtime.origins) == (
        (DEFAULT_LOCAL_PROVIDER_APPROVAL_ID, DEFAULT_LOCAL_PROVIDER_BASE_URL),
    )
    assert runtime.key_environments == ()
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


def test_city_workbench_accepts_repeatable_non_secret_provider_startup_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applications: list[FastAPI] = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda application, **_options: applications.append(application),
    )

    result = CliRunner().invoke(
        app,
        [
            "city-workbench",
            "--workspace",
            str(tmp_path / "workspace"),
            "--provider-origin",
            "local-alt=http://[::1]:11434/v1/",
            "--provider-origin",
            "remote=https://API.EXAMPLE.INVALID:443/v1/",
            "--provider-key-env",
            "remote=REMOTE_API_KEY",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(applications) == 1
    runtime = applications[0].state.provider_runtime
    assert tuple((item.approval_id, item.base_url) for item in runtime.origins) == (
        ("local-alt", "http://[::1]:11434/v1"),
        ("local-ollama", "http://127.0.0.1:11434/v1"),
        ("remote", "https://api.example.invalid/v1"),
    )
    assert tuple(
        (item.origin_approval_id, item.environment_variable) for item in runtime.key_environments
    ) == (("remote", "REMOTE_API_KEY"),)
    assert "REMOTE_API_KEY" not in result.output


@pytest.mark.parametrize(
    ("option", "binding", "message"),
    [
        (
            "--provider-origin",
            "remote=http://api.example.invalid/v1",
            "provider origin configuration is invalid",
        ),
        (
            "--provider-key-env",
            "unknown=UNKNOWN_API_KEY",
            "provider environment configuration is invalid",
        ),
        (
            "--provider-origin",
            "sk-test-abcdefghijklmnopqrst=https://api.example.invalid/v1",
            "provider origin configuration is invalid",
        ),
    ],
)
def test_city_workbench_refuses_bad_provider_policy_before_creating_workspace(
    option: str,
    binding: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "must-not-exist"
    server_calls: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: server_calls.append(args))

    result = CliRunner().invoke(
        app,
        ["city-workbench", "--workspace", str(workspace), option, binding],
    )

    assert result.exit_code == 2
    assert message in result.output
    assert binding not in result.output
    assert "Traceback" not in result.output
    assert server_calls == []
    assert not workspace.exists()


def test_city_workbench_has_no_command_line_credential_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-test-000000000000000000000000"
    calls: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(
        app,
        [
            "city-workbench",
            "--workspace",
            str(tmp_path / "workspace"),
            "--provider-api-key",
            secret,
        ],
    )

    assert result.exit_code == 2
    assert secret not in result.output
    assert calls == []
