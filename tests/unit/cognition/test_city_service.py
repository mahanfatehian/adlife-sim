from __future__ import annotations

import asyncio
import importlib
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from adlife.adapters.cognition import city_service
from adlife.adapters.cognition.city_cache import (
    CityCognitionCache,
    CityCognitionCacheCorruption,
    CityCognitionCachePublicationError,
    CityCognitionCacheRecordV1,
)
from adlife.adapters.cognition.city_openai_compatible import (
    CityProviderAuthenticationError,
    CityProviderCallError,
    CityProviderProtocolError,
    CityProviderRateLimitError,
    CityProviderResponseTooLargeError,
    CityProviderServerError,
    CityProviderUnavailableError,
)
from adlife.adapters.cognition.city_service import (
    RETRY_DELAYS_SECONDS,
    RETRYABLE_PROVIDER_STATUSES,
    CityCognitionCancellation,
    CityCognitionService,
)
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DISCLOSURE,
    CITY_COGNITION_RESERVATION_TOKENS,
    CityCognitionInputV1,
    CityCognitionProviderProfileSnapshotV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
    CityProviderAttemptV1,
    CityProviderRepairContextV1,
    CityProviderUsageV1,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.simulation.city_cognition import build_city_cognition_requests
from tests.unit.city.test_city_cognition import _evidence, _input, _provider_profile


@pytest.mark.parametrize(
    ("name", "reason", "attempts"),
    [
        ("CityProviderAuthenticationError", "authentication-refused", 1),
        ("CityProviderTimeoutError", "timeout-exhausted", 3),
        ("CityProviderRateLimitError", "rate-limit-exhausted", 3),
        ("CityProviderServerError", "server-error-exhausted", 3),
        ("CityProviderUnavailableError", "endpoint-resolution-refused", 3),
        ("CityProviderResponseTooLargeError", "response-too-large", 1),
        ("CityProviderProtocolError", "protocol-incompatible", 1),
    ],
)
async def test_shared_transport_errors_keep_v1_service_retry_and_fallback(name, reason, attempts):
    shared = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)

    async def refuse(request):
        del request
        raise getattr(shared, name)()

    sleeps = _Sleeps()
    provider = _Provider(refuse)
    evaluation = await CityCognitionService(sleep=sleeps).resolve_many(
        cognition_input, requests, provider=provider, cancellation=CityCognitionCancellation()
    )
    assert evaluation.records[0].reason == reason
    assert evaluation.records[0].attempt_count == attempts
    assert len(provider.calls) == attempts
    assert sleeps.delays == ([0.25, 0.75] if attempts == 3 else [])


def _requests(count: int = 1) -> tuple[object, tuple[CityCognitionRequestV1, ...]]:
    compiled, responses, context = _evidence()
    selected = build_city_cognition_requests(context, compiled.mobility, responses)[:count]
    assert len(selected) == count
    return context, selected


def _profile(
    *,
    max_output_tokens: int = 384,
    attempt_timeout_seconds: int = 30,
    transient_retries: int = 2,
    repair_attempts: int = 1,
) -> CityCognitionProviderProfileSnapshotV1:
    facts = _provider_profile().model_dump(mode="python", exclude={"revision_sha256"})
    facts.update(
        max_output_tokens=max_output_tokens,
        attempt_timeout_seconds=attempt_timeout_seconds,
        transient_retries=transient_retries,
        repair_attempts=repair_attempts,
    )
    return CityCognitionProviderProfileSnapshotV1(
        **facts,
        revision_sha256=city_cognition_provider_profile_revision_sha256(facts),
    )


def _provider_input(
    context: object,
    requests: tuple[CityCognitionRequestV1, ...],
    *,
    funded_requests: int | None = None,
    profile: CityCognitionProviderProfileSnapshotV1 | None = None,
) -> CityCognitionInputV1:
    funded = len(requests) if funded_requests is None else funded_requests
    return _input(
        context,
        requests,
        mode="local",
        provider_profile=_provider_profile() if profile is None else profile,
        aggregate_output_token_ceiling=funded * CITY_COGNITION_RESERVATION_TOKENS,
    )


