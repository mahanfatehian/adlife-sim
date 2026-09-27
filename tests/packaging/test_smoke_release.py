"""Exercise smoke workspace lifetime without creating another environment."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import smoke_release


def test_smoke_subprocesses_do_not_create_console_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        expected = 0x08000000 if sys.platform == "win32" else 0
        assert kwargs.get("creationflags", 0) == expected
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(command, 0, "captured", "")

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)
    assert smoke_release._run(["adlife", "--version"]).stdout == "captured"


def test_smoke_uses_the_running_supported_interpreter_without_fetching_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def record_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smoke_release, "_uv_executable", lambda: "uv")
    monkeypatch.setattr(smoke_release, "_run", record_command)
    smoke_release._create_interpreter(tmp_path)
    assert commands == [["uv", "venv", "--python", sys.executable, str(tmp_path / "venv")]]


def test_smoke_cleans_workspace_and_returns_no_dead_artifact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    # Environment provisioning is external; run all CLI/report checks for real
    # with the already installed environment so this regression stays offline.
    monkeypatch.setattr(
        smoke_release, "_create_interpreter", lambda path: (Path(sys.executable), None)
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args: None)

    result = smoke_release.smoke(tmp_path / "unused.whl")

    assert not workspace.exists()
    assert result is None


def test_smoke_cleans_workspace_when_installation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))

    def fail_provisioning(path: Path) -> None:
        raise RuntimeError("environment unavailable")

    monkeypatch.setattr(smoke_release, "_create_interpreter", fail_provisioning)
    with pytest.raises(RuntimeError, match="environment unavailable"):
        smoke_release.smoke(tmp_path / "unused.whl")
    assert not workspace.exists()
