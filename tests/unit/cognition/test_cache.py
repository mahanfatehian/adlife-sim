"""The content-addressed cognition cache: key recipe, secrecy, and atomic storage."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.adapters.cognition.cache import (
    CACHE_KEY_VERSION,
    CognitionCache,
    CorruptCacheRecord,
)
from adlife.core.domain.campaign import Campaign
from adlife.core.domain.person import PersonProfile
from adlife.core.ports.cognition import (
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
)

_KEY_HEX = 64


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _result(request: CognitionRequest) -> CognitionResult:
    return CognitionResult(
        request_id=request.request_id,
        interpretation="A calm fictional handset, shown while commuting.",
        emotion="curious",
        valence=0.30,
        relevance=0.50,
        credibility=0.70,
        sentiment_delta=0.06,
        recall_delta=0.12,
        purchase_reason="Rule-derived intention; a language model never supplies it.",
        share_probability=0.04,
        discussion_hook="A phone pitched at a calmer routine.",
        grounded_reasons=("matches technology interest", "second exposure", "calm framing"),
        memory_summary="Saw campaign-phone on mobile-feed while commuting.",
        rule_modifier=0.02,
        safety_flags=(),
    )


def _record(
    request: CognitionRequest,
    metadata: ProviderMetadata,
    *,
    raw_response: str | None = '{"emotion":"curious"}',
) -> CognitionRecord:
    key = CognitionCache.make_key(request, metadata)
    return CognitionRecord(
        key=key,
        request=request,
        provider_metadata=metadata,
        raw_response=raw_response,
        result=_result(request),
        usage=ProviderUsage(
            provider_kind=metadata.kind,
            model_id=metadata.model_id,
            prompt_tokens=118,
            completion_tokens=64,
            latency_ms=0,
        ),
    )


# --- the documented key recipe --------------------------------------------------------


def test_the_cache_key_is_the_documented_canonical_digest(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """Derive the key by hand from the documented recipe, not from the code under test."""
    expected_request = {
        "schema_version": 1,
        "request_id": "run-demo:event-00000007",
        "run_id": "run-demo",
        "simulated_minute": 480,
        "agent_id": "person-001",
        "fictional_persona": {
            "age_band": "25-34",
            "household_type": "shared-apartment",
            "interests": ["fitness", "technology"],
            "occupation": "office-worker",
        },
        "activity": "commute",
        "mood": 0.2,
        "relevant_memories": ["Noticed a fictional phone advertisement yesterday."],
        "campaign": {
            "call_to_action": "Explore the fictional product",
            "campaign_id": "campaign-phone",
            "message": "A fictional phone designed for a calmer daily routine.",
            "product_category": "consumer-electronics",
            "product_name": "Fictional Phone",
        },
        "channel": "mobile-feed",
        "exposure_count": 2,
        "creative_sha256": "a" * 64,
        "prompt_version": "cognition-v1",
    }
    expected_key_input = {
        "base_url": "",
        "cache_version": CACHE_KEY_VERSION,
        "model_digest": "",
        "model_id": "mock-v1",
        "prompt_sha256": "b" * 64,
        "prompt_version": "cognition-v1",
        "provider_kind": "mock",
        "request_sha256": _digest(expected_request),
        "sampling": {
            "max_output_tokens": 512,
            "seed": None,
            "temperature": 0.0,
            "top_p": 1.0,
        },
    }
    assert CognitionCache.make_key(cognition_request, provider_metadata) == _digest(
        expected_key_input
    )


def test_identical_inputs_return_the_same_key(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    first = CognitionCache.make_key(cognition_request, provider_metadata)
    second = CognitionCache.make_key(
        cognition_request.model_copy(),
        provider_metadata.model_copy(),
    )
    assert first == second
    assert len(first) == _KEY_HEX


def test_mapping_order_never_changes_the_key(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    provider_metadata: ProviderMetadata,
) -> None:
    shuffled = dict(reversed(list(cognition_campaign.items())))
    reordered = cognition_request.model_copy(update={"campaign": shuffled})
    assert CognitionCache.make_key(reordered, provider_metadata) == CognitionCache.make_key(
        cognition_request, provider_metadata
    )


_REQUEST_TERMS: tuple[tuple[str, Callable[[CognitionRequest], CognitionRequest]], ...] = (
    ("request_id", lambda r: r.model_copy(update={"request_id": "run-demo:event-00000008"})),
    (
        "run_id",
        lambda r: r.model_copy(
            update={"run_id": "run-other", "request_id": "run-other:event-00000007"}
        ),
    ),
    ("simulated_minute", lambda r: r.model_copy(update={"simulated_minute": 495})),
    ("agent_id", lambda r: r.model_copy(update={"agent_id": "person-002"})),
    ("activity", lambda r: r.model_copy(update={"activity": "leisure"})),
    ("mood", lambda r: r.model_copy(update={"mood": 0.21})),
    ("channel", lambda r: r.model_copy(update={"channel": "highway-billboard"})),
    ("exposure_count", lambda r: r.model_copy(update={"exposure_count": 3})),
    ("creative_sha256", lambda r: r.model_copy(update={"creative_sha256": "c" * 64})),
    ("prompt_version", lambda r: r.model_copy(update={"prompt_version": "cognition-v2"})),
    ("relevant_memories", lambda r: r.model_copy(update={"relevant_memories": ()})),
    (
        "fictional_persona",
        lambda r: r.model_copy(
            update={"fictional_persona": dict(r.fictional_persona) | {"age_band": "35-44"}}
        ),
    ),
    (
        "campaign",
        lambda r: r.model_copy(
            update={"campaign": dict(r.campaign) | {"product_name": "Fictional Tablet"}}
        ),
    ),
)


@pytest.mark.parametrize(("term", "mutate"), _REQUEST_TERMS, ids=[t for t, _ in _REQUEST_TERMS])
def test_changing_a_request_term_changes_the_key(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    term: str,
    mutate: Callable[[CognitionRequest], CognitionRequest],
) -> None:
    assert CognitionCache.make_key(mutate(cognition_request), provider_metadata) != (
        CognitionCache.make_key(cognition_request, provider_metadata)
    )


_METADATA_TERMS: tuple[tuple[str, Callable[[ProviderMetadata], ProviderMetadata]], ...] = (
    ("kind", lambda m: m.model_copy(update={"kind": "replay"})),
    ("model_id", lambda m: m.model_copy(update={"model_id": "mock-v2"})),
    ("model_digest", lambda m: m.model_copy(update={"model_digest": "d" * 64})),
    ("prompt_sha256", lambda m: m.model_copy(update={"prompt_sha256": "e" * 64})),
    (
        "sampling.temperature",
        lambda m: m.model_copy(update={"sampling": SamplingSettings(temperature=0.7)}),
    ),
    ("sampling.top_p", lambda m: m.model_copy(update={"sampling": SamplingSettings(top_p=0.9)})),
    (
        "sampling.max_output_tokens",
        lambda m: m.model_copy(update={"sampling": SamplingSettings(max_output_tokens=256)}),
    ),
    ("sampling.seed", lambda m: m.model_copy(update={"sampling": SamplingSettings(seed=7)})),
)


@pytest.mark.parametrize(("term", "mutate"), _METADATA_TERMS, ids=[t for t, _ in _METADATA_TERMS])
def test_changing_a_provider_term_changes_the_key(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    term: str,
    mutate: Callable[[ProviderMetadata], ProviderMetadata],
) -> None:
    assert CognitionCache.make_key(cognition_request, mutate(provider_metadata)) != (
        CognitionCache.make_key(cognition_request, provider_metadata)
    )


def test_changing_the_base_url_changes_the_key(
    cognition_request: CognitionRequest,
) -> None:
    def remote(host: str) -> ProviderMetadata:
        return ProviderMetadata(
            kind="remote-llm",
            base_url=f"https://{host}/v1",
            model_id="fictional-model",
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )

    assert CognitionCache.make_key(cognition_request, remote("first.invalid")) != (
        CognitionCache.make_key(cognition_request, remote("second.invalid"))
    )


# --- secrecy --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "normalized"),
    [
        ("https://analyst:super-secret@api.invalid/v1", "https://api.invalid/v1"),
        ("https://api.invalid/v1?api_key=super-secret", "https://api.invalid/v1"),
        ("https://api.invalid/v1#super-secret", "https://api.invalid/v1"),
    ],
)
def test_provider_metadata_strips_credentials_from_the_base_url(
    supplied: str,
    normalized: str,
) -> None:
    metadata = ProviderMetadata(
        kind="remote-llm",
        base_url=supplied,
        model_id="fictional-model",
        sampling=SamplingSettings(),
        prompt_sha256="b" * 64,
    )
    assert metadata.base_url == normalized


def test_credentials_in_the_base_url_never_change_the_key(
    cognition_request: CognitionRequest,
) -> None:
    def remote(base_url: str) -> ProviderMetadata:
        return ProviderMetadata(
            kind="remote-llm",
            base_url=base_url,
            model_id="fictional-model",
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )

    with_key = remote("https://analyst:super-secret@api.invalid/v1?api_key=super-secret")
    without_key = remote("https://api.invalid/v1")
    assert CognitionCache.make_key(cognition_request, with_key) == CognitionCache.make_key(
        cognition_request, without_key
    )


def test_credentials_never_reach_a_stored_record(
    tmp_path: Path,
    cognition_request: CognitionRequest,
) -> None:
    metadata = ProviderMetadata(
        kind="remote-llm",
        base_url="https://analyst:super-secret@api.invalid/v1?api_key=super-secret",
        model_id="fictional-model",
        sampling=SamplingSettings(),
        prompt_sha256="b" * 64,
    )
    cache = CognitionCache(tmp_path)
    record = _record(cognition_request, metadata)
    cache.put(record.key, record)
    stored = (tmp_path / f"{record.key}.json").read_text(encoding="utf-8")
    assert "super-secret" not in stored


# --- storage --------------------------------------------------------------------------


def test_put_then_get_round_trips_the_record_byte_identically(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    cache = CognitionCache(tmp_path / "nested" / "cache")
    record = _record(cognition_request, provider_metadata)
    cache.put(record.key, record)
    loaded = cache.get(record.key)
    assert loaded is not None
    assert loaded.model_dump_json() == record.model_dump_json()


def test_the_cache_creates_its_directory_on_first_put(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    directory = tmp_path / "runs" / "run-demo" / "cognition-cache"
    assert not directory.exists()
    record = _record(cognition_request, provider_metadata)
    CognitionCache(directory).put(record.key, record)
    assert (directory / f"{record.key}.json").is_file()


def test_get_returns_nothing_for_an_unrecorded_key(tmp_path: Path) -> None:
    assert CognitionCache(tmp_path).get("f" * 64) is None


def test_put_refuses_a_record_whose_key_does_not_match_its_content(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    record = _record(cognition_request, provider_metadata)
    other = record.model_copy(update={"key": "0" * 64})
    with pytest.raises(ValueError, match="key"):
        CognitionCache(tmp_path).put("0" * 64, other)


def test_put_refuses_a_key_that_is_not_a_digest(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    record = _record(cognition_request, provider_metadata)
    with pytest.raises(ValueError, match="cache key"):
        CognitionCache(tmp_path).put("../../escape", record)


def test_get_refuses_a_key_that_is_not_a_digest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cache key"):
        CognitionCache(tmp_path).get("../../escape")


def test_get_refuses_a_corrupt_record_file(tmp_path: Path) -> None:
    key = "f" * 64
    (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CorruptCacheRecord, match=key):
        CognitionCache(tmp_path).get(key)


def test_get_refuses_a_record_stored_under_another_key(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    record = _record(cognition_request, provider_metadata)
    impostor = "f" * 64
    (tmp_path / f"{impostor}.json").write_text(record.model_dump_json(), encoding="utf-8")
    with pytest.raises(CorruptCacheRecord, match=impostor):
        CognitionCache(tmp_path).get(impostor)


def test_put_leaves_no_temporary_file_behind(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    record = _record(cognition_request, provider_metadata)
    CognitionCache(tmp_path).put(record.key, record)
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{record.key}.json"]


def test_a_failed_replace_leaves_the_previous_record_intact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """A direct write to the final path would destroy the earlier record; a sibling cannot."""
    cache = CognitionCache(tmp_path)
    original = _record(cognition_request, provider_metadata)
    cache.put(original.key, original)

    def _failing_replace(source: object, target: object) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", _failing_replace)
    replacement = original.model_copy(update={"raw_response": "a completely different body"})
    with pytest.raises(OSError, match="simulated interruption"):
        cache.put(original.key, replacement)

    monkeypatch.undo()
    loaded = cache.get(original.key)
    assert loaded is not None
    assert loaded.model_dump_json() == original.model_dump_json()
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{original.key}.json"]


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://api.invalid/v1",
        "file:///models/v1",
        "https:///v1",
        "https://api.invalid:not-a-port/v1",
    ],
)
def test_provider_metadata_refuses_a_base_url_that_is_not_a_usable_endpoint(
    base_url: str,
) -> None:
    with pytest.raises(ValueError, match="base_url"):
        ProviderMetadata(
            kind="remote-llm",
            base_url=base_url,
            model_id="fictional-model",
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


def test_provider_metadata_keeps_an_explicit_port(
    cognition_request: CognitionRequest,
) -> None:
    metadata = ProviderMetadata(
        kind="local-llm",
        base_url="http://127.0.0.1:11434/v1",
        model_id="fictional-local-model",
        sampling=SamplingSettings(),
        prompt_sha256="b" * 64,
    )
    assert metadata.base_url == "http://127.0.0.1:11434/v1"
    other = metadata.model_copy(update={"base_url": "http://127.0.0.1:11435/v1"})
    assert CognitionCache.make_key(cognition_request, metadata) != CognitionCache.make_key(
        cognition_request, other
    )


def test_make_key_refuses_inputs_that_are_not_the_published_contracts(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    with pytest.raises(TypeError, match="CognitionRequest"):
        CognitionCache.make_key("not a request", provider_metadata)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ProviderMetadata"):
        CognitionCache.make_key(cognition_request, "not metadata")  # type: ignore[arg-type]


def test_the_cache_refuses_a_directory_that_is_not_a_path() -> None:
    with pytest.raises(TypeError, match="Path"):
        CognitionCache("cache")  # type: ignore[arg-type]


def test_put_refuses_a_value_that_is_not_a_record(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="CognitionRecord"):
        CognitionCache(tmp_path).put("0" * 64, "not a record")  # type: ignore[arg-type]


def test_get_refuses_a_record_whose_provider_metadata_no_longer_derives_its_key(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """Content addressing is re-derived on read, not taken from the record's own claim."""
    record = _record(cognition_request, provider_metadata)
    tampered = record.model_copy(
        update={"provider_metadata": provider_metadata.model_copy(update={"model_id": "mock-v2"})}
    )
    assert tampered.key == record.key
    (tmp_path / f"{record.key}.json").write_text(tampered.model_dump_json(), encoding="utf-8")
    with pytest.raises(CorruptCacheRecord, match=record.key):
        CognitionCache(tmp_path).get(record.key)


