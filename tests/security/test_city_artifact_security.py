"""V10 artifact refusal, filesystem identity, and publication commit boundaries."""

from __future__ import annotations

import errno
import json
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from adlife.city import run_store as store_module
from adlife.city.run_store import CityRunStore
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
)
from tests.integration.test_city_cognition_v2_artifacts import (
    V10_PATHS,
    _prepared_v10,
    _rebind_terminal_stream,
    _rehash_terminal,
    _save_prepared,
    _saved_v10,
)


def test_v10_modified_terminal_record_is_corruption_not_fallback(tmp_path):
    _, prepared, directory = _saved_v10(tmp_path, mode="local")
    path = directory / "outputs/cognition.jsonl"
    documents = [json.loads(line) for line in path.read_bytes().splitlines()]
    documents[0]["result"]["affect"] = "annoyed"
    # Complete content-addressed forgery still cannot change request/baseline bindings.
    documents[0]["baseline_sha256"] = "e" * 64
    _rehash_terminal(documents[0])
    _rebind_terminal_stream(directory, documents)
    before = path.read_bytes()
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "identifier",
    [
        "../escape",
        "..",
        ".",
        "a/b",
        "a\\b",
        "/escape",
        "C:\\escape",
        "C:escape",
        "\\\\server\\share",
        "con",
        "nul",
        "prn",
        "aux",
        "com1",
        "lpt9",
        "name.",
        "name ",
        "Bearer synthetic-private-token",
    ],
)
def test_v10_run_identifier_is_refused_before_path_access(tmp_path, monkeypatch, identifier):
    store = CityRunStore(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid identifier reached filesystem access")

    monkeypatch.setattr(store, "_runs_directory", forbidden)
    with pytest.raises(UnsafeRunLocation) as captured:
        store.run_directory(identifier)
    assert identifier not in str(captured.value)


@pytest.mark.parametrize(
    "relative",
    [
        "inputs/cognition.json",
        "outputs/cognition.jsonl",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "run.json",
    ],
)
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_v10_refuses_leaf_symlinks_and_external_hardlinks(tmp_path, relative, link_kind):
    _, prepared, directory = _saved_v10(tmp_path / "root")
    path = directory / relative
    outside = tmp_path / "external-bytes.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    try:
        if link_kind == "symlink":
            path.symlink_to(outside)
        else:
            os.link(outside, path)
    except OSError as error:
        pytest.skip(
            f"{link_kind} creation unavailable on this platform: {error.__class__.__name__}"
        )
    before = outside.read_bytes()
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path / "root").load(prepared.manifest.run_id)
    assert outside.read_bytes() == before


@pytest.mark.parametrize("component", ["root", "city-runs", "run", "inputs", "outputs"])
def test_v10_refuses_linked_original_path_components_even_if_target_is_inside_root(
    tmp_path, component
):
    root = tmp_path / "root"
    _, prepared, directory = _saved_v10(root)
    source = {
        "root": root,
        "city-runs": root / "city-runs",
        "run": directory,
        "inputs": directory / "inputs",
        "outputs": directory / "outputs",
    }[component]
    moved = source.with_name(source.name + "-moved")
    source.rename(moved)
    try:
        source.symlink_to(moved, target_is_directory=True)
    except OSError as error:
        moved.rename(source)
        pytest.skip(f"directory symlink privilege unavailable: {error.__class__.__name__}")
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(root).load(prepared.manifest.run_id)


