from __future__ import annotations

import json
import multiprocessing
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

import adlife.city.provider_profiles as provider_profiles
import adlife.city.workbench_immutable_registry as immutable_registry
from adlife.city.provider_profiles import (
    MAX_PROVIDER_PROFILE_IDS,
    MAX_PROVIDER_PROFILE_REVISION_BYTES,
    MAX_PROVIDER_PROFILE_REVISIONS_PER_ID,
    InvalidProviderProfile,
    ProviderProfileConflict,
    ProviderProfileDraftV1,
    ProviderProfileNotFound,
    ProviderProfilePublicationError,
    ProviderProfileRegistry,
    ProviderProfileRegistryUnavailable,
    ProviderProfileRevisionV1,
)
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import (
    canonical_city_cognition_provider_profile_payload,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.serialization import canonical_json


def _draft(**updates: object) -> ProviderProfileDraftV1:
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "local-ollama",
        "display_label": "Local Ollama",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen2.5:7b",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "local-ollama",
    }
    document.update(updates)
    return ProviderProfileDraftV1.model_validate(document)


def _registry(tmp_path: Path, name: str = "workspace") -> ProviderProfileRegistry:
    workspace = prepare_workbench_workspace(tmp_path / name)
    return ProviderProfileRegistry(workspace)


def _record_path(registry: ProviderProfileRegistry, revision: ProviderProfileRevisionV1) -> Path:
    return (
        registry.workspace.root
        / "provider-profiles"
        / revision.profile_id
        / f"{revision.revision_sha256}.json"
    )


def _create_profile_revision_in_process(workspace_path: str, index: int) -> str:
    registry = ProviderProfileRegistry(prepare_workbench_workspace(Path(workspace_path)))
    try:
        registry.create(_draft(model_id=f"process-model-{index:02d}"))
    except ProviderProfileConflict:
        return "conflict"
    except ProviderProfileRegistryUnavailable:
        return "unavailable"
    return "created"


def test_profile_models_are_strict_frozen_and_accept_exact_boundaries() -> None:
    maximum_base_url_prefix = "https://example.test/"
    minimum = _draft(
        display_label="x",
        base_url="http://[::1]",
        model_id="x",
        temperature=0.0,
        top_p=0.000_001,
        seed=0,
        max_output_tokens=1,
        attempt_timeout_seconds=1,
        transient_retries=0,
        repair_attempts=0,
    )
    maximum = _draft(
        profile_id="p" * 64,
        display_label="d" * 80,
        mode="remote",
        base_url=maximum_base_url_prefix + "u" * (2_048 - len(maximum_base_url_prefix)),
        model_id="m" * 120,
        temperature=2.0,
        top_p=1.0,
        seed=2**63 - 1,
    )

    assert minimum.max_output_tokens == 1
    assert maximum.seed == 2**63 - 1
    with pytest.raises(ValidationError):
        minimum.profile_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ProviderProfileDraftV1.model_validate({**minimum.model_dump(), "unexpected": True})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("profile_id", ""),
        ("profile_id", "UPPER"),
        ("profile_id", "con"),
        ("profile_id", "sk-" + "a" * 30),
        ("profile_id", "p" * 65),
        ("display_label", ""),
        ("display_label", "d" * 81),
        ("display_label", " bad"),
        ("display_label", "bad\nlabel"),
        ("mode", "rules"),
        ("base_url", ""),
        ("base_url", "u" * 2_049),
        ("base_url", "https://example.test/v1?api_key=opaquevalue1234567890"),
        ("model_id", ""),
        ("model_id", "m" * 121),
        ("protocol", "openai-chat-completions-v1"),
        ("temperature", True),
        ("temperature", -0.01),
        ("temperature", 2.01),
        ("temperature", float("nan")),
        ("top_p", False),
        ("top_p", 0.0),
        ("top_p", 1.01),
        ("seed", True),
        ("seed", -1),
        ("seed", 2**63),
        ("max_output_tokens", True),
        ("max_output_tokens", 0),
        ("max_output_tokens", 385),
        ("attempt_timeout_seconds", True),
        ("attempt_timeout_seconds", 0),
        ("attempt_timeout_seconds", 31),
        ("transient_retries", True),
        ("transient_retries", -1),
        ("transient_retries", 3),
        ("repair_attempts", True),
        ("repair_attempts", -1),
        ("repair_attempts", 2),
        ("origin_approval_id", "aux"),
        ("origin_approval_id", "sk-" + "a" * 30),
        ("origin_approval_id", "approval/escape"),
    ],
)
def test_profile_draft_refuses_invalid_or_nonportable_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _draft(**{field: value})


