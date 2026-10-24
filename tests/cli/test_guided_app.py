"""``adlife app``: the one-command start of the guided app."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.utils import strip_ansi
from fastapi import FastAPI
from typer.testing import CliRunner

from adlife.cli.app import app
from adlife.cli.commands import guided_app


def test_app_is_registered_with_defaults_and_plain_help() -> None:
    result = CliRunner().invoke(app, ["app", "--help"], env={"COLUMNS": "200"})
    # CI forces colour (GITHUB_ACTIONS), so compare the help as plain text.
    output = " ".join(strip_ansi(result.output).replace("│", " ").split())

    assert result.exit_code == 0
    assert "--workspace" in output and "adlife-workspace" in output
    assert "--port" in output and "8765" in output
    assert "--open" in output and "--no-open" in output
    assert "OpenAI-compatible" in output
    assert Path("adlife-workspace") == guided_app.DEFAULT_APP_WORKSPACE


@pytest.mark.parametrize(("flag", "opens"), [((), True), (("--no-open",), False)])
def test_app_serves_the_guide_on_loopback_and_opens_the_browser_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: tuple[str, ...], opens: bool
) -> None:
    served: list[tuple[FastAPI, dict[str, object]]] = []
    opened: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "uvicorn.run", lambda application, **options: served.append((application, options))
    )
    monkeypatch.setattr(
        guided_app, "_open_in_background", lambda port, url: opened.append((port, url))
    )
    workspace = tmp_path / "my workspace"

    result = CliRunner().invoke(
        app, ["app", "--workspace", str(workspace), "--port", "8770", *flag]
    )

    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8770/guide" in result.output
    assert workspace.is_dir()
    ((application, options),) = served
    assert options["host"] == "127.0.0.1" and options["port"] == 8770
    assert opened == ([(8770, "http://127.0.0.1:8770/guide")] if opens else [])
    # The server refuses to call itself as an "AI on this computer".
    registry = application.state.provider_origins
    with pytest.raises(ValueError):
        registry.approve("127.0.0.1:8770/v1")
    assert registry.approve("127.0.0.1:1234/v1").trust_class == "local"


def test_city_workbench_never_opens_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("uvicorn.run", lambda application, **options: None)
    monkeypatch.setattr(
        guided_app, "_open_in_background", lambda port, url: pytest.fail("browser opened")
    )
    result = CliRunner().invoke(
        app, ["city-workbench", "--workspace", str(tmp_path / "workspace"), "--port", "8771"]
    )
    assert result.exit_code == 0, result.output
