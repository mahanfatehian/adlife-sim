from __future__ import annotations

import json
import os
import stat
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app
from adlife.core.domain.serialization import canonical_json
from tests.integration.test_city_spatial_study import analyze, definition, make_runs
from tests.unit.city.test_city_analysis import _artifact_bytes
from tests.unit.reporting.test_spatial_html import report_module


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("publish-evidence")
    make_runs(root, response=True)
    return analyze(root, definition(response=True))


def test_publisher_emits_fixed_relative_nine_field_receipt_and_exact_bytes(result, tmp_path):
    module = report_module()
    assert callable(getattr(module, "publish_spatial_study_report", None)), "publisher is missing"
    receipt = module.publish_spatial_study_report(result, tmp_path)
    content = (tmp_path / "city-reports" / "repeated-study.html").read_bytes()
    assert content == module.render_spatial_study_html(result)
    assert receipt.model_dump(mode="json") == {
        "schema_version": 1,
        "format_id": "spatial-study-html-v1",
        "claim_scope": "synthetic-study-not-observed-or-causal-effect",
        "study_id": "repeated-study",
        "study_definition_sha256": result.study_definition_sha256,
        "study_result_sha256": sha256(canonical_json(result).encode("utf-8")).hexdigest(),
        "report_path": "city-reports/repeated-study.html",
        "report_sha256": sha256(content).hexdigest(),
        "report_bytes": len(content),
    }
    assert str(tmp_path) not in receipt.model_dump_json()
    assert list((tmp_path / "city-reports").iterdir()) == [
        tmp_path / "city-reports" / "repeated-study.html"
    ]


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
def test_every_existing_destination_is_unchanged_conflict(result, tmp_path, kind, monkeypatch):
    module = report_module()
    destination = tmp_path / "city-reports" / "repeated-study.html"
    destination.parent.mkdir()
    target = tmp_path / "outside.txt"
    target.write_bytes(b"private-original")
    if kind == "file":
        destination.write_bytes(b"original-report")
    elif kind == "directory":
        destination.mkdir()
    else:
        try:
            destination.symlink_to(target if kind == "symlink" else tmp_path / "missing")
        except OSError:
            pytest.skip("this account cannot create file symlinks")
    original_stat = destination.lstat()
    monkeypatch.setattr(module.os, "replace", lambda *args, **kwargs: pytest.fail("no replace"))
    with pytest.raises(module.SpatialReportConflict, match="report destination already exists"):
        module.publish_spatial_study_report(result, tmp_path)
    assert destination.lstat() == original_stat
    assert target.read_bytes() == b"private-original"
    if kind == "file":
        assert destination.read_bytes() == b"original-report"
    assert list(destination.parent.iterdir()) == [destination]


def test_report_directory_symlink_is_refused_without_writes(result, tmp_path):
    module = report_module()
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "city-reports").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")
    with pytest.raises(module.SpatialReportPublicationError, match="report directory is invalid"):
        module.publish_spatial_study_report(result, root)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("real_junction", [False, True])
def test_windows_reparse_directory_refused_without_path_is_junction(
    result, tmp_path, monkeypatch, real_junction
):
    module = report_module()
    directory = tmp_path / "city-reports"
    if real_junction:
        if os.name != "nt":
            pytest.skip("Windows junction boundary")
        import subprocess

        outside = tmp_path / "outside"
        outside.mkdir()
        # cmd's one junction operation uses exact test-owned paths; it never deletes.
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(directory), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    else:
        directory.mkdir()
        real_lstat = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == directory:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
    if hasattr(Path, "is_junction"):
        monkeypatch.setattr(Path, "is_junction", lambda *args: pytest.fail("3.11 has no helper"))
    with pytest.raises(module.SpatialReportPublicationError, match="report directory is invalid"):
        module.publish_spatial_study_report(result, tmp_path)
    if real_junction:
        assert list(outside.iterdir()) == []
    else:
        assert list(directory.iterdir()) == []


