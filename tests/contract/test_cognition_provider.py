"""The shared cognition provider contract, exercised against every offline provider."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.cognition.mock import MOCK_FIXTURE_COUNT, MockCognitionProvider
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.adapters.cognition.rules import (
    ANNOYED_VALENCE,
    CURIOUS_RELEVANCE,
    POSITIVE_VALENCE,
    SKEPTICAL_CREDIBILITY,
    MismatchedRuleResponse,
    RuleCognitionProvider,
    UnknownCognitionRequest,
    _fit,
    rule_cognition_result,
)
from adlife.core.domain.campaign import Campaign, CampaignId
from adlife.core.domain.events import EventSource
from adlife.core.domain.person import PersonProfile, contains_sensitive_text
from adlife.core.domain.state import Activity, Channel
from adlife.core.ports.cognition import (
    MAX_GROUNDED_REASONS,
    MAX_RELEVANT_MEMORIES,
    MAX_REQUEST_JSON_BYTES,
    MAX_SAFETY_FLAGS,
    CognitionError,
    CognitionProvider,
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    Emotion,
    ProviderKind,
    ProviderMetadata,
    ProviderUsage,
)
from adlife.core.simulation.decision import RuleResponse
from adlife.core.simulation.engine import canonical_sha256, stable_event_id


def _rule_provider(
    cognition_request: CognitionRequest,
    rule_response: RuleResponse,
) -> RuleCognitionProvider:
    return RuleCognitionProvider({cognition_request.request_id: rule_response})


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


async def test_the_rules_provider_satisfies_the_shared_contract(
    cognition_request: CognitionRequest,
    rule_response: RuleResponse,
) -> None:
    await assert_provider_contract(
        lambda: _rule_provider(cognition_request, rule_response),
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
    rule_response: RuleResponse,
) -> None:
    recorded = await MockCognitionProvider().evaluate(cognition_request)
    cache = _recorded_cache(tmp_path, cognition_request, provider_metadata, recorded)
    providers: tuple[CognitionProvider, ...] = (
        _rule_provider(cognition_request, rule_response),
        MockCognitionProvider(),
        ReplayCognitionProvider(cache, provider_metadata),
    )
    assert all(isinstance(provider, CognitionProvider) for provider in providers)


def test_every_provider_kind_names_a_persistable_event_source() -> None:
    sources = {member.value for member in EventSource}
    assert set(get_args(ProviderKind)) <= sources


async def test_providers_evaluate_without_a_wall_clock_or_a_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
    rule_response: RuleResponse,
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
        _rule_provider(cognition_request, rule_response),
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


async def test_rule_cognition_carries_the_rule_numbers_through_unchanged(
    cognition_request: CognitionRequest,
) -> None:
    response = _response()
    result = await _rule_provider(cognition_request, response).evaluate(cognition_request)
    assert result.valence == 0.40
    assert result.relevance == 0.50
    assert result.credibility == 0.70
    assert result.sentiment_delta == 0.08
    assert result.recall_delta == 0.14
    assert result.share_probability == 0.03


async def test_rule_cognition_never_modifies_its_own_baseline(
    cognition_request: CognitionRequest,
) -> None:
    result = await _rule_provider(cognition_request, _response()).evaluate(cognition_request)
    assert result.rule_modifier == 0.0


async def test_rule_cognition_reports_three_grounded_reasons(
    cognition_request: CognitionRequest,
) -> None:
    result = await _rule_provider(cognition_request, _response()).evaluate(cognition_request)
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


async def test_rule_cognition_refuses_a_request_it_has_no_rule_response_for(
    cognition_request: CognitionRequest,
) -> None:
    provider = RuleCognitionProvider({})
    with pytest.raises(UnknownCognitionRequest, match=cognition_request.request_id):
        await provider.evaluate(cognition_request)


async def test_rule_cognition_refuses_a_rule_response_for_another_campaign(
    cognition_request: CognitionRequest,
) -> None:
    provider = _rule_provider(cognition_request, _response(campaign_id="campaign-billboard"))
    with pytest.raises(MismatchedRuleResponse, match="campaign"):
        await provider.evaluate(cognition_request)


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


def test_the_rules_provider_refuses_a_value_that_is_not_a_rule_response() -> None:
    with pytest.raises(TypeError, match="RuleResponse"):
        RuleCognitionProvider({"run-demo:event-00000007": "not a rule response"})  # type: ignore[dict-item]


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


# --- the terminal fallback provider stays inside the cognition error hierarchy ---------


async def test_rule_cognition_refuses_a_mismatched_campaign_inside_the_error_hierarchy(
    cognition_request: CognitionRequest,
) -> None:
    """The rule provider is the last resort, so its refusals must be catchable as one.

    Plan Task 10 makes ``RuleCognitionProvider`` the final step after retry and repair. A
    service that catches :class:`CognitionError` in order to honour specification section
    12's "provider failures must never abort a run" has to catch this too, exactly as it
    catches the sibling :class:`UnknownCognitionRequest` and replay's ``CorruptCacheRecord``.
    """
    provider = _rule_provider(cognition_request, _response(campaign_id="campaign-billboard"))
    with pytest.raises(CognitionError, match="campaign"):
        await provider.evaluate(cognition_request)


def test_every_rule_provider_refusal_is_a_cognition_error() -> None:
    assert issubclass(UnknownCognitionRequest, CognitionError)
    assert issubclass(MismatchedRuleResponse, CognitionError)


# --- prompt objects are order-canonical, not insertion-ordered ------------------------


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


def test_a_request_canonicalises_array_order_inside_its_prompt_objects(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
) -> None:
    """A projection of a domain ``frozenset`` must not depend on this process's hashing.

    ``PersonProfile.interests`` and ``Campaign.target_interests`` are frozensets, and
    ``freeze_json_mapping`` refuses a real frozenset, so the only projection a caller can
    write is a list. ``list(frozenset)`` is ordered by the interpreter's per-process
    string hash seed, so an unsorted array would give the same scenario a different
    request, a different cache key and a different provider answer in every process.
    """
    interests = sorted({*valid_profile.interests, "cycling", "cooking"})
    targets = sorted({*valid_campaign.target_interests, "travel", "audio"})

    forward = cognition_request.model_copy(
        update={
            "fictional_persona": _persona_with(valid_profile, interests),
            "campaign": _campaign_with(valid_campaign, targets),
        }
    )
    backward = cognition_request.model_copy(
        update={
            "fictional_persona": _persona_with(valid_profile, list(reversed(interests))),
            "campaign": _campaign_with(valid_campaign, list(reversed(targets))),
        }
    )

    assert forward == backward
    assert forward.fictional_persona["interests"] == tuple(interests)
    assert forward.campaign["target_interests"] == tuple(targets)
    assert backward.fictional_persona["interests"] == tuple(interests)
    assert backward.campaign["target_interests"] == tuple(targets)
    assert forward.model_dump_json() == backward.model_dump_json()


async def test_array_order_never_changes_the_mock_answer(
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
) -> None:
    interests = sorted({*valid_profile.interests, "cycling", "cooking"})
    targets = sorted({*valid_campaign.target_interests, "travel", "audio"})
    provider = MockCognitionProvider(seed=5)
    answers = set()
    for ordering in (interests, list(reversed(interests))):
        request = cognition_request.model_copy(
            update={
                "fictional_persona": _persona_with(valid_profile, ordering),
                "campaign": _campaign_with(valid_campaign, list(reversed(targets))),
            }
        )
        answers.add((await provider.evaluate(request)).model_dump_json())
    assert len(answers) == 1


def test_array_order_is_canonicalised_at_every_nesting_depth(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    forward = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign)
            | {"creative": {"colors": ["white", "green", "amber"]}}
        }
    )
    backward = cognition_request.model_copy(
        update={
            "campaign": dict(cognition_campaign)
            | {"creative": {"colors": ["green", "amber", "white"]}}
        }
    )
    assert forward == backward
    nested = forward.campaign["creative"]
    assert isinstance(nested, Mapping)
    assert nested["colors"] == ("amber", "green", "white")


def test_array_order_is_canonicalised_for_objects_inside_an_array(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    first = {"channel": "mobile-feed", "visibility": 0.8}
    second = {"channel": "highway-billboard", "visibility": 0.4}
    forward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"placements": [first, second]}}
    )
    backward = cognition_request.model_copy(
        update={"campaign": dict(cognition_campaign) | {"placements": [second, first]}}
    )
    assert forward == backward
    assert forward.model_dump_json() == backward.model_dump_json()


# --- the mock fixture preserves common random numbers across treatment arms -----------


_Variation = Callable[[CognitionRequest, int], dict[str, object]]

_NON_TREATMENT_VARIATIONS: tuple[tuple[str, _Variation], ...] = (
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
)
_NON_TREATMENT_IDS = [name for name, _ in _NON_TREATMENT_VARIATIONS]


@pytest.mark.parametrize(
    ("term", "vary"),
    _NON_TREATMENT_VARIATIONS,
    ids=_NON_TREATMENT_IDS,
)
def test_the_mock_fixture_ignores_every_term_a_treatment_itself_moves(
    cognition_request: CognitionRequest,
    term: str,
    vary: _Variation,
) -> None:
    """Paired treatment arms must draw the same mock answer for the same question.

    Specification section 18 runs its A/B comparisons in rule or mock mode, so the mock
    has to preserve common random numbers: a treatment that changes how many events came
    before, or that moves the mood it is measuring, must not re-draw every later agent's
    cognition for reasons unrelated to the treatment.
    """
    provider = MockCognitionProvider(seed=2)
    indexes = {
        provider.fixture_index(cognition_request.model_copy(update=vary(cognition_request, step)))
        for step in range(20)
    }
    assert indexes == {provider.fixture_index(cognition_request)}


_TREATMENT_VARIATIONS: tuple[tuple[str, Callable[[int], dict[str, object]]], ...] = (
    ("agent_id", lambda step: {"agent_id": f"person-{step:03d}"}),
    ("exposure_count", lambda step: {"exposure_count": 1 + step % 14}),
    ("simulated_minute", lambda step: {"simulated_minute": step * 15}),
)
_TREATMENT_IDS = [name for name, _ in _TREATMENT_VARIATIONS]


@pytest.mark.parametrize(("term", "vary"), _TREATMENT_VARIATIONS, ids=_TREATMENT_IDS)
def test_the_mock_fixture_varies_with_every_treatment_term(
    cognition_request: CognitionRequest,
    term: str,
    vary: Callable[[int], dict[str, object]],
) -> None:
    provider = MockCognitionProvider()
    indexes = {
        provider.fixture_index(cognition_request.model_copy(update=vary(step)))
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


def test_the_mock_fixture_key_is_exactly_the_documented_treatment_tuple(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Pin the full selection key, so no term can be added, renamed or dropped silently."""
    for seed in range(3):
        provider = MockCognitionProvider(seed=seed)
        for step in range(6):
            request = cognition_request.model_copy(
                update={
                    "agent_id": f"person-{step:03d}",
                    "campaign": dict(cognition_campaign) | {"campaign_id": f"campaign-{step}"},
                    "channel": list(get_args(Channel))[step % 2],
                    "exposure_count": 1 + step,
                    "simulated_minute": 15 * step,
                }
            )
            expected = (
                int(
                    canonical_sha256(
                        {
                            "agent_id": request.agent_id,
                            "campaign_id": request.campaign_id,
                            "channel": request.channel,
                            "exposure_count": request.exposure_count,
                            "seed": seed,
                            "simulated_minute": request.simulated_minute,
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
