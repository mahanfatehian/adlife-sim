"""The bounded cognition service: cache, budget, retry, repair, and rule fallback.

The provider under test is the real :class:`OpenAICompatibleProvider` driven by
:class:`httpx.MockTransport`, so the service meets real transport errors, real HTTP
statuses and real malformed bodies rather than a hand-written double. Nothing here
opens a socket, reads a credential from the environment or sleeps for real time: the
retry sleep is injected and recorded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from adlife.adapters.cognition.cache import CacheMiss, CognitionCache
from adlife.adapters.cognition.mock import MockCognitionProvider
from adlife.adapters.cognition.openai_compatible import (
    InvalidProviderResponse,
    OpenAICompatibleProvider,
    ProviderCall,
)
from adlife.adapters.cognition.prompts import (
    BEGIN_SIMULATION_DATA,
    END_SIMULATION_DATA,
)
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.adapters.cognition.rules import (
    RULE_MODEL_ID,
    RuleCognitionInputs,
    RuleCognitionProvider,
    UnknownCognitionRequest,
)
from adlife.adapters.cognition.service import (
    MAX_CONCURRENT_PROVIDER_CALLS,
    MAX_PROVIDER_ATTEMPTS,
    MAX_PROVIDER_RETRIES,
    NO_BODY_PLACEHOLDER,
    RETRY_BASE_DELAYS_SECONDS,
    RETRY_JITTER_SECONDS,
    RETRY_NAMESPACE,
    CognitionBudget,
    CognitionResolution,
    CognitionService,
    DuplicateCognitionRequest,
)
from adlife.config.models import ProviderSettings
from adlife.core.domain.campaign import Campaign
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.cognition import (
    CognitionError,
    CognitionProvider,
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    ProviderMetadata,
    ProviderUsage,
)
from adlife.core.simulation.rng import RandomOracle

REMOTE_BASE_URL = "https://provider.invalid/v1"
ROOT_SEED = 7
REALISTIC_FAKE_KEY = "sk-live-AAAABBBBCCCCDDDD"

ECHO = "<answer-whichever-request-arrives>"
"""A script entry that answers the posted request, so script order cannot matter."""

Script = Sequence[object]


def _content(request_id: str, **overrides: object) -> str:
    payload: dict[str, object] = {
        "schema_version": 1,
        "request_id": request_id,
        "interpretation": "A calm fictional handset, shown while commuting.",
        "emotion": "curious",
        "valence": 0.3,
        "relevance": 0.5,
        "credibility": 0.7,
        "sentiment_delta": 0.06,
        "recall_delta": 0.12,
        "purchase_reason": "Rule-derived intention; a language model never supplies it.",
        "share_probability": 0.04,
        "discussion_hook": "A phone pitched at a calmer routine.",
        "grounded_reasons": ["matches technology interest", "second exposure"],
        "memory_summary": "Saw campaign-phone on mobile-feed while commuting.",
        "rule_modifier": 0.05,
        "safety_flags": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _completion(content: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-0000",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _posted_request_id(http_request: httpx.Request) -> str:
    """Read back the request identity the prompt carries, exactly as a model would.

    A repair exchange appends further messages, so the fenced block is located rather
    than assumed to be last.
    """
    messages = json.loads(http_request.content)["messages"]
    fenced = next(
        message["content"]
        for message in messages
        if message["role"] == "user" and BEGIN_SIMULATION_DATA in message["content"]
    )
    start = fenced.index(BEGIN_SIMULATION_DATA) + len(BEGIN_SIMULATION_DATA)
    end = fenced.index(END_SIMULATION_DATA)
    data = json.loads(fenced[start:end].strip())
    request_id = data["request_id"]
    assert isinstance(request_id, str)
    return request_id


class _Sleeps:
    """The injected retry sleep: it records the delay and never blocks the test."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _RecordingCache(CognitionCache):
    """A real cache that also remembers the order records were written in."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.writes: list[str] = []

    def put(self, key: str, record: CognitionRecord) -> None:
        self.writes.append(record.request.request_id)
        super().put(key, record)


def _scripted(
    script: Script,
    *,
    api_key: str = "test-secret",
) -> tuple[OpenAICompatibleProvider, list[str]]:
    """A real provider whose transport returns the scripted answers in order."""
    calls: list[str] = []
    remaining = list(script)

    def handler(http_request: httpx.Request) -> httpx.Response:
        posted = _posted_request_id(http_request)
        calls.append(posted)
        entry = remaining.pop(0) if remaining else "exhausted-script"
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, httpx.Response):
            return entry
        if entry == ECHO:
            return httpx.Response(200, json=_completion(_content(posted)))
        assert isinstance(entry, str)
        return httpx.Response(200, json=_completion(entry))

    provider = OpenAICompatibleProvider(
        base_url=REMOTE_BASE_URL,
        model="test-model",
        api_key=api_key,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )
    return provider, calls


def _request(
    persona: Mapping[str, object],
    campaign: Mapping[str, object],
    *,
    sequence: int = 7,
    agent_id: str = "person-001",
    simulated_minute: int = 480,
) -> CognitionRequest:
    return CognitionRequest(
        request_id=f"run-demo:event-{sequence:08d}",
        run_id="run-demo",
        simulated_minute=simulated_minute,
        agent_id=agent_id,
        fictional_persona=dict(persona),
        activity="commute",
        mood=0.2,
        relevant_memories=("Noticed a fictional phone advertisement yesterday.",),
        campaign=dict(campaign),
        channel="mobile-feed",
        exposure_count=2,
        creative_sha256="a" * 64,
    )


def _requests(
    persona: Mapping[str, object],
    campaign: Mapping[str, object],
    count: int,
) -> tuple[CognitionRequest, ...]:
    return tuple(
        _request(
            persona,
            campaign,
            sequence=index + 1,
            agent_id=f"person-{index + 1:03d}",
        )
        for index in range(count)
    )


def _fallback(
    requests: Sequence[CognitionRequest],
    profile: PersonProfile,
    state: ConsumerState,
    campaign: Campaign,
) -> RuleCognitionProvider:
    inputs = {
        request.request_id: RuleCognitionInputs(
            profile=profile.model_copy(update={"agent_id": request.agent_id}),
            state=state.model_copy(update={"agent_id": request.agent_id}),
            campaign=campaign,
            placement=campaign.placements[0],
        )
        for request in requests
    }
    return RuleCognitionProvider.for_requests(requests, inputs)


def _service(
    fallback: CognitionProvider,
    *,
    budget: CognitionBudget | None = None,
    cache: CognitionCache | None = None,
    sleep: Callable[[float], Any] | None = None,
) -> CognitionService:
    return CognitionService(
        fallback=fallback,
        budget=budget if budget is not None else CognitionBudget(per_agent=6, total=180),
        oracle=RandomOracle(root_seed=ROOT_SEED),
        cache=cache,
        sleep=sleep if sleep is not None else _Sleeps(),
    )


def _cached_record(
    cache: CognitionCache,
    request: CognitionRequest,
    metadata: ProviderMetadata,
    result: CognitionResult,
) -> str:
    key = cache.make_key(request, metadata)
    cache.put(
        key,
        CognitionRecord(
            key=key,
            request=request,
            provider_metadata=metadata,
            raw_response='{"cached":true}',
            result=result,
            usage=ProviderUsage(
                provider_kind="remote-llm",
                model_id="test-model",
                prompt_tokens=3,
                completion_tokens=4,
                latency_ms=5,
            ),
        ),
    )
    return key


# --- the cache ------------------------------------------------------------------------


async def test_a_cache_hit_answers_without_a_provider_call(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, calls = _scripted([])
    cache = CognitionCache(tmp_path)
    recorded = CognitionResult.model_validate_json(_content(cognition_request.request_id))
    _cached_record(cache, cognition_request, provider.provider_metadata, recorded)

    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        cache=cache,
    )
    resolution = await service.evaluate_one(cognition_request, provider)

    assert calls == []
    assert resolution.result == recorded
    assert resolution.usage.cache_hit is True
    assert resolution.source == "remote-llm"
    assert resolution.fallback_reason is None


async def test_a_cache_hit_does_not_consume_the_cognition_budget(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A replayed run must not exhaust the budget it never spent."""
    provider, _ = _scripted([])
    cache = CognitionCache(tmp_path)
    recorded = CognitionResult.model_validate_json(_content(cognition_request.request_id))
    _cached_record(cache, cognition_request, provider.provider_metadata, recorded)

    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        budget=CognitionBudget(per_agent=1, total=1),
        cache=cache,
    )
    await service.evaluate_one(cognition_request, provider)
    await service.evaluate_one(cognition_request, provider)
    assert service.spent_total == 0


