"""Bounded orchestration for optional city narrative explanations.

The deterministic city simulation is complete before this service runs.  This module
only chooses a frozen explanatory source, performs bounded provider attempts, and builds
one terminal record for every selected response.  It never feeds provider prose back
into movement, response, state, events, or metrics.

The provider passed to :meth:`CityCognitionService.resolve_many` is operation-scoped.
When it exposes ``aclose()``, the service owns and awaits that close on every outcome so
the concrete transport can clear its copied credential.  Close defects are not hidden.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import Final, Literal, TypeVar, cast

from adlife.adapters.cognition.city_cache import (
    CityCognitionCache,
    CityCognitionCacheRecordV1,
)
from adlife.adapters.cognition.city_openai_compatible import (
    CityProviderCallError,
    CityProviderProtocolError,
    CityProviderTimeoutError,
)
from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_USAGE_INTEGER,
    CityCognitionEvaluationV1,
    CityCognitionFallbackReason,
    CityCognitionInputV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
    CityCognitionRuntimeFallbackReason,
    CityCognitionSource,
    CityProviderAttemptV1,
    CityProviderRepairContextV1,
    CityProviderUsageV1,
)
from adlife.core.ports.city_cognition import CityCognitionProvider
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.city_cognition import (
    allocate_city_cognition_reservations,
    build_city_budget_skipped_result,
    build_city_cognition_terminal_record,
    build_city_fallback_result,
    build_city_mock_result,
    build_city_rules_result,
    validate_city_cognition_evaluation,
)

RETRYABLE_PROVIDER_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
RETRY_DELAYS_SECONDS: Final[tuple[float, float]] = (0.25, 0.75)

_REPAIRABLE_RESULT_REASONS = frozenset({"invalid-json", "invalid-schema", "request-id-mismatch"})

_ValueT = TypeVar("_ValueT")


class CityCognitionCancellation:
    """A one-operation cancellation signal safe to trip from another thread.

    The executor worker binds its running event loop.  A controller thread calls
    :meth:`cancel`, which records cancellation under a lock and wakes the loop through
    ``call_soon_threadsafe``.  A cancellation that wins before binding is retained and
    applied as soon as the loop is bound.
    """

    __slots__ = ("_cancelled", "_event", "_lock", "_loop")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None

    def bind_running_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind exactly the loop that currently owns the service coroutine."""
        if not isinstance(loop, asyncio.AbstractEventLoop):
            raise TypeError("loop must be an asyncio event loop")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError("city cognition cancellation must bind on a running loop") from None
        if running is not loop or not loop.is_running():
            raise RuntimeError("city cognition cancellation must bind its running loop")
        with self._lock:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("city cognition cancellation is already bound to another loop")
            if self._event is None:
                self._event = asyncio.Event()
                self._loop = loop
            if self._cancelled:
                self._event.set()

    def cancel(self) -> None:
        """Signal cancellation idempotently without touching loop state unsafely."""
        with self._lock:
            self._cancelled = True
            loop = self._loop
            event = self._event
        if loop is not None and event is not None:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(event.set)

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        self.bind_running_loop(loop)
        with self._lock:
            event = self._event
            cancelled = self._cancelled
        assert event is not None
        if cancelled:
            return
        await event.wait()

    def raise_if_cancelled(self) -> None:
        with self._lock:
            cancelled = self._cancelled
        if cancelled:
            raise asyncio.CancelledError("city cognition operation was cancelled")

    def _run_if_active(self, operation: Callable[[], _ValueT]) -> _ValueT:
        """Linearize a synchronous publication against ``cancel()``."""
        with self._lock:
            if self._cancelled:
                raise asyncio.CancelledError("city cognition operation was cancelled")
            return operation()


@dataclass(slots=True)
class _AttemptState:
    count: int = 0


@dataclass(slots=True)
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    present: bool = False

    def add(self, usage: CityProviderUsageV1 | None) -> None:
        if usage is None:
            return
        prompt = self.prompt_tokens + usage.prompt_tokens
        completion = self.completion_tokens + usage.completion_tokens
        if prompt > MAX_CITY_COGNITION_USAGE_INTEGER or completion > (
            MAX_CITY_COGNITION_USAGE_INTEGER
        ):
            raise CityProviderProtocolError from None
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.present = True


@dataclass(frozen=True, slots=True)
class _Resolution:
    request: CityCognitionRequestV1
    source: CityCognitionSource
    result: CityCognitionResultV1
    attempt_count: int
    reason: CityCognitionFallbackReason | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