@pytest.mark.parametrize(
    "relative",
    ["inputs", "outputs", "inputs/cognition.json", "outputs/cognition.jsonl", "run.json"],
)
def test_v10_refuses_synthetic_windows_reparse_attributes_without_following_target(
    tmp_path, monkeypatch, relative
):
    _, prepared, directory = _saved_v10(tmp_path)
    attacked = directory / relative
    original = os.lstat

    def reparse(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if Path(path) == attacked:
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            fields["st_file_attributes"] = getattr(value, "st_file_attributes", 0) | 0x400
            fields["st_reparse_tag"] = 0xA0000003
            return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(os, "lstat", reparse)
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


def test_v10_refuses_open_handle_identity_drift(tmp_path, monkeypatch):
    _, prepared, directory = _saved_v10(tmp_path)
    attacked = directory / "inputs/cognition.json"
    original_open = store_module._open_artifact
    original_fstat = os.fstat
    descriptors = set()

    @contextmanager
    def remember_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as handle:
            if path == attacked:
                descriptors.add(handle.fileno())
            yield handle

    def changed_identity(descriptor):
        value = original_fstat(descriptor)
        if descriptor in descriptors:
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            fields["st_ino"] = value.st_ino + 1
            return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(store_module, "_open_artifact", remember_open)
    monkeypatch.setattr(os, "fstat", changed_identity)
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


def test_v10_refuses_nonregular_sidecar_metadata(tmp_path, monkeypatch):
    _, prepared, directory = _saved_v10(tmp_path)
    attacked = directory / "outputs/cognition.jsonl"
    original = os.lstat

    def nonregular(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if Path(path) == attacked:
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            fields["st_mode"] = stat.S_IFIFO | 0o600
            return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(os, "lstat", nonregular)
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)


@pytest.mark.parametrize("relative", [*sorted(V10_PATHS - {"run.json"}), ".run.json.tmp"])
@pytest.mark.parametrize("exception_type", [OSError, KeyboardInterrupt])
def test_v10_every_precommit_file_failure_leaves_reserved_unloadable_id(
    tmp_path, monkeypatch, relative, exception_type
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = store_module._open_artifact
    injected = False

    @contextmanager
    def fail_one_write(path, mode="r", **kwargs):
        nonlocal injected
        if "x" in mode or "w" in mode:
            assert not (directory / "run.json").exists(), (
                "manifest appeared before staged writes finished"
            )
            if path == directory / relative:
                injected = True
                raise exception_type("synthetic-write-fault")
        with original(path, mode, **kwargs) as handle:
            yield handle

    with monkeypatch.context() as patch:
        patch.setattr(store_module, "_open_artifact", fail_one_write)
        expected = StorageError if exception_type is OSError else KeyboardInterrupt
        with pytest.raises(expected) as error:
            _save_prepared(tmp_path, prepared)
        assert type(error.value) is expected
    assert injected, "the selected publication write was never exercised"
    assert directory.is_dir()
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(prepared.manifest.run_id)
    with pytest.raises(DuplicateRun):
        _save_prepared(tmp_path, prepared)


@pytest.mark.parametrize("relative", [*sorted(V10_PATHS - {"run.json"}), ".run.json.tmp"])
def test_v10_each_staged_readback_failure_precedes_manifest_commit(tmp_path, monkeypatch, relative):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = store_module._open_artifact
    injected = False

    @contextmanager
    def fail_one_read(path, mode="r", **kwargs):
        nonlocal injected
        if "r" in mode and path.is_relative_to(directory):
            assert not (directory / "run.json").exists()
            if path == directory / relative:
                injected = True
                raise OSError("synthetic-readback-fault")
        with original(path, mode, **kwargs) as handle:
            yield handle

    with monkeypatch.context() as patch:
        patch.setattr(store_module, "_open_artifact", fail_one_read)
        if relative == ".run.json.tmp":

            def fail_manifest_read(_source, _limit):
                nonlocal injected
                assert not (directory / "run.json").exists()
                injected = True
                raise OSError("synthetic-readback-fault")

            patch.setattr(store_module, "_read_manifest_stage", fail_manifest_read)
        with pytest.raises(StorageError):
            _save_prepared(tmp_path, prepared)
    assert injected
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()
    assert directory.is_dir()


@pytest.mark.parametrize("failure_index", range(1, 17))
def test_v10_each_file_fsync_failure_precedes_manifest_visibility(
    tmp_path, monkeypatch, failure_index
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = os.fsync
    calls = 0

    def fail_sync(descriptor):
        nonlocal calls
        assert not (directory / "run.json").exists()
        calls += 1
        if calls == failure_index:
            raise OSError("synthetic-fsync-fault")
        return original(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_sync)
        with pytest.raises(StorageError):
            _save_prepared(tmp_path, prepared)
    assert calls == failure_index
    assert directory.is_dir()
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()


def test_v10_existing_destination_is_never_overwritten_or_removed(tmp_path):
    _, prepared, directory = _saved_v10(tmp_path)
    manifest = directory / "run.json"
    before = manifest.read_bytes(), manifest.stat().st_ino
    with pytest.raises(DuplicateRun):
        _save_prepared(tmp_path, prepared)
    assert (manifest.read_bytes(), manifest.stat().st_ino) == before


def _patch_publication(monkeypatch, directory, *, after=False):
    """Inject at actual no-replace syscalls, including a raced legacy replace."""
    marker = b"prior-manifest-must-survive\n"
    invoked = []
    if sys.platform in {"win32", "darwin"}:
        from adlife.city import workbench_city_registry as registry

        native_name = (
            "_link_windows_descriptor" if sys.platform == "win32" else "_clone_stage_on_darwin"
        )
        original = getattr(registry, native_name)

        def native_publish(descriptor, held, name):
            invoked.append("native-exclusive-link")
            if after:
                original(descriptor, held, name)
                raise KeyboardInterrupt("synthetic-after-commit")
            (directory / "run.json").write_bytes(marker)
            return original(descriptor, held, name)

        monkeypatch.setattr(registry, native_name, native_publish)
        return marker, invoked
    for name in ("link", "rename", "replace"):
        original = getattr(os, name)

        def publish(source, destination, *args, _name=name, _original=original, **kwargs):
            if Path(destination).name == "run.json":
                invoked.append(_name)
                if after:
                    _original(source, destination, *args, **kwargs)
                    raise KeyboardInterrupt("synthetic-after-commit")
                (directory / "run.json").write_bytes(marker)
            return _original(source, destination, *args, **kwargs)

        monkeypatch.setattr(os, name, publish)
    return marker, invoked


def test_verified_manifest_stage_allows_only_its_deliberate_publication_link(tmp_path):
    stage = tmp_path / ".run.json.tmp"
    final = tmp_path / "run.json"
    stage.write_bytes(b"verified bytes\n")
    try:
        with store_module._open_artifact(stage, "rb", published_stage=True) as source:
            os.link(stage, final)
            assert source.read() == b"verified bytes\n"
    except OSError as error:
        pytest.skip(f"hard-link creation unavailable: {error.__class__.__name__}")
    with pytest.raises(UnsafeRunLocation), store_module._open_artifact(final, "rb"):
        pass
    stage.unlink()
    with store_module._open_artifact(final, "rb") as source:
        assert source.read() == b"verified bytes\n"


def test_manifest_stage_without_new_publication_link_still_refuses_ctime_change(
    tmp_path, monkeypatch
):
    stage = tmp_path / ".run.json.tmp"
    stage.write_bytes(b"verified bytes\n")
    original = os.fstat
    with (
        pytest.raises(UnsafeRunLocation),
        store_module._open_artifact(stage, "rb", published_stage=True) as source,
    ):
        descriptor = source.fileno()

        def changed_metadata(current):
            status = original(current)
            if current != descriptor:
                return status
            fields = {name: getattr(status, name) for name in dir(status) if name.startswith("st_")}
            fields["st_ctime_ns"] += 1
            return SimpleNamespace(**fields)

        monkeypatch.setattr(os, "fstat", changed_metadata)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory handle sharing contract")
@pytest.mark.parametrize("ancestor", [False, True])
def test_windows_parent_pin_prevents_rename_until_io_scope_closes(tmp_path, ancestor):
    parent = tmp_path / "pinned"
    parent.mkdir()
    child = parent / "inputs"
    child.mkdir()
    with store_module._hold_parents(child if ancestor else parent), pytest.raises(OSError):
        parent.rename(tmp_path / "replacement")
    parent.rename(tmp_path / "replacement")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory sharing contract")
@pytest.mark.parametrize("ancestor", [False, True])
def test_windows_directory_leases_are_reentrant_and_shared_between_readers(tmp_path, ancestor):
    parent = tmp_path / "shared"
    child = parent / "inputs"
    child.mkdir(parents=True)
    entered, second = Event(), Event()

    def first_reader():
        with store_module._hold_parents(child if ancestor else parent):
            entered.set()
            assert second.wait(5)

    def second_reader():
        assert entered.wait(5)
        try:
            with (
                store_module._hold_parents(parent),
                store_module._hold_parents(parent),
                pytest.raises(OSError),
            ):
                parent.rename(tmp_path / "renamed")
        finally:
            second.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first_reader), pool.submit(second_reader)]
        for future in futures:
            future.result(timeout=10)
    parent.rename(tmp_path / "renamed")


def test_v10_directory_inspection_errors_are_typed_and_value_free(tmp_path, monkeypatch):
    _, prepared, _ = _saved_v10(tmp_path)
    original = os.lstat
    marker = "credential-shaped-private-marker"

    def inaccessible(path, *args, **kwargs):
        if Path(path) == tmp_path:
            raise PermissionError(13, marker, str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", inaccessible)
    with pytest.raises(UnsafeRunLocation) as captured:
        CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert marker not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_linux_unsupported_anonymous_stage_fails_closed_without_named_fallback(
    tmp_path, monkeypatch
):
    import errno

    _, prepared = _prepared_v10(tmp_path)
    original_open = os.open
    flag = getattr(os, "O_TMPFILE", 0x40000000)

    @contextmanager
    def synthetic_parents(_path):
        yield None

    def unsupported(path, flags, *args, **kwargs):
        if flags & flag:
            raise OSError(errno.ENOTSUP, "private-filesystem-marker")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(
        store_module, "sys", SimpleNamespace(platform="linux", version_info=sys.version_info)
    )
    monkeypatch.setattr(store_module, "_hold_parents", synthetic_parents)
    monkeypatch.setattr(os, "O_TMPFILE", flag, raising=False)
    monkeypatch.setattr(os, "open", unsupported)
    with pytest.raises(StorageError) as captured:
        _save_prepared(tmp_path, prepared)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    assert directory.is_dir()
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()
    assert "private-filesystem-marker" not in str(captured.value)
    with pytest.raises(DuplicateRun):
        _save_prepared(tmp_path, prepared)


@pytest.mark.parametrize(
    "error_number,accepted", [(errno.EINVAL, True), (errno.ENOTSUP, True), (errno.EIO, False)]
)
def test_directory_sync_ignores_only_unsupported_filesystem_errors(
    tmp_path, monkeypatch, error_number, accepted
):
    import errno

    path = tmp_path / "directory-sync"
    path.mkdir()

    @contextmanager
    def synthetic_parents(_path):
        yield 12345

    def failed_sync(_descriptor):
        raise OSError(error_number, "synthetic-sync-error")

    with monkeypatch.context() as patch:
        patch.setattr(store_module, "sys", SimpleNamespace(platform="linux"))
        patch.setattr(store_module, "_hold_parents", synthetic_parents)
        patch.setattr(os, "fsync", failed_sync)
        if accepted:
            store_module._sync_directory(path)
        else:
            with pytest.raises(OSError):
                store_module._sync_directory(path)
    monkeypatch.setattr(
        os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError(errno.EINVAL, "file-sync-failed"))
    )
    with pytest.raises(OSError):
        store_module._write_new(tmp_path / "file-sync", b"bytes")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows denies writes to pinned evidence")
def test_v10_previously_verified_sidecars_remain_write_pinned_until_commit(tmp_path, monkeypatch):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = store_module._write_manifest_stage
    observed = False

    def attack_before_manifest(source, contents):
        nonlocal observed
        observed = True
        with pytest.raises(OSError):
            (directory / "inputs/cognition.json").write_bytes(b"replacement")
        return original(source, contents)

    monkeypatch.setattr(store_module, "_write_manifest_stage", attack_before_manifest)
    _save_prepared(tmp_path, prepared)
    assert observed
    assert CityRunStore(tmp_path).load(prepared.manifest.run_id).manifest == prepared.manifest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows denies writes to pinned evidence")
def test_v10_load_pins_the_complete_evidence_snapshot_during_reconstruction(tmp_path, monkeypatch):
    _, prepared, directory = _saved_v10(tmp_path)
    original = CityRunStore._reconstruct_v10
    observed = False

    def attack_while_reconstructing(self, *args, **kwargs):
        nonlocal observed
        observed = True
        with pytest.raises(OSError):
            (directory / "inputs/cognition.json").write_bytes(b"replacement")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CityRunStore, "_reconstruct_v10", attack_while_reconstructing)
    assert CityRunStore(tmp_path).load(prepared.manifest.run_id).manifest == prepared.manifest
    assert observed


