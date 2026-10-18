from __future__ import annotations

import importlib
import os
import stat
from pathlib import Path

import pytest

import adlife.city.workbench_immutable_registry as immutable_registry
from adlife.city.workbench_workspace import prepare_workbench_workspace

KEY = "a" * 64
NAMESPACE = "city-cognition-cache"


def _module():
    # Import inside tests: a missing implementation must fail a collected node.
    return importlib.import_module("adlife.adapters.cognition.city_cache_storage")


def _storage(tmp_path: Path, *, namespace: str = NAMESPACE, limit: int = 32):
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    return _module().CityCognitionCacheByteStorage(
        workspace, namespace=namespace, max_record_bytes=limit
    )


def _path(storage, key: str = KEY, namespace: str = NAMESPACE) -> Path:
    return storage.workspace.root / namespace / f"{key}.json"


def test_cache_byte_storage_refuses_namespace_and_link_escape(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError):
        module.CityCognitionCacheByteStorage(workspace, namespace="../outside", max_record_bytes=32)
    directory = workspace.root / NAMESPACE
    try:
        directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")
    storage = module.CityCognitionCacheByteStorage(
        workspace, namespace=NAMESPACE, max_record_bytes=32
    )
    with pytest.raises(module.CityCognitionCacheUnavailable):
        storage.read(KEY)
    with pytest.raises(module.CityCognitionCacheUnavailable):
        storage.publish(KEY, b"opaque")
    assert tuple(outside.iterdir()) == ()


@pytest.mark.parametrize("namespace", ["../outside", "other", "", "/abs", None, []])
def test_invalid_namespace_is_rejected_before_workspace_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: object
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid namespace reached the filesystem")

    monkeypatch.setattr(module, "verify_workbench_workspace", forbidden)
    with pytest.raises(ValueError):
        module.CityCognitionCacheByteStorage(workspace, namespace=namespace, max_record_bytes=32)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, None])
def test_invalid_byte_bound_is_refused_before_workspace_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: object
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid bound reached the filesystem")

    monkeypatch.setattr(module, "verify_workbench_workspace", forbidden)
    with pytest.raises(ValueError):
        module.CityCognitionCacheByteStorage(workspace, namespace=NAMESPACE, max_record_bytes=limit)


