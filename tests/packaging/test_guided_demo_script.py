"""The presenter's one-command start script, `scripts/start-guided-demo.ps1`.

The static checks pin the key-handling contract on every platform: the key is read
hidden, travels only as a process environment variable named on the command line, and
leaves the environment when the server stops. On Windows the script also runs: once with
a fake `uv` that records its arguments, and once for real, serving the guided start page
from a temporary workspace with practice answers only.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "start-guided-demo.ps1"
DOCS = ROOT / "docs" / "guided-demo.md"
IS_WINDOWS = sys.platform == "win32"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _powershell(*arguments: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        *arguments,
    ]


def test_script_is_ascii_so_windows_powershell_reads_it_without_a_bom() -> None:
    assert SCRIPT.read_bytes().isascii()


def test_defaults_match_the_presenter_guide() -> None:
    text = _text()
    assert "[int]$Port = 50050" in text
    assert '[string]$Workspace = ".\\demo-guided"' in text
    assert '[string]$ProviderName = "gapgpt"' in text
    assert '[string]$ProviderUrl = "https://api.gapgpt.app/v1"' in text
    assert '[string]$KeyVariable = "GAPGPT_API_KEY"' in text


def test_the_key_is_read_hidden_and_only_named_on_the_command_line() -> None:
    text = _text()
    assert re.search(r"Read-Host [^\n]*-AsSecureString", text)
    # The command line carries the variable's name, never its value.
    assert '"--provider-key-env", "$ProviderName=$KeyVariable"' in text
    assert "SetEnvironmentVariable(" in text
    assert '"Process"' in text
    assert '"User"' not in text and '"Machine"' not in text
    for forbidden in ("ConvertFrom-SecureString", "Out-File", "Set-Content", "Add-Content", "setx"):
        assert forbidden not in text


def test_the_key_leaves_the_environment_however_the_server_ends() -> None:
    text = _text()
    finally_block = text.split("} finally {", 1)[1]
    assert '[Environment]::SetEnvironmentVariable($KeyVariable, $null, "Process")' in (
        finally_block
    )


def test_the_presenter_guide_starts_with_the_script() -> None:
    docs = DOCS.read_text(encoding="utf-8")
    assert "scripts\\start-guided-demo.ps1" in docs


@pytest.mark.skipif(not IS_WINDOWS, reason="the start script is Windows PowerShell")
def test_practice_only_passes_the_origin_but_no_key_variable(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "uv-args.txt"
    # The fake uv records its arguments and whether a key variable reached it, then fails,
    # so the script reports that the server stopped before it was ready.
    (fake / "uv.cmd").write_text(
        "@echo off\r\n"
        f'echo %*> "{log}"\r\n'
        f'if defined GAPGPT_API_KEY (echo key-present>> "{log}")\r\n'
        "exit /b 3\r\n",
        encoding="ascii",
    )
    env = {**os.environ, "PATH": os.pathsep.join([str(fake), os.environ.get("PATH", "")])}
    env.pop("GAPGPT_API_KEY", None)
    port = _free_port()
    completed = subprocess.run(
        _powershell("-PracticeOnly", "-NoBrowser", "-Port", str(port)),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 1, completed.stderr
    assert "stopped before it was ready" in completed.stderr
    recorded = log.read_text(encoding="ascii").split()
    assert recorded[:3] == ["run", "adlife", "city-workbench"]
    assert recorded[recorded.index("--port") + 1] == str(port)
    assert recorded[recorded.index("--workspace") + 1] == ".\\demo-guided"
    assert recorded[recorded.index("--provider-origin") + 1] == "gapgpt=https://api.gapgpt.app/v1"
    assert "--provider-key-env" not in recorded
    assert "key-present" not in recorded


@pytest.mark.skipif(not IS_WINDOWS, reason="the start script is Windows PowerShell")
@pytest.mark.skipif(os.environ.get("ADLIFE_SKIP_LONG_TESTS") == "1", reason="starts a real server")
def test_practice_only_serves_the_guided_start_page(tmp_path: Path) -> None:
    port = _free_port()
    workspace = tmp_path / "demo guided"
    env = {**os.environ}
    env.pop("GAPGPT_API_KEY", None)
    process = subprocess.Popen(
        _powershell(
            "-PracticeOnly",
            "-NoBrowser",
            "-Port",
            str(port),
            "-Workspace",
            str(workspace),
        ),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert process.stdout is not None
        ready = f"Ready: http://127.0.0.1:{port}/guide"
        deadline = time.monotonic() + 180
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
            if line.strip() == ready:
                break
        assert ready in lines, "\n".join(lines)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/guide?lang=fa", timeout=10) as page:
            assert page.status == 200
            assert 'dir="rtl"' in page.read().decode("utf-8")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/providers", timeout=10) as api:
            body = api.read().decode("utf-8")
        assert '"approval_id":"gapgpt"' in body.replace(" ", "")
        assert workspace.is_dir()
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        process.wait(timeout=30)
