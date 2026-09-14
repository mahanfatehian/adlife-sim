"""The shared cognition provider contract, exercised against every offline provider."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.cognition.mock import (
    MOCK_FIXTURE_COUNT,
    MOCK_MODEL_ID,
    MockCognitionProvider,
)
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.adapters.cognition.rules import (
    ANNOYED_VALENCE,
    CURIOUS_RELEVANCE,
    POSITIVE_VALENCE,
    RULE_MODEL_ID,
    SKEPTICAL_CREDIBILITY,
    MismatchedRuleResponse,
    RuleCognitionInputs,
    RuleCognitionProvider,
    UnknownCognitionRequest,
    _fit,
    rule_cognition_result,
)
from adlife.core.domain.campaign import Campaign, CampaignId
from adlife.core.domain.events import EventSource
from adlife.core.domain.person import (
    REDACTION_PLACEHOLDER,
    PersonProfile,
    contains_secret_or_email_text,
    contains_sensitive_text,
)
from adlife.core.domain.state import Activity, Channel, ConsumerState
from adlife.core.ports.cognition import (
    MAX_GROUNDED_REASONS,
    MAX_RELEVANT_MEMORIES,
    MAX_REQUEST_JSON_BYTES,
    MAX_SAFETY_FLAGS,
    CognitionAnswer,
    CognitionError,
    CognitionProvider,
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    Emotion,
    ProviderKind,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
    redact_provider_body,
)
from adlife.core.simulation.decision import RuleResponse, evaluate_rule_response
from adlife.core.simulation.engine import canonical_sha256, stable_event_id


def _rule_inputs(
    profile: PersonProfile,
    state: ConsumerState,
    campaign: Campaign,
) -> RuleCognitionInputs:
    return RuleCognitionInputs(
        profile=profile,
        state=state,
        campaign=campaign,
        placement=campaign.placements[0],
    )


def _rule_provider(
    cognition_request: CognitionRequest,
    profile: PersonProfile,
    state: ConsumerState,
    campaign: Campaign,
) -> RuleCognitionProvider:
    return RuleCognitionProvider(
        {cognition_request.request_id: _rule_inputs(profile, state, campaign)}
    )


def _recorded_cache(
    directory: Path,
    request: CognitionRequest,
    metadata: ProviderMetadata,
    result: CognitionResult,
) -> CognitionCache:
    cache = CognitionCache(directory)
    key = CognitionCache.make_key(request, metadata)
    cache.put(
        key,
        CognitionRecord(
            key=key,
            request=request,
            provider_metadata=metadata,
            raw_response=None,
            result=result,
            usage=ProviderUsage(
                provider_kind=metadata.kind,
                model_id=metadata.model_id,
                prompt_tokens=118,
                completion_tokens=64,
                latency_ms=0,
            ),
        ),
    )
    return cache


@pytest.mark.asyncio
async def assert_provider_contract(
    provider_factory: Callable[[], CognitionProvider],
    cognition_request: CognitionRequest,
) -> None:
    provider = provider_factory()
    result = await provider.evaluate(cognition_request)
    assert -1 <= result.valence <= 1
    assert 0 <= result.relevance <= 1
    assert 0 <= result.credibility <= 1
    assert -0.10 <= result.rule_modifier <= 0.10
    assert len(result.grounded_reasons) <= 3
    assert -0.25 <= result.sentiment_delta <= 0.25
    assert 0 <= result.recall_delta <= 0.30
    assert 0 <= result.share_probability <= 1
    assert result.emotion in set(get_args(Emotion))
    assert result.request_id == cognition_request.request_id
    repeated = await provider_factory().evaluate(cognition_request)
    assert repeated.model_dump_json() == result.model_dump_json()
    answer = await provider_factory().answer(cognition_request)
    assert answer.result.model_dump_json() == result.model_dump_json()
    assert answer.usage.model_id


async def test_the_rules_provider_satisfies_the_shared_contract(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    await assert_provider_contract(
        lambda: _rule_provider(cognition_request, valid_profile, consumer_state, valid_campaign),
        cognition_request,
    )


async def test_the_mock_provider_satisfies_the_shared_contract(
    cognition_request: CognitionRequest,
) -> None:
    await assert_provider_contract(MockCognitionProvider, cognition_request)


async def test_the_replay_provider_satisfies_the_shared_contract(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    recorded = await MockCognitionProvider().evaluate(cognition_request)
    cache = _recorded_cache(tmp_path, cognition_request, provider_metadata, recorded)
    await assert_provider_contract(
        lambda: ReplayCognitionProvider(cache, provider_metadata),
        cognition_request,
    )


async def test_every_offline_provider_implements_the_published_protocol(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    recorded = await MockCognitionProvider().evaluate(cognition_request)
    cache = _recorded_cache(tmp_path, cognition_request, provider_metadata, recorded)
    providers: tuple[CognitionProvider, ...] = (
        _rule_provider(cognition_request, valid_profile, consumer_state, valid_campaign),
        MockCognitionProvider(),
        ReplayCognitionProvider(cache, provider_metadata),
    )
    assert all(isinstance(provider, CognitionProvider) for provider in providers)


async def test_every_offline_provider_reports_its_provenance_through_the_port(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A service coded against the port must be able to stamp the right event source.

    Specification section 13 requires every event to carry its source and model
    identifier, and the brief's emphasis is a replay that reproduces a recorded run
    including its failure and fallback paths. If provenance were reachable only through a
    method one adapter happens to publish off-protocol, a service taking a
    ``CognitionProvider`` would stamp the wrong source on a replayed request.
    """
    recorded = await MockCognitionProvider().evaluate(cognition_request)
    cache = _recorded_cache(tmp_path, cognition_request, provider_metadata, recorded)
    expected: tuple[tuple[CognitionProvider, str, str], ...] = (
        (
            _rule_provider(cognition_request, valid_profile, consumer_state, valid_campaign),
            "rule",
            RULE_MODEL_ID,
        ),
        (MockCognitionProvider(), "mock", MOCK_MODEL_ID),
        (ReplayCognitionProvider(cache, provider_metadata), "mock", provider_metadata.model_id),
    )
    for provider, kind, model_id in expected:
        answer = await provider.answer(cognition_request)
        assert isinstance(answer, CognitionAnswer)
        assert answer.result.request_id == cognition_request.request_id
        assert answer.usage.provider_kind == kind
        assert answer.usage.model_id == model_id
        assert (
            answer.result.model_dump_json()
            == (await provider.evaluate(cognition_request)).model_dump_json()
        )


async def test_an_offline_provider_reports_no_spend_and_no_elapsed_time(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """No model was called and no clock may be read, so the honest numbers are zeros."""
    providers: tuple[CognitionProvider, ...] = (
        _rule_provider(cognition_request, valid_profile, consumer_state, valid_campaign),
        MockCognitionProvider(),
    )
    for provider in providers:
        usage = (await provider.answer(cognition_request)).usage
        assert (usage.prompt_tokens, usage.completion_tokens, usage.latency_ms) == (0, 0, 0)
        assert usage.cache_hit is False
        assert usage.fallback_reason is None


def test_every_provider_kind_names_a_persistable_event_source() -> None:
    sources = {member.value for member in EventSource}
    assert set(get_args(ProviderKind)) <= sources


async def test_providers_evaluate_without_a_wall_clock_or_a_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """Offline providers must read no wall clock and open no outbound connection.

    ``time.monotonic`` and the ``socket.socket`` type itself are deliberately left alone:
    the asyncio event loop owns both. Name resolution and outbound connection are the
    entry points any real network client must pass through.
    """
    recorded = await MockCognitionProvider().evaluate(cognition_request)
    cache = _recorded_cache(tmp_path, cognition_request, provider_metadata, recorded)

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("an offline cognition provider reached for the outside world")

    monkeypatch.setattr(time, "time", _forbidden)
    monkeypatch.setattr(time, "perf_counter", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)

    providers: tuple[CognitionProvider, ...] = (
        _rule_provider(cognition_request, valid_profile, consumer_state, valid_campaign),
        MockCognitionProvider(),
        ReplayCognitionProvider(cache, provider_metadata),
    )
    for provider in providers:
        assert (await provider.evaluate(cognition_request)).request_id == (
            cognition_request.request_id
        )


async def test_the_mock_provider_ignores_credentials_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    cognition_request: CognitionRequest,
) -> None:
    monkeypatch.setenv("ADLIFE_API_KEY", "first-secret-value")
    first = await MockCognitionProvider().evaluate(cognition_request)
    monkeypatch.setenv("ADLIFE_API_KEY", "second-secret-value")
    second = await MockCognitionProvider().evaluate(cognition_request)
    assert first.model_dump_json() == second.model_dump_json()


# --- rules provider -------------------------------------------------------------------