def test_get_refuses_a_record_whose_request_no_longer_derives_its_key(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    record = _record(cognition_request, provider_metadata)
    tampered = record.model_copy(
        update={"request": cognition_request.model_copy(update={"agent_id": "person-002"})}
    )
    assert tampered.key == record.key
    (tmp_path / f"{record.key}.json").write_text(tampered.model_dump_json(), encoding="utf-8")
    with pytest.raises(CorruptCacheRecord, match=record.key):
        CognitionCache(tmp_path).get(record.key)


def test_get_returns_a_record_that_still_derives_its_own_key(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    cache = CognitionCache(tmp_path)
    record = _record(cognition_request, provider_metadata)
    cache.put(record.key, record)
    loaded = cache.get(record.key)
    assert loaded is not None
    assert CognitionCache.make_key(loaded.request, loaded.provider_metadata) == record.key


def test_a_sorted_frozenset_projection_keys_identically_in_every_process(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
    provider_metadata: ProviderMetadata,
) -> None:
    """The canonical digest sorts object keys, never array elements.

    A caller therefore sorts its own ``list(frozenset)`` projection, and the key is then
    the same in every process. The port preserves array order rather than rewriting the
    payload, so this obligation is the caller's and is pinned here.
    """

    def build() -> CognitionRequest:
        return cognition_request.model_copy(
            update={
                "fictional_persona": {
                    "age_band": "25-34",
                    "household_type": valid_profile.household_type,
                    "interests": sorted({*valid_profile.interests, "cycling", "cooking"}),
                    "occupation": valid_profile.occupation,
                },
                "campaign": dict(cognition_campaign)
                | {
                    "target_interests": sorted(
                        {*valid_campaign.target_interests, "travel", "audio"}
                    )
                },
            }
        )

    assert CognitionCache.make_key(build(), provider_metadata) == CognitionCache.make_key(
        build(), provider_metadata
    )


def test_an_unsorted_array_projection_keys_differently(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    provider_metadata: ProviderMetadata,
) -> None:
    """Array order is prompt content, so it is key material - the recorded consequence."""
    targets = ["audio", "technology", "travel"]
    forward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"target_interests": targets}}
    )
    backward = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign) | {"target_interests": list(reversed(targets))}
        }
    )
    assert CognitionCache.make_key(forward, provider_metadata) != CognitionCache.make_key(
        backward, provider_metadata
    )


