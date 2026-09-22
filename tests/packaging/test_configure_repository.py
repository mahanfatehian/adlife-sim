"""The repository-URL configurator, pinned at its parsing and replacement boundary.

``scripts/configure_repository.py`` is the one tool allowed to rewrite the README's
machine-editable installation block, so its behaviour is pinned exactly: which origins
it accepts, what it writes between the markers, and above all that it refuses to touch
anything when the origin is not a GitHub URL it can validate.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "configure_repository.py"

START = "<!-- adlife-install:start -->"
END = "<!-- adlife-install:end -->"


@pytest.fixture()
def module():
    spec = importlib.util.spec_from_file_location("configure_repository", SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize(
    ("remote", "slug"),
    [
        ("git@github.com:mahanfatehian/adlife-sim.git", "mahanfatehian/adlife-sim"),
        ("git@github.com:mahanfatehian/adlife-sim", "mahanfatehian/adlife-sim"),
        ("https://github.com/mahanfatehian/adlife-sim.git", "mahanfatehian/adlife-sim"),
        ("https://github.com/mahanfatehian/adlife-sim", "mahanfatehian/adlife-sim"),
        ("https://github.com/Org_1/name.example.git", "Org_1/name.example"),
    ],
)
def test_parse_github_slug_accepts_valid_origins(module, remote: str, slug: str) -> None:
    assert module.parse_github_slug(remote) == slug


@pytest.mark.parametrize(
    "remote",
    [
        "https://gitlab.com/mahanfatehian/adlife-sim.git",
        "ssh://git@github.com/mahanfatehian/adlife-sim.git",
        "git@gitlab.com:mahanfatehian/adlife-sim.git",
        "/local/path/only",
        "",
    ],
)
def test_parse_github_slug_refuses_non_github_origins(module, remote: str) -> None:
    with pytest.raises(ValueError):
        module.parse_github_slug(remote)


@pytest.mark.parametrize("slug", ["../evil/adlife-sim", "owner/", "/repo", "a b/c d"])
def test_parse_github_slug_refuses_invalid_characters(module, slug: str) -> None:
    with pytest.raises(ValueError):
        module.parse_github_slug(slug)


def test_installation_block_pins_tag_and_has_no_variables(module) -> None:
    block = module.installation_block("mahanfatehian/adlife-sim", "0.1.0")
    assert block.startswith(START) and block.endswith(END)
    url = "https://raw.githubusercontent.com/mahanfatehian/adlife-sim/v0.1.0/scripts/install.sh"
    assert url in block
    assert "curl -fsSL" in block and "| sh" in block
    for forbidden in ("ADLIFE_INSTALLER_URL", "example.com", "your-user", "OWNER"):
        assert forbidden not in block


def test_script_updates_only_the_marked_block(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    before = (
        "# Title\n\n"
        f"{START}\nuv tool install adlife-sim\n{END}\n\n"
        "## Unrelated section\n\nUntouched prose.\n"
    )
    readme.write_text(before, encoding="utf-8")
    env = {
        "ADLIFE_README": str(readme),
        "ADLIFE_VERSION": "0.1.0",
        "ADLIFE_ORIGIN": "git@github.com:mahanfatehian/adlife-sim.git",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    text = readme.read_text(encoding="utf-8")
    assert "uv tool install" not in text
    assert (
        "https://raw.githubusercontent.com/mahanfatehian/adlife-sim/v0.1.0/scripts/install.sh"
        in text
    )
    assert text.startswith("# Title\n\n")
    assert "## Unrelated section\n\nUntouched prose." in text


def test_script_exits_unchanged_without_a_github_origin(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    original = f"{START}\nuv tool install adlife-sim\n{END}\n"
    readme.write_text(original, encoding="utf-8")
    env = {
        "ADLIFE_README": str(readme),
        "ADLIFE_VERSION": "0.1.0",
        "ADLIFE_ORIGIN": "https://gitlab.com/a/b.git",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 2
    assert readme.read_text(encoding="utf-8") == original