def _response(**overrides: float | str) -> RuleResponse:
    base = RuleResponse(
        campaign_id="campaign-phone",
        sentiment_delta=0.08,
        recall_delta=0.14,
        purchase_intention=0.42,
        share_probability=0.03,
        valence=0.40,
        relevance=0.50,
        credibility=0.70,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


async def test_rule_cognition_derives_its_answer_from_evaluate_rule_response(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """The provider computes the rule response itself; it does not restate an injected one.

    Every number below comes out of :func:`evaluate_rule_response` with the inputs the
    provider was wired with, so the single formula in
    :mod:`adlife.core.simulation.decision` is what answers a rule-mode request.
    """
    expected = evaluate_rule_response(
        valid_profile,
        consumer_state,
        valid_campaign,
        valid_campaign.placements[0],
    )
    provider = _rule_provider(cognition_request, valid_profile, consumer_state, valid_campaign)
    result = await provider.evaluate(cognition_request)
    assert provider.rule_response_for(cognition_request) == expected
    assert result.valence == expected.valence
    assert result.relevance == expected.relevance
    assert result.credibility == expected.credibility
    assert result.sentiment_delta == expected.sentiment_delta
    assert result.recall_delta == expected.recall_delta
    assert result.share_probability == expected.share_probability
    assert f"{expected.purchase_intention:.3f}" in result.purchase_reason


async def test_rule_cognition_reacts_to_the_state_it_was_wired_with(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A different consumer state is a different rule response, not the same one restated."""
    other_state = consumer_state.model_copy(update={"brand_sentiment": -0.8})
    first = await _rule_provider(
        cognition_request, valid_profile, consumer_state, valid_campaign
    ).evaluate(cognition_request)
    second = await _rule_provider(
        cognition_request, valid_profile, other_state, valid_campaign
    ).evaluate(cognition_request)
    assert first.purchase_reason != second.purchase_reason


async def test_rule_cognition_never_modifies_its_own_baseline(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    result = await _rule_provider(
        cognition_request, valid_profile, consumer_state, valid_campaign
    ).evaluate(cognition_request)
    assert result.rule_modifier == 0.0


async def test_rule_cognition_reports_three_grounded_reasons(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    result = await _rule_provider(
        cognition_request, valid_profile, consumer_state, valid_campaign
    ).evaluate(cognition_request)
    assert len(result.grounded_reasons) == 3


@pytest.mark.parametrize(
    ("valence", "relevance", "credibility", "expected"),
    [
        (-0.60, 0.90, 0.90, "annoyed"),
        (-0.35, 0.90, 0.90, "annoyed"),
        (-0.34, 0.90, 0.90, "skeptical"),
        (-0.10, 0.90, 0.90, "skeptical"),
        (0.60, 0.90, 0.30, "skeptical"),
        (0.60, 0.90, 0.34, "skeptical"),
        (0.60, 0.90, 0.35, "positive"),
        (0.40, 0.10, 0.90, "positive"),
        (0.35, 0.10, 0.90, "positive"),
        (0.34, 0.10, 0.90, "neutral"),
        (0.20, 0.50, 0.90, "curious"),
        (0.20, 0.49, 0.90, "neutral"),
        (0.0, 0.10, 0.90, "neutral"),
    ],
)
def test_rule_cognition_labels_each_documented_emotion_band(
    cognition_request: CognitionRequest,
    valence: float,
    relevance: float,
    credibility: float,
    expected: str,
) -> None:
    """Each threshold is bracketed by the pair of rows either side of it.

    The rows pin every band constant to a 0.01 window: ``ANNOYED_VALENCE`` by the
    -0.35/-0.34 pair, ``SKEPTICAL_CREDIBILITY`` by the 0.34/0.35 credibility pair,
    ``POSITIVE_VALENCE`` by the 0.34/0.35 valence pair and ``CURIOUS_RELEVANCE`` by the
    0.49/0.50 pair. Moving any constant, not only deleting its clause, turns a row red.
    """
    response = _response(valence=valence, relevance=relevance, credibility=credibility)
    assert rule_cognition_result(cognition_request, response).emotion == expected


def test_the_emotion_band_thresholds_are_the_published_constants() -> None:
    """The band table above is written against these exact values."""
    assert (ANNOYED_VALENCE, POSITIVE_VALENCE, SKEPTICAL_CREDIBILITY, CURIOUS_RELEVANCE) == (
        -0.35,
        0.35,
        0.35,
        0.50,
    )


async def test_rule_cognition_refuses_a_request_it_has_no_rule_inputs_for(
    cognition_request: CognitionRequest,
) -> None:
    provider = RuleCognitionProvider({})
    with pytest.raises(UnknownCognitionRequest, match=cognition_request.request_id):
        await provider.evaluate(cognition_request)


async def test_rule_cognition_refuses_rule_inputs_for_another_campaign(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    other = valid_campaign.model_copy(update={"campaign_id": "campaign-billboard"})
    provider = _rule_provider(cognition_request, valid_profile, consumer_state, other)
    with pytest.raises(MismatchedRuleResponse, match="campaign"):
        await provider.evaluate(cognition_request)


async def test_rule_cognition_refuses_rule_inputs_for_another_agent(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    other_profile = valid_profile.model_copy(update={"agent_id": "person-002"})
    other_state = consumer_state.model_copy(update={"agent_id": "person-002"})
    provider = _rule_provider(cognition_request, other_profile, other_state, valid_campaign)
    with pytest.raises(MismatchedRuleResponse, match="agent"):
        await provider.evaluate(cognition_request)


def test_rule_cognition_is_built_for_a_whole_tick_and_refuses_an_uncovered_request(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """The terminal fallback proves its coverage BEFORE the tick, not during it.

    Specification section 12 makes this provider the last step before a run aborts, so
    "the caller must remember to register every request" cannot be left as prose. Building
    the provider for a tick's requests is an enforced seam: an unwired request is refused
    at construction, before any provider call exists to fail.
    """
    covered = cognition_request
    uncovered = cognition_request.model_copy(
        update={"request_id": stable_event_id(cognition_request.run_id, 11)}
    )
    inputs = {covered.request_id: _rule_inputs(valid_profile, consumer_state, valid_campaign)}
    with pytest.raises(UnknownCognitionRequest, match=uncovered.request_id):
        RuleCognitionProvider.for_requests((covered, uncovered), inputs)


async def test_a_rule_provider_built_for_a_tick_answers_every_request_of_that_tick(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    requests = tuple(
        cognition_request.model_copy(
            update={"request_id": stable_event_id(cognition_request.run_id, sequence)}
        )
        for sequence in (7, 8, 9)
    )
    inputs = {
        request.request_id: _rule_inputs(valid_profile, consumer_state, valid_campaign)
        for request in requests
    }
    provider = RuleCognitionProvider.for_requests(requests, inputs)
    for request in requests:
        assert (await provider.evaluate(request)).request_id == request.request_id


def test_rule_cognition_refuses_inputs_it_could_never_evaluate(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """A wiring error surfaces at construction rather than as a mid-run abort."""
    other_agent = consumer_state.model_copy(update={"agent_id": "person-002"})
    with pytest.raises(ValueError, match="same agent"):
        RuleCognitionProvider(
            {
                "run-demo:event-00000007": RuleCognitionInputs(
                    profile=valid_profile,
                    state=other_agent,
                    campaign=valid_campaign,
                    placement=valid_campaign.placements[0],
                )
            }
        )


# --- mock provider --------------------------------------------------------------------


async def test_the_mock_provider_uses_every_one_of_its_fixtures(
    cognition_request: CognitionRequest,
) -> None:
    provider = MockCognitionProvider()
    emotions = set()
    for index in range(200):
        request = cognition_request.model_copy(update={"agent_id": f"person-{index % 200:03d}"})
        emotions.add((await provider.evaluate(request)).emotion)
    assert emotions == set(get_args(Emotion))
    assert len(get_args(Emotion)) == MOCK_FIXTURE_COUNT


async def test_the_mock_answer_follows_the_agent_it_answers_for(
    cognition_request: CognitionRequest,
) -> None:
    """Exercised through ``evaluate``, not only through ``fixture_index``."""
    provider = MockCognitionProvider()
    emotions = set()
    for index in range(20):
        request = cognition_request.model_copy(update={"agent_id": f"person-{index:03d}"})
        emotions.add((await provider.evaluate(request)).emotion)
    assert len(emotions) > 1


async def test_the_mock_answer_follows_the_exposure_it_answers_at(
    cognition_request: CognitionRequest,
) -> None:
    provider = MockCognitionProvider()
    emotions = set()
    for exposure_count in range(1, 15):
        request = cognition_request.model_copy(update={"exposure_count": exposure_count})
        emotions.add((await provider.evaluate(request)).emotion)
    assert len(emotions) > 1


async def test_the_mock_seed_selects_its_own_fixture_sequence(
    cognition_request: CognitionRequest,
) -> None:
    emotions = {
        (await MockCognitionProvider(seed=seed).evaluate(cognition_request)).emotion
        for seed in range(20)
    }
    assert len(emotions) > 1


# --- request and result schema bounds -------------------------------------------------


@pytest.mark.parametrize(
    "path_text",
    [
        "/home/analyst/projects/adlife",
        "C:\\Users\\analyst\\adlife",
        "\\\\fileserver\\share",
        "file:///etc/passwd",
    ],
)
def test_a_request_refuses_a_filesystem_path(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    path_text: str,
) -> None:
    campaign = dict(cognition_campaign) | {"message": f"See {path_text} for details."}
    with pytest.raises(ValidationError, match="filesystem path"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_request_keeps_an_ordinary_url_in_campaign_text(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    campaign = dict(cognition_campaign) | {
        "call_to_action": "Visit https://fictional.example/store/offers today.",
    }
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["call_to_action"] == (
        "Visit https://fictional.example/store/offers today."
    )


def test_a_request_refuses_more_than_three_relevant_memories(
    cognition_request: CognitionRequest,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(
            update={"relevant_memories": ("a", "b", "c", "d")},
        )


def test_a_request_refuses_an_oversized_prompt_payload(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    campaign = dict(cognition_campaign) | {"message": "x" * 9000}
    with pytest.raises(ValidationError, match="minimized"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_request_requires_a_campaign_identifier(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    campaign = {key: value for key, value in cognition_campaign.items() if key != "campaign_id"}
    with pytest.raises(ValidationError, match="campaign_id"):
        cognition_request.model_copy(update={"campaign": campaign})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rule_modifier", 0.11),
        ("rule_modifier", -0.11),
        ("sentiment_delta", 0.26),
        ("sentiment_delta", -0.26),
        ("recall_delta", 0.31),
        ("recall_delta", -0.01),
        ("valence", 1.01),
        ("relevance", 1.01),
        ("credibility", -0.01),
        ("share_probability", 1.01),
    ],
)
def test_a_result_refuses_a_value_outside_its_documented_band(
    cognition_request: CognitionRequest,
    field: str,
    value: float,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError):
        result.model_copy(update={field: value})


def test_a_result_refuses_more_than_three_grounded_reasons(
    cognition_request: CognitionRequest,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError):
        result.model_copy(update={"grounded_reasons": ("a", "b", "c", "d")})


def test_a_result_refuses_a_non_finite_number(
    cognition_request: CognitionRequest,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError):
        result.model_copy(update={"valence": float("nan")})


def test_a_usage_record_requires_a_reason_exactly_for_a_fallback() -> None:
    with pytest.raises(ValidationError, match="fallback"):
        ProviderUsage(
            provider_kind="fallback",
            model_id="rule-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )
    with pytest.raises(ValidationError, match="fallback"):
        ProviderUsage(
            provider_kind="mock",
            model_id="mock-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            fallback_reason="timeout",
        )


@pytest.mark.parametrize("kind", ["local-llm", "remote-llm"])
def test_provider_metadata_requires_a_base_url_for_a_network_provider(kind: str) -> None:
    from adlife.core.ports.cognition import SamplingSettings

    with pytest.raises(ValidationError, match="base_url"):
        ProviderMetadata(
            kind=kind,  # type: ignore[arg-type]
            model_id="local-model",
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


@pytest.mark.parametrize("kind", ["rule", "mock", "replay", "fallback"])
def test_provider_metadata_refuses_a_base_url_for_an_offline_provider(kind: str) -> None:
    from adlife.core.ports.cognition import SamplingSettings

    with pytest.raises(ValidationError, match="base_url"):
        ProviderMetadata(
            kind=kind,  # type: ignore[arg-type]
            base_url="https://example.invalid/v1",
            model_id="mock-v1",
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


def test_rule_cognition_keeps_the_longest_permitted_identifiers_intact(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """The longest campaign id and channel a request may carry must still fit the result."""
    long_campaign_id = "c" + "a" * 79
    long_channel = max(get_args(Channel), key=len)
    request = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign) | {"campaign_id": long_campaign_id},
            "channel": long_channel,
        }
    )
    result = rule_cognition_result(request, _response(campaign_id=long_campaign_id))
    assert long_campaign_id in result.interpretation
    assert long_channel in result.interpretation
    assert long_campaign_id in result.memory_summary
    assert long_channel in result.memory_summary


def test_composed_text_is_trimmed_rather_than_rejected_beyond_its_bound() -> None:
    assert _fit("abcdef", 4) == "abcd"
    assert _fit("abc", 4) == "abc"


def test_a_cognition_record_refuses_a_result_from_another_request(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="bind its result"):
        CognitionRecord(
            key="0" * 64,
            request=cognition_request,
            provider_metadata=provider_metadata,
            raw_response=None,
            result=result.model_copy(update={"request_id": "run-demo:event-00000099"}),
            usage=ProviderUsage(
                provider_kind="mock",
                model_id="mock-v1",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
            ),
        )


def test_the_rules_provider_refuses_a_value_that_is_not_a_set_of_rule_inputs() -> None:
    with pytest.raises(TypeError, match="RuleCognitionInputs"):
        RuleCognitionProvider({"run-demo:event-00000007": "not rule inputs"})  # type: ignore[dict-item]


@pytest.mark.parametrize("seed", [-1, 0.5, True])
def test_the_mock_provider_refuses_a_seed_that_is_not_a_nonnegative_integer(
    seed: object,
) -> None:
    with pytest.raises(ValueError, match="seed"):
        MockCognitionProvider(seed=seed)  # type: ignore[arg-type]


def test_the_replay_provider_refuses_something_that_is_not_a_cache(
    provider_metadata: ProviderMetadata,
) -> None:
    with pytest.raises(TypeError, match="CognitionCache"):
        ReplayCognitionProvider(object(), provider_metadata)  # type: ignore[arg-type]


# --- prompt boundary: sensitive identifiers and secrets (spec section 19) --------------


_SENSITIVE_TEXTS: tuple[tuple[str, str], ...] = (
    ("email", "reach me at analyst@example.invalid"),
    ("phone", "+1 415 555 0134"),
    ("national-id", "1234567890"),
    ("api-key", "api_key: sk-live-abcdef1234"),
    ("password", "password: hunter2-abcdef"),
    ("access-token", "access_token = abcdef123456"),
)
_SENSITIVE_IDS = [label for label, _ in _SENSITIVE_TEXTS]


@pytest.mark.parametrize(("label", "text"), _SENSITIVE_TEXTS, ids=_SENSITIVE_IDS)
def test_a_request_refuses_a_sensitive_identifier_in_the_persona(
    cognition_request: CognitionRequest,
    label: str,
    text: str,
) -> None:
    persona = dict(cognition_request.fictional_persona) | {"note": text}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"fictional_persona": persona})


@pytest.mark.parametrize(("label", "text"), _SENSITIVE_TEXTS, ids=_SENSITIVE_IDS)
def test_a_request_refuses_a_sensitive_identifier_in_a_memory(
    cognition_request: CognitionRequest,
    label: str,
    text: str,
) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"relevant_memories": (text,)})


def test_the_prompt_boundary_reuses_the_domain_sensitive_text_rule(
    cognition_request: CognitionRequest,
) -> None:
    """Text the persona contract rejects is exactly the text a prompt rejects."""
    for _, text in _SENSITIVE_TEXTS:
        assert contains_sensitive_text(text)
        with pytest.raises(ValidationError, match="sensitive"):
            cognition_request.model_copy(update={"relevant_memories": (text,)})


# --- prompt boundary: ordinary filesystem path shapes (spec section 19) ---------------


_PATH_SHAPES: tuple[str, ...] = (
    "/tmp",
    "/home",
    "/var",
    "/etc/passwd",
    "~/secrets.env",
    "~\\Documents",
    "./config",
    "..\\..\\windows\\system32",
    "config/secrets.env",
    "folder\\file",
    "C:\\Users\\analyst\\adlife",
    "C:/Users/analyst",
    "\\\\fileserver\\share",
    "\\\\fileserver\\",
    "file:///etc/passwd",
)
"""Every pattern in the guard is reached by at least one shape no other pattern catches."""


@pytest.mark.parametrize("path_text", _PATH_SHAPES)
def test_a_request_refuses_an_ordinary_filesystem_path_shape(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    path_text: str,
) -> None:
    campaign = dict(cognition_campaign) | {"message": f"See {path_text} for details."}
    with pytest.raises(ValidationError, match="filesystem path"):
        cognition_request.model_copy(update={"campaign": campaign})


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "Support is available 24/7 in every fictional city.",
        "He/she/they all noticed the fictional handset.",
        "Just $5/month after the fictional trial ends.",
        "Visit https://fictional.example/store/index.html today.",
        "Read more at https://fictional.example/store/offers.",
    ],
)
def test_a_request_keeps_ordinary_prose_that_merely_contains_a_slash(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    ordinary_text: str,
) -> None:
    """The path guard must not reject ordinary marketing copy or an ordinary URL."""
    campaign = dict(cognition_campaign) | {"message": ordinary_text}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["message"] == ordinary_text


# --- prompt boundary: every screened field, not only campaign -------------------------


_SCREENED_FIELDS: tuple[tuple[str, Callable[[str], dict[str, object]]], ...] = (
    (
        "fictional_persona",
        lambda text: {"fictional_persona": {"occupation": "office-worker", "note": text}},
    ),
    ("relevant_memories", lambda text: {"relevant_memories": (text,)}),
)
_SCREENED_IDS = [name for name, _ in _SCREENED_FIELDS]


@pytest.mark.parametrize(("field", "build"), _SCREENED_FIELDS, ids=_SCREENED_IDS)
def test_a_request_refuses_a_filesystem_path_in_every_free_text_field(
    cognition_request: CognitionRequest,
    field: str,
    build: Callable[[str], dict[str, object]],
) -> None:
    with pytest.raises(ValidationError, match="filesystem path"):
        cognition_request.model_copy(update=build("/home/analyst/projects/adlife"))


@pytest.mark.parametrize(("field", "build"), _SCREENED_FIELDS, ids=_SCREENED_IDS)
def test_a_request_refuses_a_secret_in_every_free_text_field(
    cognition_request: CognitionRequest,
    field: str,
    build: Callable[[str], dict[str, object]],
) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update=build("api_key: sk-live-abcdef1234"))


# --- the request reuses the domain channel and activity contracts ---------------------


@pytest.mark.parametrize("channel", list(get_args(Channel)))
def test_a_request_accepts_every_domain_channel(
    cognition_request: CognitionRequest,
    channel: str,
) -> None:
    assert cognition_request.model_copy(update={"channel": channel}).channel == channel


@pytest.mark.parametrize("activity", list(get_args(Activity)))
def test_a_request_accepts_every_domain_activity(
    cognition_request: CognitionRequest,
    activity: str,
) -> None:
    assert cognition_request.model_copy(update={"activity": activity}).activity == activity


@pytest.mark.parametrize(
    "channel",
    ["Mobile-Feed", "telepathy", "mobile-feed\x00", "/home/analyst/channels", ""],
)
def test_a_request_refuses_a_channel_outside_the_domain_contract(
    cognition_request: CognitionRequest,
    channel: str,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"channel": channel})


@pytest.mark.parametrize(
    "activity",
    ["skydiving", "Commute", "commute ", "/home/analyst/activities", ""],
)
def test_a_request_refuses_an_activity_outside_the_domain_contract(
    cognition_request: CognitionRequest,
    activity: str,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"activity": activity})


# --- the request identifier is a deterministic function of the run --------------------


@pytest.mark.parametrize(
    "request_id",
    [
        "6f1c0f2e-0b7a-4d2a-9f1e-2a7c0b3d4e5f",
        "run-demo:probe-1",
        "run-demo:event-7",
        "RUN-DEMO:event-00000007",
        "run-demo-event-00000007",
        "run-demo:event-000000071",
    ],
)
def test_a_request_refuses_a_request_id_that_is_not_a_stable_event_id(
    cognition_request: CognitionRequest,
    request_id: str,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"request_id": request_id})


def test_a_request_refuses_a_request_id_minted_for_another_run(
    cognition_request: CognitionRequest,
) -> None:
    with pytest.raises(ValidationError, match="request_id"):
        cognition_request.model_copy(update={"request_id": "run-other:event-00000001"})


def test_a_request_id_is_exactly_the_stable_event_id_of_its_own_run(
    cognition_request: CognitionRequest,
) -> None:
    """The cache key, the replay hit and the mock fixture all rest on this shape."""
    assert cognition_request.request_id == stable_event_id(cognition_request.run_id, 7)
    rebound = cognition_request.model_copy(
        update={"run_id": "run-other", "request_id": stable_event_id("run-other", 7)}
    )
    assert rebound.request_id == "run-other:event-00000007"


# --- the campaign identifier obeys the domain rule exactly ----------------------------


@pytest.mark.parametrize(
    "campaign_id",
    [
        "campaign-phone\n",
        "Campaign-Phone",
        "-leading-dash",
        "campaign phone",
        "c" * 81,
    ],
)
def test_a_request_refuses_a_campaign_id_the_domain_contract_rejects(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    campaign_id: str,
) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(CampaignId)
    with pytest.raises(ValidationError):
        adapter.validate_python(campaign_id)
    campaign = dict(cognition_campaign) | {"campaign_id": campaign_id}
    with pytest.raises(ValidationError, match="campaign_id"):
        cognition_request.model_copy(update={"campaign": campaign})


# --- a result is screened on the way in, not at the next request ----------------------


_RESULT_TEXT_FIELDS: tuple[tuple[str, Callable[[str], dict[str, object]]], ...] = (
    ("interpretation", lambda text: {"interpretation": text}),
    ("purchase_reason", lambda text: {"purchase_reason": text}),
    ("discussion_hook", lambda text: {"discussion_hook": text}),
    ("memory_summary", lambda text: {"memory_summary": text}),
    ("grounded_reasons", lambda text: {"grounded_reasons": (text,)}),
    ("safety_flags", lambda text: {"safety_flags": (text,)}),
)
_RESULT_TEXT_IDS = [name for name, _ in _RESULT_TEXT_FIELDS]


@pytest.mark.parametrize(("field", "build"), _RESULT_TEXT_FIELDS, ids=_RESULT_TEXT_IDS)
def test_a_result_refuses_a_filesystem_path_in_every_text_field(
    cognition_request: CognitionRequest,
    field: str,
    build: Callable[[str], dict[str, object]],
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="filesystem path"):
        result.model_copy(update=build("/home/analyst/notes.txt"))


@pytest.mark.parametrize("path_text", ["/home/analyst/notes.txt", "~/notes.txt", "/tmp"])
def test_a_result_never_accepts_memory_text_a_later_request_would_reject(
    cognition_request: CognitionRequest,
    path_text: str,
) -> None:
    """Feeding a memory summary back into a prompt can never fail late."""
    summary = f"Saw the advertisement while reading {path_text} on the commute."
    with pytest.raises(ValidationError, match="filesystem path"):
        cognition_request.model_copy(update={"relevant_memories": (summary,)})
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="filesystem path"):
        result.model_copy(update={"memory_summary": summary})


def test_a_rule_result_memory_summary_is_always_a_legal_prompt_memory(
    cognition_request: CognitionRequest,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    fed_back = cognition_request.model_copy(update={"relevant_memories": (result.memory_summary,)})
    assert fed_back.relevant_memories == (result.memory_summary,)


@pytest.mark.parametrize(("field", "build"), _RESULT_TEXT_FIELDS, ids=_RESULT_TEXT_IDS)
def test_a_result_refuses_a_sensitive_identifier_in_every_text_field(
    cognition_request: CognitionRequest,
    field: str,
    build: Callable[[str], dict[str, object]],
) -> None:
    """A provider may not smuggle a secret back in, least of all into a future prompt."""
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update=build("api_key: sk-live-abcdef1234"))


@pytest.mark.parametrize(("label", "text"), _SENSITIVE_TEXTS, ids=_SENSITIVE_IDS)
def test_a_result_never_accepts_sensitive_memory_text_a_later_request_would_reject(
    cognition_request: CognitionRequest,
    label: str,
    text: str,
) -> None:
    summary = f"The advertisement mentioned {text} while commuting."
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"relevant_memories": (summary,)})
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update={"memory_summary": summary})


# --- prompt boundary: untrusted campaign copy is screened, not mangled ----------------


_CAMPAIGN_SENSITIVE_TEXTS: tuple[tuple[str, str], ...] = (
    ("email", "reach me at analyst@example.invalid"),
    ("api-key", "api_key: sk-live-abcdef1234"),
    ("password", "password: hunter2-abcdef"),
    ("access-token", "access_token = abcdef123456"),
)
"""What untrusted campaign copy may never carry into a provider prompt."""

_PERSONA_ONLY_SENSITIVE_TEXTS: tuple[tuple[str, str], ...] = (
    ("phone", "+1 415 555 0134"),
    ("national-id", "1234567890"),
)
"""Digit-run shapes spec section 19 rejects in persona fields, and only there."""

_ORDINARY_NUMERIC_COPY: tuple[str, ...] = (
    "Ships in 7 - 10 - 14 business days.",
    "Save 20 - 30 - 40 percent on the fictional handset.",
    "Only 1 299 000 IDR for a calmer routine.",
    "Call it 12-345-6789 value.",
    "Serial 1234567890 units sold.",
    "The fictional offer runs 2024 - 2026 in every fictional city.",
)
"""Marketing copy the domain ``Campaign`` accepts; a prompt must accept it too."""


@pytest.mark.parametrize(
    ("label", "text"),
    _CAMPAIGN_SENSITIVE_TEXTS,
    ids=[label for label, _ in _CAMPAIGN_SENSITIVE_TEXTS],
)
def test_a_request_refuses_a_credential_or_a_contact_in_untrusted_campaign_text(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    label: str,
    text: str,
) -> None:
    """Campaign files may be untrusted, so their text is still screened for these."""
    campaign = dict(cognition_campaign) | {"message": f"Special offer. {text}"}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


@pytest.mark.parametrize("numeric_text", _ORDINARY_NUMERIC_COPY)
def test_a_request_keeps_ordinary_numeric_campaign_copy(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    numeric_text: str,
) -> None:
    """A price, a delivery window or a date range is not a personal identifier."""
    campaign = dict(cognition_campaign) | {"message": numeric_text}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["message"] == numeric_text


@pytest.mark.parametrize("numeric_text", _ORDINARY_NUMERIC_COPY)
def test_a_campaign_the_domain_accepts_can_always_be_turned_into_a_prompt(
    cognition_request: CognitionRequest,
    valid_campaign: Campaign,
    numeric_text: str,
) -> None:
    """The port may not refuse campaign data the binding domain contract admits."""
    campaign = valid_campaign.model_copy(update={"message": numeric_text})
    assert campaign.message == numeric_text
    updated = cognition_request.model_copy(
        update={
            "campaign": {
                "call_to_action": campaign.call_to_action,
                "campaign_id": campaign.campaign_id,
                "message": campaign.message,
                "product_category": campaign.product_category,
                "product_name": campaign.product_name,
            }
        }
    )
    assert updated.campaign["message"] == numeric_text


@pytest.mark.parametrize(
    ("label", "text"),
    _PERSONA_ONLY_SENSITIVE_TEXTS,
    ids=[label for label, _ in _PERSONA_ONLY_SENSITIVE_TEXTS],
)
def test_the_persona_screen_stays_stricter_than_the_untrusted_campaign_screen(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    label: str,
    text: str,
) -> None:
    """Spec section 19 scopes digit-run rejection to persona fields, not to ad copy."""
    persona = dict(cognition_request.fictional_persona) | {"note": text}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"fictional_persona": persona})

    campaign = dict(cognition_campaign) | {"message": f"Special offer. {text}"}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["message"] == f"Special offer. {text}"


# --- an answer that paraphrases ad copy is screened the way that ad copy is ------------


_PARAPHRASING_RESULT_FIELDS: tuple[tuple[str, Callable[[str], dict[str, object]]], ...] = (
    ("interpretation", lambda text: {"interpretation": text}),
    ("purchase_reason", lambda text: {"purchase_reason": text}),
    ("discussion_hook", lambda text: {"discussion_hook": text}),
    ("grounded_reasons", lambda text: {"grounded_reasons": (text,)}),
    ("safety_flags", lambda text: {"safety_flags": (text,)}),
)
"""Answer fields whose content is a paraphrase of the campaign copy the prompt carried."""

_PARAPHRASING_IDS = [name for name, _ in _PARAPHRASING_RESULT_FIELDS]


@pytest.mark.parametrize(("field", "build"), _PARAPHRASING_RESULT_FIELDS, ids=_PARAPHRASING_IDS)
@pytest.mark.parametrize("numeric_text", _ORDINARY_NUMERIC_COPY)
def test_a_result_may_restate_the_ordinary_numeric_copy_it_was_shown(
    cognition_request: CognitionRequest,
    field: str,
    build: Callable[[str], dict[str, object]],
    numeric_text: str,
) -> None:
    """A provider that quotes the price or delivery window it was given is not leaking.

    The persona screen's phone and national-identifier clauses are digit-run heuristics;
    the port already refuses to apply them to the campaign copy a prompt carries, because
    a price, a discount range or a delivery window is ordinary advertising text. Applying
    them to a provider's paraphrase of that same text would invalidate the answer,
    consume specification section 12's single repair attempt and drop to the rule
    fallback systematically rather than exceptionally.
    """
    result = rule_cognition_result(cognition_request, _response())
    updated = result.model_copy(update=build(numeric_text))
    assert numeric_text in str(build(numeric_text)[field])
    assert updated.request_id == result.request_id


@pytest.mark.parametrize(("field", "build"), _PARAPHRASING_RESULT_FIELDS, ids=_PARAPHRASING_IDS)
@pytest.mark.parametrize(
    ("label", "text"),
    _CAMPAIGN_SENSITIVE_TEXTS,
    ids=[label for label, _ in _CAMPAIGN_SENSITIVE_TEXTS],
)
def test_a_result_still_refuses_a_credential_or_a_contact_in_every_paraphrasing_field(
    cognition_request: CognitionRequest,
    field: str,
    build: Callable[[str], dict[str, object]],
    label: str,
    text: str,
) -> None:
    """Relaxing the digit-run heuristics relaxes nothing about credentials or contacts."""
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update=build(text))


@pytest.mark.parametrize(
    ("label", "text"),
    _PERSONA_ONLY_SENSITIVE_TEXTS,
    ids=[label for label, _ in _PERSONA_ONLY_SENSITIVE_TEXTS],
)
def test_memory_summary_and_relevant_memories_keep_the_strict_screen_as_one_pair(
    cognition_request: CognitionRequest,
    label: str,
    text: str,
) -> None:
    """``memory_summary`` is written straight back into a later ``relevant_memories``.

    The two fields therefore have to carry the SAME screen, or a summary accepted at the
    provider boundary would raise at the next request build, where no fallback is left.
    They keep the strict persona rule together, because a memory is agent state rather
    than third-party copy; the five paraphrasing fields above do not feed a prompt and
    carry the narrow rule instead.
    """
    summary = f"The advertisement mentioned {text} while commuting."
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"relevant_memories": (summary,)})
    result = rule_cognition_result(cognition_request, _response())
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update={"memory_summary": summary})


# --- the terminal fallback provider stays inside the cognition error hierarchy ---------


async def test_rule_cognition_refuses_a_mismatched_campaign_inside_the_error_hierarchy(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    """The rule provider is the last resort, so its refusals must be catchable as one.

    Plan Task 10 makes ``RuleCognitionProvider`` the final step after retry and repair. A
    service that catches :class:`CognitionError` in order to honour specification section
    12's "provider failures must never abort a run" has to catch this too, exactly as it
    catches the sibling :class:`UnknownCognitionRequest` and replay's ``CorruptCacheRecord``.
    """
    other = valid_campaign.model_copy(update={"campaign_id": "campaign-billboard"})
    provider = _rule_provider(cognition_request, valid_profile, consumer_state, other)
    with pytest.raises(CognitionError, match="campaign"):
        await provider.evaluate(cognition_request)


def test_every_rule_provider_refusal_is_a_cognition_error() -> None:
    assert issubclass(UnknownCognitionRequest, CognitionError)
    assert issubclass(MismatchedRuleResponse, CognitionError)


# --- a domain frozenset projection is the CALLER's obligation to sort ------------------


def _persona_with(profile: PersonProfile, interests: list[str]) -> dict[str, object]:
    return {
        "age_band": "25-34",
        "household_type": profile.household_type,
        "interests": interests,
        "occupation": profile.occupation,
    }


def _campaign_with(campaign: Campaign, target_interests: list[str]) -> dict[str, object]:
    return {
        "call_to_action": campaign.call_to_action,
        "campaign_id": campaign.campaign_id,
        "message": campaign.message,
        "product_category": campaign.product_category,
        "product_name": campaign.product_name,
        "target_interests": target_interests,
    }


def test_a_sorted_frozenset_projection_builds_the_same_request_in_every_process(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
) -> None:
    """``sorted(profile.interests)`` is the whole determinism obligation on a caller.

    ``PersonProfile.interests`` and ``Campaign.target_interests`` are frozensets, and
    ``freeze_json_mapping`` refuses a real frozenset, so the only projection a caller can
    write is a list. ``list(frozenset)`` is ordered by this process's string hash seed;
    ``sorted(...)`` is not, and the port preserves what it is handed.
    """
    interests = sorted({*valid_profile.interests, "cycling", "cooking"})
    targets = sorted({*valid_campaign.target_interests, "travel", "audio"})

    def build() -> CognitionRequest:
        return cognition_request.model_copy(
            update={
                "fictional_persona": _persona_with(
                    valid_profile, sorted({*valid_profile.interests, "cycling", "cooking"})
                ),
                "campaign": _campaign_with(
                    valid_campaign, sorted({*valid_campaign.target_interests, "travel", "audio"})
                ),
            }
        )

    first, second = build(), build()
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.fictional_persona["interests"] == tuple(interests)
    assert first.campaign["target_interests"] == tuple(targets)


def test_an_unsorted_frozenset_projection_is_a_different_request(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
) -> None:
    """The port no longer hides an unsorted projection, so the hazard stays visible.

    Silently reordering the array made two different payloads one request at the cost of
    rewriting whatever the caller actually wrote. This is the recorded consequence of not
    doing that: an unsorted projection is a different question, and Tasks 10, 12 and 16
    must sort before they build the payload.
    """
    interests = sorted({*valid_profile.interests, "cycling", "cooking"})
    forward = cognition_request.model_copy(
        update={"fictional_persona": _persona_with(valid_profile, interests)}
    )
    backward = cognition_request.model_copy(
        update={"fictional_persona": _persona_with(valid_profile, list(reversed(interests)))}
    )
    assert forward != backward
    assert forward.fictional_persona["interests"] == tuple(interests)
    assert backward.fictional_persona["interests"] == tuple(reversed(interests))


def test_array_order_is_preserved_at_every_nesting_depth(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    colors = ["white", "green", "amber"]
    request = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"creative": {"colors": list(colors)}}}
    )
    nested = request.campaign["creative"]
    assert isinstance(nested, Mapping)
    assert nested["colors"] == tuple(colors)


def test_array_order_is_preserved_for_objects_inside_an_array(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Object keys inside an array element are sorted; the elements keep their places."""
    first = {"visibility": 0.8, "channel": "mobile-feed"}
    second = {"visibility": 0.4, "channel": "highway-billboard"}
    request = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"placements": [first, second]}}
    )
    placements = request.campaign["placements"]
    assert isinstance(placements, tuple)
    assert [dict(element) for element in placements] == [  # type: ignore[call-overload]
        {"channel": "mobile-feed", "visibility": 0.8},
        {"channel": "highway-billboard", "visibility": 0.4},
    ]


