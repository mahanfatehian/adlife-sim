from pathlib import Path

import pytest

from adlife.config.paths import resolve_project_path


def test_path_cannot_escape_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside project root"):
        resolve_project_path(tmp_path, Path("../secret.txt"))


def test_resolves_project_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()

    resolved = resolve_project_path(root, Path("campaigns/../adlife.yaml"))

    assert resolved == root.resolve() / "adlife.yaml"


def test_accepts_absolute_path_inside_project(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    candidate = root / "campaigns" / "phone.yaml"

    assert resolve_project_path(root, candidate) == candidate.resolve()


def test_rejects_absolute_path_outside_project(tmp_path: Path) -> None:
    root = tmp_path / "study"
    root.mkdir()
    candidate = tmp_path / "secret.txt"

    with pytest.raises(ValueError, match="outside project root"):
        resolve_project_path(root, candidate)
