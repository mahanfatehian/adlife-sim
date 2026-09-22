"""The shell installer, pinned without ever touching a real user environment.

Every test runs ``scripts/install.sh`` under ``sh`` with a temporary ``HOME`` and a
fake ``uv`` executable on ``PATH`` that records its arguments, so the contract is
pinned exactly - the package name, the version pin, the refusal to escalate or to
edit shell startup files, the failure when ``uv`` is absent - while nothing outside
the temporary sandbox is modified. The installer is POSIX shell: on Windows the
platform-refusal path is tested (the real ``uname`` is not Linux or Darwin) and the
POSIX paths are skipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "install.sh"
IS_POSIX = sys.platform in {"linux", "darwin"}


def _write_fake_uv(directory: Path, *, fail: bool = False) -> Path:
    body = '#!/bin/sh\necho "$@" >> "$UV_CALL_LOG"\n' + ("exit 3\n" if fail else "exit 0\n")
    fake = directory / "uv"
    fake.write_text(body, encoding="utf-8")
    fake.chmod(0o755)
    return fake


def _run(
    fake_uv_dir: Path | None,
    home: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": str(fake_uv_dir) if fake_uv_dir else "/usr/bin:/bin",
        "HOME": str(home),
        "UV_CALL_LOG": str(home / "uv-calls.log"),
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["sh", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_installer_refuses_unsupported_platform() -> None:
    """On Windows the real uname is neither Linux nor Darwin: the gate must fire."""
    if sys.platform in {"linux", "darwin"}:
        pytest.skip("this machine uname is a supported platform")
    home = Path(os.environ.get("TEMP", "/tmp")) / "adlife-inst-home"
    home.mkdir(parents=True, exist_ok=True)
    result = _run(None, home)
    assert result.returncode == 2
    assert "Linux and macOS" in (result.stderr + result.stdout)


def test_installer_rejects_missing_uv_without_chaining_another_installer() -> None:
    if not IS_POSIX:
        pytest.skip("POSIX shell behaviour")
    home = Path("/tmp") / "adlife-inst-home"
    result = _run(None, home)
    assert result.returncode == 2
    output = result.stdout + result.stderr
    assert "docs.astral.sh/uv" in output, "must print the official uv installation URL"
    assert "uv tool install" not in output.split("uv is required")[-1]


@pytest.mark.parametrize("version", [None, "1.2.3"])
def test_installer_installs_exact_package_and_runs_health_checks(
    tmp_path: Path, version: str | None
) -> None:
    if not IS_POSIX:
        pytest.skip("POSIX shell behaviour")
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _write_fake_uv(fake_dir)
    env_extra = {"ADLIFE_VERSION": version} if version else None
    result = _run(fake_dir, tmp_path, env_extra)
    assert result.returncode == 0, result.stdout + result.stderr

    log = (tmp_path / "uv-calls.log").read_text(encoding="utf-8")
    expected = "tool install --upgrade adlife-sim"
    if version:
        expected = f"tool install --upgrade adlife-sim=={version}"
    assert expected in log.replace("\n", " ")
    # The recorded calls must be exactly install, then the two health checks.
    calls = [line.strip() for line in log.strip().splitlines() if line.strip()]
    assert calls[-2:] == ["--version", "doctor --offline"]


def test_installer_never_escalates_or_edits_startup_files(tmp_path: Path) -> None:
    if not IS_POSIX:
        pytest.skip("POSIX shell behaviour")
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    _write_fake_uv(fake_dir)
    assert _run(fake_dir, tmp_path).returncode == 0
    log = (tmp_path / "uv-calls.log").read_text(encoding="utf-8")
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "sudo" not in script_text
    for startup in (".bashrc", ".zshrc", ".profile", ".bash_profile"):
        assert startup not in script_text
        assert not (tmp_path / startup).exists()
    assert "sudo" not in log