@pytest.mark.parametrize("stage", ["open", "write", "zero-write", "fsync", "link"])
def test_precommit_io_failures_remove_temporary_and_leave_no_destination(
    result, tmp_path, monkeypatch, stage
):
    module = report_module()
    real_open, real_write, real_fsync = module.os.open, module.os.write, module.os.fsync

    def open_file(path, flags, *args, **kwargs):
        if flags & os.O_CREAT and stage == "open":
            raise OSError(f"private open at {tmp_path}")
        return real_open(path, flags, *args, **kwargs)

    def write(fd, content):
        if stage == "write":
            raise OSError(f"private write at {tmp_path}")
        if stage == "zero-write":
            return 0
        return real_write(fd, content)

    def fsync(fd):
        if stage == "fsync" and not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(f"private fsync at {tmp_path}")
        return real_fsync(fd)

    def link(*args, **kwargs):
        raise OSError(f"private link at {tmp_path}")

    monkeypatch.setattr(module.os, "open", open_file)
    monkeypatch.setattr(module.os, "write", write)
    monkeypatch.setattr(module.os, "fsync", fsync)
    if stage == "link":
        monkeypatch.setattr(module.os, "link", link)
    with pytest.raises(module.SpatialReportPublicationError) as caught:
        module.publish_spatial_study_report(result, tmp_path)
    assert str(caught.value) == "spatial study report could not be published"
    assert list((tmp_path / "city-reports").iterdir()) == []


def test_partial_write_is_completed_before_atomic_commit(result, tmp_path, monkeypatch):
    module = report_module()
    real_write = module.os.write
    monkeypatch.setattr(module.os, "write", lambda fd, data: real_write(fd, data[:97]))
    module.publish_spatial_study_report(result, tmp_path)
    assert (tmp_path / "city-reports" / "repeated-study.html").read_bytes() == (
        module.render_spatial_study_html(result)
    )


def test_link_commit_late_conflict_cannot_replace_existing_bytes(result, tmp_path, monkeypatch):
    module = report_module()
    real_link = module.os.link
    destination = tmp_path / "city-reports" / "repeated-study.html"

    def link(*args, **kwargs):
        destination.write_bytes(b"concurrent-original")
        return real_link(*args, **kwargs)

    monkeypatch.setattr(module.os, "link", link)
    with pytest.raises(module.SpatialReportConflict):
        module.publish_spatial_study_report(result, tmp_path)
    assert destination.read_bytes() == b"concurrent-original"
    assert list(destination.parent.iterdir()) == [destination]


def test_postcommit_directory_fsync_failure_preserves_complete_success(
    result, tmp_path, monkeypatch
):
    module = report_module()

    def fail(*args):
        raise OSError("directory fsync unsupported or failed")

    monkeypatch.setattr(module, "_fsync_directory", fail)
    receipt = module.publish_spatial_study_report(result, tmp_path)
    destination = tmp_path / receipt.report_path
    assert destination.read_bytes() == module.render_spatial_study_html(result)
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("response", [False, True])
def test_city_report_json_success_then_conflict_preserves_sources(tmp_path, response):
    make_runs(tmp_path, response=response)
    study = tmp_path / "study.json"
    study.write_text(canonical_json(definition(response=response)), encoding="utf-8")
    before = _artifact_bytes(tmp_path / "city-runs")
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-report", str(tmp_path), str(study)]
    )
    assert invocation.exit_code == 0, invocation.output
    receipt = json.loads(invocation.stdout)
    assert len(receipt) == 9 and receipt["report_path"] == "city-reports/repeated-study.html"
    assert invocation.stderr == "" and invocation.stdout.count("\n") == 1
    content = (tmp_path / receipt["report_path"]).read_bytes()
    assert sha256(content).hexdigest() == receipt["report_sha256"]
    assert len(content) == receipt["report_bytes"]
    assert _artifact_bytes(tmp_path / "city-runs") == before
    conflict = CliRunner().invoke(
        app, ["--format", "json", "city-report", str(tmp_path), str(study)]
    )
    assert conflict.exit_code == 3
    assert json.loads(conflict.stdout)["error"]["message"] == "report destination already exists"
    assert (tmp_path / receipt["report_path"]).read_bytes() == content
    assert _artifact_bytes(tmp_path / "city-runs") == before


@pytest.mark.parametrize("kind", ["file", "dangling-directory"])
def test_non_directory_report_parent_is_refused(result, tmp_path, kind):
    module = report_module()
    directory = tmp_path / "city-reports"
    if kind == "file":
        directory.write_bytes(b"original-parent-file")
    else:
        try:
            directory.symlink_to(tmp_path / "missing", target_is_directory=True)
        except OSError:
            pytest.skip("this account cannot create directory symlinks")
    with pytest.raises(module.SpatialReportPublicationError, match="report directory is invalid"):
        module.publish_spatial_study_report(result, tmp_path)
    if kind == "file":
        assert directory.read_bytes() == b"original-parent-file"


