"""The bounded cognition service: cache, budget, retry, one repair, rule fallback.

This is the component specification section 12's hardest sentence belongs to - "provider
failures must never abort a run" - and everything here follows from it. Every failure is
resolved into a valid :class:`~adlife.core.ports.cognition.CognitionResult` carrying the
reason it fell back to the transparent rule formula, and the single failure the service
does NOT absorb is a terminal fallback that cannot answer, because there is nothing
below it to fall back to.

ORDER OF RESOLUTION, per request
--------------------------------
1. the cognition cache, if the provider describes itself well enough to key one;
2. the per-agent and total budgets, checked BEFORE dispatch;
3. the provider, retried only for failures that could plausibly succeed on a retry;
4. one schema-repair attempt, and only for an invalid answer;
5. the deterministic rule fallback.

DETERMINISM
-----------
Concurrency must not be observable in a run's output, so:

* budget is reserved for the whole batch in ``request_id`` order BEFORE any task starts,
  which means who wins a scarce budget is a function of the batch, not of scheduling;
* provider calls run concurrently under a semaphore, but nothing is committed inside a
  task: results, cache writes and fallback composition all happen afterwards in
  ``request_id`` order;
* retry delays are drawn from the keyed :class:`~adlife.core.simulation.rng.RandomOracle`
  rather than from a global random stream or a clock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, field_validator, model_validator

from adlife.adapters.cognition.cache import CacheMiss, CognitionCache, CorruptCacheRecord
from adlife.adapters.cognition.openai_compatible import (
    InvalidProviderResponse,
    ProviderCall,
    ProviderCallError,
)
from adlife.config.models import SimulationSettings
from adlife.core.ports.cognition import (
    MAX_RAW_RESPONSE_CHARS,
    CognitionAnswer,
    CognitionError,
    CognitionModel,
    CognitionProvider,
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    FallbackReason,
    ProviderKind,
    ProviderMetadata,
    ProviderUsage,
    RequestId,
    bound_raw_provider_body,
    redact_provider_body,
)
from adlife.core.simulation.rng import RandomOracle

logger = logging.getLogger(__name__)

MAX_CONCURRENT_PROVIDER_CALLS: Final = 4
"""How many provider calls may be in flight at once."""


def _configured_cognition_ceiling(field_name: str) -> int:
    """Read a cognitive-event ceiling off the configuration field rather than restating it.

    The same technique as
    :func:`adlife.adapters.cognition.openai_compatible._configured_timeout_ceiling`, for
    the same reason: a number written twice is a number that drifts, and the point of the
    ceiling is that the configuration field and the component that spends the budget
    agree about it.
    """
    for constraint in SimulationSettings.model_fields[field_name].metadata:
        upper = getattr(constraint, "le", None)
        if upper is not None:
            return int(upper)
    raise AssertionError(  # pragma: no cover - both fields declare ``le`` in this repository
        f"SimulationSettings.{field_name} must declare an upper bound"
    )


MAX_COGNITION_PER_AGENT: Final[int] = _configured_cognition_ceiling("max_cognition_per_agent")
"""Specification section 7: "Maximum cognitive events: 6 per agent per run"."""

MAX_COGNITION_TOTAL: Final[int] = _configured_cognition_ceiling("max_cognition_total")
"""The run-wide ceiling, read off :attr:`SimulationSettings.max_cognition_total`.

Specification section 7 states the per-agent limit and section 7's population ceiling of
30 agents bounds the rest; the configuration field carries the run-wide number this
repository commits to, and this is the one place that enforces it.
"""

MAX_PROVIDER_RETRIES: Final = 2
"""Specification section 7's ceiling, and the upper bound of ``ProviderSettings.retries``.

A run may CONFIGURE fewer. :class:`CognitionService` takes the configured count as a
constructor argument so the knob has a consumer rather than a promise that a later task
will wire it; this constant is the maximum it may be given, not the value it must take.
"""

MAX_PROVIDER_ATTEMPTS: Final = MAX_PROVIDER_RETRIES + 1
"""The most attempts any run may make: the initial one plus the retry ceiling."""

RETRY_BASE_DELAYS_SECONDS: Final[tuple[float, ...]] = (0.5, 1.5)
"""The documented backoff before the first and second retry."""

RETRY_JITTER_SECONDS: Final = 0.25
"""The width of the keyed deterministic jitter added to each base delay.

