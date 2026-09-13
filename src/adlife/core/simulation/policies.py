from math import exp, isfinite

from adlife.core.domain.campaign import Campaign, Placement
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation._validation import revalidate_model, revalidate_placement


def _finite_number(value: float, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite number")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def sigmoid(value: float) -> float:
    value = _finite_number(value, label="sigmoid value")
    if value >= 0:
        inverse = exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = exp(value)
    return exponential / (1.0 + exponential)


def clamp(value: float, lower: float, upper: float) -> float:
    value = _finite_number(value, label="clamp value")
    lower = _finite_number(lower, label="clamp lower bound")
    upper = _finite_number(upper, label="clamp upper bound")
    if lower > upper:
        raise ValueError("clamp lower bound must not exceed upper bound")
    return max(lower, min(upper, value))


def notice_probability(
    profile: PersonProfile,
    state: ConsumerState,
    campaign: Campaign,
    placement: Placement,
) -> float:
    profile = revalidate_model(profile, PersonProfile, label="profile")
    state = revalidate_model(state, ConsumerState, label="state")
    campaign = revalidate_model(campaign, Campaign, label="campaign")
    placement = revalidate_placement(placement)
    if profile.agent_id != state.agent_id:
        raise ValueError("profile and state must identify the same agent")
    if placement not in campaign.placements:
        raise ValueError("placement must belong to campaign")

    attention = (
        profile.traits.mobile_attention
        if placement.channel == "mobile-feed"
        else profile.traits.outdoor_attention
    )
    overlap = len(profile.interests & campaign.target_interests)
    union = len(profile.interests | campaign.target_interests)
    interest_match = overlap / union if union else 0.0
    prior = state.exposure_count(campaign.campaign_id, placement.channel)
    fatigue = min(1.0, prior / placement.frequency_cap)
    raw = (
        -1.2
        + 1.5 * attention
        + interest_match
        + 0.8 * placement.visibility
        - 0.7 * profile.traits.advertising_skepticism
        - 0.5 * fatigue
    )
    return clamp(sigmoid(raw), 0.0, 1.0)


__all__ = ["clamp", "notice_probability", "sigmoid"]
