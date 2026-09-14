"""The offline mock cognition provider.

It exercises the complete cognition pipeline with five fixed fixtures and no model. It
reads no clock, no socket, no environment variable and no global random stream: the
fixture is chosen by a content-addressed digest of the request identifier and the
canonical request JSON, so the same question always draws the same answer.
"""

from __future__ import annotations

from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    CognitionResult,
    ProviderUsage,
)
from adlife.core.simulation.engine import canonical_sha256

MOCK_MODEL_ID = "mock-v1"

_FIXTURE_REQUEST_ID = "mock-fixture:event-00000000"
"""A placeholder identity; every answer rebinds it to the request it answers."""

_FIXTURES: tuple[CognitionResult, ...] = (
    CognitionResult(
        request_id=_FIXTURE_REQUEST_ID,
        interpretation="A calm, uncluttered pitch that invites a second look.",
        emotion="curious",
        valence=0.32,
        relevance=0.61,
        credibility=0.68,
        sentiment_delta=0.08,
        recall_delta=0.17,
        purchase_reason="Interesting enough to look up later, not enough to act now.",
        share_probability=0.11,
        discussion_hook="Worth asking a friend whether the quiet design holds up.",
        grounded_reasons=(
            "the message matches a stated interest",
            "the claim is specific rather than sweeping",
            "this is an early exposure",
        ),
        memory_summary="A calm advertisement that read as an invitation rather than a push.",
        rule_modifier=0.04,
        safety_flags=(),
    ),
    CognitionResult(
        request_id=_FIXTURE_REQUEST_ID,
        interpretation="A warm, concrete promise that lands well in this moment.",
        emotion="positive",
        valence=0.64,
        relevance=0.72,
        credibility=0.74,
        sentiment_delta=0.15,
        recall_delta=0.24,
        purchase_reason="The stated benefit answers a need that is already present.",
        share_probability=0.23,
        discussion_hook="The promise is concrete enough to repeat out loud.",
        grounded_reasons=(
            "the benefit is concrete",
            "the framing suits the current activity",
            "the tone is unhurried",
        ),
        memory_summary="A warm advertisement whose promise felt concrete and unhurried.",
        rule_modifier=0.07,
        safety_flags=(),
    ),
    CognitionResult(
        request_id=_FIXTURE_REQUEST_ID,
        interpretation="An unremarkable message that passes without friction.",
        emotion="neutral",
        valence=0.02,
        relevance=0.34,
        credibility=0.55,
        sentiment_delta=0.01,
        recall_delta=0.06,
        purchase_reason="Nothing in the message argues for or against acting.",
        share_probability=0.02,
        discussion_hook="Little here that would start a conversation.",
        grounded_reasons=(
            "the message is generic",
            "the moment is distracted",
        ),
        memory_summary="An ordinary advertisement that left almost no trace.",
        rule_modifier=0.0,
        safety_flags=(),
    ),
    CognitionResult(
        request_id=_FIXTURE_REQUEST_ID,
        interpretation="A broad claim with no supporting detail, so it invites doubt.",
        emotion="skeptical",
        valence=-0.28,
        relevance=0.41,
        credibility=0.22,
        sentiment_delta=-0.09,
        recall_delta=0.09,
        purchase_reason="The claim is too broad to justify spending on it.",
        share_probability=0.05,
        discussion_hook="The claim would need evidence before repeating it.",
        grounded_reasons=(
            "the claim is unsupported",
            "the superlative overreaches",
            "prior advertising set a low expectation",
        ),
        memory_summary="A sweeping advertisement claim that invited doubt rather than trust.",
        rule_modifier=-0.04,
        safety_flags=(),
    ),
    CognitionResult(
        request_id=_FIXTURE_REQUEST_ID,
        interpretation="A repetitive interruption that arrives at an unwelcome moment.",
        emotion="annoyed",
        valence=-0.61,
        relevance=0.18,
        credibility=0.30,
        sentiment_delta=-0.18,
        recall_delta=0.04,
        purchase_reason="Repetition has made the offer less appealing, not more.",
        share_probability=0.01,
        discussion_hook="Only worth mentioning as a complaint about repetition.",
        grounded_reasons=(
            "the same message has been seen repeatedly",
            "the interruption fits the moment badly",
        ),
        memory_summary="A repeated interruption that became irritating rather than persuasive.",
        rule_modifier=-0.08,
        safety_flags=(),
    ),
)

MOCK_FIXTURE_COUNT = len(_FIXTURES)


class MockCognitionProvider:
    """Deterministically map a cognition request onto one of five fixed fixtures.

    ``seed`` lets one scenario be exercised against a different fixture sequence without
    editing the request. It defaults to zero because a mock that varied by default would
    not be reproducible.
    """

    __slots__ = ("_seed",)

    def __init__(self, *, seed: int = 0) -> None:
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        self._seed = seed

    def fixture_index(self, request: CognitionRequest) -> int:
        """Select a fixture from the request identifier and the canonical request JSON.

        This is the published rule: every term of the request participates, so a mock
        answer moves with the mood the agent is in and the memories it carries, exactly
        as a model's answer would. ``request_id`` is named separately as well as being a
        field of the digested request, because the identifier is what a caller reasons
        about when it reads this selection back.

        An earlier revision digested a six-term treatment tuple instead, to hold common
        random numbers across the paired arms of specification section 18. That kept the
        pairing at the cost of a mock that could not react to mood or memory at all, and
        it was an unratified departure from an explicit directive; the pairing belongs to
        the experiment harness rather than to the provider. Determinism is unaffected
        either way: the key is a pure function of the request and the provider seed.
        """
        if not isinstance(request, CognitionRequest):
            raise TypeError("request must be a CognitionRequest")
        digest = canonical_sha256(
            {
                "request_id": request.request_id,
                "request_sha256": canonical_sha256(request),
                "seed": self._seed,
            }
        )
        return int(digest, 16) % MOCK_FIXTURE_COUNT

    async def evaluate(self, request: CognitionRequest) -> CognitionResult:
        fixture = _FIXTURES[self.fixture_index(request)]
        return fixture.model_copy(update={"request_id": request.request_id})

    async def answer(self, request: CognitionRequest) -> CognitionAnswer:
        """Report the fixture and the provenance an event must carry with it.

        The counts are zero because they are true: no model was called, so no tokens were
        spent, and an offline provider may not read a clock to time itself. Reporting an
        invented estimate would let Task 14 and Task 16 present a fabricated cost.
        """
        return CognitionAnswer(
            result=await self.evaluate(request),
            usage=ProviderUsage(
                provider_kind="mock",
                model_id=MOCK_MODEL_ID,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                cache_hit=False,
            ),
        )


__all__ = ["MOCK_FIXTURE_COUNT", "MOCK_MODEL_ID", "MockCognitionProvider"]