async def _default_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _request_sort_key(request: CityCognitionRequestV1) -> tuple[object, ...]:
    return (
        request.model_minute,
        request.agent_id,
        request.campaign_id,
        request.response_event_id,
    )


def _preflight(
    cognition_input: CityCognitionInputV1,
    requests: tuple[CityCognitionRequestV1, ...],
) -> tuple[CityCognitionInputV1, tuple[CityCognitionRequestV1, ...]]:
    checked_input = revalidate_model(
        cognition_input,
        CityCognitionInputV1,
        label="city cognition input",
    )
    if type(requests) is not tuple:
        raise TypeError("requests must be a tuple of CityCognitionRequestV1 values")
    checked_requests = tuple(
        revalidate_model(request, CityCognitionRequestV1, label="city cognition request")
        for request in requests
    )
    ordered = tuple(sorted(checked_requests, key=_request_sort_key))
    if checked_requests != ordered:
        raise ValueError("city cognition requests are not in canonical deterministic order")
    expected_reservations = allocate_city_cognition_reservations(
        ordered,
        aggregate_output_token_ceiling=checked_input.aggregate_output_token_ceiling,
    )
    if checked_input.reservations != expected_reservations:
        raise ValueError("city cognition reservations do not match selected requests")

    # Exercise the complete cross-binding validator before cache or provider side effects.
    provisional = []
    profile = checked_input.provider_profile
    for selection_index, (request, reservation) in enumerate(
        zip(ordered, expected_reservations, strict=True)
    ):
        if checked_input.mode == "rules":
            source: CityCognitionSource = "rules"
            result = build_city_rules_result(request, policy=checked_input.fallback_policy)
            reason: CityCognitionFallbackReason | None = None
            bound_profile = None
        elif checked_input.mode == "mock":
            source = "mock"
            result = build_city_mock_result(request, policy=checked_input.fallback_policy)
            reason = None
            bound_profile = None
        elif reservation.reserved_output_tokens == 0:
            assert profile is not None
            source = "budget-skipped"
            result = build_city_budget_skipped_result(
                request,
                policy=checked_input.fallback_policy,
            )
            reason = "budget-skipped"
            bound_profile = profile
        else:
            assert profile is not None
            source = "fallback"
            result = build_city_fallback_result(
                request,
                reason="invalid-result",
                policy=checked_input.fallback_policy,
            )
            reason = "invalid-result"
            bound_profile = profile
        provisional.append(
            build_city_cognition_terminal_record(
                request,
                selection_index=selection_index,
                source=source,
                result=result,
                reason=reason,
                provider_profile=bound_profile,
            )
        )
    validate_city_cognition_evaluation(checked_input, ordered, tuple(provisional))
    return checked_input, ordered


def _usage_values(usage: _Usage) -> tuple[int | None, int | None]:
    if not usage.present:
        return None, None
    return usage.prompt_tokens, usage.completion_tokens