def test_v10_replaced_stage_name_is_preserved_while_verified_descriptor_publishes(
    tmp_path, monkeypatch
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = store_module._read_manifest_stage
    marker = b"unrelated stage replacement\n"

    replaced = False

    def replace_stage(source, limit):
        nonlocal replaced
        contents = original(source, limit)
        path = directory / ".run.json.tmp"
        if path.exists():
            try:
                path.rename(path.with_name("owned-stage-moved"))
            except OSError:
                return contents
        path.write_bytes(marker)
        replaced = True
        return contents

    monkeypatch.setattr(store_module, "_read_manifest_stage", replace_stage)
    _save_prepared(tmp_path, prepared)
    if replaced:
        assert (directory / ".run.json.tmp").read_bytes() == marker
    assert CityRunStore(tmp_path).load(prepared.manifest.run_id).manifest == prepared.manifest


def test_v10_published_manifest_identity_is_rechecked_after_final_read(tmp_path, monkeypatch):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = CityRunStore._read_document
    observed = False

    def replace_during_final_read(self, path, *, limit, label):
        nonlocal observed
        if label == "published manifest":
            observed = True
            contents = path.read_bytes()
            path.rename(path.with_name("verified-manifest-moved"))
            path.write_bytes(contents)
        return original(self, path, limit=limit, label=label)

    monkeypatch.setattr(CityRunStore, "_read_document", replace_during_final_read)
    with pytest.raises(UnsafeRunLocation):
        _save_prepared(tmp_path, prepared)
    assert observed
    assert (directory / "run.json").is_file()
    assert (directory / "verified-manifest-moved").is_file()
    assert not (directory / ".run.json.tmp").exists()


def test_v10_parent_drift_before_exclusive_publication_cannot_commit_manifest(
    tmp_path, monkeypatch
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original_read = store_module._read_manifest_stage
    original_lstat = os.lstat
    attack = False

    def drift_after_manifest_verification(source, limit):
        nonlocal attack
        result = original_read(source, limit)
        attack = True
        return result

    def replaced_parent(path, *args, **kwargs):
        status = original_lstat(path, *args, **kwargs)
        if attack and Path(path) == directory / "inputs":
            fields = {name: getattr(status, name) for name in dir(status) if name.startswith("st_")}
            fields["st_ino"] += 1
            return SimpleNamespace(**fields)
        return status

    with monkeypatch.context() as patch:
        patch.setattr(store_module, "_read_manifest_stage", drift_after_manifest_verification)
        patch.setattr(os, "lstat", replaced_parent)
        with pytest.raises(UnsafeRunLocation):
            _save_prepared(tmp_path, prepared)
    assert attack
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows holds the directory against rename")
def test_posix_input_directory_replacement_before_link_is_not_committed(tmp_path, monkeypatch):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original = store_module._read_manifest_stage

    def replace_inputs(source, limit):
        data = original(source, limit)
        (directory / "inputs").rename(directory / "inputs-moved")
        (directory / "inputs").mkdir()
        return data

    monkeypatch.setattr(store_module, "_read_manifest_stage", replace_inputs)
    with pytest.raises(UnsafeRunLocation):
        _save_prepared(tmp_path, prepared)
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()


def test_manifest_stage_cleanup_never_unlinks_a_postcheck_replacement_name(tmp_path, monkeypatch):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original_unlink = Path.unlink
    attempted = False

    def race_at_unlink(path, *args, **kwargs):
        nonlocal attempted
        if path == directory / ".run.json.tmp":
            attempted = True
            if path.exists():
                path.rename(path.with_name("stage-owned-inode-moved"))
            path.write_bytes(b"unrelated replacement must survive")
            raise AssertionError("manifest cleanup used a mutable pathname")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", race_at_unlink)
    _save_prepared(tmp_path, prepared)
    assert not attempted
    assert CityRunStore(tmp_path).load(prepared.manifest.run_id).manifest == prepared.manifest


@pytest.mark.parametrize("operation", ["save", "load"])
@pytest.mark.parametrize("programming_error", [False, True])
@pytest.mark.parametrize("close_error", ["missing", "unsafe"])
def test_v10_missing_sidecar_at_snapshot_close_has_value_free_typed_error(
    tmp_path, monkeypatch, operation, programming_error, close_error
):
    if operation == "save":
        _, prepared = _prepared_v10(tmp_path)
        directory = tmp_path / "city-runs" / prepared.manifest.run_id
    else:
        _, prepared, directory = _saved_v10(tmp_path)
    original_lstat = os.lstat
    original_read = store_module._read_manifest_stage
    original_reconstruct = CityRunStore._reconstruct_v10
    attack = False

    def vanished_leaf(path, *args, **kwargs):
        if attack and Path(path) == directory / "inputs/cognition.json":
            if close_error == "unsafe":
                raise UnsafeRunLocation("synthetic unsafe snapshot")
            raise FileNotFoundError(errno.ENOENT, "credential-shaped-marker", str(path))
        return original_lstat(path, *args, **kwargs)

    def before_commit(source, limit):
        nonlocal attack
        result = original_read(source, limit)
        attack = True
        if programming_error:
            raise TypeError("injected programming defect")
        return result

    def after_reconstruction(self, *args, **kwargs):
        nonlocal attack
        result = original_reconstruct(self, *args, **kwargs)
        attack = True
        if programming_error:
            raise TypeError("injected programming defect")
        return result

    monkeypatch.setattr(os, "lstat", vanished_leaf)
    if operation == "save":
        monkeypatch.setattr(store_module, "_read_manifest_stage", before_commit)
    else:
        monkeypatch.setattr(CityRunStore, "_reconstruct_v10", after_reconstruction)
    expected = (
        TypeError
        if programming_error
        else (
            UnsafeRunLocation
            if close_error == "unsafe"
            else (StorageError if operation == "save" else CorruptRunArtifact)
        )
    )
    with pytest.raises(expected) as error:
        if operation == "save":
            _save_prepared(tmp_path, prepared)
        else:
            CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert attack
    assert "credential-shaped-marker" not in str(error.value)
    assert str(directory) not in str(error.value)
    if operation == "save":
        assert not (directory / "run.json").exists()


@pytest.mark.parametrize("operation", ["save", "load"])
def test_v10_ctime_only_sidecar_change_cannot_evade_snapshot_verification(
    tmp_path, monkeypatch, operation
):
    if operation == "save":
        _, prepared = _prepared_v10(tmp_path)
        directory = tmp_path / "city-runs" / prepared.manifest.run_id
    else:
        _, prepared, directory = _saved_v10(tmp_path)
    original_lstat = os.lstat
    original_fstat = os.fstat
    original_read = store_module._read_manifest_stage
    original_reconstruct = CityRunStore._reconstruct_v10
    attack = False

    def changed_status(status):
        fields = {name: getattr(status, name) for name in dir(status) if name.startswith("st_")}
        fields["st_ctime_ns"] += 1
        return SimpleNamespace(**fields)

    def replaced_metadata(path, *args, **kwargs):
        status = original_lstat(path, *args, **kwargs)
        if attack and Path(path) == directory / "inputs/cognition.json":
            return changed_status(status)
        return status

    def modified_handle(descriptor):
        status = original_fstat(descriptor)
        if attack:
            expected = original_lstat(directory / "inputs/cognition.json")
            if (status.st_dev, status.st_ino) == (expected.st_dev, expected.st_ino):
                return changed_status(status)
        return status

    def before_commit(source, limit):
        nonlocal attack
        result = original_read(source, limit)
        attack = True
        return result

    def after_reconstruction(self, *args, **kwargs):
        nonlocal attack
        result = original_reconstruct(self, *args, **kwargs)
        attack = True
        return result

    monkeypatch.setattr(os, "lstat", replaced_metadata)
    monkeypatch.setattr(os, "fstat", modified_handle)
    if operation == "save":
        monkeypatch.setattr(store_module, "_read_manifest_stage", before_commit)
    else:
        monkeypatch.setattr(CityRunStore, "_reconstruct_v10", after_reconstruction)
    with pytest.raises(UnsafeRunLocation):
        if operation == "save":
            _save_prepared(tmp_path, prepared)
        else:
            CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert attack
    if operation == "save":
        assert not (directory / "run.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows denies writes to pinned sidecars")
def test_posix_same_size_sidecar_write_with_restored_mtime_cannot_commit(tmp_path, monkeypatch):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    original_read = store_module._read_manifest_stage

    def modify_before_commit(source, limit):
        result = original_read(source, limit)
        path = directory / "inputs/cognition.json"
        before = path.stat()
        data = path.read_bytes()
        path.write_bytes(b"!" + data[1:])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(store_module, "_read_manifest_stage", modify_before_commit)
    with pytest.raises(UnsafeRunLocation):
        _save_prepared(tmp_path, prepared)
    assert not (directory / "run.json").exists()


def test_v10_raced_manifest_collision_preserves_prior_bytes_at_publication_syscall(
    tmp_path, monkeypatch
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    with monkeypatch.context() as patch:
        marker, invoked = _patch_publication(patch, directory)
        with pytest.raises(DuplicateRun):
            _save_prepared(tmp_path, prepared)
    assert invoked, "test must intercept the final publication syscall"
    assert (directory / "run.json").read_bytes() == marker
    assert not (directory / ".run.json.tmp").exists()


@pytest.mark.parametrize("phase", ["initial-load", "final-load", "precommit"])
def test_v10_sidecar_whitelist_refuses_first_unknown_without_enumerating_rest(
    tmp_path, monkeypatch, phase
):
    if phase == "precommit":
        _, prepared = _prepared_v10(tmp_path)
        directory = tmp_path / "city-runs" / prepared.manifest.run_id
    else:
        _, prepared, directory = _saved_v10(tmp_path)
    original_scandir = os.scandir
    original_reconstruct = CityRunStore._reconstruct_v10
    original_read = store_module._read_manifest_stage
    attack = phase == "initial-load"
    visited = 0

    def unknown_then_unbounded(path):
        nonlocal visited
        visited += 1
        yield SimpleNamespace(name="undeclared-secret-shaped-entry")
        raise AssertionError("enumerated attacker entries after first undeclared name")

    @contextmanager
    def hostile_entries(path):
        if attack and Path(path) == directory / "inputs":
            yield unknown_then_unbounded(path)
        else:
            with original_scandir(path) as entries:
                yield entries

    def after_reconstruction(self, *args, **kwargs):
        nonlocal attack
        result = original_reconstruct(self, *args, **kwargs)
        attack = True
        return result

    def before_commit(source, limit):
        nonlocal attack
        result = original_read(source, limit)
        attack = True
        return result

    monkeypatch.setattr(os, "scandir", hostile_entries)
    if phase == "final-load":
        monkeypatch.setattr(CityRunStore, "_reconstruct_v10", after_reconstruction)
    elif phase == "precommit":
        monkeypatch.setattr(store_module, "_read_manifest_stage", before_commit)
    expected = StorageError if phase == "precommit" else CorruptRunArtifact
    with pytest.raises(expected) as error:
        if phase == "precommit":
            _save_prepared(tmp_path, prepared)
        else:
            CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert type(error.value) is expected
    assert "undeclared-secret" not in str(error.value)
    assert visited == 1
    if phase == "precommit":
        assert not (directory / "run.json").exists()


@pytest.mark.parametrize("fail_parent_sync", [False, True])
def test_manifest_publication_syncs_reserved_run_parent_before_visibility(
    tmp_path, monkeypatch, fail_parent_sync
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    calls = []

    def observe_sync(path):
        calls.append((path, (directory / "run.json").exists()))
        if path == directory.parent and fail_parent_sync:
            raise OSError(errno.EIO, "parent-sync-failed")

    monkeypatch.setattr(store_module, "_sync_directory", observe_sync)
    if fail_parent_sync:
        with pytest.raises(StorageError):
            _save_prepared(tmp_path, prepared)
        assert not (directory / "run.json").exists()
    else:
        _save_prepared(tmp_path, prepared)
    assert (directory.parent, False) in calls
    assert calls.index((directory, False)) < calls.index((directory.parent, False))


def test_v10_interrupt_after_successful_exclusive_publication_preserves_committed_bytes(
    tmp_path, monkeypatch
):
    _, prepared = _prepared_v10(tmp_path)
    directory = tmp_path / "city-runs" / prepared.manifest.run_id
    with monkeypatch.context() as patch:
        _, invoked = _patch_publication(patch, directory, after=True)
        with pytest.raises(KeyboardInterrupt):
            _save_prepared(tmp_path, prepared)
    assert invoked
    assert (directory / "run.json").is_file()
    assert not (directory / ".run.json.tmp").exists()
    assert CityRunStore(tmp_path).load(prepared.manifest.run_id).manifest == prepared.manifest
