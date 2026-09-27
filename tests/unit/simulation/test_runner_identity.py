import subprocess
import sys

from adlife.core.simulation.runner import _git_sha_of


def test_git_metadata_probe_never_opens_a_desktop_console(tmp_path, monkeypatch):
    def run(command, **kwargs):
        assert kwargs.get("creationflags", 0) == (0x08000000 if sys.platform == "win32" else 0)
        return subprocess.CompletedProcess(command, 0, "a" * 40, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert _git_sha_of(tmp_path) == "a" * 40