# --- prompt object KEYS are sorted; prompt array ELEMENTS are left exactly as authored -


def test_a_request_serializes_its_prompt_objects_byte_identically_whatever_the_key_order(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Two ``==``-equal requests must write the same bytes into the cache file.

    The cache key digests a key-sorted canonical JSON, but the stored record is
    ``model_dump_json()``. Without sorted keys the same key addresses different bytes
    depending on which order the prompt builder happened to insert in.
    """
    forward = cognition_request.model_copy(update={"campaign": dict(cognition_campaign)})
    backward = cognition_request.model_copy(
        update={"campaign": dict(reversed(list(cognition_campaign.items())))}
    )
    assert forward == backward
    assert forward.model_dump_json() == backward.model_dump_json()


def test_prompt_object_keys_are_sorted_at_every_nesting_depth(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    nested = {"visual_style": "minimal-product", "description": "A calm still life."}
    forward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"creative": dict(nested)}}
    )
    backward = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign)
            | {"creative": dict(reversed(list(nested.items())))}
        }
    )
    assert forward == backward
    assert forward.model_dump_json() == backward.model_dump_json()
    assert list(forward.campaign) == sorted(forward.campaign)
    creative = forward.campaign["creative"]
    assert isinstance(creative, Mapping)
    assert list(creative) == sorted(creative)


def test_prompt_object_keys_are_sorted_inside_an_array_element(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    element = {"visibility": 0.8, "channel": "mobile-feed"}
    forward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"placements": [dict(element)]}}
    )
    backward = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign)
            | {"placements": [dict(reversed(list(element.items())))]}
        }
    )
    assert forward == backward
    assert forward.model_dump_json() == backward.model_dump_json()
    placements = forward.campaign["placements"]
    assert isinstance(placements, tuple)
    first = placements[0]
    assert isinstance(first, Mapping)
    assert list(first) == ["channel", "visibility"]


_AUTHORED_ARRAYS: tuple[tuple[str, list[object]], ...] = (
    ("numeric-exposure-history", [2, 10, 3, 1, 21]),
    ("ranked-placements", ["highway-billboard", "mobile-feed"]),
    ("ordered-price-points", [1299.0, 999.0, 1499.0]),
)


@pytest.mark.parametrize(
    ("label", "authored"),
    _AUTHORED_ARRAYS,
    ids=[label for label, _ in _AUTHORED_ARRAYS],
)
def test_a_prompt_array_reaches_the_provider_in_the_order_its_caller_authored(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    label: str,
    authored: list[object],
) -> None:
    """Array order is prompt content, and a provider is shown the text as written.

    Sorting array elements by their canonical JSON text turns ``[2, 10, 3, 1, 21]`` into
    ``[10, 1, 21, 2, 3]``, which is arithmetically nonsense in the text the model reads.
    Determinism is a property of the digest, not a licence to rewrite the payload.
    """
    request = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"series": list(authored)}}
    )
    assert request.campaign["series"] == tuple(authored)
    assert json.loads(request.model_dump_json())["campaign"]["series"] == authored


def test_a_prompt_array_in_a_different_order_is_a_different_request(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Callers projecting a domain frozenset must sort; the port no longer does it for them."""
    forward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"series": [1, 2, 3]}}
    )
    backward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"series": [3, 2, 1]}}
    )
    assert forward != backward
    assert forward.campaign["series"] == (1, 2, 3)
    assert backward.campaign["series"] == (3, 2, 1)


