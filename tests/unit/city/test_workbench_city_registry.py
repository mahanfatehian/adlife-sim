from __future__ import annotations

import copy
import errno
import importlib
import importlib.util
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adlife.city import workbench_workspace as workspace_module
from adlife.city.catalog import select_catalog_city
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.serialization import canonical_json


def _module():
    spec = importlib.util.find_spec("adlife.city.workbench_city_registry")
    assert spec is not None, "workspace city registry is not implemented"
    return importlib.import_module("adlife.city.workbench_city_registry")


def _payload(city_id: str = "operator-city") -> dict[str, Any]:
    payload = select_catalog_city("fictional-grid-v2").model_dump(mode="json")
    payload["city_id"] = city_id
    payload["name"] = f"Operator City {city_id}"
    return payload


def _document(city_id: str = "operator-city", *, indent: int | None = None) -> bytes:
    return json.dumps(
        _payload(city_id),
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
    ).encode("utf-8")


def _registry(tmp_path: Path):
    return _module().WorkspaceCityRegistry(prepare_workbench_workspace(tmp_path / "workspace"))


def _stored_bytes(registry: object, city_id: str) -> bytes:
    workspace = registry.workspace  # type: ignore[attr-defined]
    return (workspace.root / "city-packs" / f"{city_id}.json").read_bytes()


def _assert_no_live_stage_bytes(registry: object) -> None:
    workspace = registry.workspace  # type: ignore[attr-defined]
    staging = workspace.root / "city-pack-staging"
    paths = list(staging.iterdir()) if staging.exists() else []
    if os.name != "nt":
        records = workspace.root / "city-packs"
        record_paths = list(records.iterdir()) if records.exists() else []
        for path in paths:
            assert path.is_file() and not path.is_symlink()
            if path.read_bytes() != b"":
                assert any(
                    record.is_file() and not record.is_symlink() and os.path.samefile(path, record)
                    for record in record_paths
                )
    else:
        assert paths == []


