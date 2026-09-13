from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.state import ConsumerState, Memory


def _memory_module() -> ModuleType:
    try:
        return import_module("adlife.core.simulation.memory")
    except ModuleNotFoundError:
        pytest.fail("Task 8 memory behavior is not implemented", pytrace=False)


def _decision_module() -> ModuleType:
    try:
        return import_module("adlife.core.simulation.decision")
    except ModuleNotFoundError:
        pytest.fail("Task 8 decision behavior is not implemented", pytrace=False)


def _memory(
    memory_id: str,
    *,
    salience: float,
    created_minute: int,
    kind: str = "advertising",
    campaign_id: str | None = "campaign-phone",
) -> Memory:
    return Memory(
        memory_id=memory_id,
        created_minute=created_minute,
        kind=kind,
        summary=f"A fictional impression recorded as {memory_id}.",
        salience=salience,
        campaign_id=campaign_id,
        caused_by_event_ids=(f"event-{memory_id}",),
    )


def _state(
    *,
    memories: tuple[Memory, ...] = (),
    recall_strength: float = 0.0,
    daily_reinforcement: float = 0.0,
    ad_fatigue: float = 0.0,
) -> ConsumerState:
    return ConsumerState(
        agent_id="person-001",
        location="online",
        activity="reflection",
        mood=0.0,
        fatigue=0.4,
        brand_sentiment=0.1,
        recall_strength=recall_strength,
        purchase_intention=0.2,
        memories=memories,
        cognition_budget_remaining=6,
        ad_fatigue=ad_fatigue,
        daily_reinforcement=daily_reinforcement,
    )


def _five_memories() -> tuple[Memory, ...]:
    return (
        _memory("m-1", salience=0.9, created_minute=100),
        _memory("m-2", salience=0.8, created_minute=200),
        _memory("m-3", salience=0.7, created_minute=300),
        _memory("m-4", salience=0.6, created_minute=400),
        _memory("m-5", salience=0.5, created_minute=500),
    )


def test_encode_memory_appends_the_new_memory() -> None:
    memory = _memory_module()
    new_memory = _memory("m-9", salience=0.4, created_minute=600)

    updated = memory.encode_memory(_state(), new_memory)

    assert updated.memories == (new_memory,)


def test_encode_memory_retains_only_five_episodic_entries() -> None:
    memory = _memory_module()

    updated = memory.encode_memory(
        _state(memories=_five_memories()),
        _memory("m-6", salience=0.95, created_minute=600),
    )

    assert len(updated.memories) == 5


def test_encode_memory_drops_the_weakest_entry_when_full() -> None:
    memory = _memory_module()

    updated = memory.encode_memory(
        _state(memories=_five_memories()),
        _memory("m-6", salience=0.95, created_minute=600),
    )

    assert [item.memory_id for item in updated.memories] == ["m-6", "m-1", "m-2", "m-3", "m-4"]


def test_encode_memory_discards_a_weaker_new_memory_when_full() -> None:
    memory = _memory_module()

    updated = memory.encode_memory(
        _state(memories=_five_memories()),
        _memory("m-6", salience=0.1, created_minute=600),
    )

    assert [item.memory_id for item in updated.memories] == ["m-1", "m-2", "m-3", "m-4", "m-5"]


def test_encode_memory_breaks_a_salience_tie_by_recency() -> None:
    memory = _memory_module()
    older = _memory("m-old", salience=0.5, created_minute=100)
    newer = _memory("m-new", salience=0.5, created_minute=900)

    updated = memory.encode_memory(_state(memories=(older,)), newer)

    assert [item.memory_id for item in updated.memories] == ["m-new", "m-old"]


def test_recency_outranks_the_identifier_when_salience_ties() -> None:
    """The tie is broken by recency; the identifier is only the last resort."""
    memory = _memory_module()
    older = _memory("m-a", salience=0.5, created_minute=100)
    newer = _memory("m-b", salience=0.5, created_minute=900)

    updated = memory.encode_memory(_state(memories=(older,)), newer)

    assert [item.memory_id for item in updated.memories] == ["m-b", "m-a"]


def test_the_cap_drops_the_older_of_two_equally_salient_memories() -> None:
    memory = _memory_module()
    state = _state(
        memories=(
            _memory("m-1", salience=0.9, created_minute=100),
            _memory("m-2", salience=0.8, created_minute=200),
            _memory("m-3", salience=0.7, created_minute=300),
            _memory("m-4", salience=0.6, created_minute=400),
            _memory("m-a", salience=0.5, created_minute=100),
        )
    )

    updated = memory.encode_memory(state, _memory("m-b", salience=0.5, created_minute=900))

    assert [item.memory_id for item in updated.memories] == ["m-1", "m-2", "m-3", "m-4", "m-b"]