def _fallback(
    request: CityCognitionRequestV1,
    cognition_input: CityCognitionInputV1,
    *,
    attempts: int,
    reason: CityCognitionRuntimeFallbackReason,
    usage: _Usage | None = None,
) -> _Resolution:
    prompt_tokens, completion_tokens = _usage_values(_Usage() if usage is None else usage)
    return _Resolution(
        request=request,
        source="fallback",
        result=build_city_fallback_result(
            request,
            reason=reason,
            policy=cognition_input.fallback_policy,
        ),
        attempt_count=attempts,
        reason=reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _checked_attempt(value: CityProviderAttemptV1) -> CityProviderAttemptV1:
    return revalidate_model(value, CityProviderAttemptV1, label="city provider attempt")


async def _close_provider(provider: CityCognitionProvider | None) -> None:
    if provider is None:
        return
    close = getattr(provider, "aclose", None)
    if close is None:
        return
    if not callable(close):
        raise TypeError("city cognition provider aclose must be callable")
    outcome = close()
    if not inspect.isawaitable(outcome):
        raise TypeError("city cognition provider aclose must return an awaitable")
    await outcome


class CityCognitionService:
    """Resolve a complete city explanation batch within its frozen hard bounds."""

    __slots__ = ("_cache", "_sleep")

    def __init__(
        self,
        *,
        cache: CityCognitionCache | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if cache is not None and not isinstance(cache, CityCognitionCache):
            raise TypeError("cache must be a CityCognitionCache")
        if sleep is not None and not callable(sleep):
            raise TypeError("sleep must be an async callable")
        self._cache = cache
        self._sleep = _default_sleep if sleep is None else sleep

    async def resolve_many(
        self,
        cognition_input: CityCognitionInputV1,
        requests: tuple[CityCognitionRequestV1, ...],
        *,
        provider: CityCognitionProvider | None,
        cancellation: CityCognitionCancellation,
    ) -> CityCognitionEvaluationV1:
        """Return only a complete, validated evaluation or raise without one."""
        checked_input: CityCognitionInputV1
        ordered: tuple[CityCognitionRequestV1, ...]
        resolutions: tuple[_Resolution, ...]
        try:
            if not isinstance(cancellation, CityCognitionCancellation):
                raise TypeError("cancellation must be a CityCognitionCancellation")
            cancellation.bind_running_loop(asyncio.get_running_loop())
            cancellation.raise_if_cancelled()
            checked_input, ordered = _preflight(cognition_input, requests)
            cancellation.raise_if_cancelled()
            if checked_input.mode in {"rules", "mock"}:
                if provider is not None:
                    raise ValueError("offline city cognition must not receive a provider")
                resolutions = self._offline_resolutions(checked_input, ordered)
            else:
                if provider is None or not isinstance(provider, CityCognitionProvider):
                    raise TypeError("provider cognition requires a CityCognitionProvider")
                resolutions = await self._provider_resolutions(
                    checked_input,
                    ordered,
                    provider=provider,
                    cancellation=cancellation,
                )
        finally:
            await _close_provider(provider)

        records = tuple(
            build_city_cognition_terminal_record(
                resolution.request,
                selection_index=selection_index,
                source=resolution.source,
                result=resolution.result,
                attempt_count=resolution.attempt_count,
                reason=resolution.reason,
                provider_profile=(
                    checked_input.provider_profile
                    if resolution.source in {"provider", "cache", "fallback", "budget-skipped"}
                    else None
                ),
                prompt_tokens=resolution.prompt_tokens,
                completion_tokens=resolution.completion_tokens,
            )
            for selection_index, resolution in enumerate(resolutions)
        )
        evaluation = validate_city_cognition_evaluation(checked_input, ordered, records)
        cancellation.raise_if_cancelled()
        self._publish_cache_records(
            checked_input,
            resolutions,
            cancellation=cancellation,
        )
        cancellation.raise_if_cancelled()
        return evaluation

    @staticmethod
    def _offline_resolutions(
        cognition_input: CityCognitionInputV1,
        requests: tuple[CityCognitionRequestV1, ...],
    ) -> tuple[_Resolution, ...]:
        if cognition_input.mode == "rules":
            source: Literal["rules", "mock"] = "rules"
            builder = build_city_rules_result
        else:
            assert cognition_input.mode == "mock"
            source = "mock"
            builder = build_city_mock_result
        return tuple(
            _Resolution(
                request=request,
                source=source,
                result=builder(request, policy=cognition_input.fallback_policy),
                attempt_count=0,
            )
            for request in requests
        )

    async def _provider_resolutions(
        self,
        cognition_input: CityCognitionInputV1,
        requests: tuple[CityCognitionRequestV1, ...],
        *,
        provider: CityCognitionProvider,
        cancellation: CityCognitionCancellation,
    ) -> tuple[_Resolution, ...]:
        profile = cognition_input.provider_profile
        settings = cognition_input.effective_settings
        assert profile is not None and settings is not None
        resolved: dict[int, _Resolution] = {}
        pending: list[tuple[int, CityCognitionRequestV1]] = []
        for index, (request, reservation) in enumerate(
            zip(requests, cognition_input.reservations, strict=True)
        ):
            cancellation.raise_if_cancelled()
            if reservation.reserved_output_tokens == 0:
                resolved[index] = _Resolution(
                    request=request,
                    source="budget-skipped",
                    result=build_city_budget_skipped_result(
                        request,
                        policy=cognition_input.fallback_policy,
                    ),
                    attempt_count=0,
                    reason="budget-skipped",
                )
                continue
            cached = self._cache_resolution(request, cognition_input)
            if cached is not None:
                resolved[index] = cached
            else:
                pending.append((index, request))

        if pending:
            provider_results = await self._run_provider_batch(
                cognition_input,
                tuple(pending),
                provider=provider,
                cancellation=cancellation,
            )
            resolved.update(provider_results)
        cancellation.raise_if_cancelled()
        if set(resolved) != set(range(len(requests))):
            raise RuntimeError("city cognition service did not resolve every selected request")
        return tuple(resolved[index] for index in range(len(requests)))

    def _cache_resolution(
        self,
        request: CityCognitionRequestV1,
        cognition_input: CityCognitionInputV1,
    ) -> _Resolution | None:
        if self._cache is None:
            return None
        key = self._cache.make_key(request, cognition_input)
        record = self._cache.get(key)
        if record is None:
            return None
        checked = revalidate_model(
            record,
            CityCognitionCacheRecordV1,
            label="city cognition cache record",
        )
        if checked.key != key:
            raise ValueError("city cognition cache record is cross-bound")
        return _Resolution(
            request=request,
            source="cache",
            result=checked.result,
            attempt_count=0,
            prompt_tokens=checked.prompt_tokens,
            completion_tokens=checked.completion_tokens,
        )

    async def _run_provider_batch(
        self,
        cognition_input: CityCognitionInputV1,
        pending_requests: tuple[tuple[int, CityCognitionRequestV1], ...],
        *,
        provider: CityCognitionProvider,
        cancellation: CityCognitionCancellation,
    ) -> dict[int, _Resolution]:
        semaphore = asyncio.Semaphore(cognition_input.concurrency_limit)
        states = {index: _AttemptState() for index, _request in pending_requests}
        tasks = {
            index: asyncio.create_task(
                self._resolve_one(
                    request,
                    cognition_input,
                    provider=provider,
                    semaphore=semaphore,
                    cancellation=cancellation,
                    state=states[index],
                ),
                name=f"city-cognition:{request.request_id}",
            )
            for index, request in pending_requests
        }
        task_to_index = {task: index for index, task in tasks.items()}
        cancellation_waiter = asyncio.create_task(
            cancellation.wait(),
            name="city-cognition:cancellation",
        )
        resolved: dict[int, _Resolution] = {}
        pending_tasks = set(tasks.values())
        timeout_context = asyncio.timeout(cognition_input.aggregate_deadline_seconds)
        expired = False
        done_at_deadline: set[asyncio.Task[_Resolution]] = set()
        try:
            try:
                async with timeout_context:
                    while pending_tasks:
                        done, _remaining = await asyncio.wait(
                            pending_tasks | {cancellation_waiter},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if cancellation_waiter in done:
                            raise asyncio.CancelledError("city cognition operation was cancelled")
                        worker_done = sorted(
                            (
                                cast("asyncio.Task[_Resolution]", task)
                                for task in done
                                if task is not cancellation_waiter
                            ),
                            key=lambda worker: task_to_index[worker],
                        )
                        for task in worker_done:
                            pending_tasks.remove(task)
                            resolved[task_to_index[task]] = task.result()
            except TimeoutError:
                if not timeout_context.expired():
                    raise
                expired = True
                done_at_deadline = {task for task in pending_tasks if task.done()}
        except BaseException as error:
            cancelled_outcomes = await self._cancel_tasks(pending_tasks)
            if isinstance(error, asyncio.CancelledError):
                cleanup_defects = tuple(
                    outcome
                    for outcome in cancelled_outcomes
                    if isinstance(outcome, Exception)
                    and not isinstance(outcome, CityProviderCallError)
                )
                if cleanup_defects:
                    raise cleanup_defects[0] from None
            raise
        finally:
            cancellation_waiter.cancel()
            await asyncio.gather(cancellation_waiter, return_exceptions=True)

        if expired:
            # Only tasks complete at the deadline may contribute a live result.  A
            # coroutine that ignores cancellation cannot turn post-deadline work into
            # a provider outcome.
            deadline_errors: list[Exception] = []
            for task in sorted(done_at_deadline, key=lambda item: task_to_index[item]):
                try:
                    resolved[task_to_index[task]] = task.result()
                except Exception as error:
                    if not isinstance(error, CityProviderCallError):
                        deadline_errors.append(error)
            cancelled_outcomes = await self._cancel_tasks(pending_tasks - done_at_deadline)
            deadline_errors.extend(
                outcome
                for outcome in cancelled_outcomes
                if isinstance(outcome, Exception) and not isinstance(outcome, CityProviderCallError)
            )
            if deadline_errors:
                raise deadline_errors[0]
            for index, request in pending_requests:
                if index not in resolved:
                    resolved[index] = _fallback(
                        request,
                        cognition_input,
                        attempts=states[index].count,
                        reason="timeout-exhausted",
                    )
        return resolved

    @staticmethod
    async def _cancel_tasks(tasks: set[asyncio.Task[_Resolution]]) -> tuple[object, ...]:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            return tuple(await asyncio.gather(*tasks, return_exceptions=True))
        return ()

    async def _resolve_one(
        self,
        request: CityCognitionRequestV1,
        cognition_input: CityCognitionInputV1,
        *,
        provider: CityCognitionProvider,
        semaphore: asyncio.Semaphore,
        cancellation: CityCognitionCancellation,
        state: _AttemptState,
    ) -> _Resolution:
        settings = cognition_input.effective_settings
        assert settings is not None
        usage = _Usage()
        for retry_index in range(settings.transient_retries + 1):
            try:
                attempt = await self._invoke_provider(
                    request,
                    provider=provider,
                    semaphore=semaphore,
                    cancellation=cancellation,
                    state=state,
                    timeout_seconds=settings.attempt_timeout_seconds,
                )
            except CityProviderCallError as error:
                if error.retryable and retry_index < settings.transient_retries:
                    await self._sleep(RETRY_DELAYS_SECONDS[retry_index])
                    cancellation.raise_if_cancelled()
                    continue
                return _fallback(
                    request,
                    cognition_input,
                    attempts=state.count,
                    reason=error.reason_code,
                    usage=usage,
                )
            try:
                usage.add(attempt.usage)
            except CityProviderProtocolError as error:
                return _fallback(
                    request,
                    cognition_input,
                    attempts=state.count,
                    reason=error.reason_code,
                    usage=_Usage(),
                )
            if attempt.status == "valid":
                assert attempt.result is not None
                prompt_tokens, completion_tokens = _usage_values(usage)
                return _Resolution(
                    request=request,
                    source="provider",
                    result=attempt.result,
                    attempt_count=state.count,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

            context = attempt.repair_context
            assert context is not None
            if (
                settings.repair_attempts == 0
                or context.reason_code not in _REPAIRABLE_RESULT_REASONS
            ):
                return _fallback(
                    request,
                    cognition_input,
                    attempts=state.count,
                    reason="invalid-result",
                    usage=usage,
                )
            try:
                repaired = await self._invoke_provider(
                    request,
                    provider=provider,
                    semaphore=semaphore,
                    cancellation=cancellation,
                    state=state,
                    timeout_seconds=settings.attempt_timeout_seconds,
                    repair_context=context,
                )
            except CityProviderCallError as error:
                return _fallback(
                    request,
                    cognition_input,
                    attempts=state.count,
                    reason=error.reason_code,
                    usage=usage,
                )
            try:
                usage.add(repaired.usage)
            except CityProviderProtocolError as error:
                return _fallback(
                    request,
                    cognition_input,
                    attempts=state.count,
                    reason=error.reason_code,
                    usage=_Usage(),
                )
            if repaired.status == "valid":
                assert repaired.result is not None
                prompt_tokens, completion_tokens = _usage_values(usage)
                return _Resolution(
                    request=request,
                    source="provider",
                    result=repaired.result,
                    attempt_count=state.count,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            return _fallback(
                request,
                cognition_input,
                attempts=state.count,
                reason="invalid-result",
                usage=usage,
            )
        raise AssertionError("provider retry loop did not terminate")

    @staticmethod
    async def _invoke_provider(
        request: CityCognitionRequestV1,
        *,
        provider: CityCognitionProvider,
        semaphore: asyncio.Semaphore,
        cancellation: CityCognitionCancellation,
        state: _AttemptState,
        timeout_seconds: int,
        repair_context: CityProviderRepairContextV1 | None = None,
    ) -> CityProviderAttemptV1:
        cancellation.raise_if_cancelled()
        async with semaphore:
            cancellation.raise_if_cancelled()
            state.count += 1
            try:
                async with asyncio.timeout(timeout_seconds):
                    if repair_context is None:
                        value = await provider.call(request)
                    else:
                        value = await provider.repair_call(
                            request,
                            context=repair_context,
                        )
            except TimeoutError:
                raise CityProviderTimeoutError from None
        cancellation.raise_if_cancelled()
        return _checked_attempt(value)

    def _publish_cache_records(
        self,
        cognition_input: CityCognitionInputV1,
        resolutions: tuple[_Resolution, ...],
        *,
        cancellation: CityCognitionCancellation,
    ) -> None:
        cache = self._cache
        if cache is None:
            return
        for resolution in resolutions:
            if resolution.source != "provider":
                continue
            key = cache.make_key(resolution.request, cognition_input)
            record = CityCognitionCacheRecordV1(
                schema_version=1,
                key=key,
                request=resolution.request,
                cognition_input=cognition_input,
                result=resolution.result,
                prompt_tokens=resolution.prompt_tokens,
                completion_tokens=resolution.completion_tokens,
            )
            cancellation._run_if_active(partial(cache.put, key, record))


__all__ = [
    "RETRYABLE_PROVIDER_STATUSES",
    "RETRY_DELAYS_SECONDS",
    "CityCognitionCancellation",
    "CityCognitionService",
]