def test_register_is_canonical_immutable_and_restart_discoverable(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    registry = module.WorkspaceCityRegistry(workspace)

    first = registry.register(_document("zeta-city", indent=2), forbidden_city_ids=frozenset())
    second = registry.register(_document("alpha-city"), forbidden_city_ids=frozenset())

    assert first.city_id == "zeta-city"
    assert second.city_id == "alpha-city"
    assert _stored_bytes(registry, "zeta-city") == (canonical_json(first).encode("utf-8") + b"\n")
    restarted = module.WorkspaceCityRegistry(prepare_workbench_workspace(tmp_path / "workspace"))
    discovered = restarted.discover()
    assert tuple(pack.city_id for pack in discovered) == ("alpha-city", "zeta-city")
    assert discovered[1] == first
    assert discovered[1].fingerprint == first.fingerprint

    with pytest.raises(module.CityPackRegistrationConflict):
        restarted.register(_document("zeta-city"), forbidden_city_ids=frozenset())
    assert _stored_bytes(registry, "zeta-city") == canonical_json(first).encode("utf-8") + b"\n"


def test_registry_instances_for_different_workspaces_are_isolated(tmp_path: Path) -> None:
    first = _module().WorkspaceCityRegistry(prepare_workbench_workspace(tmp_path / "first"))
    second = _module().WorkspaceCityRegistry(prepare_workbench_workspace(tmp_path / "second"))

    first.register(_document(), forbidden_city_ids=frozenset())

    assert tuple(pack.city_id for pack in first.discover()) == ("operator-city",)
    assert second.discover() == ()


def test_register_owns_one_strict_parse_and_never_reflects_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    calls: list[tuple[bytes, int]] = []
    real_parser = module.strict_json_object

    def counting_parser(data: bytes, *, max_body_bytes: int) -> dict[str, object]:
        calls.append((data, max_body_bytes))
        return real_parser(data, max_body_bytes=max_body_bytes)

    monkeypatch.setattr(module, "strict_json_object", counting_parser)
    document = _document("one-parse")
    registry.register(document, forbidden_city_ids=frozenset())

    assert calls == [(document, module.MAX_CITY_PACK_UPLOAD_BYTES)]

    marker = "private-marker-never-reflect"
    with pytest.raises(module.MalformedCityPackUpload) as captured:
        registry.register(marker.encode(), forbidden_city_ids=frozenset())
    assert marker not in str(captured.value)


@pytest.mark.parametrize(
    "document",
    [
        b"{",
        b"[]",
        b'"scalar"',
        b'{"schema_version":2,"schema_version":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e999}',
        b"\xef\xbb\xbf{}",
        "{}".encode("utf-16"),
        "{}".encode("utf-32"),
        b'{"value":"\\ud800"}',
        ('{"value":' + "[" * 40 + "0" + "]" * 40 + "}").encode(),
    ],
)
def test_register_refuses_malformed_or_ambiguous_json_without_artifacts(
    tmp_path: Path, document: bytes
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    with pytest.raises(module.MalformedCityPackUpload):
        registry.register(document, forbidden_city_ids=frozenset())

    assert registry.discover() == ()
    _assert_no_live_stage_bytes(registry)


@pytest.mark.parametrize("schema_version", [1, True, 2.0, "2", None, 3])
def test_register_requires_exact_v2_schema_version(tmp_path: Path, schema_version: object) -> None:
    module = _module()
    registry = _registry(tmp_path)
    payload = _payload()
    payload["schema_version"] = schema_version

    with pytest.raises(module.InvalidCityPackUpload):
        registry.register(json.dumps(payload).encode(), forbidden_city_ids=frozenset())

    assert registry.discover() == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": "field"}),
        lambda payload: payload.update({"city_id": "con"}),
        lambda payload: payload.update({"city_id": "../escape"}),
        lambda payload: payload.update({"city_id": "C-drive"}),
        lambda payload: payload["roads"][0].update({"target_node": "missing-node"}),
        lambda payload: payload["source"].update({"provider": ""}),
        lambda payload: payload["source"].update({"source_url": "https://user@example.org/x"}),
    ],
)
def test_register_refuses_invalid_graph_provenance_and_portable_identifiers(
    tmp_path: Path, mutate: Any
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    payload = copy.deepcopy(_payload())
    mutate(payload)

    with pytest.raises(module.InvalidCityPackUpload):
        registry.register(json.dumps(payload).encode(), forbidden_city_ids=frozenset())

    assert registry.discover() == ()


def test_raw_and_canonical_byte_limits_are_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    oversized = b"{" + b"x" * module.MAX_CITY_PACK_UPLOAD_BYTES
    with pytest.raises(module.CityPackUploadTooLarge):
        registry.register(oversized, forbidden_city_ids=frozenset())

    real_canonical = module.canonical_json
    monkeypatch.setattr(
        module,
        "canonical_json",
        lambda pack: real_canonical(pack) + " " * module.MAX_CITY_PACK_UPLOAD_BYTES,
    )
    with pytest.raises(module.CityPackUploadTooLarge):
        registry.register(_document(), forbidden_city_ids=frozenset())
    assert registry.discover() == ()


def test_forbidden_and_existing_identifiers_conflict_without_mutation(tmp_path: Path) -> None:
    module = _module()
    registry = _registry(tmp_path)
    marker = _document("packaged-city")

    with pytest.raises(module.CityPackRegistrationConflict):
        registry.register(marker, forbidden_city_ids=frozenset({"packaged-city"}))
    assert registry.discover() == ()

    accepted = registry.register(_document("existing-city"), forbidden_city_ids=frozenset())
    before = _stored_bytes(registry, "existing-city")
    with pytest.raises(module.CityPackRegistrationConflict):
        registry.register(_document("existing-city", indent=2), forbidden_city_ids=frozenset())
    assert _stored_bytes(registry, "existing-city") == before
    assert accepted.city_id == "existing-city"


def test_discovery_fails_closed_on_unknown_noncanonical_or_mismatched_records(
    tmp_path: Path,
) -> None:
    module = _module()
    cases = (
        ("README.txt", b"not a record"),
        ("../impossible", b""),
        (
            "other-city.json",
            canonical_json(module._validate_city_payload(_payload())).encode() + b"\n",
        ),
        ("operator-city.json", _document(indent=2) + b"\n"),
        ("operator-city.json", canonical_json(module._validate_city_payload(_payload())).encode()),
    )
    for index, (name, content) in enumerate(cases):
        workspace = prepare_workbench_workspace(tmp_path / f"workspace-{index}")
        records = workspace.root / "city-packs"
        records.mkdir()
        if name != "../impossible":
            (records / name).write_bytes(content)
        else:
            (records / "..escape.json").write_bytes(content)
        registry = module.WorkspaceCityRegistry(workspace)
        with pytest.raises(module.WorkspaceCityRegistryError):
            registry.discover()


def test_discovery_refuses_symlink_and_nonregular_entries(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    records = workspace.root / "city-packs"
    records.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(_document())
    linked = records / "operator-city.json"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("this account cannot create file symlinks")

    with pytest.raises(module.WorkspaceCityRegistryError):
        module.WorkspaceCityRegistry(workspace).discover()

    assert outside.read_bytes() == _document()


def test_discovery_rechecks_the_complete_directory_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    registry.register(_document(), forbidden_city_ids=frozenset())
    real_load = module._load_record

    def mutate_after_load(directory: object, name: str, **kwargs: object):
        pack = real_load(directory, name, **kwargs)
        records = registry.workspace.root / "city-packs"
        (records / "unexpected.txt").write_text("late mutation", encoding="utf-8")
        return pack

    monkeypatch.setattr(module, "_load_record", mutate_after_load)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()


def test_staging_orphans_are_ignored_but_registry_directory_links_are_refused(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    staging = workspace.root / "city-pack-staging"
    staging.mkdir()
    (staging / "opaque-orphan.stage").write_bytes(b"incomplete secret marker")
    assert module.WorkspaceCityRegistry(workspace).discover() == ()

    records = workspace.root / "city-packs"
    records.mkdir()
    records.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        records.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")
    with pytest.raises(module.WorkspaceCityRegistryError):
        module.WorkspaceCityRegistry(workspace).discover()


def test_capacity_is_exactly_fifty_and_discovery_is_stable(tmp_path: Path) -> None:
    module = _module()
    registry = _registry(tmp_path)
    records = registry.workspace.root / "city-packs"
    records.mkdir()
    for number in reversed(range(module.MAX_WORKSPACE_CITY_PACKS)):
        city_id = f"city-{number:02d}"
        pack = module._validate_city_payload(_payload(city_id))
        (records / f"{city_id}.json").write_bytes(canonical_json(pack).encode() + b"\n")

    assert tuple(pack.city_id for pack in registry.discover()) == tuple(
        f"city-{number:02d}" for number in range(module.MAX_WORKSPACE_CITY_PACKS)
    )
    with pytest.raises(module.CityPackRegistrationConflict):
        registry.register(_document("city-50"), forbidden_city_ids=frozenset())


def test_registry_refuses_a_replaced_workspace_root(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    registry = module.WorkspaceCityRegistry(workspace)
    original = tmp_path / "original"
    workspace.root.rename(original)
    workspace.root.mkdir()

    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.register(_document(), forbidden_city_ids=frozenset())
    assert not list(workspace.root.iterdir())


def test_registry_refuses_a_subdirectory_without_stable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    (registry.workspace.root / "city-packs").mkdir()
    real_subdirectory = module._subdirectory

    def erase_identity(*args: object, **kwargs: object):
        pinned = real_subdirectory(*args, **kwargs)
        if pinned is None:
            return None
        return type(pinned)(
            workspace=pinned.workspace,
            name=pinned.name,
            path=pinned.path,
            device=None,
            inode=None,
        )

    monkeypatch.setattr(module, "_subdirectory", erase_identity)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()


@pytest.mark.parametrize("failure_point", ["mkdir", "post_create_stat"])
def test_posix_subdirectory_creation_normalizes_inspection_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    root = module._HeldRoot(
        workspace=workspace,
        path=workspace.root,
        identity=(1, 1),
        file_descriptor=101,
        windows_handle=None,
    )
    stat_calls = 0

    def failing_stat(*_args: object, **_kwargs: object) -> os.stat_result:
        nonlocal stat_calls
        stat_calls += 1
        if stat_calls == 1:
            raise FileNotFoundError
        raise PermissionError

    def failing_mkdir(*_args: object, **_kwargs: object) -> None:
        if failure_point == "mkdir":
            raise PermissionError

    monkeypatch.setattr(module, "_verify_held_root", lambda _root: None)
    monkeypatch.setattr(module.os, "stat", failing_stat)
    monkeypatch.setattr(module.os, "mkdir", failing_mkdir)

    with pytest.raises(module.WorkspaceCityRegistryError):
        module._subdirectory(root, "city-packs", create=True)


def test_register_normalizes_post_open_lock_inspection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_status = module._root_entry_status
    lock_status_calls = 0

    def fail_after_open(root: object, name: str) -> os.stat_result:
        nonlocal lock_status_calls
        if name == module._LOCK_FILE:
            lock_status_calls += 1
            if lock_status_calls == 2:
                raise PermissionError
        return real_status(root, name)

    monkeypatch.setattr(module, "_root_entry_status", fail_after_open)

    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.register(_document(), forbidden_city_ids=frozenset())

    assert lock_status_calls == 2


def test_interrupt_after_lock_open_return_closes_the_owned_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_lock_file
    opened_descriptor: int | None = None

    def open_then_interrupt(*args: object, **kwargs: object):
        nonlocal opened_descriptor
        opened = real_open(*args, **kwargs)
        opened_descriptor = opened if isinstance(opened, int) else opened.fileno()
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_open_lock_file", open_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        registry.discover()

    assert opened_descriptor is not None
    try:
        with pytest.raises(OSError):
            os.fstat(opened_descriptor)
    finally:
        with suppress(OSError):
            os.close(opened_descriptor)


def test_interrupt_after_root_open_return_closes_the_owned_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    if os.name == "nt":
        import ctypes

        real_open = module._open_windows_root
        opened_handle: int | None = None

        def open_then_interrupt(*args: object, **kwargs: object):
            nonlocal opened_handle
            owner = real_open(*args, **kwargs)
            opened_handle = owner.handle()
            raise KeyboardInterrupt

        monkeypatch.setattr(module, "_open_windows_root", open_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            registry.discover()
        assert opened_handle is not None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        assert kernel32.CloseHandle(ctypes.c_void_p(opened_handle)) == 0
        assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE
        return

    real_open = module._open_posix_path
    opened_descriptor: int | None = None

    def open_then_interrupt(*args: object, **kwargs: object):
        nonlocal opened_descriptor
        owner = real_open(*args, **kwargs)
        opened_descriptor = owner.fileno()
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_open_posix_path", open_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        registry.discover()
    assert opened_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(opened_descriptor)


def test_interrupt_after_registry_directory_open_closes_the_owned_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    if os.name == "nt":
        import ctypes

        real_open = module._open_windows_directory
        opened_handle: int | None = None

        def open_then_interrupt(path: Path, state: object | None = None):
            nonlocal opened_handle
            owner = real_open(path, state)
            if path.name == "city-packs":
                opened_handle = owner.handle()
                raise KeyboardInterrupt
            return owner

        monkeypatch.setattr(module, "_open_windows_directory", open_then_interrupt)
        with pytest.raises(module.CityPackPublicationError) as captured:
            registry.register(_document("directory-gap"), forbidden_city_ids=frozenset())
        assert captured.value.potentially_committed is False
        assert opened_handle is not None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        assert kernel32.CloseHandle(ctypes.c_void_p(opened_handle)) == 0
        assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE
        return

    real_open = module._open_posix_owned_at
    opened_descriptor: int | None = None

    def open_then_interrupt(
        directory_descriptor: int,
        name: str,
        **kwargs: object,
    ):
        nonlocal opened_descriptor
        owner = real_open(directory_descriptor, name, **kwargs)
        if name == "city-packs":
            opened_descriptor = owner.fileno()
            raise KeyboardInterrupt
        return owner

    monkeypatch.setattr(module, "_open_posix_owned_at", open_then_interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("directory-gap"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is False
    assert opened_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(opened_descriptor)


def test_root_swap_between_verification_and_lock_open_never_touches_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    registry = module.WorkspaceCityRegistry(workspace)
    displaced = tmp_path / "displaced-workspace"
    real_open = module._open_lock_file

    def swap_then_open(*args: object, **kwargs: object) -> int:
        workspace.root.rename(displaced)
        workspace.root.mkdir()
        return real_open(*args, **kwargs)

    monkeypatch.setattr(module, "_open_lock_file", swap_then_open)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()

    assert list(workspace.root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-at child creation contract")
def test_root_swap_during_child_creation_never_writes_to_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    registry = module.WorkspaceCityRegistry(workspace)
    displaced = tmp_path / "displaced-workspace"
    real_mkdir = os.mkdir
    swapped = False

    def swap_before_path_child(path: object, *args: object, **kwargs: object) -> None:
        nonlocal swapped
        if not swapped and (Path(path) == workspace.root / "city-packs" or path == "city-packs"):
            swapped = True
            workspace.root.rename(displaced)
            real_mkdir(workspace.root)
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(workspace_module.os, "mkdir", swap_before_path_child)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.register(_document(), forbidden_city_ids=frozenset())

    assert swapped is True
    assert list(workspace.root.iterdir()) == []


@pytest.mark.parametrize("lock_bytes", [b"", b"\0"])
def test_hardlinked_registry_lock_is_refused_without_modifying_its_other_name(
    tmp_path: Path, lock_bytes: bytes
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    outside = tmp_path / "outside-lock"
    outside.write_bytes(lock_bytes)
    os.link(outside, workspace.root / ".city-pack-registry.lock")

    with pytest.raises(module.WorkspaceCityRegistryError):
        module.WorkspaceCityRegistry(workspace).discover()

    assert outside.read_bytes() == lock_bytes
    assert outside.stat().st_nlink == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows lock-open race contract")
def test_lock_replacement_between_prestat_and_open_never_enters_critical_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    lock_path = workspace.root / ".city-pack-registry.lock"
    lock_path.write_bytes(b"\0")
    outside = tmp_path / "competing-lock"
    outside.write_bytes(b"\0")
    real_open = module._open_windows_lock
    entered = False
    real_subdirectory = module._subdirectory

    def replace_then_open(path: Path) -> int:
        lock_path.unlink()
        os.link(outside, lock_path)
        return real_open(path)

    def mark_critical_section(*args: object, **kwargs: object):
        nonlocal entered
        entered = True
        return real_subdirectory(*args, **kwargs)

    monkeypatch.setattr(module, "_open_windows_lock", replace_then_open)
    monkeypatch.setattr(module, "_subdirectory", mark_critical_section)
    with pytest.raises(module.WorkspaceCityRegistryError):
        module.WorkspaceCityRegistry(workspace).discover()

    assert entered is False
    assert outside.read_bytes() == b"\0"


@pytest.mark.skipif(os.name != "nt", reason="Windows root-anchor contract")
def test_root_swap_during_windows_lock_open_never_touches_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    registry = module.WorkspaceCityRegistry(workspace)
    displaced = tmp_path / "displaced-workspace"
    real_open = module._open_windows_lock

    def swap_then_open(path: Path) -> int:
        workspace.root.rename(displaced)
        workspace.root.mkdir()
        return real_open(path)

    monkeypatch.setattr(module, "_open_windows_lock", swap_then_open)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()

    assert list(workspace.root.iterdir()) == []


def test_discovery_refuses_a_valid_same_id_rewrite_after_record_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    original = registry.register(_document(), forbidden_city_ids=frozenset())
    path = registry.workspace.root / "city-packs" / "operator-city.json"
    before = path.stat()
    replacement = original.model_copy(update={"name": "Altered! City operator-city"})
    replacement_bytes = canonical_json(replacement).encode("utf-8") + b"\n"
    assert len(replacement_bytes) == len(path.read_bytes())
    real_load = module._load_record
    rewritten = False

    def rewrite_after_load(directory: object, name: str, **kwargs: object):
        nonlocal rewritten
        loaded = real_load(directory, name, **kwargs)
        if not rewritten:
            rewritten = True
            path.write_bytes(replacement_bytes)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return loaded

    monkeypatch.setattr(module, "_load_record", rewrite_after_load)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()


@pytest.mark.skipif(os.name != "nt", reason="Windows record-handle stability contract")
def test_windows_discovery_linearizes_before_the_final_record_handle_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    original = registry.register(_document(), forbidden_city_ids=frozenset())
    path = registry.workspace.root / "city-packs" / "operator-city.json"
    before = path.stat()
    replacement = original.model_copy(update={"name": "Altered! City operator-city"})
    replacement_bytes = canonical_json(replacement).encode("utf-8") + b"\n"
    assert len(replacement_bytes) == len(path.read_bytes())
    real_open = module._open_platform_record
    real_close = module._StageFile.close
    real_status = module._entry_status
    target_owner: object | None = None
    rewritten = False
    write_denials = 0

    def track_record_open(directory: object, name: str):
        nonlocal target_owner
        owner = real_open(directory, name)
        if name == path.name and not rewritten:
            target_owner = owner
        return owner

    def rewrite_after_close(stage_file: Any) -> None:
        nonlocal rewritten
        owner = stage_file.stream
        real_close(stage_file)
        if owner is target_owner and not rewritten:
            rewritten = True
            path.write_bytes(replacement_bytes)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    def observe_status(directory: object, name: str):
        nonlocal write_denials
        if target_owner is not None and name == path.name and not rewritten:
            with pytest.raises(OSError):
                path.open("r+b")
            write_denials += 1
        return real_status(directory, name)

    monkeypatch.setattr(module, "_open_platform_record", track_record_open)
    monkeypatch.setattr(module._StageFile, "close", rewrite_after_close)
    monkeypatch.setattr(module, "_entry_status", observe_status)
    discovered = registry.discover()
    assert discovered == (original,)
    assert rewritten is True
    assert write_denials >= 1


@pytest.mark.skipif(os.name != "nt", reason="Windows stat-interface contract")
def test_windows_path_handle_snapshot_ignores_only_cross_interface_ctime(
    tmp_path: Path,
) -> None:
    module = _module()
    path = tmp_path / "record.json"
    path.write_bytes(b"{}\n")
    path_status = path.stat()

    class DifferentCtime:
        def __init__(self, base: os.stat_result) -> None:
            self._base = base
            self.st_ctime_ns = base.st_ctime_ns + 1

        def __getattr__(self, name: str) -> object:
            return getattr(self._base, name)

    handle_status = DifferentCtime(path_status)
    assert module._same_path_handle_snapshot(path_status, handle_status) is True
    assert module._same_file_snapshot(path_status, handle_status) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows whole-snapshot linearization contract")
def test_windows_discovery_never_returns_cross_record_aba_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    first_zero = registry.register(_document("aba-a"), forbidden_city_ids=frozenset())
    second_zero = registry.register(_document("aba-b"), forbidden_city_ids=frozenset())
    first_one = first_zero.model_copy(update={"name": "ABA A alternate"})
    second_one = second_zero.model_copy(update={"name": "ABA B alternate"})
    records = registry.workspace.root / "city-packs"
    first_path = records / "aba-a.json"
    second_path = records / "aba-b.json"
    first_slot = tmp_path / "aba-a-slot.json"
    second_slot = tmp_path / "aba-b-slot.json"
    first_alternate = tmp_path / "aba-a-alternate.json"
    second_alternate = tmp_path / "aba-b-alternate.json"
    first_alternate.write_bytes(canonical_json(first_one).encode("utf-8") + b"\n")
    second_alternate.write_bytes(canonical_json(second_one).encode("utf-8") + b"\n")
    real_read = module._read_regular_file_with_evidence
    read_count = 0
    write_denied = False

    def exchange(current: Path, slot: Path, replacement: Path) -> None:
        current.replace(slot)
        replacement.replace(current)

    def scheduled_read(*args: object, **kwargs: object):
        nonlocal read_count, write_denied
        result = real_read(*args, **kwargs)
        read_count += 1
        try:
            if read_count == 1:
                exchange(first_path, first_slot, first_alternate)
                exchange(second_path, second_slot, second_alternate)
            elif read_count == 2:
                exchange(second_path, second_alternate, second_slot)
                exchange(first_path, first_alternate, first_slot)
            elif read_count == 3:
                exchange(first_path, first_slot, first_alternate)
                exchange(second_path, second_slot, second_alternate)
        except OSError:
            write_denied = True
        return result

    monkeypatch.setattr(module, "_read_regular_file_with_evidence", scheduled_read)
    try:
        discovered = registry.discover()
    except module.WorkspaceCityRegistryError:
        return

    names = tuple(pack.name for pack in discovered)
    assert names != (first_zero.name, second_one.name)
    assert write_denied is True


def test_interrupt_after_regular_record_open_return_closes_the_owned_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    registry.register(_document("owned-record"), forbidden_city_ids=frozenset())
    real_open = module._open_regular_file
    opened_descriptor: int | None = None

    def open_then_interrupt(*args: object, **kwargs: object):
        nonlocal opened_descriptor
        opened = real_open(*args, **kwargs)
        opened_descriptor = opened.fileno()
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_open_regular_file", open_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        registry.discover()

    assert opened_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(opened_descriptor)


def test_postlink_discovery_refuses_a_peer_rewrite_after_its_record_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    original = registry.register(_document("stable-city"), forbidden_city_ids=frozenset())
    stable_path = registry.workspace.root / "city-packs" / "stable-city.json"
    replacement = original.model_copy(update={"name": "Changed City"})
    replacement_bytes = canonical_json(replacement).encode("utf-8") + b"\n"
    real_load = module._load_record
    stable_load_count = 0

    def rewrite_during_postcommit_scan(directory: object, name: str, **kwargs: object):
        nonlocal stable_load_count
        loaded = real_load(directory, name, **kwargs)
        if name == "stable-city.json":
            stable_load_count += 1
            if stable_load_count == 2:
                stable_path.write_bytes(replacement_bytes)
        return loaded

    monkeypatch.setattr(module, "_load_record", rewrite_during_postcommit_scan)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("new-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert (registry.workspace.root / "city-packs" / "new-city.json").is_file()


def test_postlink_snapshot_refuses_same_bytes_under_a_new_peer_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    registry.register(_document("stable-city"), forbidden_city_ids=frozenset())
    stable_path = registry.workspace.root / "city-packs" / "stable-city.json"
    stable_bytes = stable_path.read_bytes()
    real_verify = module._verify_committed

    def replace_peer_inode(*args: object, **kwargs: object) -> None:
        real_verify(*args, **kwargs)
        stable_path.unlink()
        stable_path.write_bytes(stable_bytes)

    monkeypatch.setattr(module, "_verify_committed", replace_peer_inode)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("new-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert stable_path.read_bytes() == stable_bytes


def test_indeterminate_file_identity_can_never_prove_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_status = module._entry_status
    link_attempted = False
    competing_bytes = module._canonical_pack_bytes(
        module._validate_city_payload(_payload("competing-city"))
    )

    class IdentityUnavailable:
        def __init__(self, base: os.stat_result) -> None:
            self._base = base
            self.st_dev = 0
            self.st_ino = 0

        def __getattr__(self, name: str) -> object:
            return getattr(self._base, name)

    def create_competing_final(
        staging: Any,
        stage_name: str,
        records: Any,
        record_name: str,
        _operation: object,
    ) -> None:
        nonlocal link_attempted
        destination = records.path / record_name
        destination.write_bytes(competing_bytes)
        link_attempted = True
        raise FileExistsError

    def unavailable_after_link(directory: object, name: str):
        status = real_status(directory, name)
        return IdentityUnavailable(status) if link_attempted else status

    monkeypatch.setattr(module, "_link_stage", create_competing_final)
    monkeypatch.setattr(module, "_entry_status", unavailable_after_link)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("competing-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert (registry.workspace.root / "city-packs" / "competing-city.json").is_file()


def test_link_then_raise_with_unreadable_commit_evidence_is_potentially_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_publish = module._publish_pinned_stage
    real_status = module._entry_status
    evidence_unreadable = False

    def publish_then_hide_evidence(*args: object, **kwargs: object) -> None:
        nonlocal evidence_unreadable
        real_publish(*args, **kwargs)
        evidence_unreadable = True

    def fail_evidence_once(directory: object, name: str):
        nonlocal evidence_unreadable
        if evidence_unreadable:
            evidence_unreadable = False
            raise module.WorkspaceCityRegistryError()
        return real_status(directory, name)

    monkeypatch.setattr(module, "_publish_pinned_stage", publish_then_hide_evidence)
    monkeypatch.setattr(module, "_entry_status", fail_evidence_once)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("ambiguous-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("ambiguous-city",)


def test_completed_link_cannot_be_downgraded_by_a_late_exists_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_link = module._link_stage

    def link_then_report_exists(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)
        raise FileExistsError

    monkeypatch.setattr(module, "_link_stage", link_then_report_exists)
    monkeypatch.setattr(module, "_publication_relation", lambda *_args: "different")

    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("late-exists-city"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("late-exists-city",)


@pytest.mark.parametrize(
    "error_number",
    [
        errno.EPERM,
        errno.EACCES,
        errno.EIO,
        errno.ENOSPC,
        errno.EROFS,
        errno.EXDEV,
        errno.EBADF,
    ],
)
def test_native_publication_synchronous_failure_is_precommit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    city_id = f"publication-refused-{error_number}"

    def refuse_publication(*_args: object, **_kwargs: object) -> None:
        raise OSError(error_number, "publication refused")

    monkeypatch.setattr(module, "_publish_pinned_stage", refuse_publication)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document(city_id), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert registry.discover() == ()
    assert not (registry.workspace.root / "city-packs" / f"{city_id}.json").exists()
    _assert_no_live_stage_bytes(registry)


def test_native_publication_interrupt_remains_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    city_id = "ambiguous-interrupt"

    def fail_publication(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_publish_pinned_stage", fail_publication)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document(city_id), forbidden_city_ids=frozenset())

    final = registry.workspace.root / "city-packs" / f"{city_id}.json"
    stage_paths = list((registry.workspace.root / "city-pack-staging").iterdir())
    expected = module._canonical_pack_bytes(module._validate_city_payload(_payload(city_id)))
    assert captured.value.potentially_committed is True
    assert not final.exists()
    assert all(
        path.is_file() and not path.is_symlink() and path.read_bytes() in {b"", expected}
        for path in stage_paths
    )


def test_native_link_return_gap_cannot_be_downgraded_after_final_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_publish = module._publish_pinned_stage

    def publish_remove_then_interrupt(
        staging: object,
        stage_name: str,
        records: Any,
        record_name: str,
        descriptor: int,
        operation: Any,
    ) -> None:
        real_publish(staging, stage_name, records, record_name, descriptor, operation)
        (records.path / record_name).unlink()
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_publish_pinned_stage", publish_remove_then_interrupt)

    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("native-return-gap"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True
    assert not (registry.workspace.root / "city-packs" / "native-return-gap.json").exists()


def test_native_link_exists_gap_cannot_be_downgraded_after_final_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_publish = module._publish_pinned_stage

    def publish_remove_then_report_exists(
        staging: object,
        stage_name: str,
        records: Any,
        record_name: str,
        descriptor: int,
        operation: Any,
    ) -> None:
        real_publish(staging, stage_name, records, record_name, descriptor, operation)
        (records.path / record_name).unlink()
        raise FileExistsError

    monkeypatch.setattr(module, "_publish_pinned_stage", publish_remove_then_report_exists)

    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("native-exists-gap"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True
    assert not (registry.workspace.root / "city-packs" / "native-exists-gap.json").exists()


def test_link_then_missing_stage_evidence_is_potentially_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_link = module._link_stage

    def link_remove_stage_then_fail(
        staging: object,
        stage_name: str,
        records: object,
        record_name: str,
        operation: object,
    ) -> None:
        real_link(staging, stage_name, records, record_name, operation)
        (staging.path / stage_name).unlink()
        raise OSError("fault after final link and stage loss")

    monkeypatch.setattr(module, "_link_stage", link_remove_stage_then_fail)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("missing-stage-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("missing-stage-city",)


def test_link_then_raise_with_interrupted_commit_evidence_is_potentially_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_link = module._link_stage

    def link_then_fail(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)
        raise OSError("fault after final link")

    def interrupt_evidence(*_args: object, **_kwargs: object):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_link_stage", link_then_fail)
    monkeypatch.setattr(module, "_publication_relation", interrupt_evidence)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("interrupted-evidence-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert (registry.workspace.root / "city-packs" / "interrupted-evidence-city.json").is_file()


def test_discovery_stops_at_the_fifty_first_entry_without_requesting_a_fifty_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    registry.workspace.root.joinpath("city-packs").mkdir()
    requested = 0

    class Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class Scanner:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal requested
            requested += 1
            if requested == 52:
                raise AssertionError("the fifty-second entry must never be requested")
            return Entry(f"city-{requested:02d}.json")

    monkeypatch.setattr(module, "_scandir_directory", lambda _directory: Scanner(), raising=False)
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()
    assert requested == 51


@pytest.mark.parametrize("fault_name", ["_flush_staging", "_verify_staging", "_link_stage"])
def test_precommit_faults_clean_only_the_random_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault_name: str
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("private marker")

    monkeypatch.setattr(module, fault_name, fail)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("sensitive-city-id"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is False
    assert "private" not in str(captured.value)
    assert not (registry.workspace.root / "city-packs" / "sensitive-city-id.json").exists()
    _assert_no_live_stage_bytes(registry)


def test_stage_inode_swap_after_preverification_cannot_publish_wrong_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_verify = module._verify_staging
    replacement_created = False

    def verify_then_replace(*args: object, **kwargs: object) -> None:
        nonlocal replacement_created
        real_verify(*args, **kwargs)
        directory = args[0]
        stage_name = args[1]
        stage_path = directory.path / stage_name
        with suppress(FileNotFoundError):
            stage_path.unlink()
        stage_path.write_bytes(b"corrupt\n")
        replacement_created = True

    monkeypatch.setattr(module, "_verify_staging", verify_then_replace)
    try:
        registered = registry.register(_document("stage-swap-city"), forbidden_city_ids=frozenset())
    except module.CityPackPublicationError as error:
        assert os.name == "nt" or sys.platform.startswith("linux")
        assert error.potentially_committed is False
        assert not (registry.workspace.root / "city-packs" / "stage-swap-city.json").exists()
    else:
        assert _stored_bytes(registry, registered.city_id) == (
            canonical_json(registered).encode("utf-8") + b"\n"
        )

    stage_files = list((registry.workspace.root / "city-pack-staging").iterdir())
    expected_stage_bytes = [b"corrupt\n"] if replacement_created else []
    assert [path.read_bytes() for path in stage_files] == expected_stage_bytes


def test_stage_inode_swap_at_hard_link_boundary_cannot_leave_a_corrupt_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_publish = module._publish_pinned_stage
    replacement_created = False

    def swap_then_publish(
        staging: object,
        stage_name: str,
        records: object,
        record_name: str,
        descriptor: int,
        operation: Any,
    ) -> None:
        nonlocal replacement_created
        stage_path = staging.path / stage_name
        with suppress(FileNotFoundError):
            stage_path.unlink()
        stage_path.write_bytes(b"corrupt\n")
        replacement_created = True
        real_publish(staging, stage_name, records, record_name, descriptor, operation)

    monkeypatch.setattr(module, "_publish_pinned_stage", swap_then_publish)
    try:
        registered = registry.register(
            _document("link-boundary-race"), forbidden_city_ids=frozenset()
        )
    except module.CityPackPublicationError as error:
        assert os.name == "nt" or sys.platform.startswith("linux")
        assert error.potentially_committed is False
        assert not (registry.workspace.root / "city-packs" / "link-boundary-race.json").exists()
    else:
        assert _stored_bytes(registry, registered.city_id) == (
            canonical_json(registered).encode("utf-8") + b"\n"
        )

    stage_files = list((registry.workspace.root / "city-pack-staging").iterdir())
    expected_stage_bytes = [b"corrupt\n"] if replacement_created else []
    assert [path.read_bytes() for path in stage_files] == expected_stage_bytes


@pytest.mark.skipif(os.name != "nt", reason="Windows native hard-link publication contract")
def test_windows_native_descriptor_publication_is_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_publish = module._publish_pinned_stage
    collision_mapped = False

    def publish_then_repeat(*args: object, **kwargs: object) -> None:
        nonlocal collision_mapped
        real_publish(*args, **kwargs)
        with pytest.raises(FileExistsError):
            real_publish(*args, **kwargs)
        collision_mapped = True

    monkeypatch.setattr(module, "_publish_pinned_stage", publish_then_repeat)
    registered = registry.register(_document("windows-link"), forbidden_city_ids=frozenset())

    assert registered.city_id == "windows-link"
    assert collision_mapped is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("windows-link",)


@pytest.mark.skipif(os.name != "nt", reason="Windows native durability contract")
def test_windows_publication_flushes_the_linked_file_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_link = module._link_windows_descriptor
    real_fsync = module.os.fsync
    linked = False
    postlink_flushes = 0

    def link_then_mark(*args: object, **kwargs: object) -> None:
        nonlocal linked
        real_link(*args, **kwargs)
        linked = True

    def count_postlink_flush(descriptor: int) -> None:
        nonlocal postlink_flushes
        real_fsync(descriptor)
        if linked:
            postlink_flushes += 1

    monkeypatch.setattr(module, "_link_windows_descriptor", link_then_mark)
    monkeypatch.setattr(module.os, "fsync", count_postlink_flush)

    registry.register(_document("windows-durable"), forbidden_city_ids=frozenset())
    assert postlink_flushes >= 1


def test_wrong_inode_link_result_is_preserved_as_an_ambiguous_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    def publish_wrong_inode(
        _staging: Any,
        _stage_name: str,
        records: Any,
        record_name: str,
        _descriptor: int,
        _operation: Any,
    ) -> None:
        destination_path = records.path / record_name
        destination_path.write_bytes(b"corrupt\n")

    monkeypatch.setattr(module, "_publish_pinned_stage", publish_wrong_inode)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("wrong-link-inode"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True
    assert (
        registry.workspace.root / "city-packs" / "wrong-link-inode.json"
    ).read_bytes() == b"corrupt\n"
    with pytest.raises(module.WorkspaceCityRegistryError):
        registry.discover()


def test_wrong_destination_evidence_never_unlinks_an_unexpected_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    destination_name = "wrong-link-race.json"
    real_unlink = module.os.unlink
    destination_unlink_attempted = False

    def publish_wrong_inode(
        _staging: Any,
        _stage_name: str,
        records: Any,
        record_name: str,
        _descriptor: int,
        _operation: Any,
    ) -> None:
        destination_path = records.path / record_name
        destination_path.write_bytes(b"corrupt\n")

    def observe_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal destination_unlink_attempted
        if Path(path).name == destination_name:
            destination_unlink_attempted = True
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(module, "_publish_pinned_stage", publish_wrong_inode)
    monkeypatch.setattr(module.os, "unlink", observe_unlink)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("wrong-link-race"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True
    assert destination_unlink_attempted is False
    assert (
        registry.workspace.root / "city-packs" / "wrong-link-race.json"
    ).read_bytes() == b"corrupt\n"


def test_interrupt_after_stage_open_return_cleans_owned_file_and_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_stage_file
    opened_descriptor: int | None = None

    def open_then_interrupt(*args: object, **kwargs: object):
        nonlocal opened_descriptor
        stage_file = real_open(*args, **kwargs)
        opened_descriptor = stage_file.fileno()
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_open_stage_file", open_then_interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("open-return-interrupt"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert opened_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(opened_descriptor)
    _assert_no_live_stage_bytes(registry)


@pytest.mark.skipif(os.name == "nt", reason="POSIX named-stage ownership boundary")
def test_interrupt_after_platform_stage_open_return_leaves_no_live_stage_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_platform_stage
    opened_descriptor: int | None = None

    def open_then_interrupt(*args: object, **kwargs: object):
        nonlocal opened_descriptor
        stage_file = real_open(*args, **kwargs)
        opened_descriptor = stage_file.fileno()
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_open_platform_stage", open_then_interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("platform-open-gap"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert opened_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(opened_descriptor)
    _assert_no_live_stage_bytes(registry)


def test_stage_creation_never_uses_an_unowned_raw_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module.os.open
    raw_stage_open_attempted = False

    def observe_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal raw_stage_open_attempted
        if flags & os.O_EXCL and str(path).endswith(".stage"):
            raw_stage_open_attempted = True
            raise AssertionError("stage acquisition returned an unowned raw descriptor")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", observe_open)
    registered = registry.register(_document("owned-stage-open"), forbidden_city_ids=frozenset())

    assert registered.city_id == "owned-stage-open"
    assert raw_stage_open_attempted is False
    _assert_no_live_stage_bytes(registry)


def test_interrupt_after_stage_verification_open_return_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_stage_file
    opened_descriptor: int | None = None

    def open_then_interrupt(*args: object, **kwargs: object):
        nonlocal opened_descriptor
        stage_file = real_open(*args, **kwargs)
        if args[2] == "rb":
            opened_descriptor = stage_file.fileno()
            raise KeyboardInterrupt
        return stage_file

    monkeypatch.setattr(module, "_open_stage_file", open_then_interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("verify-open-interrupt"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert opened_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(opened_descriptor)
    _assert_no_live_stage_bytes(registry)


def test_interrupt_after_stage_close_never_double_closes_a_reused_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    sentinel_path = tmp_path / "sentinel"
    sentinel_path.write_bytes(b"sentinel")
    real_close = module._close_staging
    reused_descriptor: int | None = None
    interrupted = False

    def close_reuse_then_interrupt(stage_file: Any) -> None:
        nonlocal interrupted, reused_descriptor
        descriptor = stage_file.fileno()
        real_close(stage_file)
        if not interrupted:
            interrupted = True
            reused_descriptor = os.open(sentinel_path, os.O_RDONLY)
            assert reused_descriptor == descriptor
            raise KeyboardInterrupt

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("precommit fault")

    monkeypatch.setattr(module, "_close_staging", close_reuse_then_interrupt)
    monkeypatch.setattr(module, "_link_stage", fail_link)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("close-return-interrupt"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert reused_descriptor is not None
    try:
        assert os.fstat(reused_descriptor).st_size == len(b"sentinel")
    finally:
        os.close(reused_descriptor)
    _assert_no_live_stage_bytes(registry)


def test_interrupt_before_stage_descriptor_close_is_retried_without_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_complete = module._CloseTicket.complete
    target_descriptor: int | None = None
    interrupted = False

    def capture_then_fail(*args: object, **kwargs: object) -> None:
        nonlocal target_descriptor
        operation = args[-1]
        target_descriptor = operation.stage_file.fileno()
        raise OSError("precommit fault")

    def interrupt_before_close(ticket: Any) -> None:
        nonlocal interrupted
        if ticket.token == target_descriptor and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        real_complete(ticket)

    monkeypatch.setattr(module, "_link_stage", capture_then_fail)
    monkeypatch.setattr(module._CloseTicket, "complete", interrupt_before_close)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("close-before-call"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert interrupted is True
    assert target_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(target_descriptor)
    _assert_no_live_stage_bytes(registry)


def test_close_fault_after_same_file_descriptor_reuse_never_closes_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    source = tmp_path / "same-file"
    source.write_bytes(b"stable")
    if os.name == "nt":
        owner = module._open_windows_owned_file(
            source,
            flags=os.O_RDONLY | getattr(os, "O_BINARY", 0) | 0x0080,
            share_mode=0x0040,
        )
    else:
        owner = module._open_posix_path(source, flags=os.O_RDONLY)
    real_close = module._close_descriptor_token
    replacement: int | None = None

    def close_reopen_then_interrupt(target: int) -> None:
        nonlocal replacement
        real_close(target)
        replacement = os.open(source, os.O_RDONLY)
        assert replacement == target
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_close_descriptor_token", close_reopen_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        owner.close()
    owner.close()

    assert replacement is not None
    try:
        assert os.read(replacement, len(b"stable")) == b"stable"
    finally:
        os.close(replacement)


@pytest.mark.skipif(os.name != "nt", reason="Windows raw-handle ownership contract")
def test_interrupt_before_raw_handle_close_is_retried_without_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    owner = module._open_windows_directory(tmp_path)
    raw_handle = owner.handle()
    real_complete = module._CloseTicket.complete
    interrupted = False

    def interrupt_once(ticket: Any) -> None:
        nonlocal interrupted
        if ticket.token == raw_handle and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        real_complete(ticket)

    monkeypatch.setattr(module._CloseTicket, "complete", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        owner.close()
    owner.close()

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assert kernel32.CloseHandle(ctypes.c_void_p(raw_handle)) == 0
    assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE


def test_staging_close_failure_cleans_the_exact_precommit_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_close = module._close_staging

    def close_then_fail(stage_file: object) -> None:
        real_close(stage_file)
        raise OSError("close failure after descriptor release")

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("precommit fault")

    monkeypatch.setattr(module, "_close_staging", close_then_fail)
    monkeypatch.setattr(module, "_link_stage", fail_link)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("close-fault-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is False
    assert registry.discover() == ()
    _assert_no_live_stage_bytes(registry)


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-on-close replacement contract")
def test_stage_close_fault_never_deletes_a_replacement_created_after_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    staging = registry.workspace.root / "city-pack-staging"
    real_close = module._close_staging
    marker = b"replacement must survive\n"

    def close_replace_then_fail(stage_file: Any) -> None:
        stage_paths = list(staging.iterdir())
        assert len(stage_paths) == 1
        stage_path = stage_paths[0]
        real_close(stage_file)
        stage_path.write_bytes(marker)
        raise OSError("failure after exact stage close")

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("precommit fault")

    monkeypatch.setattr(module, "_close_staging", close_replace_then_fail)
    monkeypatch.setattr(module, "_link_stage", fail_link)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("stage-close-race"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert [path.read_bytes() for path in staging.iterdir()] == [marker]


@pytest.mark.skipif(os.name == "nt", reason="POSIX staging-name cleanup contract")
def test_posix_cleanup_never_unlinks_a_replacement_at_the_unlink_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("precommit fault")

    def refuse_pathname_unlink(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cleanup must not unlink a mutable staging name")

    monkeypatch.setattr(module, "_link_stage", fail_link)
    monkeypatch.setattr(module.os, "unlink", refuse_pathname_unlink)

    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("cleanup-boundary"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux staging fallback")
@pytest.mark.parametrize(
    "error_number",
    sorted({errno.EOPNOTSUPP, errno.EISDIR, errno.ENOENT}),
)
def test_linux_falls_back_when_unnamed_staging_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_posix_owned_file
    city_id = f"fallback-city-{error_number}"

    def reject_unnamed_stage(
        directory: object,
        name: str,
        *,
        flags: int,
        mode: int = 0o600,
    ):
        if name == "." and flags & getattr(os, "O_TMPFILE", 0):
            raise OSError(error_number, "unsupported")
        return real_open(directory, name, flags=flags, mode=mode)

    monkeypatch.setattr(module, "_open_posix_owned_file", reject_unnamed_stage)
    registered = registry.register(_document(city_id), forbidden_city_ids=frozenset())

    canonical = module._canonical_pack_bytes(registered)
    final = registry.workspace.root / "city-packs" / f"{city_id}.json"
    stage_paths = list((registry.workspace.root / "city-pack-staging").iterdir())
    assert registered.city_id == city_id
    assert final.read_bytes() == canonical
    assert tuple(pack.city_id for pack in registry.discover()) == (city_id,)
    assert len(stage_paths) == 1
    assert stage_paths[0].read_bytes() == canonical
    assert os.path.samefile(stage_paths[0], final)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux staging fallback")
def test_linux_named_stage_fallback_refuses_an_unlinked_inode_and_preserves_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_posix_owned_file
    real_publish = module._publish_pinned_stage
    replacement = b"unrelated replacement\n"

    def reject_unnamed_stage(
        directory: object,
        name: str,
        *,
        flags: int,
        mode: int = 0o600,
    ):
        if name == "." and flags & getattr(os, "O_TMPFILE", 0):
            raise OSError(errno.EOPNOTSUPP, "unsupported")
        return real_open(directory, name, flags=flags, mode=mode)

    def replace_name_then_publish(
        staging: Any,
        stage_name: str,
        records: Any,
        record_name: str,
        descriptor: int,
        operation: Any,
    ) -> None:
        stage_path = staging.path / stage_name
        stage_path.unlink()
        stage_path.write_bytes(replacement)
        real_publish(staging, stage_name, records, record_name, descriptor, operation)

    monkeypatch.setattr(module, "_open_posix_owned_file", reject_unnamed_stage)
    monkeypatch.setattr(module, "_publish_pinned_stage", replace_name_then_publish)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("fallback-replacement"), forbidden_city_ids=frozenset())

    final = registry.workspace.root / "city-packs" / "fallback-replacement.json"
    stage_paths = list((registry.workspace.root / "city-pack-staging").iterdir())
    assert captured.value.potentially_committed is False
    assert not final.exists()
    assert [path.read_bytes() for path in stage_paths] == [replacement]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux staging fallback")
def test_linux_does_not_fallback_for_unnamed_stage_permission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_posix_owned_file
    named_stage_attempted = False

    def refuse_unnamed_stage(
        directory: object,
        name: str,
        *,
        flags: int,
        mode: int = 0o600,
    ):
        nonlocal named_stage_attempted
        if name == "." and flags & getattr(os, "O_TMPFILE", 0):
            raise PermissionError(errno.EPERM, "denied")
        if name.endswith(".stage"):
            named_stage_attempted = True
        return real_open(directory, name, flags=flags, mode=mode)

    monkeypatch.setattr(module, "_open_posix_owned_file", refuse_unnamed_stage)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("no-permission-fallback"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert named_stage_attempted is False
    _assert_no_live_stage_bytes(registry)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux staging fallback")
def test_linux_named_fallback_precommit_failure_leaves_only_a_safe_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_open = module._open_posix_owned_file

    def reject_unnamed_stage(
        directory: object,
        name: str,
        *,
        flags: int,
        mode: int = 0o600,
    ):
        if name == "." and flags & getattr(os, "O_TMPFILE", 0):
            raise OSError(errno.EOPNOTSUPP, "unsupported")
        return real_open(directory, name, flags=flags, mode=mode)

    def refuse_publication(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "publication refused")

    monkeypatch.setattr(module, "_open_posix_owned_file", reject_unnamed_stage)
    monkeypatch.setattr(module, "_publish_pinned_stage", refuse_publication)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("fallback-precommit"), forbidden_city_ids=frozenset())

    stage_paths = list((registry.workspace.root / "city-pack-staging").iterdir())
    assert captured.value.potentially_committed is False
    assert len(stage_paths) == 1
    assert stage_paths[0].is_file()
    assert not stage_paths[0].is_symlink()
    assert stage_paths[0].read_bytes() == b""
    assert registry.discover() == ()


def test_link_then_raise_reports_a_durable_ambiguous_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_link = module._link_stage

    def link_then_fail(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)
        raise OSError("failure after exclusive link")

    monkeypatch.setattr(module, "_link_stage", link_then_fail)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("link-fault-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("link-fault-city",)


def test_completed_publication_cannot_be_downgraded_after_final_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_link = module._link_stage

    def link_remove_then_fail(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)
        record_name = args[3]
        assert isinstance(record_name, str)
        (registry.workspace.root / "city-packs" / record_name).unlink()
        raise OSError("failure after completed publication")

    monkeypatch.setattr(module, "_link_stage", link_remove_then_fail)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("vanished-final"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True


def test_bad_held_directory_descriptor_is_not_treated_as_unsupported_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    def bad_descriptor(_descriptor: int) -> None:
        raise OSError(errno.EBADF, "bad descriptor")

    monkeypatch.setattr(module.os, "fsync", bad_descriptor)
    with pytest.raises(OSError) as captured:
        module._sync_directory(SimpleNamespace(file_descriptor=42))

    assert captured.value.errno == errno.EBADF


def test_linux_publication_links_the_pinned_descriptor_beneath_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    calls: list[tuple[object, object, dict[str, object]]] = []

    def observe_link(source: object, destination: object, **kwargs: object) -> None:
        calls.append((source, destination, kwargs))

    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.os, "link", observe_link)
    operation = module._PublicationState()
    module._publish_pinned_stage(
        SimpleNamespace(),
        "ignored-random.stage",
        SimpleNamespace(file_descriptor=41),
        "portable-city.json",
        17,
        operation,
    )

    assert calls == [
        (
            "/proc/self/fd/17",
            "portable-city.json",
            {"dst_dir_fd": 41, "follow_symlinks": True},
        )
    ]
    assert operation.publication_completed is True


def test_darwin_publication_clones_the_pinned_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    calls: list[tuple[int, int, str]] = []

    def observe_clone(source: int, destination: int, name: str) -> None:
        calls.append((source, destination, name))

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "_clone_stage_on_darwin", observe_clone)
    operation = module._PublicationState()
    module._publish_pinned_stage(
        SimpleNamespace(),
        "ignored-random.stage",
        SimpleNamespace(file_descriptor=43),
        "portable-city.json",
        19,
        operation,
    )

    assert calls == [(19, 43, "portable-city.json")]
    assert operation.publication_completed is True


def test_darwin_commit_verification_fsyncs_the_cloned_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    expected = b"canonical\n"
    calls: list[dict[str, object]] = []

    def observe_read(*_args: object, **kwargs: object):
        calls.append(kwargs)
        return expected, object()

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "_read_regular_file_with_evidence", observe_read)
    module._verify_committed(
        SimpleNamespace(),
        SimpleNamespace(),
        "portable-city.json",
        expected,
    )

    assert calls == [
        {
            "limit": module.MAX_CITY_PACK_UPLOAD_BYTES,
            "descriptor": None,
            "sync": True,
        }
    ]


def test_darwin_stage_quarantine_is_zeroed_and_flushed_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    calls: list[tuple[object, ...]] = []
    stage_file = SimpleNamespace(
        fileno=lambda: 23,
        close=lambda: calls.append(("close",)),
    )
    operation = module._PublicationState(
        stage_name="random.stage",
        stage_file=stage_file,
        publication_completed=True,
    )

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(
        module.os,
        "ftruncate",
        lambda descriptor, size: calls.append(("truncate", descriptor, size)),
    )
    monkeypatch.setattr(
        module.os,
        "fsync",
        lambda descriptor: calls.append(("sync", descriptor)),
    )

    module._remove_owned_stage(SimpleNamespace(), operation, required=True)

    assert calls == [("truncate", 23, 0), ("sync", 23), ("close",)]


def test_darwin_ambiguous_publication_quarantines_stage_without_touching_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    calls: list[tuple[object, ...]] = []
    stage_file = SimpleNamespace(
        fileno=lambda: 29,
        close=lambda: calls.append(("close",)),
    )
    operation = module._PublicationState(
        stage_name="random.stage",
        stage_file=stage_file,
        potentially_committed=True,
        publication_completed=True,
    )

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(
        module.os,
        "ftruncate",
        lambda descriptor, size: calls.append(("truncate", descriptor, size)),
    )
    monkeypatch.setattr(
        module.os,
        "fsync",
        lambda descriptor: calls.append(("sync", descriptor)),
    )

    module._release_ambiguous_stage(SimpleNamespace(), operation)

    assert calls == [("truncate", 29, 0), ("sync", 29), ("close",)]


@pytest.mark.parametrize(
    ("error_number", "error_type"),
    [(errno.EEXIST, FileExistsError), (errno.EIO, OSError)],
)
def test_darwin_clone_maps_native_failures_without_losing_errno(
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    error_type: type[OSError],
) -> None:
    module = _module()
    import ctypes

    class Clone:
        argtypes: object = None
        restype: object = None

        def __call__(self, *_args: object) -> int:
            return -1

    monkeypatch.setattr(
        ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(fclonefileat=Clone())
    )
    monkeypatch.setattr(ctypes, "get_errno", lambda: error_number)

    with pytest.raises(error_type) as captured:
        module._clone_stage_on_darwin(17, 41, "portable-city.json")
    assert captured.value.errno == error_number


def test_postcommit_validation_refuses_a_concurrently_corrupted_peer_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    registry.register(_document("existing-city"), forbidden_city_ids=frozenset())
    existing_path = registry.workspace.root / "city-packs" / "existing-city.json"
    original = existing_path.read_bytes()
    real_verify = module._verify_committed

    def corrupt_peer_after_new_link(*args: object, **kwargs: object) -> None:
        real_verify(*args, **kwargs)
        existing_path.write_bytes(b"corrupt\n")

    monkeypatch.setattr(module, "_verify_committed", corrupt_peer_after_new_link)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("new-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert (registry.workspace.root / "city-packs" / "new-city.json").is_file()
    assert existing_path.read_bytes() != original


def test_postcommit_snapshot_requires_each_preexisting_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    original = registry.register(_document("stable-city"), forbidden_city_ids=frozenset())
    stable_path = registry.workspace.root / "city-packs" / "stable-city.json"
    replacement = original.model_copy(update={"name": "Changed City"})
    replacement_bytes = canonical_json(replacement).encode("utf-8") + b"\n"
    real_verify = module._verify_committed

    def replace_peer_with_another_valid_pack(*args: object, **kwargs: object) -> None:
        real_verify(*args, **kwargs)
        stable_path.write_bytes(replacement_bytes)

    monkeypatch.setattr(module, "_verify_committed", replace_peer_with_another_valid_pack)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("new-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert (registry.workspace.root / "city-packs" / "new-city.json").is_file()
    assert stable_path.read_bytes() == replacement_bytes


@pytest.mark.parametrize("fault", [OSError("failure"), KeyboardInterrupt()])
def test_postlink_stage_removal_fault_preserves_complete_discoverable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: BaseException
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise fault

    monkeypatch.setattr(module, "_remove_owned_stage", fail)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("durable-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("durable-city",)
    stage_files = list((registry.workspace.root / "city-pack-staging").iterdir())
    assert len(stage_files) <= 1
    assert all("durable-city" not in path.name for path in stage_files)
    assert all(path.read_bytes() == _stored_bytes(registry, "durable-city") for path in stage_files)


@pytest.mark.parametrize("fault_name", ["_verify_committed", "_sync_directory"])
def test_every_postlink_verification_or_sync_fault_reports_possible_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault_name: str
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("post-link private fault")

    monkeypatch.setattr(module, fault_name, fail)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("postlink-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True
    assert (registry.workspace.root / "city-packs" / "postlink-city.json").is_file()


def test_keyboard_interrupt_before_link_is_clean_and_uncommitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)

    def interrupt(_file_descriptor: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_flush_staging", interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("interrupted-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is False
    assert registry.discover() == ()
    _assert_no_live_stage_bytes(registry)


def test_keyboard_interrupt_after_write_stage_return_cleans_caller_visible_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_write_stage = module._write_stage

    def write_then_interrupt(*args: object, **kwargs: object):
        real_write_stage(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_write_stage", write_then_interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("write-return-interrupt"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is False
    assert registry.discover() == ()
    _assert_no_live_stage_bytes(registry)


def test_keyboard_interrupt_after_publish_return_retains_commit_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_publish = module._publish

    def publish_then_interrupt(*args: object, **kwargs: object) -> None:
        real_publish(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_publish", publish_then_interrupt)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("publish-return-interrupt"), forbidden_city_ids=frozenset())

    assert captured.value.potentially_committed is True
    assert tuple(pack.city_id for pack in registry.discover()) == ("publish-return-interrupt",)


def test_context_exit_failure_after_publication_preserves_commit_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_verify = module._verify_lock_file
    verification_count = 0

    def fail_only_on_lock_context_exit(path: Path, descriptor: int) -> None:
        nonlocal verification_count
        verification_count += 1
        real_verify(path, descriptor)
        if verification_count == 2:
            raise module.WorkspaceCityRegistryError()

    monkeypatch.setattr(module, "_verify_lock_file", fail_only_on_lock_context_exit)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document("exit-fault-city"), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is True

    monkeypatch.setattr(module, "_verify_lock_file", real_verify)
    assert tuple(pack.city_id for pack in registry.discover()) == ("exit-fault-city",)


def test_short_and_zero_staging_writes_are_refused_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    registry = _registry(tmp_path)
    real_write = module._write_staging_chunk
    calls = 0

    def short_then_zero(file_descriptor: int, data: memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(file_descriptor, data[: max(1, len(data) // 2)])
        return 0

    monkeypatch.setattr(module, "_write_staging_chunk", short_then_zero)
    with pytest.raises(module.CityPackPublicationError) as captured:
        registry.register(_document(), forbidden_city_ids=frozenset())
    assert captured.value.potentially_committed is False
    assert registry.discover() == ()
    _assert_no_live_stage_bytes(registry)


def test_registry_error_messages_are_constant_and_value_free(tmp_path: Path) -> None:
    module = _module()
    classes = (
        module.MalformedCityPackUpload,
        module.InvalidCityPackUpload,
        module.CityPackUploadTooLarge,
        module.CityPackRegistrationConflict,
        module.WorkspaceCityRegistryError,
    )
    for error_class in classes:
        marker = "operator-secret-marker"
        error = error_class(marker)
        assert marker not in str(error)

    error = module.CityPackPublicationError(True)
    assert error.potentially_committed is True
    assert "true" not in str(error).lower()