@pytest.mark.parametrize(
    "key", ["../outside", "A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n", "", None]
)
@pytest.mark.parametrize("operation", ["read", "publish"])
def test_invalid_keys_never_reach_directory_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: object, operation: str
) -> None:
    module = _module()
    storage = _storage(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid key reached the filesystem")

    monkeypatch.setattr(module, "find_workbench_subdirectory", forbidden)
    monkeypatch.setattr(module, "prepare_workbench_subdirectory", forbidden)
    with pytest.raises(ValueError) as caught:
        if operation == "read":
            storage.read(key)
        else:
            storage.publish(key, b"opaque")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert not _path(storage).parent.exists()


def test_namespace_isolation_preserves_exact_schema_blind_bytes(tmp_path: Path) -> None:
    first = _storage(tmp_path)
    second = _module().CityCognitionCacheByteStorage(
        first.workspace, namespace="city-cognition-cache-v2", max_record_bytes=32
    )
    first.publish(KEY, b"\xff\x00not-json\r\n")
    second.publish(KEY, b"second namespace\n")

    assert _path(first).read_bytes() == b"\xff\x00not-json\r\n"
    assert _path(second, namespace="city-cognition-cache-v2").read_bytes() == b"second namespace\n"
    assert first.read(KEY) == b"\xff\x00not-json\r\n"
    assert second.read(KEY) == b"second namespace\n"


def test_only_absent_names_miss_and_empty_bytes_are_a_hit(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    assert storage.read(KEY) is None
    assert not _path(storage).parent.exists()
    storage.publish(KEY, b"")
    assert storage.read(KEY) == b""
    assert storage.read("b" * 64) is None


def test_size_bound_applies_to_reads_and_publications(tmp_path: Path) -> None:
    module = _module()
    storage = _storage(tmp_path, limit=3)
    with pytest.raises(ValueError):
        storage.publish(KEY, b"four")
    assert not _path(storage).parent.exists()
    storage.publish(KEY, b"123")
    assert storage.read(KEY) == b"123"
    _path(storage).write_bytes(b"1234")
    with pytest.raises(module.CityCognitionCacheCorruption):
        storage.read(KEY)
    assert _path(storage).read_bytes() == b"1234"


def test_read_remains_bounded_if_size_grows_after_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    storage = _storage(tmp_path, limit=3)
    storage.publish(KEY, b"123")
    real_read = os.read
    requests = []

    def grow_then_read(descriptor: int, count: int) -> bytes:
        requests.append(count)
        if len(requests) == 1:
            _path(storage).write_bytes(b"123456789")
        return real_read(descriptor, count)

    monkeypatch.setattr(module.os, "read", grow_then_read)
    with pytest.raises(module.CityCognitionCacheCorruption):
        storage.read(KEY)
    assert requests == [4]


@pytest.mark.parametrize("document", ["text", bytearray(b"bytes"), None])
def test_non_bytes_are_refused_without_creating_a_namespace(
    tmp_path: Path, document: object
) -> None:
    storage = _storage(tmp_path)
    with pytest.raises(TypeError):
        storage.publish(KEY, document)
    assert not _path(storage).parent.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_redirected_or_nonregular_records_are_preserved_and_refused(
    tmp_path: Path, kind: str
) -> None:
    module = _module()
    storage = _storage(tmp_path)
    path = _path(storage)
    path.parent.mkdir()
    outside = tmp_path / "outside-record"
    outside.write_bytes(b"outside")
    if kind == "directory":
        path.mkdir()
    else:
        try:
            if kind == "symlink":
                path.symlink_to(outside)
            else:
                os.link(outside, path)
        except OSError:
            pytest.skip(f"this filesystem cannot create a {kind}")
    with pytest.raises(module.CityCognitionCacheCorruption):
        storage.read(KEY)
    assert path.exists()
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("mutation", ["reparse", "links", "identity"])
def test_post_read_entry_checks_refuse_reparse_link_count_and_identity_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    module = _module()
    storage = _storage(tmp_path)
    storage.publish(KEY, b"original")
    real_read = immutable_registry._read_record
    real_lstat = os.lstat
    read_finished = False

    class ChangedStatus:
        def __init__(self, base: os.stat_result) -> None:
            self._base = base
            self.st_file_attributes = getattr(base, "st_file_attributes", 0)
            self.st_nlink = base.st_nlink
            self.st_ino = base.st_ino
            if mutation == "reparse":
                self.st_file_attributes |= getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            elif mutation == "links":
                self.st_nlink = 2
            else:
                self.st_ino += 1

        def __getattr__(self, name: str) -> object:
            return getattr(self._base, name)

    def read_then_mutate(*args, **kwargs):
        nonlocal read_finished
        result = real_read(*args, **kwargs)
        read_finished = True
        return result

    def changed_lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if read_finished and Path(path) == _path(storage):
            return ChangedStatus(result)
        return result

    monkeypatch.setattr(immutable_registry, "_read_record", read_then_mutate)
    monkeypatch.setattr(module.os, "lstat", changed_lstat)
    with pytest.raises(module.CityCognitionCacheCorruption):
        storage.read(KEY)
    assert _path(storage).read_bytes() == b"original"


def test_no_clobber_preserves_first_bytes_and_releases_conflicting_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    storage = _storage(tmp_path)
    storage.publish(KEY, b"first")
    real_release = immutable_registry._release_stage_descriptor
    released = []

    def release(descriptor, *, committed):
        real_release(descriptor, committed=committed)
        released.append((descriptor, committed))

    monkeypatch.setattr(immutable_registry, "_release_stage_descriptor", release)
    with pytest.raises(module.CityCognitionCacheConflict):
        storage.publish(KEY, b"second")
    assert storage.read(KEY) == b"first"
    assert len(released) == 1
    assert released[0][1] is False
    with pytest.raises(OSError):
        os.fstat(released[0][0])


def test_publication_fsyncs_verified_bytes_before_link_and_syncs_directory_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    storage = _storage(tmp_path)
    real_fsync = os.fsync
    real_publish = immutable_registry._publish_stage_descriptor
    real_sync = immutable_registry._sync_pinned_directory
    events = []

    def fsync(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            events.append("stage-fsync")
        return real_fsync(descriptor)

    def publish(descriptor, directory, filename):
        assert events == ["stage-fsync"]
        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, 32) == b"durable"
        result = real_publish(descriptor, directory, filename)
        events.append("link")
        return result

    def sync(directory):
        assert _path(storage).read_bytes() == b"durable"
        result = real_sync(directory)
        events.append("directory-sync")
        return result

    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(immutable_registry, "_publish_stage_descriptor", publish)
    monkeypatch.setattr(immutable_registry, "_sync_pinned_directory", sync)
    storage.publish(KEY, b"durable")
    assert events == ["stage-fsync", "link", "directory-sync"]
    assert storage.read(KEY) == b"durable"


@pytest.mark.parametrize("failure_point", ["write", "fsync", "publish", "stage-status"])
def test_abandoned_stage_handles_are_released_and_no_record_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    module = _module()
    storage = _storage(tmp_path)
    real_fstat = os.fstat
    real_fsync = os.fsync
    descriptor_seen = None

    def inspect_stage(descriptor):
        nonlocal descriptor_seen
        result = real_fstat(descriptor)
        if stat.S_ISREG(result.st_mode):
            descriptor_seen = descriptor
            if failure_point == "stage-status":
                raise OSError("hostile filesystem detail")
        return result

    def fail(*_args, **_kwargs):
        raise OSError("hostile filesystem detail")

    def fail_stage_fsync(descriptor):
        if stat.S_ISREG(real_fstat(descriptor).st_mode):
            fail()
        return real_fsync(descriptor)

    monkeypatch.setattr(module.os, "fstat", inspect_stage)
    if failure_point == "write":
        monkeypatch.setattr(module.os, "write", fail)
    elif failure_point == "fsync":
        monkeypatch.setattr(module.os, "fsync", fail_stage_fsync)
    elif failure_point == "publish":
        monkeypatch.setattr(immutable_registry, "_publish_stage_descriptor", fail)
    with pytest.raises(module.CityCognitionCachePublicationError) as caught:
        storage.publish(KEY, b"abandoned")
    assert caught.value.potentially_committed is False
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "hostile" not in repr(caught.value)
    assert descriptor_seen is not None
    with pytest.raises(OSError):
        real_fstat(descriptor_seen)
    assert not _path(storage).exists()
    assert all(path.stat().st_size == 0 for path in _path(storage).parent.iterdir())


def test_post_link_failure_with_unreadable_evidence_remains_potentially_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    storage = _storage(tmp_path)

    def fail_sync(_directory):
        raise OSError("post-link failure")

    real_read = immutable_registry._read_record
    calls = 0

    def read_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise PermissionError("hostile evidence")
        return real_read(*args, **kwargs)

    monkeypatch.setattr(module, "_sync_directory", fail_sync)
    monkeypatch.setattr(immutable_registry, "_read_record", read_then_fail)
    with pytest.raises(module.CityCognitionCachePublicationError) as caught:
        storage.publish(KEY, b"committed")
    assert caught.value.potentially_committed is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _path(storage).read_bytes() == b"committed"


@pytest.mark.parametrize(
    ("committed", "message"),
    [
        (False, "city cognition cache publication failed before commit"),
        (True, "city cognition cache publication may already be committed"),
    ],
)
def test_publication_errors_preserve_boolean_state_and_constant_messages(committed, message):
    module = _module()
    error = module.CityCognitionCachePublicationError(committed)
    assert error.potentially_committed is committed
    assert str(error) == message
    with pytest.raises(TypeError, match="publication state must be a boolean"):
        module.CityCognitionCachePublicationError(int(committed))
