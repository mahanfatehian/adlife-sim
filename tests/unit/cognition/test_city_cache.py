from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

import adlife.adapters.cognition.city_cache as city_cache_module
import adlife.city.workbench_immutable_registry as immutable_registry
from adlife.adapters.cognition.city_cache import (
    MAX_CITY_COGNITION_CACHE_RECORD_BYTES,
    CityCognitionCache,
    CityCognitionCacheConflict,
    CityCognitionCacheCorruption,
    CityCognitionCachePublicationError,
    CityCognitionCacheRecordV1,
    CityCognitionCacheUnavailable,
)
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DISCLOSURE,
    CityCognitionResultV1,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition import build_city_cognition_requests
from tests.unit.city.test_city_cognition import _evidence, _input, _provider_profile


def _bypass_model(value, **updates: object):
    fields = {name: getattr(value, name) for name in type(value).model_fields}
    fields.update(updates)
    return type(value).model_construct(**fields)


def _assert_clean_exception_chain(error: BaseException, hostile: str) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert hostile not in repr(error)


def _case(tmp_path: Path):
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    cognition_input = _input(
        context,
        (request,),
        mode="local",
        provider_profile=_provider_profile(),
        aggregate_output_token_ceiling=1_536,
    )
    result = CityCognitionResultV1(
        schema_version=1,
        request_id=request.request_id,
        interpretation="The fictional response follows the already-computed rule evidence.",
        decision_factors=("Rule-owned response evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    key = CityCognitionCache.make_key(request, cognition_input)
    record = CityCognitionCacheRecordV1(
        schema_version=1,
        key=key,
        request=request,
        cognition_input=cognition_input,
        result=result,
        prompt_tokens=120,
        completion_tokens=36,
    )
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    return CityCognitionCache(workspace), record


def _path(cache: CityCognitionCache, record: CityCognitionCacheRecordV1) -> Path:
    return cache.workspace.root / "city-cognition-cache" / f"{record.key}.json"


def test_cache_round_trip_is_canonical_bounded_and_at_the_exact_workspace_path(
    tmp_path: Path,
) -> None:
    cache, record = _case(tmp_path)

    cache.put(record.key, record)

    path = _path(cache, record)
    expected = (canonical_json(record) + "\n").encode("utf-8")
    assert path.read_bytes() == expected
    assert len(expected) <= MAX_CITY_COGNITION_CACHE_RECORD_BYTES
    assert cache.get(record.key) == record
    assert tuple(path.parent.glob("*")) == (path,)


def test_cache_miss_is_only_a_genuinely_absent_digest(tmp_path: Path) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    cache = CityCognitionCache(workspace)

    assert cache.get("f" * 64) is None
    assert not (workspace.root / "city-cognition-cache").exists()


@pytest.mark.parametrize("key", ["../escape", "F" * 64, "f" * 63, "f" * 65, ""])
def test_cache_key_validation_is_path_free_and_value_free(tmp_path: Path, key: str) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    cache = CityCognitionCache(workspace)

    with pytest.raises(ValueError) as caught:
        cache.get(key)

    if key:
        assert key not in str(caught.value)
    assert not (workspace.root / "city-cognition-cache").exists()


def test_cache_key_binds_the_complete_request_and_frozen_cognition_input(tmp_path: Path) -> None:
    _cache, record = _case(tmp_path)
    request = record.request
    cognition_input = record.cognition_input
    larger_valid_budget = cognition_input.model_copy(
        update={"aggregate_output_token_ceiling": 3_072}
    )
    rebuilt_request = _bypass_model(request, state_before_sha256="f" * 64)
    rebuilt_input = _bypass_model(cognition_input, aggregate_deadline_seconds=179)

    assert CityCognitionCache.make_key(request, cognition_input) == record.key
    assert CityCognitionCache.make_key(request, larger_valid_budget) != record.key
    with pytest.raises(ValueError):
        CityCognitionCache.make_key(rebuilt_request, cognition_input)
    with pytest.raises(ValueError):
        CityCognitionCache.make_key(request, rebuilt_input)


def test_record_refuses_cross_bound_result_and_unfunded_request(tmp_path: Path) -> None:
    _cache, record = _case(tmp_path)
    wrong_result = _bypass_model(record.result, request_id="cog-" + "f" * 64)
    unfunded = _bypass_model(record.cognition_input, aggregate_output_token_ceiling=0)

    with pytest.raises(ValidationError, match="request"):
        CityCognitionCacheRecordV1(
            schema_version=1,
            key=record.key,
            request=record.request,
            cognition_input=record.cognition_input,
            result=wrong_result,
            prompt_tokens=120,
            completion_tokens=36,
        )
    with pytest.raises(ValidationError):
        CityCognitionCacheRecordV1(
            schema_version=1,
            key=record.key,
            request=record.request,
            cognition_input=unfunded,
            result=record.result,
            prompt_tokens=120,
            completion_tokens=36,
        )


def test_corrupt_or_noncanonical_record_is_a_hard_failure_and_is_preserved(
    tmp_path: Path,
) -> None:
    cache, record = _case(tmp_path)
    directory = cache.workspace.root / "city-cognition-cache"
    directory.mkdir()
    path = _path(cache, record)
    hostile = b'{"api_key":"opaquevalue1234567890"}'
    path.write_bytes(hostile)

    with pytest.raises(CityCognitionCacheCorruption) as caught:
        cache.get(record.key)

    assert path.read_bytes() == hostile
    assert "opaquevalue1234567890" not in str(caught.value)
    _assert_clean_exception_chain(caught.value, "opaquevalue1234567890")


def test_valid_but_noncanonical_json_is_corruption_not_a_cache_hit(tmp_path: Path) -> None:
    cache, record = _case(tmp_path)
    directory = cache.workspace.root / "city-cognition-cache"
    directory.mkdir()
    path = _path(cache, record)
    path.write_text(json.dumps(record.model_dump(mode="json"), indent=2), encoding="utf-8")

    with pytest.raises(CityCognitionCacheCorruption):
        cache.get(record.key)


def test_no_clobber_conflict_preserves_the_first_canonical_record(tmp_path: Path) -> None:
    cache, first = _case(tmp_path)
    cache.put(first.key, first)
    before = _path(cache, first).read_bytes()
    alternate_result = CityCognitionResultV1(
        schema_version=1,
        request_id=first.request.request_id,
        interpretation="A different but still valid synthetic explanation.",
        decision_factors=("Frozen deterministic evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    alternate = CityCognitionCacheRecordV1(
        schema_version=1,
        key=first.key,
        request=first.request,
        cognition_input=first.cognition_input,
        result=alternate_result,
        prompt_tokens=121,
        completion_tokens=37,
    )

    with pytest.raises(CityCognitionCacheConflict) as caught:
        cache.put(first.key, alternate)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _path(cache, first).read_bytes() == before
    assert cache.get(first.key) == first


def test_failed_publication_cleans_its_owned_stage_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, record = _case(tmp_path)

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("api_key=opaquevalue1234567890")

    monkeypatch.setattr(immutable_registry, "_publish_stage_descriptor", fail_link)
    with pytest.raises(CityCognitionCachePublicationError) as caught:
        cache.put(record.key, record)

    directory = cache.workspace.root / "city-cognition-cache"
    assert caught.value.potentially_committed is False
    assert "opaquevalue1234567890" not in str(caught.value)
    _assert_clean_exception_chain(caught.value, "opaquevalue1234567890")
    assert not _path(cache, record).exists()
    assert tuple(directory.iterdir()) == ()


def test_unreadable_post_link_state_remains_conservatively_possibly_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, record = _case(tmp_path)
    final_path = _path(cache, record)
    real_lstat = city_cache_module.os.lstat
    final_lstat_calls = 0

    def fail_second_final_lstat(path: object, *args: object, **kwargs: object):
        nonlocal final_lstat_calls
        if Path(path) == final_path:
            final_lstat_calls += 1
            if final_lstat_calls == 2:
                raise PermissionError("api_key=opaquevalue1234567890")
        return real_lstat(path, *args, **kwargs)

    def fail_after_link(_path: Path) -> None:
        raise OSError("injected post-link sync failure")

    monkeypatch.setattr(city_cache_module.os, "lstat", fail_second_final_lstat)
    monkeypatch.setattr(city_cache_module, "_sync_directory", fail_after_link)

    with pytest.raises(CityCognitionCachePublicationError) as caught:
        cache.put(record.key, record)

    assert caught.value.potentially_committed is True
    assert "opaquevalue1234567890" not in str(caught.value)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux descriptor-link probe")
def test_stage_name_substitution_cannot_change_published_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, record = _case(tmp_path)
    real_link = immutable_registry.os.link
    used_descriptor_source = False

    def swap_named_stage_then_link(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal used_descriptor_source
        if str(source).startswith("/proc/self/fd/"):
            used_descriptor_source = True
            real_link(source, destination, *args, **kwargs)
            return
        stage = Path(source)
        moved = stage.with_name(f"{stage.name}.owned")
        stage.rename(moved)
        stage.write_bytes(b"attacker-owned-replacement")
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(immutable_registry.os, "link", swap_named_stage_then_link)

    cache.put(record.key, record)

    assert used_descriptor_source is True
    assert cache.get(record.key) == record
    directory = cache.workspace.root / "city-cognition-cache"
    assert all(
        path.name == f"{record.key}.json" or path.stat().st_size == 0
        for path in directory.iterdir()
    )


def test_oversized_record_is_corruption_without_an_unbounded_read(tmp_path: Path) -> None:
    cache, record = _case(tmp_path)
    directory = cache.workspace.root / "city-cognition-cache"
    directory.mkdir()
    path = _path(cache, record)
    path.write_bytes(b"x" * (MAX_CITY_COGNITION_CACHE_RECORD_BYTES + 1))

    with pytest.raises(CityCognitionCacheCorruption):
        cache.get(record.key)

    assert path.stat().st_size == MAX_CITY_COGNITION_CACHE_RECORD_BYTES + 1


def test_symlinked_cache_directory_and_record_are_never_followed(tmp_path: Path) -> None:
    cache, record = _case(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    directory = cache.workspace.root / "city-cognition-cache"
    try:
        directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")

    with pytest.raises(CityCognitionCacheUnavailable):
        cache.get(record.key)

    directory.unlink()
    directory.mkdir()
    outside_record = outside / "record.json"
    outside_record.write_bytes((canonical_json(record) + "\n").encode("utf-8"))
    _path(cache, record).symlink_to(outside_record)
    with pytest.raises(CityCognitionCacheCorruption):
        cache.get(record.key)


def test_cache_revalidates_constructed_records_before_any_write(tmp_path: Path) -> None:
    cache, record = _case(tmp_path)
    invalid = _bypass_model(record, prompt_tokens=-1)

    with pytest.raises(ValueError):
        cache.put(record.key, invalid)

    assert not (cache.workspace.root / "city-cognition-cache").exists()


def test_cache_validation_and_workspace_errors_do_not_retain_hostile_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, record = _case(tmp_path)
    hostile = "api_key=opaquevalue1234567890"
    invalid_request = _bypass_model(record.request, request_id=hostile)
    invalid_result = _bypass_model(record.result, interpretation=hostile)
    invalid = _bypass_model(record, result=invalid_result)

    with pytest.raises(ValueError, match="key material") as key_error:
        CityCognitionCache.make_key(invalid_request, record.cognition_input)
    with pytest.raises(ValueError, match="record failed validation") as validation_error:
        cache.put(record.key, invalid)

    def fail_lookup(*_args: object, **_kwargs: object) -> None:
        raise OSError(hostile)

    monkeypatch.setattr(city_cache_module, "find_workbench_subdirectory", fail_lookup)
    with pytest.raises(CityCognitionCacheUnavailable) as workspace_error:
        cache.get(record.key)

    _assert_clean_exception_chain(key_error.value, hostile)
    _assert_clean_exception_chain(validation_error.value, hostile)
    _assert_clean_exception_chain(workspace_error.value, hostile)


def test_cache_setup_and_read_errors_do_not_retain_hostile_filesystem_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, record = _case(tmp_path)
    cache.put(record.key, record)
    hostile = r"C:\secret\api_key=opaquevalue1234567890"

    def fail_verification(*_args: object, **_kwargs: object) -> None:
        raise OSError(hostile)

    monkeypatch.setattr(city_cache_module, "verify_workbench_workspace", fail_verification)
    with pytest.raises(CityCognitionCacheUnavailable) as setup_error:
        CityCognitionCache(cache.workspace)
    monkeypatch.undo()

    def fail_read(*_args: object, **_kwargs: object) -> None:
        raise OSError(hostile)

    monkeypatch.setattr(immutable_registry, "_read_record", fail_read)
    with pytest.raises(CityCognitionCacheCorruption) as read_error:
        cache.get(record.key)

    _assert_clean_exception_chain(setup_error.value, hostile)
    _assert_clean_exception_chain(read_error.value, hostile)
    assert _path(cache, record).exists()


def test_cache_types_are_refused_before_filesystem_access(tmp_path: Path) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    cache = CityCognitionCache(workspace)

    with pytest.raises(TypeError, match="CityCognitionRequestV1"):
        CityCognitionCache.make_key("request", "input")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="CityCognitionCacheRecordV1"):
        cache.put("f" * 64, "record")  # type: ignore[arg-type]

    assert not (workspace.root / "city-cognition-cache").exists()


def test_cache_revalidation_emits_no_hostile_serializer_warnings(tmp_path: Path) -> None:
    cache, record = _case(tmp_path)
    hostile = "api_key=opaquevalue1234567890"
    invalid_request = _bypass_model(record.request, request_id={"secret": hostile})
    invalid_input = _bypass_model(
        record.cognition_input,
        run_input_sha256={"secret": hostile},
    )
    invalid_record = _bypass_model(record, prompt_tokens={"secret": hostile})

    operations = (
        lambda: CityCognitionCache.make_key(invalid_request, record.cognition_input),
        lambda: CityCognitionCache.make_key(record.request, invalid_input),
        lambda: cache.put(record.key, invalid_record),
    )
    for operation in operations:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            with pytest.raises(ValueError):
                operation()

        assert emitted == []
