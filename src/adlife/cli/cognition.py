"""The cognition ports the CLI wires behind the runner's seam.

Every mode a command can run - rules, mock, hybrid local/remote, replay - is one small
port answering the runner's :class:`~adlife.core.simulation.runner.RunCognitionPort`
protocol, plus the terminal rule fallback the specification requires. The adapters own
budget, retry and cache; the commands own wiring; the core sees only the port.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.cognition.rules import RuleCognitionProvider
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionProvider,
    CognitionRequest,
    ProviderMetadata,
)


class RuleCognitionPort:
    """Answer every request straight through the documented rule formula, in-process."""

    def __init__(self) -> None:
        self.answered: list[CognitionRequest] = []

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            self.answered.append(request)
            assert isinstance(fallback_provider, RuleCognitionProvider)
            answers[request.request_id] = await fallback_provider.answer(request)
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return None


class MockCognitionPort:
    """Answer through the deterministic mock provider: no budget, no network."""

    def __init__(self, *, seed: int) -> None:
        from adlife.adapters.cognition.mock import MockCognitionProvider

        self._provider = MockCognitionProvider(seed=seed)
        self.answered: list[CognitionRequest] = []

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            self.answered.append(request)
            answers[request.request_id] = await self._provider.answer(request)
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return None


class ReplayCognitionPort:
    """Serve every answer from the recorded cache; a miss is a provider error."""

    def __init__(self, cache: CognitionCache, provider_metadata: ProviderMetadata) -> None:
        from adlife.adapters.cognition.replay import ReplayCognitionProvider

        self._provider = ReplayCognitionProvider(cache, provider_metadata)

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            answers[request.request_id] = await self._provider.answer(request)
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return None


class ServiceCognitionPort:
    """Dispatch through the real CognitionService: budgets, retries, cache, fallback.

    The dispatched provider is constructed once per run; ``service_factory`` receives
    the terminal rule provider for the tick and returns a configured service, so retry
    counts, budgets and cache wiring stay the caller's decisions. Each ``resolve`` also
    hands the service that tick's fallback: the rule formula can only answer requests
    whose inputs this tick's plan contains, and a run's budget books live in the one
    service, so the fallback travels to the service rather than the service being
    rebuilt per tick. A service defect that survives its own fallback is a provider
    error.
    """

    def __init__(
        self,
        provider: Any,
        service_factory: Callable[[Any], Any],
    ) -> None:
        self._provider = provider
        self._service_factory = service_factory
        self._service: Any = None

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> Mapping[str, CognitionAnswer]:
        from adlife.adapters.cognition.service import CognitionService

        service = self._service
        if not isinstance(service, CognitionService):
            service = self._service_factory(fallback_provider)
            if not isinstance(service, CognitionService):
                raise RuntimeError("the cognition factory did not produce a CognitionService")
            self._service = service
        # Every tick: a fallback is only valid for the requests its plan contains, so
        # the service must never answer tick N from tick 1's rule inputs.
        if not isinstance(fallback_provider, CognitionProvider):
            raise RuntimeError("the cognition fallback did not implement the provider port")
        service.replace_fallback(fallback_provider)
        resolutions = await service.evaluate_many(requests, self._provider)
        return {
            request_id: CognitionAnswer(result=resolution.result, usage=resolution.usage)
            for request_id, resolution in resolutions.items()
        }

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        described = getattr(self._provider, "provider_metadata", None)
        return described if isinstance(described, ProviderMetadata) else None


def rule_fallback_for(plan: Any) -> RuleCognitionProvider:
    """Build the terminal rule fallback for one tick's planned requests."""
    from adlife.adapters.cognition.rules import RuleCognitionInputs

    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        opportunity = decision.opportunity
        inputs[decision.events[2].event_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return RuleCognitionProvider.for_requests(plan.requests, inputs)


def rule_inputs_for(plan: Any) -> dict[str, Any]:
    """The rule inputs per request id, for wiring a service that answers with rules."""
    from adlife.adapters.cognition.rules import RuleCognitionInputs

    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        opportunity = decision.opportunity
        inputs[decision.events[2].event_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return inputs


def cache_directory(root: Path) -> Path:
    """A project's cognition cache directory, created on demand."""
    directory = root / "cache"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


__all__ = [
    "MockCognitionPort",
    "ReplayCognitionPort",
    "RuleCognitionPort",
    "ServiceCognitionPort",
    "cache_directory",
    "rule_fallback_for",
    "rule_inputs_for",
]
