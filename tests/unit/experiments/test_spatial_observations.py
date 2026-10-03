from __future__ import annotations

import pytest

from adlife.core.experiments.spatial_observations import spatial_metric_observations
from tests.unit.experiments.test_spatial_response_comparison import _response_metric_pair

ATTENTION_METRICS = (
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "opportunity_reach",
    "impression_reach",
    "noticed_reach",
    "impression_frequency",
    "notice_rate",
)
EVENT_METRICS = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
STATE_METRICS = (
    "brand_sentiment",
    "recall_strength",
    "purchase_intention_proxy",
)
STATE_AGGREGATES = ("initial_mean", "final_mean", "mean_change")
CAMPAIGN_IDS = ("fictional-launch", "phone-campaign")


def _expected_keys() -> tuple[str, ...]:
    keys = [f"attention.overall.{metric}" for metric in ATTENTION_METRICS]
    keys.extend(
        f"attention.channel.{channel}.{metric}"
        for channel in ("roadside", "mobile")
        for metric in ATTENTION_METRICS
    )
    keys.extend(f"response.overall.{metric}" for metric in EVENT_METRICS)
    keys.extend(
        f"response.channel.{channel}.{metric}"
        for channel in ("roadside", "mobile")
        for metric in EVENT_METRICS
    )
    keys.extend(
        f"response.campaign.{campaign_id}.{metric}"
        for campaign_id in CAMPAIGN_IDS
        for metric in EVENT_METRICS
    )
    keys.extend(
        f"response.overall.state.{state_metric}.{aggregate}"
        for state_metric in STATE_METRICS
        for aggregate in STATE_AGGREGATES
    )
    keys.extend(
        f"response.campaign.{campaign_id}.state.{state_metric}.{aggregate}"
        for campaign_id in CAMPAIGN_IDS
        for state_metric in STATE_METRICS
        for aggregate in STATE_AGGREGATES
    )
    return tuple(sorted(keys))


def test_observations_have_canonical_keys_and_exact_source_receipts() -> None:
    attention, response = _response_metric_pair()

    observations = spatial_metric_observations(attention, response)

    keys = tuple(observation.key for observation in observations)
    assert keys == _expected_keys()
    assert len(keys) == len(set(keys))
    by_key = {observation.key: observation for observation in observations}
    opportunity_count = by_key["attention.overall.opportunity_count"]
    assert (
        opportunity_count.value,
        opportunity_count.numerator,
        opportunity_count.denominator,
    ) == (5.0, 5.0, 1)
    assert opportunity_count.source_artifacts == ("outputs/spatial-opportunities.jsonl",)
    assert by_key["attention.overall.notice_rate"].source_artifacts == (
        "outputs/spatial-attention.jsonl",
    )
    response_count = by_key["response.overall.response_count"]
    assert (
        response_count.value,
        response_count.numerator,
        response_count.denominator,
    ) == (2.0, 2.0, 1)
    assert response_count.source_artifacts == ("outputs/spatial-responses.jsonl",)
    initial_sentiment = by_key["response.overall.state.brand_sentiment.initial_mean"]
    assert initial_sentiment.value == pytest.approx(0.1)
    assert initial_sentiment.numerator == pytest.approx(0.4)
    assert initial_sentiment.denominator == 4
    assert initial_sentiment.source_artifacts == (
        "inputs/spatial-response.json",
        "outputs/response-state.json",
    )
    for observation in observations:
        assert isinstance(observation.value, float)
        assert isinstance(observation.numerator, float)
        assert isinstance(observation.denominator, int)


def test_observations_never_attribute_committed_state_to_a_channel() -> None:
    attention, response = _response_metric_pair()

    observations = spatial_metric_observations(attention, response)

    state_keys = tuple(
        observation.key for observation in observations if ".state." in observation.key
    )
    assert state_keys
    assert all(not key.startswith("response.channel.") for key in state_keys)
    assert all(
        key.startswith("response.overall.state.") or key.startswith("response.campaign.")
        for key in state_keys
    )