@pytest.mark.parametrize(
    "updates",
    [
        {"seed_policy": "omit", "seed": 42},
        {"seed_policy": "fixed", "seed": None},
        {"display_label": "api_key", "model_id": "opaquevalue1234567890"},
        {"base_url": "secret", "model_id": "opaquevalue1234567890"},
    ],
)
def test_profile_draft_refuses_incoherent_seed_or_cross_field_secret_pair(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _draft(**updates)


def test_omit_seed_policy_requires_a_null_seed() -> None:
    draft = _draft(seed_policy="omit", seed=None)

    assert draft.seed is None


@pytest.mark.parametrize(
    ("mode", "base_url"),
    [
        ("local", "x"),
        ("local", "http://localhost:11434/v1"),
        ("local", "http://api.example.test/v1"),
        ("remote", "http://api.example.test/v1"),
        ("remote", "https://127.0.0.1:11434/v1"),
        ("remote", "https://API.example.test/v1"),
        ("remote", "https://api.example.test:443/v1"),
        ("remote", "https://api.example.test/v1/"),
        ("remote", "https://api.example.test/v1/../admin"),
        ("remote", "https://user:password@api.example.test/v1"),
    ],
)
def test_profile_draft_requires_a_canonical_url_matching_its_mode(
    mode: str,
    base_url: str,
) -> None:
    with pytest.raises(ValidationError):
        _draft(mode=mode, base_url=base_url)


def test_profile_draft_accepts_canonical_local_and_remote_urls() -> None:
    assert _draft(base_url="http://[::1]:11434/v1").mode == "local"
    assert (
        _draft(
            mode="remote",
            base_url="https://api.example.test/openai/v1",
            origin_approval_id="remote-acme",
        ).mode
        == "remote"
    )


def test_transport_words_remain_valid_user_facing_profile_text() -> None:
    draft = _draft(display_label="Cookies", model_id="authorization")

    assert draft.display_label == "Cookies"
    assert draft.model_id == "authorization"


def test_validation_and_registry_errors_never_echo_hostile_values(tmp_path: Path) -> None:
    hostile = "api_key=opaquevalue1234567890"
    with pytest.raises(ValidationError) as validation_error:
        _draft(display_label=hostile)
    assert hostile not in str(validation_error.value)

    registry = _registry(tmp_path)
    with pytest.raises(InvalidProviderProfile) as lookup_error:
        registry.load(f"../{hostile}", "f" * 64)
    assert hostile not in str(lookup_error.value)


def test_create_is_canonical_content_addressed_restart_safe_and_stable(tmp_path: Path) -> None:
    first_registry = _registry(tmp_path, "first")
    second_registry = _registry(tmp_path, "second")
    first = first_registry.create(_draft())
    second = second_registry.create(_draft())

    assert first == second
    assert first.credential_slot_id == (
        "65dcb348e53d13c216b8301ff5e06e5f26bb1b0e48eed716ab57fe3aafd7c52a"
    )
    assert first.revision_sha256 == (
        "c1f628a955f1f1727f9824bcf426818c82fcbe0c268d4ff7ac08e30a62117622"
    )
    assert (
        first.credential_slot_id
        == sha256(canonical_city_cognition_provider_profile_payload(_draft())).hexdigest()
    )
    assert first.revision_sha256 == city_cognition_provider_profile_revision_sha256(first)
    assert canonical_city_cognition_provider_profile_payload(first) == (
        canonical_json(first.model_dump(exclude={"revision_sha256"})) + "\n"
    ).encode("utf-8")
    assert _record_path(first_registry, first).read_bytes() == (
        canonical_json(first) + "\n"
    ).encode("utf-8")
    assert ProviderProfileRegistry(first_registry.workspace).list() == (first,)
    assert (
        ProviderProfileRegistry(first_registry.workspace).load(
            first.profile_id, first.revision_sha256
        )
        == first
    )
    assert not (first_registry.workspace.root / "provider-profiles" / "current").exists()
    assert not hasattr(first_registry, "delete")
    assert not hasattr(first_registry, "update")


def test_distinct_revisions_and_listing_are_sorted_by_id_then_hash(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    revisions = (
        registry.create(_draft(profile_id="zeta", origin_approval_id="zeta")),
        registry.create(_draft(profile_id="alpha", origin_approval_id="alpha", model_id="model-b")),
        registry.create(_draft(profile_id="alpha", origin_approval_id="alpha", model_id="model-a")),
    )

    assert len({item.credential_slot_id for item in revisions}) == 3
    assert registry.list() == tuple(
        sorted(revisions, key=lambda item: (item.profile_id, item.revision_sha256))
    )


def test_create_revalidates_constructed_draft_before_any_write(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    invalid = ProviderProfileDraftV1.model_construct(**{**_draft().model_dump(), "top_p": 0.0})

    with pytest.raises(InvalidProviderProfile):
        registry.create(invalid)

    assert not (registry.workspace.root / "provider-profiles").exists()


def test_create_does_not_warn_with_hostile_constructed_draft_values(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    secret = "api_key=opaquevalue1234567890"
    document = _draft().model_dump(mode="python")
    document["top_p"] = {"secret": secret}
    invalid = ProviderProfileDraftV1.model_construct(**document)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(InvalidProviderProfile):
            registry.create(invalid)

    assert all(secret not in str(item.message) for item in caught)
    assert not (registry.workspace.root / "provider-profiles").exists()


def test_duplicate_revision_is_a_no_clobber_conflict(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    expected = registry.create(_draft())
    before = _record_path(registry, expected).read_bytes()

    with pytest.raises(ProviderProfileConflict):
        registry.create(_draft())

    assert _record_path(registry, expected).read_bytes() == before
    assert registry.list() == (expected,)


def test_registry_enforces_profile_and_revision_capacities(tmp_path: Path) -> None:
    by_id = _registry(tmp_path, "by-id")
    for index in range(MAX_PROVIDER_PROFILE_IDS):
        profile_id = f"provider-{index:02d}"
        by_id.create(_draft(profile_id=profile_id, origin_approval_id=profile_id))
    with pytest.raises(ProviderProfileConflict):
        by_id.create(_draft(profile_id="provider-overflow", origin_approval_id="overflow"))

    by_revision = _registry(tmp_path, "by-revision")
    for index in range(MAX_PROVIDER_PROFILE_REVISIONS_PER_ID):
        by_revision.create(_draft(model_id=f"model-{index:02d}"))
    with pytest.raises(ProviderProfileConflict):
        by_revision.create(_draft(model_id="model-overflow"))

    assert len(by_id.list()) == MAX_PROVIDER_PROFILE_IDS
    assert len(by_revision.list()) == MAX_PROVIDER_PROFILE_REVISIONS_PER_ID


def test_concurrent_duplicate_create_publishes_exactly_once(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    def create() -> str:
        try:
            return registry.create(_draft()).revision_sha256
        except ProviderProfileConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = tuple(executor.map(lambda _index: create(), range(8)))

    assert outcomes.count("conflict") == 7
    assert len(registry.list()) == 1


def test_concurrent_distinct_revisions_cannot_exceed_capacity(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    def create(index: int) -> str:
        try:
            return registry.create(_draft(model_id=f"model-{index:02d}")).revision_sha256
        except ProviderProfileConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=11) as executor:
        outcomes = tuple(executor.map(create, range(11)))

    assert outcomes.count("conflict") == 1
    assert len(registry.list()) == MAX_PROVIDER_PROFILE_REVISIONS_PER_ID


def test_cross_process_creates_cannot_exceed_revision_capacity(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    context = multiprocessing.get_context("spawn")

    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        outcomes = tuple(
            executor.map(
                _create_profile_revision_in_process,
                (os.fspath(registry.workspace.root),) * 11,
                range(11),
            )
        )

    assert outcomes.count("created") == MAX_PROVIDER_PROFILE_REVISIONS_PER_ID
    assert outcomes.count("conflict") == 1
    assert outcomes.count("unavailable") == 0
    assert len(registry.list()) == MAX_PROVIDER_PROFILE_REVISIONS_PER_ID


def test_precommit_publication_failure_leaves_no_live_staging_bytes_and_retry_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)

    def refuse_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected precommit failure")

    with monkeypatch.context() as patch:
        if os.name == "nt":
            patch.setattr(immutable_registry, "_link_windows_descriptor", refuse_link)
        else:
            patch.setattr(immutable_registry.os, "link", refuse_link)
        with pytest.raises(ProviderProfilePublicationError) as error:
            registry.create(_draft())

    assert error.value.potentially_committed is False
    staging = registry.workspace.root / "provider-profile-staging"
    assert all(not path.is_file() or path.stat().st_size == 0 for path in staging.iterdir())
    assert registry.list() == ()
    assert ProviderProfileRegistry(registry.workspace).create(_draft()).profile_id == "local-ollama"


def test_postlink_failure_reports_commit_ambiguity_without_clobbering_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)
    real_read = immutable_registry._read_record
    calls = 0

    def fail_first_verification(*args: object, **kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected postlink failure")
        return real_read(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(immutable_registry, "_read_record", fail_first_verification)
        with pytest.raises(ProviderProfilePublicationError) as error:
            registry.create(_draft())

    assert error.value.potentially_committed is True
    committed = ProviderProfileRegistry(registry.workspace).list()
    assert len(committed) == 1
    before = _record_path(registry, committed[0]).read_bytes()
    with pytest.raises(ProviderProfileConflict):
        registry.create(_draft())
    assert _record_path(registry, committed[0]).read_bytes() == before


def test_failure_after_publish_is_reported_as_potentially_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)
    real_parse = provider_profiles._parse_snapshot
    calls = 0

    def fail_second_parse(records: tuple[object, ...]) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProviderProfileRegistryUnavailable()
        return real_parse(records)

    monkeypatch.setattr("adlife.city.provider_profiles._parse_snapshot", fail_second_parse)
    with pytest.raises(ProviderProfilePublicationError) as error:
        registry.create(_draft())

    assert error.value.potentially_committed is True
    monkeypatch.undo()
    assert len(ProviderProfileRegistry(registry.workspace).list()) == 1


@pytest.mark.parametrize("sync_name", ["_sync_workspace_root", "_sync_pinned_directory"])
def test_publication_setup_failures_are_typed_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sync_name: str,
) -> None:
    registry = _registry(tmp_path)
    hostile = "api_key=opaquevalue1234567890"

    def fail_sync(*_args: object, **_kwargs: object) -> None:
        raise OSError(hostile)

    with monkeypatch.context() as patch:
        patch.setattr(immutable_registry, sync_name, fail_sync)
        with pytest.raises(ProviderProfilePublicationError) as error:
            registry.create(_draft())

    assert error.value.potentially_committed is False
    assert hostile not in str(error.value)
    assert registry.list() == ()


def test_posix_stage_name_swap_cannot_publish_replacement_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("the descriptor publication probe is Linux-specific")
    registry = _registry(tmp_path)
    real_link = immutable_registry.os.link
    replacement = b"attacker-owned-replacement"
    staging = registry.workspace.root / "provider-profile-staging"

    def swap_then_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        stage = next(staging.iterdir())
        stage.unlink()
        stage.write_bytes(replacement)
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(immutable_registry.os, "link", swap_then_link)
    revision = registry.create(_draft())

    assert _record_path(registry, revision).read_bytes() == (
        canonical_json(revision) + "\n"
    ).encode("utf-8")
    assert any(path.read_bytes() == replacement for path in staging.iterdir())


def test_load_refuses_missing_invalid_and_reserved_identifiers_without_echo(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    revision = registry.create(_draft())

    for profile_id, digest in (
        ("missing", revision.revision_sha256),
        ("con", revision.revision_sha256),
        ("../escape", revision.revision_sha256),
        (revision.profile_id, "A" * 64),
    ):
        with pytest.raises(
            (InvalidProviderProfile, ProviderProfileNotFound, ProviderProfileRegistryUnavailable)
        ) as error:
            registry.load(profile_id, digest)
        assert profile_id not in str(error.value)


@pytest.mark.parametrize("mutation", ["whitespace", "extra", "wrong-hash", "oversized"])
def test_list_and_load_fail_closed_on_noncanonical_or_corrupt_records(
    tmp_path: Path,
    mutation: str,
) -> None:
    registry = _registry(tmp_path)
    revision = registry.create(_draft())
    path = _record_path(registry, revision)
    document = revision.model_dump(mode="json")
    if mutation == "whitespace":
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    elif mutation == "extra":
        path.write_text(canonical_json({**document, "api_key": "opaquevalue1234567890"}) + "\n")
    elif mutation == "wrong-hash":
        document["revision_sha256"] = "f" * 64
        path.write_text(canonical_json(document) + "\n", encoding="utf-8")
    else:
        path.write_bytes(b"x" * (MAX_PROVIDER_PROFILE_REVISION_BYTES + 1))

    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.load(revision.profile_id, revision.revision_sha256)


def test_registry_refuses_unexpected_entries_and_filename_identity_mismatch(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    revision = registry.create(_draft())
    root = registry.workspace.root / "provider-profiles"
    (root / "unexpected.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()

    (root / "unexpected.txt").unlink()
    original = _record_path(registry, revision)
    original.rename(original.with_name(f"{'f' * 64}.json"))
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()


def test_registry_refuses_symlinks_at_each_stored_tree_boundary(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    profiles = registry.workspace.root / "provider-profiles"
    try:
        profiles.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.create(_draft())


def test_registry_refuses_a_symlinked_staging_tree(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    staging = registry.workspace.root / "provider-profile-staging"
    try:
        staging.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")

    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.create(_draft())


def test_registry_refuses_a_hardlinked_lock_without_mutating_its_other_name(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"")
    lock = registry.workspace.root / ".provider-profile-registry.lock"
    try:
        os.link(outside, lock)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")

    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()
    assert outside.read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="open lock names cannot be replaced on Windows")
def test_registry_refuses_lock_replacement_immediately_after_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)
    lock = registry.workspace.root / ".provider-profile-registry.lock"
    real_lock = immutable_registry._lock_descriptor

    def replace_after_lock(descriptor: int) -> None:
        real_lock(descriptor)
        lock.unlink()
        lock.write_bytes(b"\0")

    monkeypatch.setattr(immutable_registry, "_lock_descriptor", replace_after_lock)
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()


@pytest.mark.skipif(os.name == "nt", reason="open lock names cannot be replaced on Windows")
def test_registry_refuses_lock_replacement_before_transaction_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)
    lock = registry.workspace.root / ".provider-profile-registry.lock"
    real_parse = provider_profiles._parse_snapshot

    def replace_during_transaction(records: tuple[object, ...]) -> object:
        result = real_parse(records)
        lock.unlink()
        lock.write_bytes(b"\0")
        return result

    monkeypatch.setattr(provider_profiles, "_parse_snapshot", replace_during_transaction)
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()


def test_registry_refuses_symlinked_profile_and_record(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    revision = registry.create(_draft())
    profiles = registry.workspace.root / "provider-profiles"
    original_profile = profiles / revision.profile_id
    moved_profile = tmp_path / "moved-profile"
    original_profile.rename(moved_profile)
    try:
        original_profile.symlink_to(moved_profile, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()

    original_profile.unlink()
    moved_profile.rename(original_profile)
    record = _record_path(registry, revision)
    outside = tmp_path / "outside-record.json"
    record.rename(outside)
    try:
        record.symlink_to(outside)
    except OSError:
        pytest.skip("this account cannot create file symlinks")
    with pytest.raises(ProviderProfileRegistryUnavailable):
        registry.list()


def test_public_limits_are_exact_and_every_valid_revision_fits(tmp_path: Path) -> None:
    assert MAX_PROVIDER_PROFILE_IDS == 20
    assert MAX_PROVIDER_PROFILE_REVISIONS_PER_ID == 10
    assert MAX_PROVIDER_PROFILE_REVISION_BYTES == 16_384
    registry = _registry(tmp_path)
    maximum_base_url_prefix = "https://example.test/"
    revision = registry.create(
        _draft(
            display_label="d" * 80,
            mode="remote",
            base_url=maximum_base_url_prefix + "u" * (2_048 - len(maximum_base_url_prefix)),
            model_id="m" * 120,
            seed=2**63 - 1,
        )
    )

    assert os.path.getsize(_record_path(registry, revision)) <= 16_384