# --- the mock fixture is the documented digest of request_id and the canonical request -


_Variation = Callable[[CognitionRequest, int], dict[str, object]]

_CANONICAL_REQUEST_TERMS: tuple[tuple[str, _Variation], ...] = (
    ("request_id", lambda request, step: {"request_id": stable_event_id(request.run_id, step)}),
    (
        "run_id",
        lambda request, step: {
            "run_id": f"run-arm-{step}",
            "request_id": stable_event_id(f"run-arm-{step}", 7),
        },
    ),
    ("mood", lambda request, step: {"mood": round(-0.9 + step * 0.09, 2)}),
    ("activity", lambda request, step: {"activity": list(get_args(Activity))[step % 5]}),
    (
        "relevant_memories",
        lambda request, step: {"relevant_memories": (f"A fictional memory number {step}.",)},
    ),
    ("prompt_version", lambda request, step: {"prompt_version": f"cognition-v{step}"}),
    ("creative_sha256", lambda request, step: {"creative_sha256": f"{step:064x}"}),
    ("agent_id", lambda request, step: {"agent_id": f"person-{step:03d}"}),
    ("exposure_count", lambda request, step: {"exposure_count": 1 + step % 14}),
    ("simulated_minute", lambda request, step: {"simulated_minute": step * 15}),
)
_CANONICAL_REQUEST_IDS = [name for name, _ in _CANONICAL_REQUEST_TERMS]


