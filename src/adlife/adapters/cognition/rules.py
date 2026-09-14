"""The transparent cognition provider: it restates a rule response, nothing more."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from adlife.core.ports.cognition import (
    CognitionError,
    CognitionRequest,
    CognitionResult,
    Emotion,
)
from adlife.core.simulation.decision import RuleResponse

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


class MismatchedRuleResponse(CognitionError):
    """Raised when a supplied rule response answers a different campaign.

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


class RuleCognitionProvider:
    """Serve cognition from rule responses the caller already computed.

    The provider is constructed with the rule responses for the requests it will answer,
    keyed by ``request_id``. A minimized prompt payload cannot be turned back into a
    :class:`~adlife.core.domain.person.PersonProfile` and a
    :class:`~adlife.core.domain.campaign.Campaign`, so re-deriving the rule response
    inside the adapter is impossible; supplying it keeps the single formula in
    :mod:`adlife.core.simulation.decision`.
    """

    __slots__ = ("_responses",)

    def __init__(self, responses: Mapping[str, RuleResponse]) -> None:
        if not isinstance(responses, Mapping):
            raise TypeError("responses must map a request_id to a RuleResponse")
        validated: dict[str, RuleResponse] = {}
        for request_id, response in responses.items():
            if not isinstance(request_id, str) or not request_id:
                raise TypeError("responses must be keyed by a non-empty request_id")
            if not isinstance(response, RuleResponse):
                raise TypeError("responses must map a request_id to a RuleResponse")
            validated[request_id] = response
        self._responses: Mapping[str, RuleResponse] = MappingProxyType(validated)

    async def evaluate(self, request: CognitionRequest) -> CognitionResult:
        if not isinstance(request, CognitionRequest):
            raise TypeError("request must be a CognitionRequest")
        response = self._responses.get(request.request_id)
        if response is None:
            raise UnknownCognitionRequest(
                f"no rule response was supplied for cognition request {request.request_id}"
            )
        return rule_cognition_result(request, response)


__all__ = [
    "ANNOYED_VALENCE",
    "CURIOUS_RELEVANCE",
    "POSITIVE_VALENCE",
    "RULE_MODEL_ID",
    "SKEPTICAL_CREDIBILITY",
    "MismatchedRuleResponse",
    "RuleCognitionProvider",
    "UnknownCognitionRequest",
    "rule_cognition_result",
    "rule_emotion",
]
