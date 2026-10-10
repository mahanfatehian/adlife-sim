from __future__ import annotations

import importlib
import importlib.util
import os
import stat
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest


def _module():
    spec = importlib.util.find_spec("adlife.city.workbench_workspace")
    assert spec is not None, "workbench workspace boundary is not implemented"
    return importlib.import_module("adlife.city.workbench_workspace")


def test_prepare_workspace_creates_and_pins_fresh_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    workspace = _module().prepare_workbench_workspace(Path("state") / "workbench")

    assert workspace.root == (tmp_path / "state" / "workbench").resolve(strict=True)
    assert workspace.root.is_dir()
    assert _module().verify_workbench_workspace(workspace) == workspace.root
    root_attribute = "root"
    with pytest.raises(FrozenInstanceError):
        setattr(workspace, root_attribute, tmp_path)


def test_prepare_workspace_accepts_existing_absolute_directory(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()

    workspace = _module().prepare_workbench_workspace(target)

    assert workspace.root == target.resolve(strict=True)
    assert _module().verify_workbench_workspace(workspace) == workspace.root


def test_prepare_workspace_refuses_file_ancestor_before_creating_descendant(
    tmp_path: Path,
) -> None:
    ancestor = tmp_path / "not-a-directory"
    ancestor.write_text("fixed", encoding="utf-8")
    descendant = ancestor / "workbench"

    with pytest.raises(_module().UnsafeWorkbenchWorkspace):
        _module().prepare_workbench_workspace(descendant)

    assert ancestor.read_text(encoding="utf-8") == "fixed"
    assert not descendant.exists()


def test_prepare_workspace_refuses_directory_symlink_without_writing_through_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")

    with pytest.raises(_module().UnsafeWorkbenchWorkspace):
        _module().prepare_workbench_workspace(linked / "must-not-exist")

    assert not (outside / "must-not-exist").exists()


def test_prepare_workspace_refuses_dangling_symlink_without_creating_target(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-target"
    linked = tmp_path / "dangling"
    try:
        linked.symlink_to(missing, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")

    with pytest.raises(_module().UnsafeWorkbenchWorkspace):
        _module().prepare_workbench_workspace(linked / "must-not-exist")

    assert not missing.exists()


def test_prepare_workspace_rechecks_chain_after_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "workspace"
    target.mkdir()
    displaced = tmp_path / "displaced"
    module = _module()
    original_create = module._create_missing_directories

    def swap_component(path: Path) -> None:
        original_create(path)
        target.rename(displaced)
        target.write_text("replacement", encoding="utf-8")

    monkeypatch.setattr(module, "_create_missing_directories", swap_component)

    with pytest.raises(module.UnsafeWorkbenchWorkspace):
        module.prepare_workbench_workspace(target)

    assert target.is_file()
    assert displaced.is_dir()


def test_verify_workspace_detects_pinned_root_replacement(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    workspace = _module().prepare_workbench_workspace(target)
    if workspace.device is None and workspace.inode is None:
        pytest.skip("this filesystem does not expose stable directory identity")
    displaced = tmp_path / "original-workspace"
    target.rename(displaced)
    target.mkdir()

    with pytest.raises(_module().UnsafeWorkbenchWorkspace):
        _module().verify_workbench_workspace(workspace)


def test_prepare_workspace_refuses_simulated_windows_reparse_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "workspace"
    target.mkdir()
    module = _module()
    real_lstat = module._lstat

    class ReparseStatus:
        def __init__(self, base: os.stat_result) -> None:
            self._base = base
            self.st_mode = base.st_mode
            self.st_file_attributes = getattr(base, "st_file_attributes", 0) | getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            )

        def __getattr__(self, name: str) -> object:
            return getattr(self._base, name)

    def simulated_lstat(path: Path) -> Any:
        result = real_lstat(path)
        if path == target:
            return ReparseStatus(result)
        return result

    monkeypatch.setattr(module, "_lstat", simulated_lstat)

    with pytest.raises(module.UnsafeWorkbenchWorkspace):
        module.prepare_workbench_workspace(target)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_prepare_workspace_refuses_real_windows_junction_without_writing_through_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("this account cannot create directory junctions")

    with pytest.raises(_module().UnsafeWorkbenchWorkspace):
        _module().prepare_workbench_workspace(junction / "must-not-exist")

    assert not (outside / "must-not-exist").exists()
