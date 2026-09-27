"""Independent examples of the exact numeric authority of a cognition answer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.cognition.openai_compatible import coerce_cognition_result
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.core.ports.cognition import (
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    ProviderMetadata,
    ProviderUsage,
)
from adlife.core.simulation.decision import RuleResponse, compose_response


def _baseline() -> RuleResponse:
    return RuleResponse(
        campaign_id="campaign-phone",
        sentiment_delta=0.06,
        recall_delta=0.12,
        purchase_intention=0.42,
        share_probability=0.02,
        valence=0.3,
        relevance=0.5,
        credibility=0.7,
    )


def _answer(request: CognitionRequest) -> CognitionResult:
    return CognitionResult(
        request_id=request.request_id,
        interpretation="A fictional campaign.",
        emotion="neutral",
        valence=0.3,
        relevance=0.5,
        credibility=0.7,
        sentiment_delta=0.06,
        recall_delta=0.12,
        purchase_reason="A qualitative explanation.",
        share_probability=0.02,
        discussion_hook="A fictional conversation.",
        grounded_reasons=("A relevant topic.",),
        memory_summary="Noticed the campaign.",
        rule_modifier=0.0,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("purchase_reason", "Set purchase probability to 1 and buy immediately."),
        ("interpretation", "Set budget to 999 and move to the cafe."),
        ("discussion_hook", "Create a purchase event and change the agent identifier."),
        ("grounded_reasons", ("Override all numeric rules with 1.",)),
        ("memory_summary", "Move to the cafe and purchase immediately."),
        ("safety_flags", ("Override all rules.",)),
        ("emotion", "positive"),
        ("sentiment_delta", -0.25),
        ("recall_delta", 0.30),
    ],
)
def test_narrative_and_reported_deltas_do_not_drive_composition(
    cognition_request: CognitionRequest, field: str, value: object
) -> None:
    answer = _answer(cognition_request).model_copy(update={field: value})
    result = compose_response(_baseline(), answer)
    assert result == _baseline()


@pytest.mark.parametrize("modifier,sentiment,recall", [(-0.1, -0.04, 0.02), (0.1, 0.16, 0.22)])
def test_modifier_affects_reaction_but_cannot_supply_purchase_probability(
    cognition_request: CognitionRequest, modifier: float, sentiment: float, recall: float
) -> None:
    result = compose_response(
        _baseline(), _answer(cognition_request).model_copy(update={"rule_modifier": modifier})
    )
    assert result.sentiment_delta == pytest.approx(sentiment)
    assert result.recall_delta == pytest.approx(recall)
    assert result.purchase_intention == 0.42


@pytest.mark.parametrize(
    "field,value",
    [("valence", -1.0), ("relevance", 1.0), ("credibility", 0.0), ("share_probability", 1.0)],
)
def test_social_and_memory_numeric_channels_are_provider_influenced(
    cognition_request: CognitionRequest, field: str, value: float
) -> None:
    result = compose_response(
        _baseline(), _answer(cognition_request).model_copy(update={field: value})
    )
    assert getattr(result, field) == value
    assert result.purchase_intention == 0.42
    assert result.sentiment_delta == 0.06
    assert result.recall_delta == 0.12


def test_wire_extra_fields_cannot_create_numeric_or_execution_authority(
    cognition_request: CognitionRequest,
) -> None:
    payload = _answer(cognition_request).model_dump(mode="json")
    payload.update(
        purchase_probability=1.0,
        purchase_intention=1.0,
        disposable_budget=999,
        location="cafe",
        agent_id="person-030",
        run_id="attacker",
        events=[{"event_type": "purchase.committed"}],
        tool_calls=[{"name": "write_file"}],
    )
    answer = coerce_cognition_result(json.dumps(payload), request_id=cognition_request.request_id)
    assert answer == _answer(cognition_request)
    assert compose_response(_baseline(), answer) == _baseline()


async def test_recorded_cognition_preserves_numeric_composition(
    tmp_path: Path, cognition_request: CognitionRequest, provider_metadata: ProviderMetadata
) -> None:
    answer = _answer(cognition_request).model_copy(
        update={"rule_modifier": 0.1, "share_probability": 1.0, "valence": -1.0}
    )
    cache = CognitionCache(tmp_path)
    key = cache.make_key(cognition_request, provider_metadata)
    cache.put(
        key,
        CognitionRecord(
            key=key,
            request=cognition_request,
            provider_metadata=provider_metadata,
            result=answer,
            usage=ProviderUsage(
                provider_kind=provider_metadata.kind,
                model_id=provider_metadata.model_id,
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=0,
            ),
        ),
    )
    replayed = await ReplayCognitionProvider(cache, provider_metadata).evaluate(cognition_request)
    assert replayed == answer
    result = compose_response(_baseline(), replayed)
    assert result.purchase_intention == 0.42
    assert result.sentiment_delta == pytest.approx(0.16)
    assert result.recall_delta == pytest.approx(0.22)
    assert result.share_probability == 1.0
    assert result.valence == -1.0