Jitter keeps a population of agents from retrying in lockstep against one endpoint. It
is drawn from the run's :class:`~adlife.core.simulation.rng.RandomOracle` rather than
from ``random``, so a rerun of the same seed waits the same amount.
"""

RETRY_NAMESPACE: Final = "cognition-retry"
"""The oracle namespace prefix; the campaign identifier completes it."""

NO_BODY_PLACEHOLDER: Final = "(the provider returned no usable body)"
"""What a repair prompt echoes when the rejected answer carried no body at all."""


class DuplicateCognitionRequest(CognitionError):
    """Raised when one batch names the same request twice.

    Two answers cannot both be committed under one ``request_id``, so a duplicate would
    silently drop one of them; refusing the batch fails closed instead.
    """


@runtime_checkable
class DescribedCognitionProvider(Protocol):
    """A provider that can name what it is, which is what a cache key is made of."""

    @property
    def provider_metadata(self) -> ProviderMetadata: ...


@runtime_checkable
class RepairableCognitionProvider(Protocol):
    """A provider that exposes its wire body and accepts a schema-repair prompt.

    Only a provider that composes prompts can be asked to repair one. An offline
    provider returns an already validated result, so it can neither produce an invalid
    answer nor be repaired, and the service uses the plain port for it.
    """

    async def call(self, request: CognitionRequest) -> ProviderCall: ...

    async def repair_call(
        self,
        request: CognitionRequest,
        *,
        invalid_content: str,
        error: str,
    ) -> ProviderCall: ...


@dataclass(frozen=True, slots=True)
class CognitionBudget:
    """How much cognition one run may spend, per agent and in total.

    A budget bounds DISPATCHES. A cache hit spends nothing, because replaying a recorded
    run must not exhaust a budget it never spent, and one dispatch is one cognitive
    event however many transport retries it needed.

    Both values are bounded ABOVE as well as below, by the ceilings read off
    :class:`~adlife.config.models.SimulationSettings`. This class is the only thing that
    enforces specification section 7's "Maximum cognitive events: 6 per agent per run":
    nothing downstream re-checks it, so a budget constructed above the ceiling spends a
    seventh cognitive event without any error. It is refused for the same reason
    :class:`CognitionService` refuses a retry count above :data:`MAX_PROVIDER_RETRIES` and
    the provider refuses a timeout above its own maximum - a seam may lower a documented
    bound and may never raise one.
    """

    per_agent: int
    total: int

    def __post_init__(self) -> None:
        if not isinstance(self.per_agent, int) or isinstance(self.per_agent, bool):
            raise TypeError("per_agent must be an integer")
        if not isinstance(self.total, int) or isinstance(self.total, bool):
            raise TypeError("total must be an integer")
        if self.per_agent < 0 or self.total < 0:
            raise ValueError("a cognition budget must not be negative")
        if self.per_agent > MAX_COGNITION_PER_AGENT:
            raise ValueError(
                "a cognition budget may not exceed the documented "
                f"{MAX_COGNITION_PER_AGENT} cognitive events per agent per run"
            )
        if self.total > MAX_COGNITION_TOTAL:
            raise ValueError(
                "a cognition budget may not exceed the documented "
                f"{MAX_COGNITION_TOTAL} cognitive events per run"
            )


class CognitionResolution(CognitionModel):
    """One request's final answer, whatever produced it.

    ``source`` and ``fallback_reason`` are read off ``usage`` rather than stored beside
    it, so a resolution cannot claim to be a fallback while its usage record says
    otherwise. ``raw_response`` is redacted and bounded by the published helpers on the
    way in, because a resolution is handed to the event and report layers.
    """

    schema_version: Literal[1] = 1
    request_id: RequestId
    result: CognitionResult
    usage: ProviderUsage
    raw_response: str | None = Field(default=None, max_length=MAX_RAW_RESPONSE_CHARS)
    attempts: int = Field(ge=0, le=MAX_PROVIDER_ATTEMPTS + 1)

    @field_validator("raw_response", mode="before")
    @classmethod
    def redact_the_raw_provider_body(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return bound_raw_provider_body(value)

    @model_validator(mode="after")
    def bind_result_to_request(self) -> Self:
        if self.result.request_id != self.request_id:
            raise ValueError("a cognition resolution must answer its own request")
        return self

    @property
    def source(self) -> ProviderKind:
        return self.usage.provider_kind

    @property
    def fallback_reason(self) -> FallbackReason | None:
        return self.usage.fallback_reason


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one request's dispatch produced, before anything is committed."""

    attempts: int
    answer: CognitionAnswer | None = None
    raw_response: str | None = None
    error: CognitionError | None = None
    reason: FallbackReason | None = None
    cached: bool = False