async def test_a_provider_answer_is_written_to_the_cache(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    body = _content(cognition_request.request_id)
    provider, _ = _scripted([body])
    cache = CognitionCache(tmp_path)
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        cache=cache,
    )

    resolution = await service.evaluate_one(cognition_request, provider)
    stored = cache.get(cache.make_key(cognition_request, provider.provider_metadata))

    assert stored is not None
    assert stored.result == resolution.result
    assert stored.raw_response == body
    assert stored.usage.provider_kind == "remote-llm"


async def test_a_fallback_is_never_written_to_the_cache(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Caching a fallback would make one transient failure permanent for every replay."""
    provider, _ = _scripted(["not-json", "still-not-json"])
    cache = _RecordingCache(tmp_path)
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        cache=cache,
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert resolution.source == "fallback"
    assert cache.writes == []
    assert cache.get(cache.make_key(cognition_request, provider.provider_metadata)) is None


async def test_a_corrupt_cache_record_falls_through_to_the_provider(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A record that fails its own integrity check is discarded, never trusted or fatal."""
    provider, calls = _scripted([_content(cognition_request.request_id)])
    cache = CognitionCache(tmp_path)
    key = cache.make_key(cognition_request, provider.provider_metadata)
    cache.path_for(key).parent.mkdir(parents=True, exist_ok=True)
    cache.path_for(key).write_text('{"not":"a record"}', encoding="utf-8")

    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        cache=cache,
    )
    resolution = await service.evaluate_one(cognition_request, provider)

    assert calls == [cognition_request.request_id]
    assert resolution.source == "remote-llm"


