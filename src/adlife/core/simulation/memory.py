from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from adlife.core.domain.state import ConsumerState, Memory
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.decision import MAX_DAILY_REINFORCEMENT, PurchaseDecision
from adlife.core.simulation.policies import clamp

EPISODIC_MEMORY_LIMIT = 5
RECALL_RETENTION = 0.85
AD_FATIGUE_RETENTION = 0.60
SALIENCE_RETENTION = 0.85

MINUTES_PER_DAY = 1440
"""Minutes in one simulated day; the scope of the documented reinforcement cap."""

PURCHASE_PROXY_MEMORY_KIND: Literal["purchase-proxy"] = "purchase-proxy"
"""The episodic memory kind a committed purchase proxy is recorded as."""


def _memory_rank(memory: Memory) -> tuple[float, int, str]:
    """Strongest first, then most recent, then identifier for a stable order."""
    return (-memory.salience, -memory.created_minute, memory.memory_id)


def _retained(memories: Iterable[Memory]) -> tuple[Memory, ...]:
    return tuple(sorted(memories, key=_memory_rank))[:EPISODIC_MEMORY_LIMIT]


def purchase_proxy_memory(
    decision: PurchaseDecision,
    *,
    memory_id: str,
    caused_by_event_ids: Sequence[str],
) -> Memory:
    """Build the episodic record a committed purchase proxy leaves behind.

    The documented event catalog is fixed and carries no purchase-specific type, so a
    committed proxy is recorded as a ``memory.created`` event whose payload is this
    record - ``kind`` ``"purchase-proxy"`` - alongside the ``agent.state_updated`` event
    that carries the budget and intention change. This function is the contract the event
    owner mints those events from: an uncommitted proxy has no record at all, and the
    record always names the campaign, the minute and the causes of the decision.
    """
    if not isinstance(decision, PurchaseDecision):
        raise TypeError("decision must be a PurchaseDecision")
    if not decision.committed:
        raise ValueError("only a committed purchase proxy leaves a memory record")
    if isinstance(caused_by_event_ids, (str, bytes)) or not isinstance(
        caused_by_event_ids, Sequence
    ):
        raise TypeError("caused_by_event_ids must be a sequence of event identifiers")
    return Memory(
        memory_id=memory_id,
        created_minute=decision.simulated_minute,
        kind=PURCHASE_PROXY_MEMORY_KIND,
        summary=(
            f"Committed a rule-only purchase proxy for {decision.campaign_id} "
            f"at {decision.price:.2f}, leaving {decision.budget_after:.2f}."
        ),
        salience=clamp(decision.intention, 0.0, 1.0),
        campaign_id=decision.campaign_id,
        caused_by_event_ids=tuple(caused_by_event_ids),
    )


def encode_memory(state: ConsumerState, memory: Memory) -> ConsumerState:
    """Encode one episodic memory and retain only the five strongest."""
    state = revalidate_model(state, ConsumerState, label="state")
    memory = revalidate_model(memory, Memory, label="memory")
    if any(existing.memory_id == memory.memory_id for existing in state.memories):
        raise ValueError(f"duplicate memory_id: {memory.memory_id}")
    return state.model_copy(update={"memories": _retained((*state.memories, memory))})


def decay_memories(
    state: ConsumerState,
    day_index: int,
    *,
    parameters: object | None = None,
) -> ConsumerState:
    """Apply the daily reflection: recall decay, fatigue relief, and salience decay.

    Recall follows the documented rule literally::

        recall_next_day = clamp(recall_current * 0.85 + reinforcement, 0, 1)

    ``recall_current`` is the recall the state carries, and ``reinforcement`` is the
    amount banked today by repeated exposure or a trusted conversation, capped at
    ``MAX_DAILY_REINFORCEMENT`` per simulated day. Encoding banks reinforcement without
    touching ``recall_strength``, so a day's reinforcement reaches recall exactly once:
    here. With nothing banked the reflection can only decay.

    The retention values are the run's memory-decay parameters (see
    ``simulation.parameters``); ``None`` means the documented constants.
    """
    from adlife.core.simulation.parameters import ModelParameters

    if parameters is None:
        recall_retention, fatigue_retention, salience_retention = (
            RECALL_RETENTION,
            AD_FATIGUE_RETENTION,
            SALIENCE_RETENTION,
        )
    else:
        if not isinstance(parameters, ModelParameters):
            raise TypeError("parameters must be ModelParameters when given")
        recall_retention = parameters.recall_retention
        fatigue_retention = parameters.ad_fatigue_retention
        salience_retention = parameters.salience_retention

    state = revalidate_model(state, ConsumerState, label="state")
    if type(day_index) is not int or day_index < 0:
        raise ValueError("day_index must be a nonnegative integer")

    day_start = day_index * MINUTES_PER_DAY
    day_end = day_start + MINUTES_PER_DAY
    decayed: list[Memory] = []
    for memory in state.memories:
        if memory.created_minute >= day_end:
            raise ValueError("a memory cannot be created after the reflection day")
        if memory.created_minute >= day_start:
            decayed.append(memory)
            continue
        decayed.append(
            memory.model_copy(
                update={"salience": clamp(memory.salience * salience_retention, 0.0, 1.0)}
            )
        )

    reinforcement = clamp(state.daily_reinforcement, 0.0, MAX_DAILY_REINFORCEMENT)
    return state.model_copy(
        update={
            "memories": _retained(decayed),
            "recall_strength": clamp(
                state.recall_strength * recall_retention + reinforcement,
                0.0,
                1.0,
            ),
            "ad_fatigue": clamp(state.ad_fatigue * fatigue_retention, 0.0, 1.0),
            "daily_reinforcement": 0.0,
        }
    )


__all__ = [
    "AD_FATIGUE_RETENTION",
    "EPISODIC_MEMORY_LIMIT",
    "MINUTES_PER_DAY",
    "PURCHASE_PROXY_MEMORY_KIND",
    "RECALL_RETENTION",
    "SALIENCE_RETENTION",
    "decay_memories",
    "encode_memory",
    "purchase_proxy_memory",
]