def test_encode_memory_rejects_a_duplicate_memory_id() -> None:
    memory = _memory_module()
    existing = _memory("m-1", salience=0.5, created_minute=100)

    with pytest.raises(ValueError, match="memory_id"):
        memory.encode_memory(_state(memories=(existing,)), existing)


def test_encode_memory_rejects_a_value_that_is_not_a_memory() -> None:
    memory = _memory_module()

    with pytest.raises(TypeError, match="Memory"):
        memory.encode_memory(_state(), "a fictional recollection")


def test_daily_reflection_decays_recall_and_applies_reinforcement() -> None:
    memory = _memory_module()

    updated = memory.decay_memories(
        _state(recall_strength=0.8, daily_reinforcement=0.1),
        0,
    )

    assert updated.recall_strength == pytest.approx(0.8 * 0.85 + 0.1)


def test_daily_reflection_clears_the_daily_reinforcement() -> None:
    memory = _memory_module()

    updated = memory.decay_memories(
        _state(recall_strength=0.8, daily_reinforcement=0.1),
        0,
    )

    assert updated.daily_reinforcement == 0.0


def test_daily_reflection_keeps_recall_within_one() -> None:
    memory = _memory_module()

    updated = memory.decay_memories(
        _state(recall_strength=1.0, daily_reinforcement=0.2),
        0,
    )

    assert 0.0 <= updated.recall_strength <= 1.0
    assert updated.recall_strength == pytest.approx(1.0)


def test_daily_reflection_reduces_ad_fatigue_by_forty_percent() -> None:
    memory = _memory_module()

    updated = memory.decay_memories(_state(ad_fatigue=0.5), 0)

    assert updated.ad_fatigue == pytest.approx(0.3)


def test_daily_reflection_decays_the_salience_of_older_memories() -> None:
    memory = _memory_module()
    yesterday = _memory("m-1", salience=0.8, created_minute=600)

    updated = memory.decay_memories(_state(memories=(yesterday,)), 1)

    assert updated.memories[0].salience == pytest.approx(0.68)


def test_daily_reflection_leaves_memories_created_today_untouched() -> None:
    memory = _memory_module()
    today = _memory("m-1", salience=0.8, created_minute=1500)

    updated = memory.decay_memories(_state(memories=(today,)), 1)

    assert updated.memories[0].salience == pytest.approx(0.8)


def test_daily_reflection_reorders_memories_after_decay() -> None:
    memory = _memory_module()
    memories = (
        _memory("m-old", salience=0.8, created_minute=600),
        _memory("m-today", salience=0.75, created_minute=1500),
    )

    updated = memory.decay_memories(_state(memories=memories), 1)

    assert [item.memory_id for item in updated.memories] == ["m-today", "m-old"]


def test_daily_reflection_rejects_a_memory_created_after_the_reflection_day() -> None:
    memory = _memory_module()
    tomorrow = _memory("m-1", salience=0.8, created_minute=1500)

    with pytest.raises(ValueError, match="reflection day"):
        memory.decay_memories(_state(memories=(tomorrow,)), 0)


def test_daily_reflection_rejects_a_negative_day_index() -> None:
    memory = _memory_module()

    with pytest.raises(ValueError, match="day_index"):
        memory.decay_memories(_state(), -1)


def test_daily_reflection_rejects_a_boolean_day_index() -> None:
    memory = _memory_module()

    with pytest.raises(ValueError, match="day_index"):
        memory.decay_memories(_state(), True)


def _positive_response(decision: ModuleType, *, recall_delta: float = 0.2) -> Any:
    return decision.RuleResponse(
        campaign_id="campaign-phone",
        sentiment_delta=0.05,
        recall_delta=recall_delta,
        purchase_intention=0.4,
        share_probability=0.1,
        valence=0.25,
        relevance=0.5,
        credibility=0.7,
    )


def test_a_daily_reflection_never_raises_recall_above_what_it_banked() -> None:
    memory = _memory_module()
    decision = _decision_module()
    encoded = decision.apply_response(
        _state(),
        _positive_response(decision),
        ("event-0001",),
    ).state

    reflected = memory.decay_memories(encoded, 0)

    assert reflected.recall_strength <= (encoded.recall_strength + encoded.daily_reinforcement)


def test_a_daily_reflection_applies_the_days_reinforcement_exactly_once() -> None:
    memory = _memory_module()
    decision = _decision_module()
    encoded = decision.apply_response(
        _state(),
        _positive_response(decision),
        ("event-0001",),
    ).state

    reflected = memory.decay_memories(encoded, 0)

    assert encoded.recall_strength == pytest.approx(0.0)
    assert encoded.daily_reinforcement == pytest.approx(0.2)
    assert reflected.recall_strength == pytest.approx(0.2)