def _result(
    request: CityCognitionRequestV1, *, text: str = "Validated explanation"
) -> CityCognitionResultV1:
    return CityCognitionResultV1(
        schema_version=1,
        request_id=request.request_id,
        interpretation=text,
        decision_factors=("Rule-owned response evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )


def _valid_attempt(
    request: CityCognitionRequestV1,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> CityProviderAttemptV1:
    usage = None
    if prompt_tokens is not None and completion_tokens is not None:
        usage = CityProviderUsageV1(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    return CityProviderAttemptV1(
        status="valid",
        result=_result(request),
        usage=usage,
    )


def _invalid_attempt(reason: str = "invalid-schema") -> CityProviderAttemptV1:
    return CityProviderAttemptV1(
        status="invalid-result",
        repair_context=CityProviderRepairContextV1(
            reason_code=reason,
            safe_excerpt="The answer did not satisfy the frozen result contract.",
        ),
    )


class _Provider:
    def __init__(
        self,
        call: Callable[[CityCognitionRequestV1], Awaitable[CityProviderAttemptV1]],
        *,
        repair: Callable[
            [CityCognitionRequestV1, CityProviderRepairContextV1],
            Awaitable[CityProviderAttemptV1],
        ]
        | None = None,
        on_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._call = call
        self._repair = repair
        self._on_close = on_close
        self.calls: list[str] = []
        self.repair_calls: list[tuple[str, str]] = []
        self.close_calls = 0
        self.secret = bytearray(b"operation-scoped-secret")

    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        self.calls.append(request.request_id)
        return await self._call(request)

    async def repair_call(
        self,
        request: CityCognitionRequestV1,
        *,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1:
        self.repair_calls.append((request.request_id, context.reason_code))
        if self._repair is None:
            raise AssertionError("repair was not expected")
        return await self._repair(request, context)

    async def aclose(self) -> None:
        self.close_calls += 1
        for index in range(len(self.secret)):
            self.secret[index] = 0
        if self._on_close is not None:
            await self._on_close()


class _ProviderWithoutClose:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        self.calls.append(request.request_id)
        return _valid_attempt(request)

    async def repair_call(
        self,
        request: CityCognitionRequestV1,
        *,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1:
        del request, context
        raise AssertionError("repair was not expected")


class _ProviderWithNonAwaitableClose(_ProviderWithoutClose):
    def aclose(self) -> None:
        return None


async def _return_valid(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
    await asyncio.sleep(0)
    return _valid_attempt(request)


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


def _cache(tmp_path: Path) -> CityCognitionCache:
    return CityCognitionCache(prepare_workbench_workspace(tmp_path / "workspace"))


def test_retry_contract_is_exact_and_has_no_jitter() -> None:
    assert frozenset({429, 500, 502, 503, 504}) == RETRYABLE_PROVIDER_STATUSES
    assert RETRY_DELAYS_SECONDS == (0.25, 0.75)


@pytest.mark.parametrize("mode", ["rules", "mock"])
async def test_offline_modes_build_one_ordered_deterministic_record_per_selection(
    mode: str,
) -> None:
    context, requests = _requests(6)
    cognition_input = _input(context, requests, mode=mode)
    cancellation = CityCognitionCancellation()

    evaluation = await CityCognitionService().resolve_many(
        cognition_input,
        requests,
        provider=None,
        cancellation=cancellation,
    )

    assert tuple(record.request_id for record in evaluation.records) == tuple(
        request.request_id for request in requests
    )
    assert {record.source for record in evaluation.records} == {mode}
    assert {record.attempt_count for record in evaluation.records} == {0}


async def test_permuted_requests_are_rejected_instead_of_silently_reordered() -> None:
    context, requests = _requests(2)
    cognition_input = _input(context, requests, mode="rules")

    with pytest.raises(ValueError, match="canonical deterministic order"):
        await CityCognitionService().resolve_many(
            cognition_input,
            tuple(reversed(requests)),
            provider=None,
            cancellation=CityCognitionCancellation(),
        )


async def test_reservations_are_verified_before_any_cache_lookup_or_provider_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    invalid = cognition_input.model_copy(update={"reservations": ()})
    cache = _cache(tmp_path)
    touched = False

    def forbidden_get(self: CityCognitionCache, key: str) -> None:
        nonlocal touched
        del self, key
        touched = True
        raise AssertionError("cache was consulted before reservation validation")

    monkeypatch.setattr(CityCognitionCache, "get", forbidden_get)
    provider = _Provider(_return_valid)

    with pytest.raises(ValueError, match="reservations"):
        await CityCognitionService(cache=cache).resolve_many(
            invalid,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )

    assert touched is False
    assert provider.calls == []
    assert provider.close_calls == 1


async def test_funded_cache_hit_uses_no_attempt_slice_and_never_reallocates_its_reservation(
    tmp_path: Path,
) -> None:
    context, requests = _requests(6)
    cognition_input = _provider_input(context, requests, funded_requests=1)
    cache = _cache(tmp_path)
    first = requests[0]
    key = cache.make_key(first, cognition_input)
    cache.put(
        key,
        CityCognitionCacheRecordV1(
            schema_version=1,
            key=key,
            request=first,
            cognition_input=cognition_input,
            result=_result(first),
            prompt_tokens=21,
            completion_tokens=8,
        ),
    )
    provider = _Provider(_return_valid)

    evaluation = await CityCognitionService(cache=cache).resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert provider.calls == []
    assert [record.source for record in evaluation.records] == [
        "cache",
        "budget-skipped",
        "budget-skipped",
        "budget-skipped",
        "budget-skipped",
        "budget-skipped",
    ]
    assert [record.attempt_count for record in evaluation.records] == [0, 0, 0, 0, 0, 0]
    assert evaluation.summary.prompt_tokens == 21
    assert evaluation.summary.completion_tokens == 8


async def test_retryable_failures_charge_fixed_attempt_slices_and_use_exact_delays() -> None:
    context, requests = _requests()
    profile = _profile(max_output_tokens=32, transient_retries=2)
    cognition_input = _provider_input(context, requests, profile=profile)
    failures: list[CityProviderCallError | CityProviderAttemptV1] = [
        CityProviderRateLimitError(),
        CityProviderServerError(),
        _valid_attempt(requests[0], prompt_tokens=33, completion_tokens=9),
    ]

    async def scripted(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        outcome = failures.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    sleeps = _Sleeps()
    provider = _Provider(scripted)
    evaluation = await CityCognitionService(sleep=sleeps).resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    record = evaluation.records[0]
    assert record.source == "provider"
    assert record.attempt_count == 3
    assert record.attempt_count * cognition_input.max_output_tokens_per_attempt == 1_152
    assert cognition_input.reservations[0].reserved_output_tokens == 1_536
    assert cognition_input.effective_settings is not None
    assert cognition_input.effective_settings.max_output_tokens == 32
    assert sleeps.delays == [0.25, 0.75]


async def test_two_retries_plus_one_repair_consume_the_four_reserved_attempt_slices() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    outcomes: list[CityProviderCallError | CityProviderAttemptV1] = [
        CityProviderRateLimitError(),
        CityProviderServerError(),
        _invalid_attempt(),
    ]

    async def retried_then_invalid(
        request: CityCognitionRequestV1,
    ) -> CityProviderAttemptV1:
        del request
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def repaired(
        request: CityCognitionRequestV1,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1:
        assert context.reason_code == "invalid-schema"
        return _valid_attempt(request)

    sleeps = _Sleeps()
    provider = _Provider(retried_then_invalid, repair=repaired)
    evaluation = await CityCognitionService(sleep=sleeps).resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    record = evaluation.records[0]
    assert record.source == "provider"
    assert record.attempt_count == 4
    assert record.attempt_count * cognition_input.max_output_tokens_per_attempt == 1_536
    assert len(provider.calls) == 3
    assert len(provider.repair_calls) == 1
    assert sleeps.delays == [0.25, 0.75]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CityProviderAuthenticationError(), "authentication-refused"),
        (CityProviderProtocolError(), "protocol-incompatible"),
        (CityProviderResponseTooLargeError(), "response-too-large"),
    ],
)
async def test_nonretryable_failures_fallback_after_exactly_one_attempt(
    error: CityProviderCallError,
    reason: str,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)

    async def refuse(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        raise error

    sleeps = _Sleeps()
    provider = _Provider(refuse)
    evaluation = await CityCognitionService(sleep=sleeps).resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    record = evaluation.records[0]
    assert record.source == "fallback"
    assert record.reason == reason
    assert record.attempt_count == 1
    assert len(provider.calls) == 1
    assert sleeps.delays == []


@pytest.mark.parametrize(
    ("retries", "expected_attempts", "expected_delays"),
    [(0, 1, []), (1, 2, [0.25]), (2, 3, [0.25, 0.75])],
)
async def test_frozen_retry_setting_is_honored_exactly(
    retries: int,
    expected_attempts: int,
    expected_delays: list[float],
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(
        context,
        requests,
        profile=_profile(transient_retries=retries),
    )

    async def unavailable(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        raise CityProviderUnavailableError

    sleeps = _Sleeps()
    provider = _Provider(unavailable)
    evaluation = await CityCognitionService(sleep=sleeps).resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert evaluation.records[0].reason == "endpoint-resolution-refused"
    assert evaluation.records[0].attempt_count == expected_attempts
    assert len(provider.calls) == expected_attempts
    assert sleeps.delays == expected_delays


async def test_cancellation_interrupts_retry_backoff_without_starting_another_attempt() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    backoff_started = asyncio.Event()
    backoff_cancelled = asyncio.Event()

    async def unavailable(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        raise CityProviderUnavailableError

    async def blocked_backoff(delay: float) -> None:
        assert delay == 0.25
        backoff_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            backoff_cancelled.set()
            raise

    provider = _Provider(unavailable)
    task = asyncio.create_task(
        CityCognitionService(sleep=blocked_backoff).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )
    )
    await asyncio.wait_for(backoff_started.wait(), timeout=1)
    cancellation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert backoff_cancelled.is_set()
    assert len(provider.calls) == 1
    assert provider.close_calls == 1


@pytest.mark.parametrize("repair_attempts", [0, 1])
async def test_frozen_repair_setting_is_honored_exactly(repair_attempts: int) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(
        context,
        requests,
        profile=_profile(repair_attempts=repair_attempts),
    )

    async def invalid(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        return _invalid_attempt()

    async def repaired(
        request: CityCognitionRequestV1,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1:
        assert context.reason_code == "invalid-schema"
        return _valid_attempt(request)

    provider = _Provider(invalid, repair=repaired)
    evaluation = await CityCognitionService().resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    record = evaluation.records[0]
    assert len(provider.repair_calls) == repair_attempts
    assert record.attempt_count == 1 + repair_attempts
    assert record.source == ("provider" if repair_attempts else "fallback")
    assert record.reason == (None if repair_attempts else "invalid-result")


async def test_unsafe_provider_content_is_never_sent_back_for_repair() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)

    async def unsafe(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        return _invalid_attempt("unsafe-content")

    provider = _Provider(unsafe)
    evaluation = await CityCognitionService().resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert provider.repair_calls == []
    assert evaluation.records[0].source == "fallback"
    assert evaluation.records[0].reason == "invalid-result"
    assert evaluation.records[0].attempt_count == 1


async def test_frozen_attempt_timeout_bounds_the_whole_provider_await(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(
        context,
        requests,
        profile=_profile(attempt_timeout_seconds=1, transient_retries=0),
    )
    real_timeout = asyncio.timeout
    seen: list[float] = []

    def shortened_timeout(delay: float | None):
        assert delay is not None
        seen.append(delay)
        return real_timeout(0.01 if delay == 1 else delay)

    monkeypatch.setattr(city_service.asyncio, "timeout", shortened_timeout)

    async def blocked(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = _Provider(blocked)
    evaluation = await CityCognitionService().resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert 1 in seen
    assert 180 in seen
    assert evaluation.records[0].reason == "timeout-exhausted"
    assert evaluation.records[0].attempt_count == 1


async def test_provider_calls_never_exceed_four_and_terminal_order_ignores_completion_order() -> (
    None
):
    context, requests = _requests(6)
    cognition_input = _provider_input(context, requests)
    active = 0
    maximum_active = 0
    first_wave = asyncio.Event()
    release = asyncio.Event()

    async def controlled(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 4:
            first_wave.set()
        await release.wait()
        # Reverse completion pressure relative to selection order.
        await asyncio.sleep((6 - requests.index(request)) / 10_000)
        active -= 1
        return _valid_attempt(request)

    provider = _Provider(controlled)
    task = asyncio.create_task(
        CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )
    )
    await asyncio.wait_for(first_wave.wait(), timeout=1)
    assert maximum_active == 4
    release.set()
    evaluation = await asyncio.wait_for(task, timeout=1)

    assert maximum_active == 4
    assert tuple(record.request_id for record in evaluation.records) == tuple(
        request.request_id for request in requests
    )


async def test_aggregate_deadline_creates_complete_ordered_fallback_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, requests = _requests(6)
    cognition_input = _provider_input(context, requests)
    real_timeout = asyncio.timeout
    seen: list[float] = []

    def shortened_timeout(delay: float | None):
        assert delay is not None
        seen.append(delay)
        return real_timeout(0.02 if delay == 180 else delay)

    monkeypatch.setattr(city_service.asyncio, "timeout", shortened_timeout)

    async def blocked(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = _Provider(blocked)
    evaluation = await CityCognitionService().resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert 180 in seen
    assert len(evaluation.records) == len(requests)
    assert tuple(record.request_id for record in evaluation.records) == tuple(
        request.request_id for request in requests
    )
    assert {record.source for record in evaluation.records} == {"fallback"}
    assert {record.reason for record in evaluation.records} == {"timeout-exhausted"}
    assert sum(record.attempt_count for record in evaluation.records) <= 4
    assert provider.close_calls == 1


async def test_aggregate_deadline_does_not_hide_a_provider_cleanup_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    real_timeout = asyncio.timeout

    def shortened_timeout(delay: float | None):
        assert delay is not None
        return real_timeout(0.01 if delay == 180 else delay)

    monkeypatch.setattr(city_service.asyncio, "timeout", shortened_timeout)

    async def broken_on_cancel(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise TypeError("provider cleanup defect") from None
        raise AssertionError("unreachable")

    provider = _Provider(broken_on_cancel)
    with pytest.raises(TypeError, match="provider cleanup defect"):
        await CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )

    assert provider.close_calls == 1


async def test_aggregate_deadline_is_not_replaced_by_an_expected_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    real_timeout = asyncio.timeout

    def shortened_timeout(delay: float | None):
        assert delay is not None
        return real_timeout(0.01 if delay == 180 else delay)

    monkeypatch.setattr(city_service.asyncio, "timeout", shortened_timeout)

    async def unavailable(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        raise CityProviderUnavailableError

    async def provider_shaped_backoff_failure(delay: float) -> None:
        assert delay == 0.25
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise CityProviderProtocolError from None

    provider = _Provider(unavailable)
    evaluation = await CityCognitionService(sleep=provider_shaped_backoff_failure).resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert evaluation.records[0].source == "fallback"
    assert evaluation.records[0].reason == "timeout-exhausted"
    assert evaluation.records[0].attempt_count == 1
    assert provider.close_calls == 1


async def test_corrupt_cache_is_a_hard_failure_not_a_provider_fallback(tmp_path: Path) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cache = _cache(tmp_path)
    key = cache.make_key(requests[0], cognition_input)
    directory = cache.workspace.root / "city-cognition-cache"
    directory.mkdir()
    (directory / f"{key}.json").write_bytes(b'{"not":"trusted"}')
    provider = _Provider(_return_valid)

    with pytest.raises(CityCognitionCacheCorruption):
        await CityCognitionService(cache=cache).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )

    assert provider.calls == []
    assert provider.close_calls == 1


async def test_cache_publication_failure_propagates_after_provider_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cache = _cache(tmp_path)

    def refuse_publication(
        self: CityCognitionCache,
        key: str,
        record: CityCognitionCacheRecordV1,
    ) -> None:
        del self, key, record
        raise CityCognitionCachePublicationError(potentially_committed=False)

    monkeypatch.setattr(CityCognitionCache, "put", refuse_publication)
    provider = _Provider(_return_valid)

    with pytest.raises(CityCognitionCachePublicationError) as caught:
        await CityCognitionService(cache=cache).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )

    assert caught.value.potentially_committed is False
    assert provider.close_calls == 1
    assert not any(provider.secret)


async def test_programming_defects_propagate_and_the_owned_provider_is_closed() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)

    async def broken(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        raise TypeError("implementation defect")

    provider = _Provider(broken)
    with pytest.raises(TypeError, match="implementation defect"):
        await CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )

    assert provider.close_calls == 1
    assert not any(provider.secret)


async def test_invalid_cancellation_argument_still_closes_the_owned_provider() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    provider = _Provider(_return_valid)

    with pytest.raises(TypeError, match="CityCognitionCancellation"):
        await CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=object(),
        )

    assert provider.calls == []
    assert provider.close_calls == 1
    assert not any(provider.secret)


async def test_valid_provider_result_is_cached_only_after_complete_resolution(
    tmp_path: Path,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cache = _cache(tmp_path)

    async def with_usage(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        return _valid_attempt(request, prompt_tokens=17, completion_tokens=5)

    first_provider = _Provider(with_usage)
    first = await CityCognitionService(cache=cache).resolve_many(
        cognition_input,
        requests,
        provider=first_provider,
        cancellation=CityCognitionCancellation(),
    )
    second_provider = _Provider(_return_valid)
    second = await CityCognitionService(cache=cache).resolve_many(
        cognition_input,
        requests,
        provider=second_provider,
        cancellation=CityCognitionCancellation(),
    )

    assert first.records[0].source == "provider"
    assert first.records[0].attempt_count == 1
    assert second.records[0].source == "cache"
    assert second.records[0].attempt_count == 0
    assert second.records[0].prompt_tokens == 17
    assert second.records[0].completion_tokens == 5
    assert second_provider.calls == []


async def test_invalid_later_result_cannot_partially_publish_an_earlier_cache_record(
    tmp_path: Path,
) -> None:
    context, requests = _requests(2)
    cognition_input = _provider_input(context, requests)
    cache = _cache(tmp_path)

    async def cross_bound_second(
        request: CityCognitionRequestV1,
    ) -> CityProviderAttemptV1:
        if request == requests[0]:
            return _valid_attempt(request)
        return CityProviderAttemptV1(status="valid", result=_result(requests[0]))

    provider = _Provider(cross_bound_second)
    with pytest.raises(ValueError, match="does not match terminal request"):
        await CityCognitionService(cache=cache).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )

    assert provider.close_calls == 1
    assert not (cache.workspace.root / "city-cognition-cache").exists()


async def test_cancellation_before_loop_binding_starts_no_work_and_still_closes_provider() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    cancellation.cancel()
    provider = _Provider(_return_valid)

    with pytest.raises(asyncio.CancelledError):
        await CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )

    assert provider.calls == []
    assert provider.close_calls == 1
    assert not any(provider.secret)


@pytest.mark.parametrize("stage", ["dns", "connect", "read"])
async def test_thread_safe_cancellation_interrupts_each_initial_provider_await(stage: str) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    entered = asyncio.Event()
    cancelled_inside = asyncio.Event()

    async def blocked(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        assert stage in {"dns", "connect", "read"}
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled_inside.set()
            raise
        raise AssertionError("unreachable")

    provider = _Provider(blocked)
    task = asyncio.create_task(
        CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    thread = threading.Thread(target=cancellation.cancel)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert cancelled_inside.is_set()
    assert provider.close_calls == 1


async def test_cancellation_does_not_hide_a_provider_cleanup_defect() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    entered = asyncio.Event()

    async def broken_on_cancel(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise TypeError("provider cleanup defect") from None
        raise AssertionError("unreachable")

    provider = _Provider(broken_on_cancel)
    task = asyncio.create_task(
        CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    cancellation.cancel()

    with pytest.raises(TypeError, match="provider cleanup defect"):
        await asyncio.wait_for(task, timeout=1)
    assert provider.close_calls == 1


async def test_cancellation_is_not_replaced_by_an_expected_provider_failure() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    backoff_started = asyncio.Event()

    async def unavailable(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        raise CityProviderUnavailableError

    async def provider_shaped_backoff_failure(delay: float) -> None:
        assert delay == 0.25
        backoff_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise CityProviderProtocolError from None

    provider = _Provider(unavailable)
    task = asyncio.create_task(
        CityCognitionService(sleep=provider_shaped_backoff_failure).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )
    )
    await asyncio.wait_for(backoff_started.wait(), timeout=1)
    cancellation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert provider.close_calls == 1


async def test_cancellation_interrupts_repair_and_never_returns_fallback() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    repair_entered = asyncio.Event()

    async def invalid(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        del request
        return _invalid_attempt()

    async def blocked_repair(
        request: CityCognitionRequestV1,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1:
        del request, context
        repair_entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = _Provider(invalid, repair=blocked_repair)
    task = asyncio.create_task(
        CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )
    )
    await asyncio.wait_for(repair_entered.wait(), timeout=1)
    cancellation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert provider.close_calls == 1


async def test_cancellation_after_provider_result_prevents_cache_publication(
    tmp_path: Path,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    cache = _cache(tmp_path)

    async def cancel_then_answer(request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        cancellation.cancel()
        return _valid_attempt(request)

    provider = _Provider(cancel_then_answer)
    with pytest.raises(asyncio.CancelledError):
        await CityCognitionService(cache=cache).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )

    assert not (cache.workspace.root / "city-cognition-cache").exists()
    assert provider.close_calls == 1


async def test_cancellation_during_service_shutdown_prevents_return_and_cache_publication(
    tmp_path: Path,
) -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    cancellation = CityCognitionCancellation()
    cache = _cache(tmp_path)

    async def cancel_on_close() -> None:
        cancellation.cancel()
        await asyncio.sleep(0)

    provider = _Provider(_return_valid, on_close=cancel_on_close)
    with pytest.raises(asyncio.CancelledError):
        await CityCognitionService(cache=cache).resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=cancellation,
        )

    assert provider.close_calls == 1
    assert not any(provider.secret)
    assert not (cache.workspace.root / "city-cognition-cache").exists()


async def test_provider_close_defect_is_not_silently_swallowed() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)

    async def broken_close() -> None:
        raise RuntimeError("close defect")

    provider = _Provider(_return_valid, on_close=broken_close)
    with pytest.raises(RuntimeError, match="close defect"):
        await CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=provider,
            cancellation=CityCognitionCancellation(),
        )


async def test_provider_close_is_optional_at_the_port_boundary() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)
    provider = _ProviderWithoutClose()

    evaluation = await CityCognitionService().resolve_many(
        cognition_input,
        requests,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )

    assert evaluation.records[0].source == "provider"
    assert provider.calls == [requests[0].request_id]


async def test_nonawaitable_provider_close_is_a_programming_error() -> None:
    context, requests = _requests()
    cognition_input = _provider_input(context, requests)

    with pytest.raises(TypeError, match="aclose must return an awaitable"):
        await CityCognitionService().resolve_many(
            cognition_input,
            requests,
            provider=_ProviderWithNonAwaitableClose(),
            cancellation=CityCognitionCancellation(),
        )


def test_cancellation_can_be_signalled_before_or_after_its_bound_loop_closes() -> None:
    cancellation = CityCognitionCancellation()
    cancellation.cancel()
    cancellation.cancel()
    with pytest.raises(asyncio.CancelledError):
        cancellation.raise_if_cancelled()