@pytest.mark.parametrize(
    ("term", "vary"),
    _CANONICAL_REQUEST_TERMS,
    ids=_CANONICAL_REQUEST_IDS,
)
def test_the_mock_fixture_follows_every_term_of_the_canonical_request(
    cognition_request: CognitionRequest,
    term: str,
    vary: _Variation,
) -> None:
    """The brief's rule is the whole canonical request, not a chosen subset of it.

    Selecting on a six-term treatment tuple left the mock answer insensitive to mood and
    to the memories the agent carries, which Tasks 12 and 13 will reasonably expect to
    move a mock answer.
    """
    provider = MockCognitionProvider(seed=2)
    indexes = {
        provider.fixture_index(cognition_request.model_copy(update=vary(cognition_request, step)))
        for step in range(20)
    }
    assert len(indexes) > 1


def test_the_mock_fixture_varies_with_the_campaign_it_answers(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    provider = MockCognitionProvider()
    indexes = {
        provider.fixture_index(
            cognition_request.model_copy(
                update={"campaign": dict(cognition_campaign) | {"campaign_id": f"campaign-{step}"}}
            )
        )
        for step in range(20)
    }
    assert len(indexes) > 1


def test_the_mock_fixture_varies_with_the_channel_for_some_agent(
    cognition_request: CognitionRequest,
) -> None:
    """The channel arm is two-valued, so it separates some agents rather than all."""
    provider = MockCognitionProvider()
    separated = [
        agent
        for agent in range(30)
        if len(
            {
                provider.fixture_index(
                    cognition_request.model_copy(
                        update={"agent_id": f"person-{agent:03d}", "channel": channel}
                    )
                )
                for channel in get_args(Channel)
            }
        )
        > 1
    ]
    assert separated


def test_the_mock_fixture_is_the_documented_digest_of_the_whole_request(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Pin the published selection rule: request_id plus the canonical request JSON.

    The digest is rebuilt here from the port's own canonical digest rather than from the
    adapter, so renaming, dropping or adding a term to the selection key turns this red.
    """
    for seed in range(3):
        provider = MockCognitionProvider(seed=seed)
        for step in range(6):
            request = cognition_request.model_copy(
                update={
                    "agent_id": f"person-{step:03d}",
                    "campaign": dict(cognition_campaign) | {"campaign_id": f"campaign-{step}"},
                    "channel": list(get_args(Channel))[step % 2],
                    "exposure_count": 1 + step,
                    "mood": round(-0.5 + step * 0.2, 2),
                    "simulated_minute": 15 * step,
                }
            )
            expected = (
                int(
                    canonical_sha256(
                        {
                            "request_id": request.request_id,
                            "request_sha256": canonical_sha256(request),
                            "seed": seed,
                        }
                    ),
                    16,
                )
                % MOCK_FIXTURE_COUNT
            )
            assert provider.fixture_index(request) == expected


# --- the persisted raw provider body carries no credential ----------------------------


_ECHOED_CREDENTIALS: tuple[tuple[str, str, str], ...] = (
    (
        "api-key-in-an-error-body",
        '{"error":{"message":"Incorrect API key provided: sk-live-SUPERSECRET0123456789",'
        '"code":401}}',
        "sk-live-SUPERSECRET0123456789",
    ),
    (
        "authorization-header",
        '{"request":{"headers":{"Authorization":"Bearer eyJhbGciSECRETTOKEN0123"}}}',
        "eyJhbGciSECRETTOKEN0123",
    ),
    (
        "api-key-assignment",
        '{"detail":"api_key=sk-live-abcdef1234 was rejected"}',
        "sk-live-abcdef1234",
    ),
    ("password-assignment", '{"detail":"password: hunter2-abcdef"}', "hunter2-abcdef"),
    ("access-token-assignment", '{"detail":"access_token = abcdef123456"}', "abcdef123456"),
    ("contact-email", '{"detail":"contact ops@vendor.invalid"}', "ops@vendor.invalid"),
    ("filesystem-path", '{"trace":"/home/analyst/projects/adlife"}', "/home/analyst/projects"),
)
_ECHOED_IDS = [label for label, _, _ in _ECHOED_CREDENTIALS]


def _record_with(
    request: CognitionRequest,
    metadata: ProviderMetadata,
    raw_response: str | None,
) -> CognitionRecord:
    return CognitionRecord(
        key="0" * 64,
        request=request,
        provider_metadata=metadata,
        raw_response=raw_response,
        result=rule_cognition_result(request, _response()),
        usage=ProviderUsage(
            provider_kind="mock",
            model_id="mock-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        ),
    )


@pytest.mark.parametrize(("label", "body", "secret"), _ECHOED_CREDENTIALS, ids=_ECHOED_IDS)
def test_a_record_redacts_a_credential_a_provider_echoed_back(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    label: str,
    body: str,
    secret: str,
) -> None:
    """``raw_response`` is persisted, so it is the largest secret channel on the record.

    A provider that rejects a call echoes the offending header back far more often than
    it echoes anything else, so the record contract - not a docstring, and not a later
    task - has to be what makes that unstorable.
    """
    record = _record_with(cognition_request, provider_metadata, body)
    assert record.raw_response is not None
    assert secret not in record.raw_response
    assert "[redacted]" in record.raw_response


def test_redacting_a_raw_response_is_idempotent(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """A record must survive a cache round trip without drifting."""
    body = '{"error":"Incorrect API key provided: sk-live-SUPERSECRET0123456789"}'
    once = _record_with(cognition_request, provider_metadata, body)
    assert once.raw_response is not None
    twice = _record_with(cognition_request, provider_metadata, once.raw_response)
    assert twice.raw_response == once.raw_response
    assert CognitionRecord.model_validate_json(once.model_dump_json()) == once


def test_a_record_keeps_an_ordinary_provider_body_intact(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """Redaction is a screen, not a rewrite: an ordinary answer body is stored verbatim."""
    body = '{"emotion":"curious","valence":0.32,"safety_flags":[],"tokens":118}'
    record = _record_with(cognition_request, provider_metadata, body)
    assert record.raw_response == body


def test_a_record_without_a_raw_response_stays_empty(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    assert _record_with(cognition_request, provider_metadata, None).raw_response is None


# --- an environment-variable-shaped secret label is a secret label too ----------------


_ENVIRONMENT_SHAPED_CREDENTIALS: tuple[tuple[str, str, str], ...] = (
    (
        "adlife-api-key",
        '{"detail":"ADLIFE_API_KEY: fictional-token-00000000 was rejected"}',
        "fictional-token-00000000",
    ),
    (
        "openai-api-key",
        '{"detail":"OPENAI_API_KEY=fictional-token-11111111 is invalid"}',
        "fictional-token-11111111",
    ),
    (
        "db-password",
        '{"detail":"DB_PASSWORD: fictional-passphrase-2222"}',
        "fictional-passphrase-2222",
    ),
    (
        "prefixed-access-token",
        '{"detail":"X_ACCESS_TOKEN = fictional-token-33333333"}',
        "fictional-token-33333333",
    ),
)
"""Specification section 6.4 names ``ADLIFE_API_KEY`` itself, so this is the live shape.

Every value here is deliberately NOT vendor-key shaped, so ``_VENDOR_KEY_PATTERN`` cannot
mask a gap in the secret-label rule; the companion test below proves that.
"""

_ENVIRONMENT_SHAPED_IDS = [label for label, _, _ in _ENVIRONMENT_SHAPED_CREDENTIALS]


@pytest.mark.parametrize(
    ("label", "body", "secret"),
    _ENVIRONMENT_SHAPED_CREDENTIALS,
    ids=_ENVIRONMENT_SHAPED_IDS,
)
def test_redaction_covers_an_environment_variable_shaped_secret_label(
    label: str,
    body: str,
    secret: str,
) -> None:
    """A credential label preceded by an underscore is still a credential label."""
    redacted = redact_provider_body(body)
    assert secret not in redacted
    assert REDACTION_PLACEHOLDER in redacted


@pytest.mark.parametrize(
    ("label", "body", "secret"),
    _ENVIRONMENT_SHAPED_CREDENTIALS,
    ids=_ENVIRONMENT_SHAPED_IDS,
)
def test_no_environment_shaped_value_is_caught_by_the_vendor_key_shape(
    label: str,
    body: str,
    secret: str,
) -> None:
    """Without its label each value survives, so the label rule is what must catch it."""
    assert redact_provider_body(secret) == secret


@pytest.mark.parametrize(
    ("label", "body", "secret"),
    _ENVIRONMENT_SHAPED_CREDENTIALS,
    ids=_ENVIRONMENT_SHAPED_IDS,
)
def test_a_record_redacts_an_environment_variable_shaped_credential(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    label: str,
    body: str,
    secret: str,
) -> None:
    record = _record_with(cognition_request, provider_metadata, body)
    assert record.raw_response is not None
    assert secret not in record.raw_response


@pytest.mark.parametrize(
    ("label", "body", "secret"),
    _ENVIRONMENT_SHAPED_CREDENTIALS,
    ids=_ENVIRONMENT_SHAPED_IDS,
)
def test_the_persona_screen_detects_an_environment_variable_shaped_secret(
    cognition_request: CognitionRequest,
    label: str,
    body: str,
    secret: str,
) -> None:
    """Detection and redaction share one label rule, so both must see the same label."""
    assert contains_sensitive_text(body)
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"relevant_memories": (body,)})


def test_a_raw_body_that_still_trips_the_secret_screen_is_dropped_entirely() -> None:
    """Redaction is pattern matching, so the record fails closed on whatever is left.

    A label with no value after it is detected by the repository secret screen and not
    matched by the value-removing pattern, so the body is replaced wholesale rather than
    stored while the repository's own detector still calls it a secret.
    """
    body = '{"error":"provide a password:"}'
    assert contains_secret_or_email_text(body)
    assert redact_provider_body(body) == REDACTION_PLACEHOLDER


def test_a_stored_raw_body_never_trips_the_repository_secret_screen(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """The claim the cache module makes about stored records, enforced rather than stated."""
    bodies = [body for _, body, _ in _ECHOED_CREDENTIALS]
    bodies += [body for _, body, _ in _ENVIRONMENT_SHAPED_CREDENTIALS]
    bodies.append('{"error":"provide a password:"}')
    for body in bodies:
        record = _record_with(cognition_request, provider_metadata, body)
        assert record.raw_response is not None
        assert not contains_secret_or_email_text(record.raw_response)


# --- every documented bound and default is pinned in both directions ------------------


def test_the_published_prompt_bounds_are_the_documented_constants() -> None:
    """These numbers are the contract Tasks 10, 12, 14, 16 and 19 are written against."""
    assert MAX_RELEVANT_MEMORIES == 3
    assert MAX_GROUNDED_REASONS == 3
    assert MAX_SAFETY_FLAGS == 8
    assert MAX_REQUEST_JSON_BYTES == 8192


def test_a_usage_record_defaults_to_not_a_cache_hit() -> None:
    """The reporting default must fail closed.

    An adapter that omits ``cache_hit`` made a call and paid for it. Defaulting to a hit
    would let Task 14 and Task 16 report a run as free that was not.
    """
    usage = ProviderUsage(
        provider_kind="mock",
        model_id="mock-v1",
        prompt_tokens=118,
        completion_tokens=64,
        latency_ms=12,
    )
    assert usage.cache_hit is False
    assert usage.model_dump()["cache_hit"] is False


@pytest.mark.parametrize("mood", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_a_request_accepts_the_whole_documented_mood_band(
    cognition_request: CognitionRequest,
    mood: float,
) -> None:
    assert cognition_request.model_copy(update={"mood": mood}).mood == mood


@pytest.mark.parametrize("mood", [-1.01, 1.01, -2.0, 2.0, -100.0, 100.0])
def test_a_request_refuses_a_mood_outside_the_documented_band(
    cognition_request: CognitionRequest,
    mood: float,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"mood": mood})


def test_a_request_accepts_exactly_the_documented_number_of_memories(
    cognition_request: CognitionRequest,
) -> None:
    memories = tuple(
        f"A fictional memory number {index}." for index in range(MAX_RELEVANT_MEMORIES)
    )
    updated = cognition_request.model_copy(update={"relevant_memories": memories})
    assert updated.relevant_memories == memories
    literal = ("First fiction.", "Second fiction.", "Third fiction.")
    assert (
        cognition_request.model_copy(update={"relevant_memories": literal}).relevant_memories
        == literal
    )


def test_a_request_refuses_one_memory_more_than_the_documented_maximum(
    cognition_request: CognitionRequest,
) -> None:
    memories = tuple(
        f"A fictional memory number {index}." for index in range(MAX_RELEVANT_MEMORIES + 1)
    )
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"relevant_memories": memories})


def test_a_result_accepts_exactly_the_documented_number_of_safety_flags(
    cognition_request: CognitionRequest,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    flags = tuple(f"fictional-flag-{index}" for index in range(MAX_SAFETY_FLAGS))
    assert result.model_copy(update={"safety_flags": flags}).safety_flags == flags
    literal = tuple(f"fictional-flag-{index}" for index in range(8))
    assert result.model_copy(update={"safety_flags": literal}).safety_flags == literal


def test_a_result_refuses_one_safety_flag_more_than_the_documented_maximum(
    cognition_request: CognitionRequest,
) -> None:
    result = rule_cognition_result(cognition_request, _response())
    flags = tuple(f"fictional-flag-{index}" for index in range(MAX_SAFETY_FLAGS + 1))
    with pytest.raises(ValidationError):
        result.model_copy(update={"safety_flags": flags})
    literal = tuple(f"fictional-flag-{index}" for index in range(9))
    with pytest.raises(ValidationError):
        result.model_copy(update={"safety_flags": literal})


@pytest.mark.parametrize("exposure_count", [1, 7, 14])
def test_a_request_accepts_the_whole_documented_exposure_band(
    cognition_request: CognitionRequest,
    exposure_count: int,
) -> None:
    """Fourteen is the largest ``frequency_cap`` the domain placement contract allows."""
    updated = cognition_request.model_copy(update={"exposure_count": exposure_count})
    assert updated.exposure_count == exposure_count


@pytest.mark.parametrize("exposure_count", [-1, 0, 15, 999])
def test_a_request_refuses_an_exposure_count_outside_the_documented_band(
    cognition_request: CognitionRequest,
    exposure_count: int,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"exposure_count": exposure_count})


@pytest.mark.parametrize("simulated_minute", [0, 480, 10080])
def test_a_request_accepts_the_whole_documented_simulated_minute_band(
    cognition_request: CognitionRequest,
    simulated_minute: int,
) -> None:
    """Ten thousand and eighty minutes is the seven-day run the specification caps at."""
    updated = cognition_request.model_copy(update={"simulated_minute": simulated_minute})
    assert updated.simulated_minute == simulated_minute


@pytest.mark.parametrize("simulated_minute", [-1, 10081, 10**9])
def test_a_request_refuses_a_simulated_minute_outside_the_documented_band(
    cognition_request: CognitionRequest,
    simulated_minute: int,
) -> None:
    with pytest.raises(ValidationError):
        cognition_request.model_copy(update={"simulated_minute": simulated_minute})


# --- every declared identifier and digest pattern is tripped by a test -----------------


def _error_fields(error: pytest.ExceptionInfo[ValidationError]) -> set[str]:
    """The field each reported validation error was raised against.

    Some of these patterns are also covered by a neighbouring rule, so "a ValidationError
    was raised" is not enough to prove the clause under test did the rejecting. Asserting
    the reported location does prove it: delete the pattern and the field disappears from
    this set even when the construction still fails for another reason.
    """
    return {str(entry["loc"][0]) for entry in error.value.errors() if entry["loc"]}


@pytest.mark.parametrize(
    "run_id",
    ["Run-Demo", "run demo", "-leading-dash", "run_demo", "r" * 41, ""],
)
def test_a_request_refuses_a_run_id_the_identifier_rule_rejects(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
    run_id: str,
) -> None:
    """``run_id`` names the run directory every artifact is filed under."""
    with pytest.raises(ValidationError) as error:
        CognitionRequest(
            request_id=f"{run_id}:event-00000007",
            run_id=run_id,
            simulated_minute=480,
            agent_id="person-001",
            fictional_persona=cognition_persona,
            activity="commute",
            mood=0.2,
            relevant_memories=(),
            campaign=cognition_campaign,
            channel="mobile-feed",
            exposure_count=2,
        )
    assert "run_id" in _error_fields(error)


@pytest.mark.parametrize(
    "agent_id",
    ["person-1", "person-0001", "PERSON-001", "person001", "agent-001", ""],
)
def test_a_request_refuses_an_agent_id_the_identifier_rule_rejects(
    cognition_request: CognitionRequest,
    agent_id: str,
) -> None:
    """``agent_id`` keys the oracle draw, the mock fixture and every agent-scoped metric."""
    with pytest.raises(ValidationError) as error:
        cognition_request.model_copy(update={"agent_id": agent_id})
    assert "agent_id" in _error_fields(error)


@pytest.mark.parametrize(
    "creative_sha256",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "not-a-digest", ""],
)
def test_a_request_refuses_a_creative_digest_that_is_not_a_sha_256(
    cognition_request: CognitionRequest,
    creative_sha256: str,
) -> None:
    """The creative digest is cache-key material, so a near-miss must not be accepted."""
    with pytest.raises(ValidationError) as error:
        cognition_request.model_copy(update={"creative_sha256": creative_sha256})
    assert "creative_sha256" in _error_fields(error)


@pytest.mark.parametrize(
    "prompt_version",
    ["Cognition-V1", "cognition v1", "-cognition-v1", "cognition_v1", "c" * 41, ""],
)
def test_a_request_refuses_a_prompt_version_the_identifier_rule_rejects(
    cognition_request: CognitionRequest,
    prompt_version: str,
) -> None:
    """The prompt version participates in every cache key, so its spelling is contractual."""
    with pytest.raises(ValidationError) as error:
        cognition_request.model_copy(update={"prompt_version": prompt_version})
    assert "prompt_version" in _error_fields(error)


@pytest.mark.parametrize("model_digest", ["a" * 63, "A" * 64, "g" * 64, "not-a-digest", ""])
def test_provider_metadata_refuses_a_model_digest_that_is_not_a_sha_256(
    sampling_settings: SamplingSettings,
    model_digest: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        ProviderMetadata(
            kind="mock",
            model_id="mock-v1",
            model_digest=model_digest,
            sampling=sampling_settings,
            prompt_sha256="b" * 64,
        )
    assert "model_digest" in _error_fields(error)


@pytest.mark.parametrize("prompt_sha256", ["b" * 63, "B" * 64, "z" * 64, "not-a-digest", ""])
def test_provider_metadata_refuses_a_prompt_digest_that_is_not_a_sha_256(
    sampling_settings: SamplingSettings,
    prompt_sha256: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        ProviderMetadata(
            kind="mock",
            model_id="mock-v1",
            sampling=sampling_settings,
            prompt_sha256=prompt_sha256,
        )
    assert "prompt_sha256" in _error_fields(error)


@pytest.mark.parametrize("key", ["0" * 63, "0" * 65, "F" * 64, "z" * 64, "not-a-digest", ""])
def test_a_record_refuses_a_key_that_is_not_a_sha_256(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    key: str,
) -> None:
    """A record is addressed by content, so its own key claim has to be a digest."""
    with pytest.raises(ValidationError) as error:
        CognitionRecord(
            key=key,
            request=cognition_request,
            provider_metadata=provider_metadata,
            raw_response=None,
            result=rule_cognition_result(cognition_request, _response()),
            usage=ProviderUsage(
                provider_kind="mock",
                model_id="mock-v1",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
            ),
        )
    assert "key" in _error_fields(error)


# --- a value-shaped secret is redacted even under an unlabelled field ------------------


_UNLABELLED_SECRET_BODIES: tuple[tuple[str, str, str], ...] = (
    (
        "vendor-key-shape",
        '{"detail":"the credential sk-test-0000000000000000 did not authorize"}',
        "sk-test-0000000000000000",
    ),
    (
        "vendor-key-underscore-shape",
        '{"detail":"rejected pk_test_0000000000000000 at the gateway"}',
        "pk_test_0000000000000000",
    ),
    (
        "jwt-under-an-unlabelled-field",
        '{"credential":"eyJhbGciOiJmaWN0aW9uIn0.eyJzdWIiOiJmaWN0aW9uYWwifQ.'
        '0000000000000000000000000000"}',
        "eyJhbGciOiJmaWN0aW9uIn0.eyJzdWIiOiJmaWN0aW9uYWwifQ.0000000000000000000000000000",
    ),
)
_UNLABELLED_SECRET_IDS = [label for label, _, _ in _UNLABELLED_SECRET_BODIES]


@pytest.mark.parametrize(
    ("label", "body", "secret"),
    _UNLABELLED_SECRET_BODIES,
    ids=_UNLABELLED_SECRET_IDS,
)
def test_a_value_shaped_secret_is_redacted_without_a_label_to_announce_it(
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    label: str,
    body: str,
    secret: str,
) -> None:
    """A provider that quotes a credential does not always name the field it came from.

    None of these bodies carries a secret LABEL, so the label rule cannot fire and the
    fail-closed residual screen cannot fire either - the value shape is the only thing
    that can catch them. Every token here is obviously fake.
    """
    assert not contains_secret_or_email_text(body)
    redacted = redact_provider_body(body)
    assert secret not in redacted
    assert REDACTION_PLACEHOLDER in redacted

    record = _record_with(cognition_request, provider_metadata, body)
    assert record.raw_response is not None
    assert secret not in record.raw_response


def test_an_ordinary_contact_address_is_redacted_in_place_rather_than_dropped() -> None:
    """The email clause removes the address; it does not cost the rest of the body.

    Losing the whole body to the fail-closed screen would still hide the address, so this
    pins the clause that keeps a diagnostic body readable.
    """
    body = '{"detail":"contact ops@vendor.invalid about the quota"}'
    redacted = redact_provider_body(body)
    assert "ops@vendor.invalid" not in redacted
    assert redacted != REDACTION_PLACEHOLDER
    assert "about the quota" in redacted


# --- a sensitive string in the KEY position is screened exactly like a value -----------


@pytest.mark.parametrize(
    ("key_text", "message"),
    [
        ("analyst@example.invalid", "sensitive"),
        ("api_key: sk-live-abcdef1234", "sensitive"),
        ("+1 415 555 0134", "sensitive"),
        ("/home/analyst/notes.txt", "filesystem path"),
        ("C:\\Users\\analyst", "filesystem path"),
    ],
)
def test_a_request_screens_the_persona_object_keys_as_well_as_their_values(
    cognition_request: CognitionRequest,
    key_text: str,
    message: str,
) -> None:
    """A prompt object is JSON a caller composes, so a key is as writable as a value."""
    persona = dict(cognition_request.fictional_persona) | {key_text: "a fictional note"}
    with pytest.raises(ValidationError, match=message):
        cognition_request.model_copy(update={"fictional_persona": persona})


@pytest.mark.parametrize(
    ("key_text", "message"),
    [
        ("analyst@example.invalid", "sensitive"),
        ("api_key: sk-live-abcdef1234", "sensitive"),
        ("/home/analyst/notes.txt", "filesystem path"),
    ],
)
def test_a_request_screens_the_campaign_object_keys_as_well_as_their_values(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    key_text: str,
    message: str,
) -> None:
    campaign = dict(cognition_campaign) | {key_text: "a fictional note"}
    with pytest.raises(ValidationError, match=message):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_request_accepts_an_ordinary_object_key(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """The key screen must not refuse ordinary prompt structure."""
    campaign = dict(cognition_campaign) | {"delivery_window": "7 to 10 days"}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["delivery_window"] == "7 to 10 days"
