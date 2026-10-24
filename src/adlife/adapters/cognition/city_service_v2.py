"""Bounded V2 signal resolution with canonical, post-validation publication."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import cast

from adlife.adapters.cognition.city_cache_v2 import (
    CityCognitionCacheCorruption,
    CityCognitionCacheRecordV2,
    CityCognitionCacheV2,
    _cache_key_material_v2,
)
from adlife.adapters.cognition.city_provider_transport import (
    CityProviderCallError,
    CityProviderProtocolError,
    CityProviderTimeoutError,
)
from adlife.adapters.cognition.city_service import (
    RETRY_DELAYS_SECONDS,
    RETRYABLE_PROVIDER_STATUSES,
    CityCognitionCancellation,
)
from adlife.core.domain.city_cognition import MAX_CITY_COGNITION_USAGE_INTEGER
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionEvaluationV2,
    CityCognitionFallbackReasonV2,
    CityCognitionInputV2,
    CityCognitionRequestV2,
    CityCognitionResultV2,
    CityCognitionRuntimeFallbackReasonV2,
    CityCognitionSourceV2,
    CityProviderAttemptV2,
    CityProviderRepairContextV2,
    CityProviderUsageV2,
)
from adlife.core.ports.city_cognition import CityCognitionProviderV2
from adlife.core.simulation.city_cognition_v2 import (
    build_city_budget_skipped_result_v2,
    build_city_cognition_terminal_record_v2,
    build_city_fallback_result_v2,
    build_city_mock_result_v2,
    build_city_rules_result_v2,
    validate_city_cognition_evaluation_v2,
)
from adlife.core.simulation.spatial_response import SpatialResponseEvaluation

_REPAIRABLE_RESULT_REASONS = frozenset({"invalid-json", "invalid-schema", "request-id-mismatch"})
# Transient retries wait 1 s, 2 s, 4 s, then 8 s at most, each spread by +/-20 %.
_BACKOFF_FIRST_SECONDS = 1.0
_BACKOFF_CEILING_SECONDS = 8.0
_BACKOFF_JITTER = 0.2
_RETRY_AFTER_CEILING_SECONDS = 30


def _retry_delay_seconds(request_id: str, retry: int, retry_after: int | None) -> float:
    """The wait before transient retry ``retry`` (0-based) of one question.

    A server's Retry-After hint wins, bounded to 30 s. Otherwise the backoff doubles,
    with jitter derived from the question and retry number: a rerun waits exactly as
    long, while simultaneous retries of different questions still spread out.
    """
    if retry_after is not None:
        return float(min(retry_after, _RETRY_AFTER_CEILING_SECONDS))
    base = min(_BACKOFF_FIRST_SECONDS * 2.0**retry, _BACKOFF_CEILING_SECONDS)
    digest = hashlib.sha256(f"{request_id}:{retry}".encode()).digest()
    unit = int.from_bytes(digest[:8], "big") / 2.0**64
    return base * (1.0 - _BACKOFF_JITTER + 2.0 * _BACKOFF_JITTER * unit)


def _interleaved_order(count: int) -> tuple[int, ...]:
    """Positions ``0..count-1`` in bit-reversed order.

    Questions start in this order, so if the run's deadline cuts the batch short, the
    unanswered questions are spread over the whole run instead of being its last days.
    """
    if count <= 1:
        return tuple(range(count))
    width = (count - 1).bit_length()
    return tuple(sorted(range(count), key=lambda position: int(f"{position:0{width}b}"[::-1], 2)))


def _loop_time() -> float:
    return asyncio.get_running_loop().time()


@dataclass(slots=True)
class _AttemptState:
    count: int = 0


@dataclass(slots=True)
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    present: bool = False

    def add(self, usage: CityProviderUsageV2 | None) -> None:
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
    request: CityCognitionRequestV2
    source: CityCognitionSourceV2
    result: CityCognitionResultV2
    attempt_count: int
    reason: CityCognitionFallbackReasonV2 | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


async def _default_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _no_progress() -> None:
    """Accept a progress report that no caller observes."""


def _preflight(
    cognition_input: CityCognitionInputV2,
    requests: tuple[CityCognitionRequestV2, ...],
    baseline_evaluation: SpatialResponseEvaluation,
) -> tuple[CityCognitionInputV2, tuple[CityCognitionRequestV2, ...], SpatialResponseEvaluation]:
    if type(cognition_input) is not CityCognitionInputV2:
        raise TypeError("cognition_input must be a CityCognitionInputV2")
    if type(requests) is not tuple:
        raise TypeError("requests must be a tuple of CityCognitionRequestV2 values")
    if any(type(request) is not CityCognitionRequestV2 for request in requests):
        raise TypeError("requests must contain CityCognitionRequestV2 values")
    if type(baseline_evaluation) is not SpatialResponseEvaluation:
        raise TypeError("baseline_evaluation must be a SpatialResponseEvaluation")
    frozen = CityCognitionInputV2.model_validate(
        cognition_input.model_dump(mode="python", warnings="none")
    )
    ordered = tuple(
        CityCognitionRequestV2.model_validate(request.model_dump(mode="python", warnings="none"))
        for request in requests
    )
    baseline = SpatialResponseEvaluation.model_validate(
        baseline_evaluation.model_dump(mode="python", warnings="none")
    )
    if len(ordered) != len(frozen.reservations):
        raise ValueError("city cognition reservations do not cover selected requests")
    provisional = []
    for index, (request, reservation) in enumerate(zip(ordered, frozen.reservations, strict=True)):
        reason: CityCognitionFallbackReasonV2 | None = None
        if frozen.mode == "rules":
            source: CityCognitionSourceV2 = "rules"
            result = build_city_rules_result_v2(request)
        elif frozen.mode == "mock":
            source = "mock"
            result = build_city_mock_result_v2(request, policy=frozen.causal_contract.mock_policy)
        elif reservation.funded:
            source = "fallback"
            reason = "invalid-result"
            result = build_city_fallback_result_v2(request, reason=reason)
        else:
            source = "budget-skipped"
            reason = "budget-skipped"
            result = build_city_budget_skipped_result_v2(request)
        provisional.append(
            build_city_cognition_terminal_record_v2(
                request, frozen, selection_index=index, source=source, result=result, reason=reason
            )
        )
    # Recompute only to validate frozen funding; the service never reallocates it.
    validate_city_cognition_evaluation_v2(
        frozen, ordered, tuple(provisional), baseline_evaluation=baseline
    )
    return frozen, ordered, baseline


def _usage_values(usage: _Usage) -> tuple[int | None, int | None]:
    if not usage.present:
        return None, None
    return usage.prompt_tokens, usage.completion_tokens


def _fallback(
    request: CityCognitionRequestV2,
    cognition_input: CityCognitionInputV2,
    *,
    attempts: int,
    reason: CityCognitionRuntimeFallbackReasonV2,
    usage: _Usage | None = None,
) -> _Resolution:
    prompt_tokens, completion_tokens = _usage_values(_Usage() if usage is None else usage)
    return _Resolution(
        request=request,
        source="fallback",
        result=build_city_fallback_result_v2(
            request,
            reason=reason,
        ),
        attempt_count=attempts,
        reason=reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _checked_attempt(
    value: CityProviderAttemptV2, request: CityCognitionRequestV2
) -> CityProviderAttemptV2:
    if type(value) is not CityProviderAttemptV2:
        raise TypeError("city provider attempt must be a CityProviderAttemptV2")
    for child, expected in (
        (value.result, CityCognitionResultV2),
        (value.usage, CityProviderUsageV2),
        (value.repair_context, CityProviderRepairContextV2),
    ):
        if child is not None and type(child) is not expected:
            raise TypeError("city provider attempt contains a wrong-version model")
    checked = CityProviderAttemptV2.model_validate(value.model_dump(mode="python", warnings="none"))
    if checked.result is not None and checked.result.request_id != request.request_id:
        return CityProviderAttemptV2(
            schema_version=2,
            status="invalid-result",
            result=None,
            usage=checked.usage,
            repair_context=CityProviderRepairContextV2(
                schema_version=2,
                reason_code="request-id-mismatch",
                safe_excerpt="The answer did not match the requested causal identifier.",
            ),
        )
    return checked


async def _close_provider(provider: CityCognitionProviderV2 | None) -> None:
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


class CityCognitionServiceV2:
    """Resolve complete bounded signals under frozen causal and funding contracts."""

    __slots__ = ("_cache", "_sleep")

    def __init__(
        self,
        *,
        cache: CityCognitionCacheV2 | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if cache is not None and not isinstance(cache, CityCognitionCacheV2):
            raise TypeError("cache must be a CityCognitionCacheV2")
        if sleep is not None and not callable(sleep):
            raise TypeError("sleep must be an async callable")
        self._cache = cache
        self._sleep = _default_sleep if sleep is None else sleep

    async def resolve_many(
        self,
        cognition_input: CityCognitionInputV2,
        requests: tuple[CityCognitionRequestV2, ...],
        *,
        baseline_evaluation: SpatialResponseEvaluation,
        provider: CityCognitionProviderV2 | None,
        cancellation: CityCognitionCancellation,
        progress: Callable[[], None] | None = None,
    ) -> CityCognitionEvaluationV2:
        """Return only a complete, validated evaluation or raise without one.

        ``progress`` is called synchronously on this event loop once for every funded
        request that reaches a terminal outcome: a provider answer, a cache hit, or a
        fallback. Offline modes and budget-skipped requests never report. It only observes
        and must not raise; it cannot change the evaluation.
        """
        checked_input: CityCognitionInputV2
        ordered: tuple[CityCognitionRequestV2, ...]
        resolutions: tuple[_Resolution, ...]
        try:
            if not isinstance(cancellation, CityCognitionCancellation):
                raise TypeError("cancellation must be a CityCognitionCancellation")
            if progress is not None and not callable(progress):
                raise TypeError("progress must be callable")
            cancellation.bind_running_loop(asyncio.get_running_loop())
            cancellation.raise_if_cancelled()
            checked_input, ordered, checked_baseline = _preflight(
                cognition_input, requests, baseline_evaluation
            )
            cancellation.raise_if_cancelled()
            if checked_input.mode in {"rules", "mock"}:
                if provider is not None:
                    raise ValueError("offline city cognition must not receive a provider")
                resolutions = self._offline_resolutions(checked_input, ordered)
            else:
                if provider is None or not isinstance(provider, CityCognitionProviderV2):
                    raise TypeError("provider cognition requires a CityCognitionProviderV2")
                resolutions = await self._provider_resolutions(
                    checked_input,
                    ordered,
                    provider=provider,
                    cancellation=cancellation,
                    progress=_no_progress if progress is None else progress,
                )
        finally:
            await _close_provider(provider)

        records = tuple(
            build_city_cognition_terminal_record_v2(
                resolution.request,
                checked_input,
                selection_index=selection_index,
                source=resolution.source,
                result=resolution.result,
                attempt_count=resolution.attempt_count,
                reason=resolution.reason,
                prompt_tokens=resolution.prompt_tokens,
                completion_tokens=resolution.completion_tokens,
            )
            for selection_index, resolution in enumerate(resolutions)
        )
        evaluation = validate_city_cognition_evaluation_v2(
            checked_input, ordered, records, baseline_evaluation=checked_baseline
        )
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
        cognition_input: CityCognitionInputV2,
        requests: tuple[CityCognitionRequestV2, ...],
    ) -> tuple[_Resolution, ...]:
        return tuple(
            _Resolution(
                request=request,
                source="rules" if cognition_input.mode == "rules" else "mock",
                result=(
                    build_city_rules_result_v2(request)
                    if cognition_input.mode == "rules"
                    else build_city_mock_result_v2(
                        request, policy=cognition_input.causal_contract.mock_policy
                    )
                ),
                attempt_count=0,
            )
            for request in requests
        )

    async def _provider_resolutions(
        self,
        cognition_input: CityCognitionInputV2,
        requests: tuple[CityCognitionRequestV2, ...],
        *,
        provider: CityCognitionProviderV2,
        cancellation: CityCognitionCancellation,
        progress: Callable[[], None],
    ) -> tuple[_Resolution, ...]:
        profile = cognition_input.provider_profile
        settings = cognition_input.effective_settings
        assert profile is not None and settings is not None
        resolved: dict[int, _Resolution] = {}
        pending: list[tuple[int, CityCognitionRequestV2]] = []
        for index, (request, reservation) in enumerate(
            zip(requests, cognition_input.reservations, strict=True)
        ):
            cancellation.raise_if_cancelled()
            if reservation.reserved_output_tokens == 0:
                resolved[index] = _Resolution(
                    request=request,
                    source="budget-skipped",
                    result=build_city_budget_skipped_result_v2(
                        request,
                    ),
                    attempt_count=0,
                    reason="budget-skipped",
                )
                continue
            cached = self._cache_resolution(request, cognition_input)
            if cached is not None:
                resolved[index] = cached
                progress()
            else:
                pending.append((index, request))

        if pending:
            provider_results = await self._run_provider_batch(
                cognition_input,
                tuple(pending),
                provider=provider,
                cancellation=cancellation,
                progress=progress,
            )
            resolved.update(provider_results)
        cancellation.raise_if_cancelled()
        if set(resolved) != set(range(len(requests))):
            raise RuntimeError("city cognition service did not resolve every selected request")
        return tuple(resolved[index] for index in range(len(requests)))

    def _cache_resolution(
        self,
        request: CityCognitionRequestV2,
        cognition_input: CityCognitionInputV2,
    ) -> _Resolution | None:
        if self._cache is None:
            return None
        key = self._cache.make_key(request, cognition_input)
        record = self._cache.get(key)
        if record is None:
            return None
        checked: CityCognitionCacheRecordV2 | None = None
        try:
            if type(record) is not CityCognitionCacheRecordV2:
                raise TypeError("wrong cache record type")
            admitted = CityCognitionCacheRecordV2.model_validate(
                record.model_dump(mode="python", warnings="none")
            )
            expected = _cache_key_material_v2(request, cognition_input)
            if admitted.key == key and all(
                getattr(admitted, field) == value for field, value in expected.items()
            ):
                checked = admitted
        except (TypeError, ValueError, RecursionError):
            pass
        if checked is None:
            raise CityCognitionCacheCorruption()
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
        cognition_input: CityCognitionInputV2,
        pending_requests: tuple[tuple[int, CityCognitionRequestV2], ...],
        *,
        provider: CityCognitionProviderV2,
        cancellation: CityCognitionCancellation,
        progress: Callable[[], None],
    ) -> dict[int, _Resolution]:
        semaphore = asyncio.Semaphore(cognition_input.concurrency_limit)
        states = {index: _AttemptState() for index, _request in pending_requests}
        deadline_at = _loop_time() + cognition_input.aggregate_deadline_seconds
        tasks = {
            index: asyncio.create_task(
                self._resolve_one(
                    request,
                    cognition_input,
                    provider=provider,
                    semaphore=semaphore,
                    cancellation=cancellation,
                    state=states[index],
                    deadline_at=deadline_at,
                ),
                name=f"city-cognition:{request.request_id}",
            )
            for index, request in (
                pending_requests[position] for position in _interleaved_order(len(pending_requests))
            )
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
                            progress()
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
            reported = len(resolved)
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
            for _outcome in range(len(resolved) - reported):
                progress()
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
        request: CityCognitionRequestV2,
        cognition_input: CityCognitionInputV2,
        *,
        provider: CityCognitionProviderV2,
        semaphore: asyncio.Semaphore,
        cancellation: CityCognitionCancellation,
        state: _AttemptState,
        deadline_at: float,
    ) -> _Resolution:
        """Ask one question until it is answered or its frozen retry budgets run out.

        One transient budget covers the first call and every repair; repairable invalid
        answers spend the repair budget. Every attempt counts, so a question never
        exceeds 1 + transient_retries + repair_attempts calls.
        """
        settings = cognition_input.effective_settings
        assert settings is not None
        usage = _Usage()
        transient_left = settings.transient_retries
        repairs_left = settings.repair_attempts
        retries = 0
        repair_context: CityProviderRepairContextV2 | None = None
        while True:
            try:
                attempt = await self._invoke_provider(
                    request,
                    provider=provider,
                    semaphore=semaphore,
                    cancellation=cancellation,
                    state=state,
                    timeout_seconds=settings.attempt_timeout_seconds,
                    repair_context=repair_context,
                )
            except CityProviderCallError as error:
                delay = _retry_delay_seconds(request.request_id, retries, error.retry_after_seconds)
                # A wait that would outlast the run's deadline cannot produce an answer.
                if error.retryable and transient_left > 0 and _loop_time() + delay < deadline_at:
                    transient_left -= 1
                    retries += 1
                    await self._sleep(delay)
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
            if repairs_left == 0 or context.reason_code not in _REPAIRABLE_RESULT_REASONS:
                return _fallback(
                    request,
                    cognition_input,
                    attempts=state.count,
                    reason="invalid-result",
                    usage=usage,
                )
            repairs_left -= 1
            repair_context = context

    @staticmethod
    async def _invoke_provider(
        request: CityCognitionRequestV2,
        *,
        provider: CityCognitionProviderV2,
        semaphore: asyncio.Semaphore,
        cancellation: CityCognitionCancellation,
        state: _AttemptState,
        timeout_seconds: int,
        repair_context: CityProviderRepairContextV2 | None = None,
    ) -> CityProviderAttemptV2:
        cancellation.raise_if_cancelled()
        async with semaphore:
            cancellation.raise_if_cancelled()
            state.count += 1
            deadline = asyncio.timeout(timeout_seconds)
            try:
                async with deadline:
                    if repair_context is None:
                        value = await provider.call(request)
                    else:
                        value = await provider.repair_call(
                            request,
                            context=repair_context,
                        )
                # Suppressing task cancellation cannot admit a post-deadline answer.
                if deadline.expired():
                    raise CityProviderTimeoutError()
            except TimeoutError:
                raise CityProviderTimeoutError from None
        cancellation.raise_if_cancelled()
        return _checked_attempt(value, request)

    def _publish_cache_records(
        self,
        cognition_input: CityCognitionInputV2,
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
            record = CityCognitionCacheRecordV2.model_validate(
                {
                    "schema_version": 2,
                    "key": key,
                    **_cache_key_material_v2(resolution.request, cognition_input),
                    "result": resolution.result,
                    "prompt_tokens": resolution.prompt_tokens,
                    "completion_tokens": resolution.completion_tokens,
                }
            )
            cancellation._run_if_active(partial(cache.put, key, record))


__all__ = [
    "RETRYABLE_PROVIDER_STATUSES",
    "RETRY_DELAYS_SECONDS",
    "CityCognitionCancellation",
    "CityCognitionServiceV2",
]
