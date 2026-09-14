"""Replay: a recorded cognition run is reproduced byte-identically and offline."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from adlife.adapters.cognition.cache import CacheMiss, CognitionCache, CorruptCacheRecord
from adlife.adapters.cognition.mock import MockCognitionProvider
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.core.ports.cognition import (
    CognitionError,
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
)


def _record(
    request: CognitionRequest,
    metadata: ProviderMetadata,
    result: CognitionResult,
    *,
    usage: ProviderUsage | None = None,
    raw_response: str | None = None,
) -> CognitionRecord:
    return CognitionRecord(
        key=CognitionCache.make_key(request, metadata),
        request=request,
        provider_metadata=metadata,
        raw_response=raw_response,
        result=result,
        usage=usage
        or ProviderUsage(
            provider_kind=metadata.kind,
            model_id=metadata.model_id,
            prompt_tokens=118,
            completion_tokens=64,
            latency_ms=0,
        ),
    )


async def _recorded(
    directory: Path,
    request: CognitionRequest,
    metadata: ProviderMetadata,
) -> tuple[CognitionCache, CognitionResult]:
    result = await MockCognitionProvider().evaluate(request)
    cache = CognitionCache(directory)
    record = _record(request, metadata, result)
    cache.put(record.key, record)
    return cache, result


async def test_replay_returns_the_recorded_validated_result(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    cache, recorded = await _recorded(tmp_path, cognition_request, provider_metadata)
    replayed = await ReplayCognitionProvider(cache, provider_metadata).evaluate(cognition_request)
    assert replayed.model_dump_json() == recorded.model_dump_json()


async def test_a_replayed_run_reproduces_every_recorded_result_byte_identically(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    live = MockCognitionProvider()
    cache = CognitionCache(tmp_path)
    requests = tuple(
        cognition_request.model_copy(
            update={
                "request_id": f"run-demo:event-{index:08d}",
                "simulated_minute": 480 + index * 15,
                "exposure_count": 1 + index % 3,
            }
        )
        for index in range(12)
    )
    recorded: list[str] = []
    for request in requests:
        result = await live.evaluate(request)
        recorded.append(result.model_dump_json())
        record = _record(request, provider_metadata, result)
        cache.put(record.key, record)

    replay = ReplayCognitionProvider(cache, provider_metadata)
    replayed = [(await replay.evaluate(request)).model_dump_json() for request in requests]
    assert replayed == recorded


async def test_replay_reports_a_cache_hit_for_a_recorded_request(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    result = await MockCognitionProvider().evaluate(cognition_request)
    stored_usage = ProviderUsage(
        provider_kind="mock",
        model_id="mock-v1",
        prompt_tokens=118,
        completion_tokens=64,
        latency_ms=0,
        cache_hit=False,
    )
    cache = CognitionCache(tmp_path)
    record = _record(cognition_request, provider_metadata, result, usage=stored_usage)
    cache.put(record.key, record)

    usage = ReplayCognitionProvider(cache, provider_metadata).usage_for(cognition_request)
    assert usage.cache_hit is True
    assert (
        usage.model_dump_json()
        == stored_usage.model_copy(update={"cache_hit": True}).model_dump_json()
    )


async def test_replay_reproduces_a_recorded_fallback_exactly(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """A recorded failure replays as the same fallback, with its reason preserved."""
    result = await MockCognitionProvider().evaluate(cognition_request)
    fallback_usage = ProviderUsage(
        provider_kind="fallback",
        model_id="rule-v1",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=0,
        fallback_reason="invalid-response",
    )
    cache = CognitionCache(tmp_path)
    record = _record(
        cognition_request,
        provider_metadata,
        result,
        usage=fallback_usage,
        raw_response="this was never valid JSON",
    )
    cache.put(record.key, record)

    provider = ReplayCognitionProvider(cache, provider_metadata)
    assert (await provider.evaluate(cognition_request)).model_dump_json() == (
        result.model_dump_json()
    )
    replayed_usage = provider.usage_for(cognition_request)
    assert replayed_usage.fallback_reason == "invalid-response"
    assert replayed_usage.provider_kind == "fallback"


async def test_replay_raises_a_cache_miss_naming_the_exact_key(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    provider = ReplayCognitionProvider(CognitionCache(tmp_path), provider_metadata)
    expected_key = CognitionCache.make_key(cognition_request, provider_metadata)
    with pytest.raises(CacheMiss, match=expected_key) as raised:
        await provider.evaluate(cognition_request)
    assert raised.value.key == expected_key


async def test_replay_misses_when_the_provider_metadata_differs(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    cache, _ = await _recorded(tmp_path, cognition_request, provider_metadata)
    other = ProviderMetadata(
        kind="remote-llm",
        base_url="https://api.invalid/v1",
        model_id="fictional-model",
        sampling=SamplingSettings(),
        prompt_sha256="b" * 64,
    )
    with pytest.raises(CacheMiss):
        await ReplayCognitionProvider(cache, other).evaluate(cognition_request)


async def test_replay_refuses_a_record_bound_to_another_request(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """The cache refuses it first: its content no longer derives the key it is filed under."""
    result = await MockCognitionProvider().evaluate(cognition_request)
    key = CognitionCache.make_key(cognition_request, provider_metadata)
    other_request = cognition_request.model_copy(update={"agent_id": "person-002"})
    tampered = CognitionRecord(
        key=key,
        request=other_request,
        provider_metadata=provider_metadata,
        raw_response=None,
        result=result,
        usage=ProviderUsage(
            provider_kind="mock",
            model_id="mock-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        ),
    )
    (tmp_path / f"{key}.json").write_text(tampered.model_dump_json(), encoding="utf-8")
    with pytest.raises(CorruptCacheRecord, match=key):
        await ReplayCognitionProvider(CognitionCache(tmp_path), provider_metadata).evaluate(
            cognition_request
        )


async def test_replay_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    cache, recorded = await _recorded(tmp_path, cognition_request, provider_metadata)

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("replay reached the network")

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    replayed = await ReplayCognitionProvider(cache, provider_metadata).evaluate(cognition_request)
    assert replayed.model_dump_json() == recorded.model_dump_json()


async def test_replay_serves_a_request_whose_mapping_order_differs(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    provider_metadata: ProviderMetadata,
) -> None:
    """Two equal requests that key identically must replay identically.

    ``fictional_persona`` and ``campaign`` are JSON objects whose insertion order the
    prompt builder does not promise. The cache key sorts them; the record-binding guard
    must agree with the key rather than with a raw serialization.
    """
    cache, recorded = await _recorded(tmp_path, cognition_request, provider_metadata)
    reordered = cognition_request.model_copy(
        update={"campaign": dict(reversed(list(cognition_campaign.items())))}
    )
    assert reordered == cognition_request
    assert CognitionCache.make_key(reordered, provider_metadata) == CognitionCache.make_key(
        cognition_request, provider_metadata
    )
    replayed = await ReplayCognitionProvider(cache, provider_metadata).evaluate(reordered)
    assert replayed.model_dump_json() == recorded.model_dump_json()


async def test_replay_reports_a_mismatched_record_inside_the_cognition_error_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """Defence in depth: a record handed back by the cache must still answer this request.

    A service that falls back on ``CognitionError`` has to be able to catch this, so the
    refusal stays inside the hierarchy rather than escaping as a bare ``ValueError``.
    """
    cache, _ = await _recorded(tmp_path, cognition_request, provider_metadata)
    other = cognition_request.model_copy(update={"agent_id": "person-002"})
    tampered = _record(other, provider_metadata, await MockCognitionProvider().evaluate(other))
    monkeypatch.setattr(CognitionCache, "get", lambda self, key: tampered)

    provider = ReplayCognitionProvider(cache, provider_metadata)
    with pytest.raises(CognitionError, match="different cognition request"):
        await provider.evaluate(cognition_request)


async def test_replay_misses_when_a_prompt_array_was_built_in_another_order(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    provider_metadata: ProviderMetadata,
) -> None:
    """Array order is prompt content, so a re-ordered array is a different question.

    The port preserves the array a caller authored rather than sorting it, so a caller
    that projects a domain frozenset must sort it. This is the recorded consequence: an
    unsorted projection misses rather than silently replaying another payload's answer,
    and it misses inside the :class:`CognitionError` hierarchy so a run can fall back.
    """
    targets = ["audio", "technology", "travel"]
    recorded_request = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"target_interests": targets}}
    )
    cache, recorded = await _recorded(tmp_path, recorded_request, provider_metadata)

    provider = ReplayCognitionProvider(cache, provider_metadata)
    replayed = await provider.evaluate(recorded_request)
    assert replayed.model_dump_json() == recorded.model_dump_json()

    reordered = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign) | {"target_interests": list(reversed(targets))}
        }
    )
    assert reordered != recorded_request
    assert reordered.campaign["target_interests"] == tuple(reversed(targets))
    with pytest.raises(CacheMiss):
        await provider.evaluate(reordered)


async def test_replay_reads_and_validates_its_record_once_per_answer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """The result and its provenance come out of one read of one file.

    Asking for the result and then for the usage re-read, re-parsed and re-validated the
    same record a second time, so the pair was not atomic over the file it describes.
    """
    cache, _ = await _recorded(tmp_path, cognition_request, provider_metadata)
    reads: list[str] = []
    original = CognitionCache.get

    def counted(self: CognitionCache, key: str) -> CognitionRecord | None:
        reads.append(key)
        return original(self, key)

    monkeypatch.setattr(CognitionCache, "get", counted)
    provider = ReplayCognitionProvider(cache, provider_metadata)

    answer = await provider.answer(cognition_request)
    assert len(reads) == 1
    assert answer.usage.cache_hit is True

    reads.clear()
    await provider.evaluate(cognition_request)
    provider.usage_for(cognition_request)
    assert len(reads) == 2


async def test_replay_reproduces_a_recorded_fallback_through_the_port(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """A replayed run must not restamp a recorded fallback as an ordinary replay.

    The original run recorded ``provider_kind="fallback"`` with a reason. A service coded
    against the port reads that provenance back through ``answer``, so the replayed event
    stream carries the same source and the same reason as the run it reproduces.
    """
    result = await MockCognitionProvider().evaluate(cognition_request)
    cache = CognitionCache(tmp_path)
    record = _record(
        cognition_request,
        provider_metadata,
        result,
        usage=ProviderUsage(
            provider_kind="fallback",
            model_id="rule-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            fallback_reason="invalid-response",
        ),
    )
    cache.put(record.key, record)

    answer = await ReplayCognitionProvider(cache, provider_metadata).answer(cognition_request)
    assert answer.usage.provider_kind == "fallback"
    assert answer.usage.fallback_reason == "invalid-response"
    assert answer.usage.model_id == "rule-v1"
    assert answer.usage.cache_hit is True
    assert answer.result.model_dump_json() == result.model_dump_json()