async def _default_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _reason_for(error: CognitionError | None) -> FallbackReason:
    """Name the documented reason one failure falls back for.

    A :class:`ProviderCallError` carries its own reason, so the mapping lives with the
    failure. A cache miss is its own reason. Anything else is a provider that could not
    answer, which is what ``provider-unavailable`` means.
    """
    if isinstance(error, ProviderCallError):
        return error.fallback_reason
    if isinstance(error, CacheMiss):
        return "cache-miss"
    return "provider-unavailable"


def _first_error(group: BaseExceptionGroup[BaseException]) -> BaseException:
    """Unwrap a single-error task group so a caller sees the defect it actually hit."""
    flattened = group.exceptions
    while len(flattened) == 1 and isinstance(flattened[0], BaseExceptionGroup):
        flattened = flattened[0].exceptions
    return flattened[0] if len(flattened) == 1 else group


class CognitionService:
    """Resolve cognition requests within a run's budget, and never abort the run."""

    __slots__ = (
        "_attempts",
        "_budget",
        "_cache",
        "_fallback",
        "_oracle",
        "_sleep",
        "_spent",
        "_spent_total",
    )

    def __init__(
        self,
        *,
        fallback: CognitionProvider,
        budget: CognitionBudget,
        oracle: RandomOracle,
        cache: CognitionCache | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        retries: int = MAX_PROVIDER_RETRIES,
    ) -> None:
        """Wire the service. ``retries`` is the configured count, not a suggestion.

        ``retries`` exists so that :attr:`adlife.config.models.ProviderSettings.retries`
        has a consumer rather than a sentence promising a later task will honour it. It
        defaults to specification section 7's maximum, which is also that field's own
        default, so a caller that omits it keeps the documented behaviour; it is refused
        above that maximum, so the seam can lower the bound and never raise it.
        """
        if not isinstance(budget, CognitionBudget):
            raise TypeError("budget must be a CognitionBudget")
        if not isinstance(retries, int) or isinstance(retries, bool):
            raise TypeError("retries must be an integer")
        if not 0 <= retries <= MAX_PROVIDER_RETRIES:
            raise ValueError(f"retries must be between 0 and {MAX_PROVIDER_RETRIES}")
        if not isinstance(oracle, RandomOracle):
            raise TypeError("oracle must be a RandomOracle")
        if cache is not None and not isinstance(cache, CognitionCache):
            raise TypeError("cache must be a CognitionCache")
        if not isinstance(fallback, CognitionProvider):
            raise TypeError("fallback must implement the cognition provider port")
        self._fallback = fallback
        self._budget = budget
        self._oracle = oracle
        self._cache = cache
        self._sleep: Callable[[float], Awaitable[None]] = _default_sleep if sleep is None else sleep
        self._attempts = retries + 1
        self._spent: dict[str, int] = {}
        self._spent_total = 0

    def replace_fallback(self, fallback: CognitionProvider) -> None:
        """Swap the terminal rule fallback, keeping every budget already spent.

        A run wires its service once and hands the service the terminal rule provider
        for each tick, because the rule formula can only answer requests whose inputs
        that tick's plan actually contains. Replacing the service per tick would reset
        the budget counters and let later ticks re-spend cognition the run no longer
        has, so the fallback travels to the service instead and the service keeps the
        run's books. A service mid-``evaluate_many`` is not required to switch cleanly;
        callers replace the fallback between batches, which is where the runner and the
        CLI ports call this.
        """
        if not isinstance(fallback, CognitionProvider):
            raise TypeError("fallback must implement the cognition provider port")
        self._fallback = fallback

    @property
    def retries(self) -> int:
        """How many retries after the initial attempt this service is configured for."""
        return self._attempts - 1

    @property
    def spent_total(self) -> int:
        """How many provider dispatches this run has made."""
        return self._spent_total

    def spent_for(self, agent_id: str) -> int:
        return self._spent.get(agent_id, 0)

    async def evaluate_one(
        self,
        request: CognitionRequest,
        provider: CognitionProvider,
    ) -> CognitionResolution:
        """Resolve one request. It is exactly a batch of one, so it cannot drift."""
        resolved = await self.evaluate_many((request,), provider)
        return resolved[request.request_id]

    async def evaluate_many(
        self,
        requests: Iterable[CognitionRequest],
        provider: CognitionProvider,
    ) -> Mapping[str, CognitionResolution]:
        """Resolve a batch of requests and return them keyed in ``request_id`` order.

        WHO WINS A SCARCE BUDGET, stated rather than left to emerge. Budget is reserved
        in ``request_id`` order, and a request identifier is
        ``<run>:event-<sequence>`` - the stable identifier of the event that CAUSED the
        request. Ordering by it is therefore chronological by cause, not alphabetical by
        agent. Inside a single tick the exposure allocator hands out sequences in agent
        order, so a total budget that binds mid-tick does favour the agents that were
        allocated first; the per-agent budget is the bound that governs fairness across
        a run, and it is checked independently. A random draw would be worse here for
        two reasons: it puts a resource bound under stochastic control, and it
        decorrelates the paired treatment and control arms the experiment harness needs.
        """
        ordered = self._ordered(requests)
        metadata = (
            provider.provider_metadata if isinstance(provider, DescribedCognitionProvider) else None
        )

        outcomes: dict[str, _Outcome] = {}
        pending: list[CognitionRequest] = []
        for request in ordered:
            cached = self._cache_hit(request, metadata)
            if cached is not None:
                outcomes[request.request_id] = cached
            else:
                pending.append(request)

        dispatchable: list[CognitionRequest] = []
        for request in pending:
            if self._reserve(request.agent_id):
                dispatchable.append(request)
            else:
                logger.info(
                    "cognition budget exhausted for %s; %s falls back to the rule formula",
                    request.agent_id,
                    request.request_id,
                )
                outcomes[request.request_id] = _Outcome(attempts=0, reason="budget-exhausted")

        if dispatchable:
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROVIDER_CALLS)
            try:
                async with asyncio.TaskGroup() as group:
                    for request in dispatchable:
                        group.create_task(
                            self._record(outcomes, request, provider, semaphore),
                            name=f"cognition:{request.request_id}",
                        )
            except BaseExceptionGroup as group_error:
                raise _first_error(group_error) from None

        return await self._commit(ordered, outcomes, metadata)

    def _ordered(self, requests: Iterable[CognitionRequest]) -> tuple[CognitionRequest, ...]:
        materialized = tuple(requests)
        for request in materialized:
            if not isinstance(request, CognitionRequest):
                raise TypeError("requests must be CognitionRequest values")
        identifiers = [request.request_id for request in materialized]
        duplicates = sorted({name for name in identifiers if identifiers.count(name) > 1})
        if duplicates:
            raise DuplicateCognitionRequest(
                f"a cognition batch named these requests more than once: {', '.join(duplicates)}"
            )
        return tuple(sorted(materialized, key=lambda request: request.request_id))

    def _reserve(self, agent_id: str) -> bool:
        """Spend one unit of budget, or report that there is none left."""
        if self._spent_total >= self._budget.total:
            return False
        if self._spent.get(agent_id, 0) >= self._budget.per_agent:
            return False
        self._spent[agent_id] = self._spent.get(agent_id, 0) + 1
        self._spent_total += 1
        return True

    def _cache_hit(
        self,
        request: CognitionRequest,
        metadata: ProviderMetadata | None,
    ) -> _Outcome | None:
        """Serve a recorded answer, or report a miss. A bad record is never trusted."""
        if self._cache is None or metadata is None:
            return None
        key = self._cache.make_key(request, metadata)
        try:
            record = self._cache.get(key)
        except CorruptCacheRecord as error:
            logger.warning(
                "discarding cognition cache record %s: %s", key, redact_provider_body(str(error))
            )
            return None
        if record is None or record.request != request:
            return None
        return _Outcome(
            attempts=0,
            answer=CognitionAnswer(
                result=record.result,
                usage=record.usage.model_copy(update={"cache_hit": True}),
            ),
            raw_response=record.raw_response,
            cached=True,
        )

    async def _record(
        self,
        outcomes: dict[str, _Outcome],
        request: CognitionRequest,
        provider: CognitionProvider,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            outcomes[request.request_id] = await self._dispatch(request, provider)

    async def _dispatch(
        self,
        request: CognitionRequest,
        provider: CognitionProvider,
    ) -> _Outcome:
        """Attempt one request, retrying and repairing within the documented bounds."""
        last_error: CognitionError | None = None
        raw_response: str | None = None
        attempts = 0
        for attempt in range(1, self._attempts + 1):
            attempts = attempt
            try:
                call = await self._invoke(provider, request)
            except ProviderCallError as error:
                last_error = error
                raw_response = error.raw_response or raw_response
                logger.warning(
                    "cognition attempt %d for %s failed: %s", attempt, request.request_id, error
                )
                if not error.retryable or attempt == self._attempts:
                    break
                await self._sleep(self._retry_delay(request, attempt))
                continue
            except CognitionError as error:
                last_error = error
                logger.warning(
                    "cognition attempt %d for %s failed: %s",
                    attempt,
                    request.request_id,
                    redact_provider_body(str(error)),
                )
                break
            return _Outcome(attempts=attempt, answer=call.answer, raw_response=call.raw_response)

        if isinstance(last_error, InvalidProviderResponse) and isinstance(
            provider, RepairableCognitionProvider
        ):
            attempts += 1
            try:
                call = await provider.repair_call(
                    request,
                    # Some invalid answers have no body to echo - a malformed envelope
                    # carries no message content at all - and an empty assistant turn is
                    # a message several endpoints refuse outright.
                    invalid_content=last_error.raw_response or NO_BODY_PLACEHOLDER,
                    error=str(last_error),
                )
            except ProviderCallError as error:
                last_error = error
                raw_response = error.raw_response or raw_response
                logger.warning(
                    "the cognition repair attempt for %s failed: %s", request.request_id, error
                )
            except CognitionError as error:
                last_error = error
                logger.warning(
                    "the cognition repair attempt for %s failed: %s",
                    request.request_id,
                    redact_provider_body(str(error)),
                )
            else:
                return _Outcome(
                    attempts=attempts,
                    answer=call.answer,
                    raw_response=call.raw_response,
                )

        return _Outcome(
            attempts=attempts,
            raw_response=raw_response,
            error=last_error,
            reason=_reason_for(last_error),
        )

    async def _invoke(
        self,
        provider: CognitionProvider,
        request: CognitionRequest,
    ) -> ProviderCall:
        if isinstance(provider, RepairableCognitionProvider):
            return await provider.call(request)
        return ProviderCall(answer=await provider.answer(request), raw_response=None)

    def _retry_delay(self, request: CognitionRequest, attempt: int) -> float:
        """The documented base delay for this attempt plus keyed deterministic jitter.

        Every term of the key matters: the campaign completes the namespace, and the
        agent, the simulated minute and the attempt index are the oracle's three
        coordinates. Two agents retrying in the same minute therefore wait different
        amounts, and so do the first and second retry of one request.
        """
        index = min(attempt, len(RETRY_BASE_DELAYS_SECONDS)) - 1
        draw = self._oracle.uniform(
            f"{RETRY_NAMESPACE}:{request.campaign_id}",
            request.agent_id,
            request.simulated_minute,
            attempt,
        )
        return RETRY_BASE_DELAYS_SECONDS[index] + RETRY_JITTER_SECONDS * draw

    async def _commit(
        self,
        ordered: tuple[CognitionRequest, ...],
        outcomes: Mapping[str, _Outcome],
        metadata: ProviderMetadata | None,
    ) -> Mapping[str, CognitionResolution]:
        """Turn outcomes into resolutions, in ``request_id`` order and nowhere else.

        Committing here rather than inside a task is what makes a concurrent batch
        reproducible: the mapping order, the cache writes and the fallback composition
        are all functions of the batch, not of which call happened to answer first.
        """
        resolved: dict[str, CognitionResolution] = {}
        for request in ordered:
            outcome = outcomes[request.request_id]
            if outcome.answer is not None:
                resolution = CognitionResolution(
                    request_id=request.request_id,
                    result=outcome.answer.result,
                    usage=outcome.answer.usage,
                    raw_response=outcome.raw_response,
                    attempts=outcome.attempts,
                )
                if not outcome.cached:
                    self._store(request, metadata, outcome)
            else:
                resolution = await self._fallback_resolution(request, outcome)
            resolved[request.request_id] = resolution
        return resolved

    def _store(
        self,
        request: CognitionRequest,
        metadata: ProviderMetadata | None,
        outcome: _Outcome,
    ) -> None:
        """Record one fresh provider answer. A fallback is deliberately never stored.

        Caching a fallback would turn one transient timeout into a permanent answer for
        every later run of the same scenario, which is the opposite of what the cache is
        for.
        """
        if self._cache is None or metadata is None or outcome.answer is None:
            return
        key = self._cache.make_key(request, metadata)
        self._cache.put(
            key,
            CognitionRecord(
                key=key,
                request=request,
                provider_metadata=metadata,
                raw_response=outcome.raw_response,
                result=outcome.answer.result,
                usage=outcome.answer.usage,
            ),
        )

    async def _fallback_resolution(
        self,
        request: CognitionRequest,
        outcome: _Outcome,
    ) -> CognitionResolution:
        """Answer from the transparent rule formula and name why.

        A failure here is NOT absorbed. The rule provider is the last step before a run
        has no answer at all, so an unwired or unrepresentable fallback raises its
        :class:`~adlife.core.ports.cognition.CognitionError` to the caller instead of
        being disguised as a resolution nobody computed.
        """
        reason: FallbackReason = outcome.reason or _reason_for(outcome.error)
        answer = await self._fallback.answer(request)
        logger.info(
            "cognition for %s fell back to the rule formula (%s)", request.request_id, reason
        )
        return CognitionResolution(
            request_id=request.request_id,
            result=answer.result,
            usage=answer.usage.model_copy(
                update={"provider_kind": "fallback", "fallback_reason": reason, "cache_hit": False}
            ),
            raw_response=outcome.raw_response,
            attempts=outcome.attempts,
        )


__all__ = [
    "MAX_COGNITION_PER_AGENT",
    "MAX_COGNITION_TOTAL",
    "MAX_CONCURRENT_PROVIDER_CALLS",
    "MAX_PROVIDER_ATTEMPTS",
    "MAX_PROVIDER_RETRIES",
    "NO_BODY_PLACEHOLDER",
    "RETRY_BASE_DELAYS_SECONDS",
    "RETRY_JITTER_SECONDS",
    "RETRY_NAMESPACE",
    "CognitionBudget",
    "CognitionResolution",
    "CognitionService",
    "DescribedCognitionProvider",
    "DuplicateCognitionRequest",
    "RepairableCognitionProvider",
]