async def test_a_provider_without_metadata_simply_skips_the_cache(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    cache = _RecordingCache(tmp_path)
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        cache=cache,
    )
    resolution = await service.evaluate_one(cognition_request, MockCognitionProvider())
    assert resolution.source == "mock"
    assert cache.writes == []


# --- repair and fallback --------------------------------------------------------------


async def test_malformed_response_falls_back_after_one_repair(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, calls = _scripted(["not-json", "still-not-json"])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == 2
    assert resolution.source == "fallback"
    assert resolution.fallback_reason == "invalid-response"
    assert resolution.result.request_id == cognition_request.request_id


async def test_a_repaired_answer_is_accepted_without_a_fallback(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, calls = _scripted(["not-json", _content(cognition_request.request_id)])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == 2
    assert resolution.source == "remote-llm"
    assert resolution.fallback_reason is None
    assert resolution.attempts == 2


async def test_a_repair_of_a_bodiless_answer_echoes_a_placeholder(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A malformed envelope carries no content, and an empty turn is not a prompt."""
    echoed: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        messages = json.loads(http_request.content)["messages"]
        echoed.extend(message["content"] for message in messages if message["role"] == "assistant")
        return httpx.Response(200, json={"choices": []})

    provider = OpenAICompatibleProvider(
        base_url=REMOTE_BASE_URL,
        model="test-model",
        api_key="test-secret",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert echoed == [NO_BODY_PLACEHOLDER]
    assert resolution.fallback_reason == "invalid-response"


async def test_only_one_repair_is_attempted(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Specification section 12 allows exactly one schema-repair prompt, not a loop."""
    provider, calls = _scripted(["bad-1", "bad-2", _content(cognition_request.request_id)])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == 2
    assert resolution.source == "fallback"


async def test_the_fallback_answer_is_the_rule_providers_own_answer(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    fallback = _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    provider, _ = _scripted(["not-json", "still-not-json"])
    service = _service(fallback)

    resolution = await service.evaluate_one(cognition_request, provider)

    assert resolution.result == await fallback.evaluate(cognition_request)
    assert resolution.usage.model_id == RULE_MODEL_ID
    assert resolution.usage.prompt_tokens == 0
    assert resolution.usage.latency_ms == 0


async def test_a_failed_call_keeps_its_body_but_never_a_credential(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
    caplog: pytest.LogCaptureFixture,
) -> None:
    leaking = f'{{"error":{{"api_key":"{REALISTIC_FAKE_KEY}"}}}}'
    provider, _ = _scripted([leaking, leaking], api_key=REALISTIC_FAKE_KEY)
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    with caplog.at_level(logging.DEBUG):
        resolution = await service.evaluate_one(cognition_request, provider)

    assert resolution.source == "fallback"
    assert resolution.raw_response is not None
    assert REALISTIC_FAKE_KEY not in resolution.raw_response
    assert REALISTIC_FAKE_KEY not in resolution.model_dump_json()
    assert all(REALISTIC_FAKE_KEY not in record.getMessage() for record in caplog.records)


# --- retries --------------------------------------------------------------------------


async def test_a_timeout_is_retried_to_the_documented_attempt_limit(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Specification section 7: two retries after the initial attempt, then stop."""
    timeouts = [httpx.ReadTimeout("timed out") for _ in range(5)]
    provider, calls = _scripted(timeouts)
    sleeps = _Sleeps()
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        sleep=sleeps,
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == MAX_PROVIDER_ATTEMPTS == 3
    assert len(sleeps.delays) == 2
    assert resolution.fallback_reason == "timeout"
    assert resolution.attempts == 3


async def test_the_retry_delays_are_the_documented_bases_plus_keyed_jitter(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """The bases and the jitter width are literals here, so both terms are pinned."""
    provider, _ = _scripted([httpx.ReadTimeout("timed out") for _ in range(3)])
    sleeps = _Sleeps()
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        sleep=sleeps,
    )

    await service.evaluate_one(cognition_request, provider)

    oracle = RandomOracle(root_seed=ROOT_SEED)
    namespace = f"{RETRY_NAMESPACE}:{cognition_request.campaign_id}"
    first = oracle.uniform(namespace, cognition_request.agent_id, 480, 1)
    second = oracle.uniform(namespace, cognition_request.agent_id, 480, 2)
    assert RETRY_BASE_DELAYS_SECONDS == (0.5, 1.5)
    assert RETRY_JITTER_SECONDS == 0.25
    assert sleeps.delays == [
        pytest.approx(0.5 + 0.25 * first),
        pytest.approx(1.5 + 0.25 * second),
    ]


async def test_the_retry_jitter_key_covers_campaign_agent_minute_and_attempt(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Drop any term from the key and two of these four delays collapse together."""

    async def _delays(request: CognitionRequest, campaign: Campaign) -> list[float]:
        provider, _ = _scripted([httpx.ReadTimeout("timed out") for _ in range(3)])
        sleeps = _Sleeps()
        service = _service(
            _fallback([request], valid_profile, consumer_state, campaign),
            sleep=sleeps,
        )
        await service.evaluate_one(request, provider)
        return sleeps.delays

    other_campaign_payload = dict(cognition_campaign)
    other_campaign_payload["campaign_id"] = "campaign-other"
    other_campaign = valid_campaign.model_copy(update={"campaign_id": "campaign-other"})

    baseline = await _delays(_request(cognition_persona, cognition_campaign), valid_campaign)
    other_agent = await _delays(
        _request(cognition_persona, cognition_campaign, agent_id="person-002"),
        valid_campaign,
    )
    other_minute = await _delays(
        _request(cognition_persona, cognition_campaign, simulated_minute=495),
        valid_campaign,
    )
    other_campaign_delays = await _delays(
        _request(cognition_persona, other_campaign_payload),
        other_campaign,
    )

    assert baseline[0] != other_agent[0]
    assert baseline[0] != other_minute[0]
    assert baseline[0] != other_campaign_delays[0]
    assert baseline[0] - RETRY_BASE_DELAYS_SECONDS[0] != baseline[1] - RETRY_BASE_DELAYS_SECONDS[1]


async def test_a_rate_limited_call_is_retried_and_then_succeeds(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, calls = _scripted(
        [
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            _content(cognition_request.request_id),
        ]
    )
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == 2
    assert resolution.source == "remote-llm"
    assert resolution.fallback_reason is None


async def test_a_rejected_credential_is_not_retried(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, calls = _scripted([httpx.Response(401, json={"error": {"message": "nope"}})])
    sleeps = _Sleeps()
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        sleep=sleeps,
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == 1
    assert sleeps.delays == []
    assert resolution.fallback_reason == "http-error"


async def test_an_invalid_body_is_repaired_rather_than_retried(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A schema failure is deterministic; repeating the same prompt cannot fix it."""
    provider, _ = _scripted(["not-json", "still-not-json"])
    sleeps = _Sleeps()
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        sleep=sleeps,
    )

    await service.evaluate_one(cognition_request, provider)

    assert sleeps.delays == []


async def test_a_repair_that_times_out_reports_the_last_failure(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, calls = _scripted(["not-json", httpx.ReadTimeout("timed out")])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == 2
    assert resolution.fallback_reason == "timeout"


# --- budgets --------------------------------------------------------------------------


async def test_an_exhausted_per_agent_budget_falls_back_without_dispatching(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    first = _request(cognition_persona, cognition_campaign, sequence=1)
    second = _request(cognition_persona, cognition_campaign, sequence=2)
    provider, calls = _scripted([_content(first.request_id)])
    service = _service(
        _fallback([first, second], valid_profile, consumer_state, valid_campaign),
        budget=CognitionBudget(per_agent=1, total=180),
    )

    answered = await service.evaluate_one(first, provider)
    refused = await service.evaluate_one(second, provider)

    assert calls == [first.request_id]
    assert answered.source == "remote-llm"
    assert refused.source == "fallback"
    assert refused.fallback_reason == "budget-exhausted"
    assert refused.attempts == 0


async def test_a_total_budget_is_spent_in_request_id_order(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Who wins a scarce budget must not depend on task scheduling."""
    requests = _requests(cognition_persona, cognition_campaign, 4)
    provider, calls = _scripted([ECHO, ECHO])
    service = _service(
        _fallback(requests, valid_profile, consumer_state, valid_campaign),
        budget=CognitionBudget(per_agent=6, total=2),
    )

    resolved = await service.evaluate_many(reversed(requests), provider)

    assert sorted(calls) == [requests[0].request_id, requests[1].request_id]
    assert [resolved[request.request_id].source for request in requests] == [
        "remote-llm",
        "remote-llm",
        "fallback",
        "fallback",
    ]
    assert resolved[requests[3].request_id].fallback_reason == "budget-exhausted"


async def test_a_zero_budget_answers_every_request_from_the_rule_fallback(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    requests = _requests(cognition_persona, cognition_campaign, 3)
    provider, calls = _scripted([])
    service = _service(
        _fallback(requests, valid_profile, consumer_state, valid_campaign),
        budget=CognitionBudget(per_agent=0, total=0),
    )

    resolved = await service.evaluate_many(requests, provider)

    assert calls == []
    assert len(resolved) == 3
    assert {resolution.fallback_reason for resolution in resolved.values()} == {"budget-exhausted"}


def test_a_budget_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="budget"):
        CognitionBudget(per_agent=-1, total=10)


# --- concurrency and ordering ---------------------------------------------------------


async def test_results_are_committed_in_request_id_order_whatever_the_completion_order(
    tmp_path: Path,
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    requests = _requests(cognition_persona, cognition_campaign, 4)
    identifiers = [request.request_id for request in requests]
    yields = {request_id: index for index, request_id in enumerate(reversed(identifiers))}
    completed: list[str] = []

    async def handler(http_request: httpx.Request) -> httpx.Response:
        request_id = _posted_request_id(http_request)
        for _ in range(yields[request_id]):
            await asyncio.sleep(0)
        completed.append(request_id)
        return httpx.Response(200, json=_completion(_content(request_id)))

    provider = OpenAICompatibleProvider(
        base_url=REMOTE_BASE_URL,
        model="test-model",
        api_key="test-secret",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )
    cache = _RecordingCache(tmp_path)
    service = _service(
        _fallback(requests, valid_profile, consumer_state, valid_campaign),
        cache=cache,
    )

    resolved = await service.evaluate_many(reversed(requests), provider)

    assert completed == list(reversed(identifiers))
    assert list(resolved) == identifiers
    assert cache.writes == identifiers


async def test_no_more_than_four_provider_calls_are_in_flight(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    requests = _requests(cognition_persona, cognition_campaign, 8)
    live = {"now": 0, "peak": 0}

    async def handler(http_request: httpx.Request) -> httpx.Response:
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        for _ in range(3):
            await asyncio.sleep(0)
        live["now"] -= 1
        return httpx.Response(200, json=_completion(_content(_posted_request_id(http_request))))

    provider = OpenAICompatibleProvider(
        base_url=REMOTE_BASE_URL,
        model="test-model",
        api_key="test-secret",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )
    service = _service(_fallback(requests, valid_profile, consumer_state, valid_campaign))

    resolved = await service.evaluate_many(requests, provider)

    assert len(resolved) == 8
    assert live["peak"] == 4
    assert MAX_CONCURRENT_PROVIDER_CALLS == 4


async def test_evaluate_many_is_deterministic_under_a_shuffled_input_order(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Two runs of the same batch differ in exactly one field, and it is a measurement.

    ``latency_ms`` is how long a network call actually took, which specification section
    6.5 already excludes from the reproducibility claim; it is removed here rather than
    ignored, so a SECOND non-reproducible field would fail this test rather than hide
    behind it.
    """
    requests = _requests(cognition_persona, cognition_campaign, 5)
    bodies = {request.request_id: _content(request.request_id) for request in requests}

    def _run(order: Sequence[CognitionRequest]) -> Any:
        def handler(http_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_completion(bodies[_posted_request_id(http_request)]),
            )

        provider = OpenAICompatibleProvider(
            base_url=REMOTE_BASE_URL,
            model="test-model",
            api_key="test-secret",
            timeout_seconds=5.0,
            transport=httpx.MockTransport(handler),
        )
        service = _service(_fallback(requests, valid_profile, consumer_state, valid_campaign))
        return service.evaluate_many(order, provider)

    forward = await _run(requests)
    backward = await _run(list(reversed(requests)))

    forward_records = [resolution.model_dump(mode="json") for resolution in forward.values()]
    backward_records = [resolution.model_dump(mode="json") for resolution in backward.values()]
    assert list(forward) == list(backward)
    for record in (*forward_records, *backward_records):
        assert record["usage"].pop("latency_ms") >= 0
    assert forward_records == backward_records


async def test_a_duplicate_request_is_refused(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, _ = _scripted([])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )
    with pytest.raises(DuplicateCognitionRequest):
        await service.evaluate_many([cognition_request, cognition_request], provider)


# --- the failure contract -------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([httpx.ReadTimeout("timed out")] * 3, "timeout"),
        ([httpx.ConnectError("refused")] * 3, "provider-unavailable"),
        ([httpx.Response(429, json={"error": "slow"})] * 3, "rate-limited"),
        ([httpx.Response(500, text="boom")] * 3, "http-error"),
        (["not-json", "still-not-json"], "invalid-response"),
    ],
)
async def test_each_provider_failure_names_its_documented_reason(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
    script: Script,
    reason: str,
) -> None:
    provider, _ = _scripted(script)
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert resolution.source == "fallback"
    assert resolution.fallback_reason == reason
    assert resolution.result.request_id == cognition_request.request_id


async def test_a_cache_miss_from_a_replay_provider_names_its_own_reason(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
    provider_metadata: ProviderMetadata,
) -> None:
    replay = ReplayCognitionProvider(CognitionCache(tmp_path), provider_metadata)
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, replay)

    assert resolution.fallback_reason == "cache-miss"
    with pytest.raises(CacheMiss):
        await replay.evaluate(cognition_request)


async def test_an_unclassified_cognition_error_still_resolves(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Specification section 12: a provider failure may never abort a run."""
    unwired = RuleCognitionProvider({})
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, unwired)

    assert resolution.fallback_reason == "provider-unavailable"
    with pytest.raises(UnknownCognitionRequest):
        await unwired.evaluate(cognition_request)


def test_every_documented_fallback_reason_is_produced_somewhere() -> None:
    """Item by item, so a reason nobody can reach is visible rather than decorative."""
    from typing import get_args

    from adlife.core.ports.cognition import FallbackReason

    covered = {
        "budget-exhausted",
        "provider-unavailable",
        "timeout",
        "rate-limited",
        "http-error",
        "invalid-response",
        "cache-miss",
    }
    assert covered == set(get_args(FallbackReason))


async def test_a_programming_error_is_not_swallowed_as_a_provider_failure(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A defect in a caller must surface, not hide behind a plausible rule answer."""

    class _Broken:
        async def evaluate(self, request: CognitionRequest) -> CognitionResult:
            raise TypeError("a defect in the caller, not a provider failure")

        async def answer(self, request: CognitionRequest) -> Any:
            raise TypeError("a defect in the caller, not a provider failure")

    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )
    with pytest.raises(TypeError):
        await service.evaluate_one(cognition_request, _Broken())


async def test_a_fallback_that_cannot_answer_raises_a_cognition_error(
    cognition_request: CognitionRequest,
) -> None:
    """The terminal fallback is the last resort; an unwired one must say so loudly."""
    provider, _ = _scripted(["not-json", "still-not-json"])
    service = _service(RuleCognitionProvider({}))
    with pytest.raises(CognitionError):
        await service.evaluate_one(cognition_request, provider)


async def test_the_resolution_mirrors_its_usage_record(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, _ = _scripted([_content(cognition_request.request_id)])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert isinstance(resolution, CognitionResolution)
    assert resolution.source == resolution.usage.provider_kind
    assert resolution.fallback_reason == resolution.usage.fallback_reason
    assert resolution.request_id == cognition_request.request_id


async def test_a_resolution_must_answer_its_own_request(
    cognition_request: CognitionRequest,
) -> None:
    result = CognitionResult.model_validate_json(_content("run-demo:event-00000009"))
    with pytest.raises(ValueError, match="own request"):
        CognitionResolution(
            request_id=cognition_request.request_id,
            result=result,
            usage=ProviderUsage(
                provider_kind="remote-llm",
                model_id="test-model",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
            ),
            attempts=1,
        )


async def test_a_repair_that_raises_any_cognition_error_still_resolves(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """The repair seam must absorb the whole error hierarchy, not only call failures."""

    class _RepairRefuses:
        async def evaluate(self, request: CognitionRequest) -> CognitionResult:
            raise InvalidProviderResponse("nothing usable", raw_response="not-json")

        async def answer(self, request: CognitionRequest) -> Any:
            raise InvalidProviderResponse("nothing usable", raw_response="not-json")

        async def call(self, request: CognitionRequest) -> ProviderCall:
            raise InvalidProviderResponse("nothing usable", raw_response="not-json")

        async def repair_call(
            self,
            request: CognitionRequest,
            *,
            invalid_content: str,
            error: str,
        ) -> ProviderCall:
            raise CacheMiss("0" * 64)

    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )
    resolution = await service.evaluate_one(cognition_request, _RepairRefuses())
    assert resolution.fallback_reason == "cache-miss"
    assert resolution.attempts == 2


async def test_a_batch_refuses_anything_that_is_not_a_request(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    provider, _ = _scripted([])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )
    with pytest.raises(TypeError):
        await service.evaluate_many(["run-demo:event-00000007"], provider)


@pytest.mark.parametrize(
    "overrides",
    [
        {"budget": "six"},
        {"oracle": 7},
        {"cache": "./cache"},
        {"fallback": object()},
    ],
)
def test_a_misconfigured_service_is_refused_at_construction(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
    overrides: Mapping[str, object],
) -> None:
    arguments: dict[str, object] = {
        "fallback": _fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        "budget": CognitionBudget(per_agent=6, total=180),
        "oracle": RandomOracle(root_seed=ROOT_SEED),
    }
    arguments.update(overrides)
    with pytest.raises(TypeError):
        CognitionService(**arguments)  # type: ignore[arg-type]


async def test_the_service_opens_no_socket_for_an_offline_provider(
    monkeypatch: pytest.MonkeyPatch,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the cognition service reached the network in a test")

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)

    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )
    resolution = await service.evaluate_one(cognition_request, MockCognitionProvider())
    assert resolution.source == "mock"


# --- fix round 1: the configured retry bound is honoured ------------------------------


def test_the_service_retry_bound_matches_the_configuration_field() -> None:
    """One bound, two modules. Drift here makes a configured retry count unrepresentable.

    ``ProviderSettings.retries`` is the only place a user states how many retries a run
    may make, so the service's own maximum is read against it rather than restated.
    """
    field = ProviderSettings.model_fields["retries"]
    upper = next(constraint.le for constraint in field.metadata if hasattr(constraint, "le"))
    lower = next(constraint.ge for constraint in field.metadata if hasattr(constraint, "ge"))
    assert (lower, upper) == (0, MAX_PROVIDER_RETRIES)
    assert ProviderSettings().retries == MAX_PROVIDER_RETRIES
    assert MAX_PROVIDER_ATTEMPTS == MAX_PROVIDER_RETRIES + 1 == 3


@pytest.mark.parametrize(("retries", "expected_calls"), [(0, 1), (1, 2), (2, 3)])
async def test_a_configured_retry_count_bounds_the_attempts(
    retries: int,
    expected_calls: int,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A configured ``retries: 0`` must make ONE attempt, not three.

    Without a seam on the service this value has no consumer at all, and a later wiring
    task cannot honour it without changing this class's published shape.
    """
    provider, calls = _scripted([httpx.ReadTimeout("timed out") for _ in range(5)])
    sleeps = _Sleeps()
    service = CognitionService(
        fallback=_fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
        budget=CognitionBudget(per_agent=6, total=180),
        oracle=RandomOracle(root_seed=ROOT_SEED),
        sleep=sleeps,
        retries=retries,
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert len(calls) == expected_calls
    assert len(sleeps.delays) == expected_calls - 1
    assert resolution.attempts == expected_calls
    assert resolution.fallback_reason == "timeout"


def test_the_service_defaults_to_the_specified_retry_maximum(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Omitting the argument keeps specification section 7's documented behaviour."""
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )
    assert service.retries == MAX_PROVIDER_RETRIES


@pytest.mark.parametrize("retries", [-1, 3, 99])
def test_a_retry_count_outside_the_configured_range_is_refused(
    retries: int,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Specification section 7's maximum is a bound, so the seam cannot raise it."""
    with pytest.raises(ValueError):
        CognitionService(
            fallback=_fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
            budget=CognitionBudget(per_agent=6, total=180),
            oracle=RandomOracle(root_seed=ROOT_SEED),
            retries=retries,
        )


@pytest.mark.parametrize("retries", [True, 1.0, "2", None])
def test_a_retry_count_that_is_not_an_integer_is_refused(
    retries: object,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    with pytest.raises(TypeError):
        CognitionService(
            fallback=_fallback([cognition_request], valid_profile, consumer_state, valid_campaign),
            budget=CognitionBudget(per_agent=6, total=180),
            oracle=RandomOracle(root_seed=ROOT_SEED),
            retries=retries,  # type: ignore[arg-type]
        )


# --- fix round 2: the run survives an untranslatable body -----------------------------


def _deeply_nested_json() -> str:
    """A JSON array nested far past any interpreter's recursion limit."""
    depth = sys.getrecursionlimit() * 20
    return "[" * depth + "]" * depth


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_deeply_nested_json(), id="deeply-nested-body"),
        pytest.param(
            _content("placeholder").replace('"valence": 0.3', '"valence": ' + "9" * 400),
            id="integer-literal-no-float-can-hold",
        ),
    ],
)
async def test_a_body_python_cannot_decode_falls_back_instead_of_aborting_the_run(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
    body: str,
) -> None:
    """Specification section 12's binding sentence, at the level it binds.

    A body nested past the recursion limit made CPython's JSON scanner raise
    ``RecursionError``; a bare integer literal larger than ``sys.float_info.max`` made
    ``math.isfinite`` raise ``OverflowError``. Neither is a ``ValueError`` and neither is
    a :class:`~adlife.core.ports.cognition.CognitionError`, so both escaped the provider,
    escaped :meth:`CognitionService._dispatch` - which catches ``ProviderCallError`` and
    ``CognitionError`` only - and aborted the whole run through the task group.
    """
    body = body.replace("placeholder", cognition_request.request_id)
    provider, _ = _scripted([body, body])
    service = _service(
        _fallback([cognition_request], valid_profile, consumer_state, valid_campaign)
    )

    resolution = await service.evaluate_one(cognition_request, provider)

    assert resolution.source == "fallback"
    assert resolution.fallback_reason == "invalid-response"
    assert resolution.result.request_id == cognition_request.request_id
