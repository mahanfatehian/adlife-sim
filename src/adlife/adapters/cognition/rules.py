"""The transparent cognition provider: it runs the documented rule formula, nothing more."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import ValidationError

from adlife.core.domain.campaign import Campaign, Placement
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionError,
    CognitionRequest,
    CognitionResult,
    Emotion,
    ProviderUsage,
)
from adlife.core.simulation.decision import RuleResponse, evaluate_rule_response

RULE_MODEL_ID = "rule-v1"

ANNOYED_VALENCE = -0.35
POSITIVE_VALENCE = 0.35
SKEPTICAL_CREDIBILITY = 0.35
CURIOUS_RELEVANCE = 0.50


class UnknownCognitionRequest(CognitionError):
    """Raised when no rule response was supplied for a cognition request.

    The provider fails closed on purpose. Returning a neutral placeholder would let an
    unwired caller silently receive an answer that no rule produced.
    """


class UnrepresentableRuleResult(CognitionError):
    """Raised when a rule answer cannot be expressed as a valid :class:`CognitionResult`.

    This exists because of the one thing specification section 12 forbids absolutely:
    "provider failures must never abort a run". A service implements that by catching
    :class:`CognitionError` around the terminal fallback, so the fallback may not raise
    anything else - and it used to raise a bare ``pydantic.ValidationError`` whenever the
    answer boundary refused the text it composed.

    The live cause was an asymmetry between two screens. A campaign identifier is
    admitted into the prompt under the NARROW rule, and ``memory_summary`` names that
    identifier while keeping the STRICT persona rule. An all-digit campaign slug such as
    ``1234567890`` is legal under the domain rule ``^[a-z0-9][a-z0-9-]{0,79}$`` and is a
    bare national-identifier shape, so the composed summary is refused. The common case -
    a slug whose digits merely continue a hyphenated token, ``spring-1234567890`` - was
    closed in :mod:`adlife.core.domain.person`; what survives is closed here, by making
    the refusal a :class:`CognitionError` a caller can actually handle.
    """


class MismatchedRuleResponse(CognitionError):
    """Raised when the rule inputs supplied for a request answer a different question.

    This provider is the terminal deterministic fallback, so every refusal it makes stays
    inside the :class:`CognitionError` hierarchy: a service catching that base class in
    order to honour specification section 12 - "provider failures must never abort a
    run" - must be able to catch a mis-wired rule response too, exactly as it catches
    :class:`UnknownCognitionRequest` and replay's ``CorruptCacheRecord``.
    """


def _fit(text: str, limit: int) -> str:
    """Keep composed text inside its documented bound without ever raising."""
    return text if len(text) <= limit else text[:limit]


def rule_emotion(response: RuleResponse) -> Emotion:
    """Label a rule response with one of the five documented emotions.

    The bands are ordered and total: strongly negative valence is ``annoyed``; any
    negative valence or low credibility is ``skeptical``; strongly positive valence is
    ``positive``; a relevant remainder is ``curious``; everything else is ``neutral``.
    """
    if response.valence <= ANNOYED_VALENCE:
        return "annoyed"
    if response.valence < 0.0 or response.credibility < SKEPTICAL_CREDIBILITY:
        return "skeptical"
    if response.valence >= POSITIVE_VALENCE:
        return "positive"
    if response.relevance >= CURIOUS_RELEVANCE:
        return "curious"
    return "neutral"


def rule_cognition_result(request: CognitionRequest, response: RuleResponse) -> CognitionResult:
    """Restate one rule response as a cognition result.

    Every number is carried through unchanged from
    :func:`adlife.core.simulation.decision.evaluate_rule_response`; no formula is
    duplicated here. ``rule_modifier`` is exactly zero because the rule response already
    IS the baseline - a rule provider that modified it would apply the rule twice.

    Every refusal this function makes is a :class:`CognitionError`. The answer boundary
    can still object to text composed from a legal campaign slug, and when it does the
    ``pydantic.ValidationError`` is translated into :class:`UnrepresentableRuleResult`
    rather than escaping: this is the terminal fallback, and specification section 12
    requires that a caller catching :class:`CognitionError` cannot have its run aborted
    by it. ``TypeError`` for a wrongly typed argument is deliberately NOT translated - a
    caller passing the wrong type has a defect, not a provider failure.
    """
    if not isinstance(request, CognitionRequest):
        raise TypeError("request must be a CognitionRequest")
    if not isinstance(response, RuleResponse):
        raise TypeError("response must be a RuleResponse")
    campaign_id = request.campaign_id
    if response.campaign_id != campaign_id:
        raise MismatchedRuleResponse(
            "rule response answers another campaign: "
            f"{response.campaign_id} rather than {campaign_id}"
        )

    emotion = rule_emotion(response)
    try:
        return _compose_rule_result(request, response, emotion)
    except ValidationError as error:
        rejected = error.error_count()
        # The count is composed here and the chain is dropped: reporting how many fields
        # were refused and not which values is pointless while ``__cause__`` carries a
        # ``ValidationError`` that renders every one of them into any traceback. The
        # refused text is composed from campaign copy, which is untrusted input.
        raise UnrepresentableRuleResult(
            "the rule fallback composed an answer the cognition contract refuses for "
            f"request {request.request_id}: {rejected} field(s) rejected"
        ) from None


def _compose_rule_result(
    request: CognitionRequest,
    response: RuleResponse,
    emotion: Emotion,
) -> CognitionResult:
    """Compose the answer text and numbers; the caller owns the failure translation."""
    campaign_id = request.campaign_id
    return CognitionResult(
        request_id=request.request_id,
        interpretation=_fit(
            f"Rule cognition read {campaign_id} on {request.channel} as {emotion}; "
            f"relevance {response.relevance:.2f}, credibility {response.credibility:.2f}.",
            240,
        ),
        emotion=emotion,
        valence=response.valence,
        relevance=response.relevance,
        credibility=response.credibility,
        sentiment_delta=response.sentiment_delta,
        recall_delta=response.recall_delta,
        purchase_reason=_fit(
            f"Rule-derived purchase intention {response.purchase_intention:.3f}; "
            "a language model never supplies this probability.",
            240,
        ),
        share_probability=response.share_probability,
        discussion_hook=_fit(
            f"{emotion} about {campaign_id} after exposure {request.exposure_count}",
            200,
        ),
        grounded_reasons=(
            _fit(f"interest relevance {response.relevance:.2f}", 200),
            _fit(f"message credibility {response.credibility:.2f}", 200),
            _fit(f"recall delta {response.recall_delta:.2f}", 200),
        ),
        memory_summary=_fit(
            f"Noticed {campaign_id} on {request.channel} at exposure "
            f"{request.exposure_count}; sentiment {response.sentiment_delta:+.3f}, "
            f"recall {response.recall_delta:.3f}.",
            280,
        ),
        rule_modifier=0.0,
        safety_flags=(),
    )


@dataclass(frozen=True, slots=True)
class RuleCognitionInputs:
    """The four domain values :func:`evaluate_rule_response` needs for one request.

    A minimized prompt payload cannot be turned back into a
    :class:`~adlife.core.domain.person.PersonProfile`, a
    :class:`~adlife.core.domain.state.ConsumerState` and a
    :class:`~adlife.core.domain.campaign.Campaign` - that is the whole point of
    minimizing it - so the caller hands the adapter the domain values and the adapter
    runs the formula. The formula itself stays in
    :mod:`adlife.core.simulation.decision` and is never duplicated here.
    """

    profile: PersonProfile
    state: ConsumerState
    campaign: Campaign
    placement: Placement


class RuleCognitionProvider:
    """Answer a cognition request by running the documented rule formula.

    The provider is constructed with the rule inputs for the requests it will answer,
    keyed by ``request_id``, and derives every number from
    :func:`~adlife.core.simulation.decision.evaluate_rule_response`. Nothing about the
    answer is supplied by the caller.

    Inputs are checked at construction rather than at answering time, and
    :meth:`for_requests` builds a provider against a whole tick's requests and refuses
    there if one of them is unwired. This provider is the last step before a run aborts
    under specification section 12, so an unwired fallback has to be a construction-time
    error - before any event is minted - rather than a mid-run refusal.
    """

    __slots__ = ("_inputs",)

    def __init__(self, inputs: Mapping[str, RuleCognitionInputs]) -> None:
        if not isinstance(inputs, Mapping):
            raise TypeError("inputs must map a request_id to a RuleCognitionInputs")
        validated: dict[str, RuleCognitionInputs] = {}
        for request_id, entry in inputs.items():
            if not isinstance(request_id, str) or not request_id:
                raise TypeError("inputs must be keyed by a non-empty request_id")
            if not isinstance(entry, RuleCognitionInputs):
                raise TypeError("inputs must map a request_id to a RuleCognitionInputs")
            if entry.profile.agent_id != entry.state.agent_id:
                raise ValueError(
                    "rule inputs must describe the same agent: "
                    f"{entry.profile.agent_id} and {entry.state.agent_id} "
                    f"for request {request_id}"
                )
            if entry.placement not in entry.campaign.placements:
                raise ValueError(
                    f"rule inputs for request {request_id} name a placement "
                    f"that does not belong to campaign {entry.campaign.campaign_id}"
                )
            validated[request_id] = entry
        self._inputs: Mapping[str, RuleCognitionInputs] = MappingProxyType(validated)

    @classmethod
    def for_requests(
        cls,
        requests: Iterable[CognitionRequest],
        inputs: Mapping[str, RuleCognitionInputs],
    ) -> RuleCognitionProvider:
        """Build the terminal fallback for a tick and prove it covers every request."""
        provider = cls(inputs)
        for request in requests:
            if not isinstance(request, CognitionRequest):
                raise TypeError("requests must be CognitionRequest values")
            if request.request_id not in provider._inputs:
                raise UnknownCognitionRequest(
                    "the terminal rule fallback has no rule inputs for cognition request "
                    f"{request.request_id}"
                )
        return provider

    def rule_response_for(self, request: CognitionRequest) -> RuleResponse:
        """Run :func:`evaluate_rule_response` for the request's own wired inputs."""
        if not isinstance(request, CognitionRequest):
            raise TypeError("request must be a CognitionRequest")
        entry = self._inputs.get(request.request_id)
        if entry is None:
            raise UnknownCognitionRequest(
                f"no rule inputs were supplied for cognition request {request.request_id}"
            )
        if entry.profile.agent_id != request.agent_id:
            raise MismatchedRuleResponse(
                "rule inputs answer another agent: "
                f"{entry.profile.agent_id} rather than {request.agent_id}"
            )
        return evaluate_rule_response(
            entry.profile,
            entry.state,
            entry.campaign,
            entry.placement,
        )

    async def evaluate(self, request: CognitionRequest) -> CognitionResult:
        return rule_cognition_result(request, self.rule_response_for(request))

    async def answer(self, request: CognitionRequest) -> CognitionAnswer:
        """Report the rule answer and the provenance an event must carry with it.

        The counts are zero because they are true: the rule formula spends no tokens, and
        an offline provider may not read a clock to time itself. A caller that uses this
        provider as specification section 12's terminal fallback restamps the kind and
        names the reason it fell back for.
        """
        return CognitionAnswer(
            result=await self.evaluate(request),
            usage=ProviderUsage(
                provider_kind="rule",
                model_id=RULE_MODEL_ID,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                cache_hit=False,
            ),
        )


__all__ = [
    "ANNOYED_VALENCE",
    "CURIOUS_RELEVANCE",
    "POSITIVE_VALENCE",
    "RULE_MODEL_ID",
    "SKEPTICAL_CREDIBILITY",
    "MismatchedRuleResponse",
    "RuleCognitionInputs",
    "RuleCognitionProvider",
    "UnknownCognitionRequest",
    "UnrepresentableRuleResult",
    "rule_cognition_result",
    "rule_emotion",
]
