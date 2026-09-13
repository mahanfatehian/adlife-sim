from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Literal, get_args

from adlife.core.domain.campaign import Campaign, Placement
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation._validation import revalidate_model, revalidate_placement
from adlife.core.simulation.policies import clamp
from adlife.core.simulation.rng import RandomOracle

PURCHASE_INTENTION_THRESHOLD = 0.70
"""A purchase proxy is considered only at or above this rule-derived intention."""

AD_FATIGUE_PER_RESPONSE = 0.10
MAX_DAILY_REINFORCEMENT = 0.20

_CAMPAIGN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

PurchaseReason = Literal[
    "committed",
    "no-active-need",
    "budget-below-price",
    "intention-below-threshold",
    "draw-above-intention",
]
_PURCHASE_REASONS: tuple[str, ...] = get_args(PurchaseReason)


def _finite(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite number")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _bounded(value: object, lower: float, upper: float, *, label: str) -> float:
    numeric = _finite(value, label=label)
    if not lower <= numeric <= upper:
        raise ValueError(f"{label} must be within [{lower}, {upper}]")
    return numeric


def _campaign_identifier(value: object, *, label: str = "campaign_id") -> str:
    if not isinstance(value, str) or not _CAMPAIGN_ID_PATTERN.match(value):
        raise ValueError(f"{label} must be a campaign identifier slug")
    return value


def _causal_chain(caused_by: object) -> tuple[str, ...]:
    if isinstance(caused_by, (str, bytes)) or not isinstance(caused_by, Sequence):
        raise TypeError("caused_by must be a sequence of event identifiers")
    causes = tuple(caused_by)
    if not causes:
        raise ValueError("a state transition must record at least one cause")
    if any(not isinstance(cause, str) or not cause for cause in causes):
        raise TypeError("caused_by must contain non-empty event identifiers")
    if len(causes) != len(set(causes)):
        raise ValueError("causal event identifiers must be unique")
    return causes


def _nonnegative_index(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


@dataclass(frozen=True, slots=True)
class RuleResponse:
    """The bounded, transparent reaction of one agent to one noticed advertisement."""

    campaign_id: str
    sentiment_delta: float
    recall_delta: float
    purchase_intention: float
    share_probability: float
    valence: float
    relevance: float
    credibility: float

    def __post_init__(self) -> None:
        _campaign_identifier(self.campaign_id)
        bands: tuple[tuple[str, float, float], ...] = (
            ("sentiment_delta", -0.20, 0.20),
            ("recall_delta", 0.0, 0.30),
            ("purchase_intention", 0.0, 1.0),
            ("share_probability", 0.0, 1.0),
            ("valence", -1.0, 1.0),
            ("relevance", 0.0, 1.0),
            ("credibility", 0.0, 1.0),
        )
        for name, lower, upper in bands:
            object.__setattr__(
                self,
                name,
                _bounded(getattr(self, name), lower, upper, label=name),
            )


@dataclass(frozen=True, slots=True)
class StateTransition:
    """One consumer state change together with the events that caused it."""

    previous: ConsumerState
    state: ConsumerState
    caused_by_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.previous, ConsumerState) or not isinstance(
            self.state, ConsumerState
        ):
            raise TypeError("a state transition must carry consumer states")
        if self.previous.agent_id != self.state.agent_id:
            raise ValueError("a state transition must not change the agent")
        object.__setattr__(
            self,
            "caused_by_event_ids",
            _causal_chain(self.caused_by_event_ids),
        )


@dataclass(frozen=True, slots=True)
class PurchaseDecision:
    """A rule-only purchase proxy; it is never a recorded transaction.

    The record re-derives every gate :func:`purchase_proxy` applies - a positive price,
    the intention threshold, an affordable budget, a draw below intention and budget
    conservation - so a rehydrated or hand-built commitment cannot escape them.
    """

    agent_id: str
    campaign_id: str
    simulated_minute: int
    decision_index: int
    intention: float
    threshold: float
    price: float
    budget_before: float
    budget_after: float
    random_draw: float | None
    committed: bool
    reason: PurchaseReason

    def __post_init__(self) -> None:
        if not isinstance(self.committed, bool):
            raise TypeError("committed must be a bool")
        for name in ("simulated_minute", "decision_index"):
            _nonnegative_index(getattr(self, name), label=name)
        if self.reason not in _PURCHASE_REASONS:
            raise ValueError(f"unsupported purchase proxy reason: {self.reason}")
        if self.committed != (self.reason == "committed"):
            raise ValueError("committed must agree with the recorded reason")
        object.__setattr__(
            self,
            "intention",
            _bounded(self.intention, 0.0, 1.0, label="intention"),
        )
        object.__setattr__(
            self,
            "threshold",
            _bounded(self.threshold, 0.0, 1.0, label="threshold"),
        )
        price = _finite(self.price, label="price")
        if price <= 0.0:
            raise ValueError("price must be a positive amount")
        object.__setattr__(self, "price", price)
        for name in ("budget_before", "budget_after"):
            object.__setattr__(
                self,
                name,
                _bounded(getattr(self, name), 0.0, float("inf"), label=name),
            )
        if self.random_draw is not None:
            draw = _bounded(self.random_draw, 0.0, 1.0, label="random_draw")
            if draw >= 1.0:
                raise ValueError("random_draw must be within [0, 1)")
            object.__setattr__(self, "random_draw", draw)
        elif self.committed:
            raise ValueError("a committed purchase proxy must record its random draw")
        if self.committed:
            if self.intention < self.threshold:
                raise ValueError(
                    "a committed purchase proxy must reach its intention threshold: "
                    f"{self.intention} is below {self.threshold}"
                )
            if self.budget_before < self.price:
                raise ValueError(
                    "a committed purchase proxy needs budget_before at least the price: "
                    f"{self.budget_before} is below {self.price}"
                )
            if self.random_draw is None or self.random_draw >= self.intention:
                raise ValueError(
                    "a committed purchase proxy must record a random_draw below its intention"
                )
        expected_after = (
            max(0.0, self.budget_before - self.price) if self.committed else self.budget_before
        )
        if self.budget_after != expected_after:
            raise ValueError(
                "budget_after must equal the budget this proxy leaves: "
                f"{expected_after} rather than {self.budget_after}"
            )


def jaccard(left: Collection[str], right: Collection[str]) -> float:
    """Interest overlap as documented in the deterministic behavior model."""
    left_set = frozenset(left)
    right_set = frozenset(right)
    union = len(left_set | right_set)
    if union == 0:
        return 0.0
    return len(left_set & right_set) / union


def channel_attention(profile: PersonProfile, channel: str) -> float:
    if channel == "mobile-feed":
        return profile.traits.mobile_attention
    if channel == "highway-billboard":
        return profile.traits.outdoor_attention
    raise ValueError(f"unsupported channel: {channel}")


def evaluate_rule_response(
    profile: PersonProfile,
    state: ConsumerState,
    campaign: Campaign,
    placement: Placement,
) -> RuleResponse:
    """Score one noticed advertisement with the documented rule-only formula.

    ``normalized_sentiment`` maps the sentiment this response leaves onto [0, 1], so the
    sum is first clamped to the band a :class:`ConsumerState` can hold; an unclamped sum
    would score intention against a sentiment no state can ever carry.
    """
    profile = revalidate_model(profile, PersonProfile, label="profile")
    state = revalidate_model(state, ConsumerState, label="state")
    campaign = revalidate_model(campaign, Campaign, label="campaign")
    placement = revalidate_placement(placement)
    if profile.agent_id != state.agent_id:
        raise ValueError("profile and state must identify the same agent")
    if placement not in campaign.placements:
        raise ValueError("placement must belong to campaign")

    traits = profile.traits
    interest_match = jaccard(profile.interests, campaign.target_interests)
    normalized_price = campaign.price.amount / campaign.category_reference_price
    affordability = clamp(1.25 - normalized_price * traits.price_sensitivity, 0.0, 1.0)
    frequency_fatigue = min(
        1.0,
        state.exposure_count(campaign.campaign_id, placement.channel) / placement.frequency_cap,
    )
    value_match = 0.55 * interest_match + 0.25 * traits.novelty_seeking + 0.20 * affordability
    sentiment_delta = clamp(
        0.18 * value_match - 0.12 * traits.advertising_skepticism - 0.06 * frequency_fatigue,
        -0.20,
        0.20,
    )
    recall_delta = clamp(
        0.22 * channel_attention(profile, placement.channel)
        + 0.12 * traits.novelty_seeking
        - 0.08 * frequency_fatigue,
        0.0,
        0.30,
    )
    projected_sentiment = clamp(state.brand_sentiment + sentiment_delta, -1.0, 1.0)
    normalized_sentiment = (projected_sentiment + 1) / 2
    intention = clamp(
        0.40 * normalized_sentiment
        + 0.25 * value_match
        + 0.20 * state.social_proof
        + 0.15 * traits.impulsivity,
        0.0,
        1.0,
    )
    return RuleResponse(
        campaign_id=campaign.campaign_id,
        sentiment_delta=sentiment_delta,
        recall_delta=recall_delta,
        purchase_intention=intention,
        share_probability=clamp(
            abs(sentiment_delta) * traits.social_susceptibility * traits.novelty_seeking,
            0.0,
            1.0,
        ),
        valence=clamp(sentiment_delta * 5, -1.0, 1.0),
        relevance=interest_match,
        credibility=clamp(1 - traits.advertising_skepticism, 0.0, 1.0),
    )


def apply_response(
    state: ConsumerState,
    response: RuleResponse,
    caused_by: Sequence[str],
) -> StateTransition:
    """Apply one bounded response; repeated encoding earns diminishing reinforcement.

    Encoding banks reinforcement in ``daily_reinforcement`` and never writes
    ``recall_strength``: the documented reflection
    ``recall_next_day = clamp(recall * 0.85 + reinforcement, 0, 1)`` reads the banked
    amount exactly once, at the daily reflection, so a day's reinforcement can never be
    counted twice. The bank is capped at ``MAX_DAILY_REINFORCEMENT`` per simulated day,
    and each repeat earns less because the gain is scaled by the headroom left above the
    recall this day will reach. Noticing the advertisement is the direct half of campaign
    awareness; a trusted conversation contributes the indirect half in
    :mod:`adlife.core.simulation.social`.
    """
    state = revalidate_model(state, ConsumerState, label="state")
    if not isinstance(response, RuleResponse):
        raise TypeError("response must be a RuleResponse")
    causes = _causal_chain(caused_by)

    headroom = max(0.0, MAX_DAILY_REINFORCEMENT - state.daily_reinforcement)
    projected_recall = clamp(state.recall_strength + state.daily_reinforcement, 0.0, 1.0)
    recall_gain = min(response.recall_delta * (1.0 - projected_recall), headroom)
    updated = state.model_copy(
        update={
            "brand_sentiment": clamp(
                state.brand_sentiment + response.sentiment_delta,
                -1.0,
                1.0,
            ),
            "purchase_intention": clamp(response.purchase_intention, 0.0, 1.0),
            "daily_reinforcement": clamp(
                state.daily_reinforcement + recall_gain,
                0.0,
                MAX_DAILY_REINFORCEMENT,
            ),
            "ad_fatigue": clamp(state.ad_fatigue + AD_FATIGUE_PER_RESPONSE, 0.0, 1.0),
            "aware_campaign_ids": state.aware_campaign_ids | {response.campaign_id},
        }
    )
    return StateTransition(previous=state, state=updated, caused_by_event_ids=causes)


def purchase_proxy(
    profile: PersonProfile,
    state: ConsumerState,
    campaign: Campaign,
    oracle: RandomOracle,
    *,
    simulated_minute: int,
    decision_index: int = 0,
) -> PurchaseDecision:
    """Rule-only purchase proxy; a language model never supplies this probability.

    The keyed draw is taken over the campaign, the agent, the simulated minute and
    ``decision_index``, so a replay of the same scenario and seed reproduces it exactly.
    ``decision_index`` distinguishes repeated proxies for the SAME agent, campaign and
    simulated minute - the first is always 0 - and must never be a running counter over
    an agent's decisions. Because the key already names the agent, the campaign and the
    minute, a paired experiment draws the same number in both arms for any agent,
    campaign and minute both arms evaluate, whatever else differs between them; a running
    counter would shift with the number of decisions taken first and break those common
    random numbers.
    """
    profile = revalidate_model(profile, PersonProfile, label="profile")
    state = revalidate_model(state, ConsumerState, label="state")
    campaign = revalidate_model(campaign, Campaign, label="campaign")
    if not isinstance(oracle, RandomOracle):
        raise TypeError("oracle must be a RandomOracle")
    if profile.agent_id != state.agent_id:
        raise ValueError("profile and state must identify the same agent")
    simulated_minute = _nonnegative_index(simulated_minute, label="simulated_minute")
    decision_index = _nonnegative_index(decision_index, label="decision_index")

    price = campaign.price.amount
    intention = state.purchase_intention
    draw: float | None = None
    if not state.active_need:
        reason: PurchaseReason = "no-active-need"
    elif state.disposable_budget < price:
        reason = "budget-below-price"
    elif intention < PURCHASE_INTENTION_THRESHOLD:
        reason = "intention-below-threshold"
    else:
        draw = oracle.uniform(
            f"purchase-proxy:{campaign.campaign_id}",
            state.agent_id,
            simulated_minute,
            decision_index,
        )
        if not isinstance(draw, float) or not isfinite(draw) or not 0.0 <= draw < 1.0:
            raise ValueError("RandomOracle draw must be a finite float in [0, 1)")
        reason = "committed" if draw < intention else "draw-above-intention"

    committed = reason == "committed"
    budget_after = state.disposable_budget
    if committed:
        budget_after = max(0.0, state.disposable_budget - price)
    return PurchaseDecision(
        agent_id=state.agent_id,
        campaign_id=campaign.campaign_id,
        simulated_minute=simulated_minute,
        decision_index=decision_index,
        intention=intention,
        threshold=PURCHASE_INTENTION_THRESHOLD,
        price=price,
        budget_before=state.disposable_budget,
        budget_after=budget_after,
        random_draw=draw,
        committed=committed,
        reason=reason,
    )


def apply_purchase(
    state: ConsumerState,
    decision: PurchaseDecision,
    caused_by: Sequence[str],
) -> StateTransition:
    state = revalidate_model(state, ConsumerState, label="state")
    if not isinstance(decision, PurchaseDecision):
        raise TypeError("decision must be a PurchaseDecision")
    causes = _causal_chain(caused_by)
    if not decision.committed:
        raise ValueError("only a committed purchase proxy changes consumer state")
    if state.agent_id != decision.agent_id:
        raise ValueError("purchase proxy and state must identify the same agent")
    if state.disposable_budget != decision.budget_before:
        raise ValueError("purchase proxy budget no longer matches the consumer state")

    updated = state.model_copy(
        update={
            "disposable_budget": decision.budget_after,
            "active_need": False,
        }
    )
    return StateTransition(previous=state, state=updated, caused_by_event_ids=causes)


__all__ = [
    "AD_FATIGUE_PER_RESPONSE",
    "MAX_DAILY_REINFORCEMENT",
    "PURCHASE_INTENTION_THRESHOLD",
    "PurchaseDecision",
    "PurchaseReason",
    "RuleResponse",
    "StateTransition",
    "apply_purchase",
    "apply_response",
    "channel_attention",
    "evaluate_rule_response",
    "jaccard",
    "purchase_proxy",
]