def test_temporary_open_collision_is_not_a_report_destination_conflict(
    result, tmp_path, monkeypatch
):
    module = report_module()
    real_open = module.os.open

    def open_file(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            raise FileExistsError("injected temporary collision")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", open_file)
    with pytest.raises(module.SpatialReportPublicationError) as caught:
        module.publish_spatial_study_report(result, tmp_path)
    assert type(caught.value) is module.SpatialReportPublicationError
    assert list((tmp_path / "city-reports").iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows directory sharing semantics")
def test_windows_parent_is_pinned_against_directory_replacement(result, tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    module = report_module()
    directory = tmp_path / "city-reports"
    directory.mkdir()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    invalid = ctypes.c_void_p(-1).value

    def replacement_handle():
        # DELETE access is required to rename or replace this directory.
        return create_file(str(directory), 0x10000, 0x7, None, 3, 0x02200000, None)

    baseline = replacement_handle()
    assert baseline not in {None, invalid}
    close_handle(baseline)
    real_open = module.os.open

    def open_file(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            handle = replacement_handle()
            error = ctypes.get_last_error()
            if handle not in {None, invalid}:
                close_handle(handle)
            assert handle == invalid and error == 32, "report parent permits directory replacement"
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", open_file)
    receipt = module.publish_spatial_study_report(result, tmp_path)
    assert (tmp_path / receipt.report_path).read_bytes() == module.render_spatial_study_html(result)


@pytest.mark.parametrize("mutation", ["identity", "reparse"])
def test_directory_identity_and_reparse_attributes_are_rechecked_before_link(
    result, tmp_path, monkeypatch, mutation
):
    module = report_module()
    directory = tmp_path / "city-reports"
    real_lstat, real_write = Path.lstat, module.os.write
    changed = False

    def write(fd, data):
        nonlocal changed
        written = real_write(fd, data)
        changed = True
        return written

    def lstat(path, *args, **kwargs):
        status = real_lstat(path, *args, **kwargs)
        if path == directory and changed:
            return SimpleNamespace(
                st_mode=status.st_mode,
                st_dev=status.st_dev,
                st_ino=status.st_ino + (1 if mutation == "identity" else 0),
                st_file_attributes=(
                    getattr(status, "st_file_attributes", 0)
                    | (0x400 if mutation == "reparse" else 0)
                ),
            )
        return status

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(module.os, "write", write)
    with pytest.raises(module.SpatialReportPublicationError):
        module.publish_spatial_study_report(result, tmp_path)
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("option", ["--output", "--force", "--open"])
def test_city_report_has_no_output_force_or_browser_option(tmp_path, option):
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-report", str(tmp_path), "study.json", option]
    )
    assert invocation.exit_code == 2
    assert "No such option" in json.loads(invocation.stdout)["error"]["message"]


@pytest.mark.parametrize(
    "stage,code,message",
    [
        ("definition", 2, "spatial study definition is invalid"),
        ("scientific", 2, "spatial study inputs are incompatible"),
        ("artifact", 4, "city run artifacts could not be verified"),
        ("publication", 1, "spatial study report could not be published"),
        ("defect", 1, "spatial study report failed"),
        ("interrupt", 130, "run interrupted"),
    ],
)
def test_city_report_safe_exit_contract(tmp_path, monkeypatch, result, stage, code, message):
    assert "city-report" in {command.name for command in app.registered_commands}, "CLI missing"
    from adlife.cli.commands import city_report
    from adlife.core.ports.run_store import CorruptRunArtifact

    study = tmp_path / "study.json"
    study.write_text(
        "{}" if stage == "definition" else canonical_json(definition(response=True)),
        encoding="utf-8",
    )
    failures = {
        "scientific": ValueError,
        "artifact": CorruptRunArtifact,
        "defect": RuntimeError,
        "publication": report_module().SpatialReportPublicationError,
        "interrupt": KeyboardInterrupt,
    }

    def fail(*args):
        raise failures[stage](f"private-content at {tmp_path}")

    if stage != "definition":
        if stage == "publication":
            monkeypatch.setattr(city_report, "analyze_stored_spatial_study", lambda *args: result)
            monkeypatch.setattr(city_report, "publish_spatial_study_report", fail)
        else:
            monkeypatch.setattr(city_report, "analyze_stored_spatial_study", fail)
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-report", str(tmp_path), str(study)]
    )
    assert invocation.exit_code == code
    assert json.loads(invocation.stdout)["error"]["message"] == message
    assert invocation.stderr == f"error: {message}\n"
    assert str(tmp_path) not in invocation.output and "private-content" not in invocation.output