def test_mapping_order_never_changes_the_stored_bytes(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    provider_metadata: ProviderMetadata,
) -> None:
    """The record filed under a key must be a function of that key, byte for byte.

    ``make_key`` digests a key-sorted canonical JSON while ``put`` stores
    ``model_dump_json()``. If the prompt objects kept their insertion order the same key
    would address different bytes depending on how the prompt builder assembled them, and
    re-recording under another ordering would silently rewrite the file.
    """
    reordered = cognition_request.model_copy(
        update={"campaign": dict(reversed(list(cognition_campaign.items())))}
    )
    assert reordered == cognition_request
    forward = _record(cognition_request, provider_metadata)
    backward = _record(reordered, provider_metadata)
    assert forward.key == backward.key
    assert forward.model_dump_json() == backward.model_dump_json()

    cache = CognitionCache(tmp_path)
    cache.put(forward.key, forward)
    first = (tmp_path / f"{forward.key}.json").read_text(encoding="utf-8")
    cache.put(backward.key, backward)
    second = (tmp_path / f"{backward.key}.json").read_text(encoding="utf-8")
    assert first == second


def test_an_echoed_credential_never_persists_in_a_stored_record(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """The base URL is not the only channel a credential can reach the cache through.

    Task 10 owns the HTTP adapter that fills ``raw_response``, and a 401 body is where a
    provider echoes an authorization header back, so the seam is enforced here.
    """
    cache = CognitionCache(tmp_path)
    record = _record(
        cognition_request,
        provider_metadata,
        raw_response=(
            '{"error":{"message":"Incorrect API key provided: sk-live-SUPERSECRET0123456789",'
            '"code":401},"contact":"ops@vendor.invalid"}'
        ),
    )
    cache.put(record.key, record)
    stored = (tmp_path / f"{record.key}.json").read_text(encoding="utf-8")
    assert "sk-live-SUPERSECRET0123456789" not in stored
    assert "ops@vendor.invalid" not in stored
    reloaded = cache.get(record.key)
    assert reloaded is not None
    assert reloaded.raw_response == record.raw_response


def test_an_environment_shaped_credential_never_persists_in_a_stored_record(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """Specification section 6.4 reads the key from ``ADLIFE_API_KEY``.

    A provider that echoes the environment variable it was configured from writes the
    live key into ``runs/RUN_ID`` cache JSON, which is exactly the persistence
    specification section 19 forbids.
    """
    cache = CognitionCache(tmp_path)
    record = _record(
        cognition_request,
        provider_metadata,
        raw_response='{"detail":"ADLIFE_API_KEY: fictional-token-00000000 was rejected"}',
    )
    cache.put(record.key, record)
    stored = (tmp_path / f"{record.key}.json").read_text(encoding="utf-8")
    assert "fictional-token-00000000" not in stored