def test_a_daily_reflection_decays_the_recall_the_state_carries() -> None:
    memory = _memory_module()
    decision = _decision_module()
    yesterday = _state(recall_strength=0.4)
    encoded = decision.apply_response(
        yesterday,
        _positive_response(decision),
        ("event-0001",),
    ).state

    reflected = memory.decay_memories(encoded, 0)

    assert encoded.recall_strength == pytest.approx(0.4)
    assert reflected.recall_strength == pytest.approx(0.4 * 0.85 + 0.12)


def test_a_daily_reflection_decays_the_recall_it_reads_at_the_documented_rate() -> None:
    memory = _memory_module()
    decision = _decision_module()
    day_start = _state(recall_strength=0.4)
    encoded = decision.apply_response(
        day_start,
        _positive_response(decision),
        ("event-0001",),
    ).state

    reflected = memory.decay_memories(encoded, 0)

    assert encoded.daily_reinforcement == pytest.approx(0.12)
    assert reflected.recall_strength == pytest.approx(
        encoded.recall_strength * memory.RECALL_RETENTION + encoded.daily_reinforcement
    )
    assert reflected.recall_strength > encoded.recall_strength * memory.RECALL_RETENTION


def test_repeated_daily_encoding_saturates_recall_without_exceeding_one() -> None:
    memory = _memory_module()
    decision = _decision_module()
    state = _state()

    for day in range(40):
        state = decision.apply_response(
            state,
            _positive_response(decision, recall_delta=0.3),
            (f"event-{day:04d}",),
        ).state
        state = memory.decay_memories(state, day)
        assert 0.0 <= state.recall_strength <= 1.0

    assert state.recall_strength > 0.5


def test_a_daily_reflection_adds_no_more_than_the_documented_daily_cap() -> None:
    """The banked amount is bounded by the cap, whatever recall it starts from."""
    memory = _memory_module()
    decision = _decision_module()
    state = _state(recall_strength=0.05, daily_reinforcement=0.2)

    updated = memory.decay_memories(state, 0)

    assert updated.recall_strength <= (state.recall_strength + decision.MAX_DAILY_REINFORCEMENT)
    assert updated.recall_strength == pytest.approx(0.05 * 0.85 + 0.2)


def test_a_daily_reflection_decays_recall_whenever_nothing_was_banked() -> None:
    memory = _memory_module()
    grid = [index / 20 for index in range(21)]

    for recall_strength in grid:
        for daily_reinforcement in (value for value in grid if value <= 0.2):
            state = _state(
                recall_strength=recall_strength,
                daily_reinforcement=daily_reinforcement,
            )

            updated = memory.decay_memories(state, 0)

            if daily_reinforcement == 0.0:
                assert updated.recall_strength <= state.recall_strength
            assert updated.recall_strength <= min(1.0, state.recall_strength + daily_reinforcement)


def test_daily_reflection_applies_the_literal_recall_formula() -> None:
    """The documented reflection is ``recall * 0.85 + reinforcement`` on the state as it is."""
    memory = _memory_module()

    updated = memory.decay_memories(
        _state(recall_strength=0.8, daily_reinforcement=0.1),
        0,
    )

    assert updated.recall_strength == pytest.approx(0.8 * 0.85 + 0.1)


def test_the_daily_reflection_is_a_pure_function_of_recall_and_reinforcement() -> None:
    """No hidden day-start trace: the reflection reads only the two documented fields."""
    memory = _memory_module()
    grid = [index / 20 for index in range(21)]

    for recall_strength in grid:
        for daily_reinforcement in (value for value in grid if value <= 0.2):
            state = _state(
                recall_strength=recall_strength,
                daily_reinforcement=daily_reinforcement,
            )

            updated = memory.decay_memories(state, 0)

            assert updated.recall_strength == pytest.approx(
                min(1.0, recall_strength * 0.85 + daily_reinforcement)
            )


def test_a_daily_reflection_never_raises_recall_without_reinforcement() -> None:
    """With nothing banked the reflection can only decay."""
    memory = _memory_module()

    for index in range(21):
        recall_strength = index / 20
        state = _state(recall_strength=recall_strength, daily_reinforcement=0.0)

        updated = memory.decay_memories(state, 0)

        assert updated.recall_strength <= state.recall_strength
        assert updated.recall_strength == pytest.approx(recall_strength * 0.85)


def test_a_days_reinforcement_reaches_recall_exactly_once() -> None:
    """Encoding banks the reinforcement; only the reflection turns it into recall."""
    memory = _memory_module()
    decision = _decision_module()
    yesterday = _state(recall_strength=0.4)

    encoded = decision.apply_response(
        yesterday,
        _positive_response(decision),
        ("event-0001",),
    ).state
    reflected = memory.decay_memories(encoded, 0)

    assert encoded.recall_strength == pytest.approx(0.4)
    assert encoded.daily_reinforcement == pytest.approx(0.12)
    assert reflected.recall_strength == pytest.approx(0.4 * 0.85 + 0.12)


def _committed_proxy(decision: ModuleType, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "agent_id": "person-001",
        "campaign_id": "campaign-phone",
        "simulated_minute": 600,
        "decision_index": 0,
        "intention": 0.9,
        "threshold": 0.7,
        "price": 850.0,
        "budget_before": 900.0,
        "budget_after": 50.0,
        "random_draw": 0.1,
        "committed": True,
        "reason": "committed",
    }
    fields.update(overrides)
    return decision.PurchaseDecision(**fields)


def test_a_committed_purchase_proxy_produces_its_memory_record() -> None:
    """Ruling 8-B: the purchase proxy is a memory record, not a new event type."""
    memory = _memory_module()
    decision = _decision_module()

    record = memory.purchase_proxy_memory(
        _committed_proxy(decision),
        memory_id="run-proxy:memory-0001",
        caused_by_event_ids=("run-proxy:noticed-person-001",),
    )

    assert record.kind == "purchase-proxy"
    assert record.campaign_id == "campaign-phone"
    assert record.created_minute == 600
    assert record.caused_by_event_ids == ("run-proxy:noticed-person-001",)
    assert record.salience == pytest.approx(0.9)
    assert "sale" not in record.summary.lower()


def test_the_purchase_proxy_memory_record_carries_a_memory_created_payload() -> None:
    """Task 12 mints memory.created from this record; no purchase event type exists."""
    memory = _memory_module()
    decision = _decision_module()

    record = memory.purchase_proxy_memory(
        _committed_proxy(decision),
        memory_id="run-proxy:memory-0001",
        caused_by_event_ids=("run-proxy:noticed-person-001",),
    )
    event = DomainEvent(
        event_id="run-proxy:memory-created-person-001",
        run_id="run-proxy",
        simulated_minute=record.created_minute,
        sequence=0,
        event_type=EventType.MEMORY_CREATED,
        agent_id="person-001",
        campaign_id=record.campaign_id,
        payload=record.model_dump(mode="json"),
        source=EventSource.RULE,
        caused_by_event_ids=record.caused_by_event_ids,
    )

    assert event.payload["kind"] == "purchase-proxy"
    assert event.event_type is EventType.MEMORY_CREATED
    assert "sale" not in event.model_dump_json().lower()
    assert not any("purchase" in item.value for item in EventType)


def test_an_uncommitted_purchase_proxy_has_no_memory_record() -> None:
    memory = _memory_module()
    decision = _decision_module()
    refused = _committed_proxy(
        decision,
        committed=False,
        reason="no-active-need",
        random_draw=None,
        budget_after=900.0,
    )

    with pytest.raises(ValueError, match="committed"):
        memory.purchase_proxy_memory(
            refused,
            memory_id="run-proxy:memory-0001",
            caused_by_event_ids=("run-proxy:noticed-person-001",),
        )


def test_the_purchase_proxy_memory_record_encodes_into_consumer_state() -> None:
    memory = _memory_module()
    decision = _decision_module()

    record = memory.purchase_proxy_memory(
        _committed_proxy(decision),
        memory_id="run-proxy:memory-0001",
        caused_by_event_ids=("run-proxy:noticed-person-001",),
    )
    updated = memory.encode_memory(_state(), record)

    assert [item.kind for item in updated.memories] == ["purchase-proxy"]
    assert memory.PURCHASE_PROXY_MEMORY_KIND == "purchase-proxy"


def test_the_purchase_proxy_memory_record_rejects_an_empty_causal_chain() -> None:
    memory = _memory_module()
    decision = _decision_module()

    with pytest.raises(ValueError):
        memory.purchase_proxy_memory(
            _committed_proxy(decision),
            memory_id="run-proxy:memory-0001",
            caused_by_event_ids=(),
        )


def test_the_purchase_proxy_memory_record_rejects_a_value_that_is_not_a_decision() -> None:
    memory = _memory_module()

    with pytest.raises(TypeError, match="PurchaseDecision"):
        memory.purchase_proxy_memory(
            "campaign-phone",
            memory_id="run-proxy:memory-0001",
            caused_by_event_ids=("run-proxy:noticed-person-001",),
        )


def test_the_purchase_proxy_memory_record_rejects_a_string_causal_chain() -> None:
    memory = _memory_module()
    decision = _decision_module()

    with pytest.raises(TypeError, match="sequence of event identifiers"):
        memory.purchase_proxy_memory(
            _committed_proxy(decision),
            memory_id="run-proxy:memory-0001",
            caused_by_event_ids="run-proxy:noticed-person-001",
        )
