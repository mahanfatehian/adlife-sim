from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from typing import Any

import pytest

from adlife.core.experiments import spatial_study
from adlife.core.experiments.spatial_study import spatial_paired_statistics

OPPORTUNITY_SOURCES = ("outputs/spatial-opportunities.jsonl",)
ATTENTION_SOURCES = ("outputs/spatial-attention.jsonl",)
RESPONSE_SOURCES = ("outputs/spatial-responses.jsonl",)
STATE_SOURCES = ("inputs/spatial-response.json", "outputs/response-state.json")
FIRST_KEY = "attention.overall.impression_count"
LAST_KEY = "response.overall.response_count"


def _maximum_sources() -> dict[str, tuple[str, ...]]:
    """The complete 20-campaign, 328-key public grammar, independently enumerated."""
    attention_metrics = (
        "opportunity_count",
        "impression_count",
        "noticed_count",
        "opportunity_reach",
        "impression_reach",
        "noticed_reach",
        "impression_frequency",
        "notice_rate",
    )
    event_metrics = (
        "response_count",
        "response_reach",
        "response_frequency",
        "mean_rule_sentiment_delta",
        "mean_rule_recall_delta",
    )
    result = {}
    for scope in ("overall", "channel.mobile", "channel.roadside"):
        for metric in attention_metrics:
            sources = (
                OPPORTUNITY_SOURCES
                if metric in ("opportunity_count", "opportunity_reach")
                else ATTENTION_SOURCES
            )
            result[f"attention.{scope}.{metric}"] = sources
    campaign_scopes = tuple(f"campaign.campaign-{index:02d}" for index in range(20))
    for scope in ("overall", "channel.mobile", "channel.roadside", *campaign_scopes):
        for metric in event_metrics:
            result[f"response.{scope}.{metric}"] = RESPONSE_SOURCES
    for scope in ("overall", *campaign_scopes):
        for metric in ("brand_sentiment", "recall_strength", "purchase_intention_proxy"):
            for aggregate in ("initial_mean", "final_mean", "mean_change"):
                result[f"response.{scope}.state.{metric}.{aggregate}"] = STATE_SOURCES
    return result


@pytest.mark.parametrize("metric_count", (1, 328))
def test_statistics_accept_metric_count_bounds_and_emit_lexicographic_sources(
    metric_count: int,
) -> None:
    source_items = tuple(_maximum_sources().items())[:metric_count]
    sources = dict(reversed(source_items))
    differences = {key: (0.0, 0.0) for key in sources}

    statistics = spatial_paired_statistics(differences, sources)

    assert len(statistics) == metric_count
    assert tuple(item.metric_key for item in statistics) == tuple(sorted(differences))
    assert {item.metric_key: item.source_artifacts for item in statistics} == sources


@pytest.mark.parametrize("sample_size", (2, 100))
def test_statistics_accept_sample_size_bounds(sample_size: int) -> None:
    statistics = spatial_paired_statistics(
        {FIRST_KEY: (0.0,) * sample_size}, {FIRST_KEY: ATTENTION_SOURCES}
    )

    assert statistics[0].n_seeds == sample_size


@pytest.mark.parametrize("sample_size", (0, 1, 101))
def test_statistics_refuse_out_of_bounds_vectors(sample_size: int) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics({FIRST_KEY: (0.0,) * sample_size}, {FIRST_KEY: ATTENTION_SOURCES})


def test_statistics_refuse_mixed_sample_sizes() -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics(
            {FIRST_KEY: (0.0, 1.0), LAST_KEY: (0.0, 1.0, 2.0)},
            {FIRST_KEY: ATTENTION_SOURCES, LAST_KEY: RESPONSE_SOURCES},
        )


class _FloatSubclass(float):
    pass


@pytest.mark.parametrize(
    "member",
    (True, False, 0, 1, Decimal("1.0"), "1.0", None, _FloatSubclass(1.0)),
)
def test_statistics_refuse_every_implicit_float_coercion(member: object) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics(
            {FIRST_KEY: (0.0, member)},
            {FIRST_KEY: ATTENTION_SOURCES},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("member", (float("nan"), float("inf"), -float("inf")))
def test_statistics_refuse_nonfinite_float_members(member: float) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics({FIRST_KEY: (0.0, member)}, {FIRST_KEY: ATTENTION_SOURCES})


@pytest.mark.parametrize(
    "values",
    (
        (1e308, 1e308),
        (-1e308, 1e308),
        (0.0, 1e200),
    ),
    ids=("finite-sum-overflow", "finite-deviation-overflow", "finite-square-overflow"),
)
def test_statistics_translate_finite_arithmetic_overflow_to_controlled_refusal(
    values: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics({FIRST_KEY: values}, {FIRST_KEY: ATTENTION_SOURCES})


@pytest.mark.parametrize("metric_count", (0, 329))
def test_statistics_refuse_empty_or_oversized_metric_mappings(metric_count: int) -> None:
    sources = _maximum_sources()
    sources["response.campaign.campaign-extra.response_count"] = RESPONSE_SOURCES
    if metric_count == 0:
        sources = {}

    with pytest.raises(ValueError):
        spatial_paired_statistics({key: (0.0, 1.0) for key in sources}, sources)


@pytest.mark.parametrize(
    "key",
    (
        "",
        "attention.overall.unknown",
        "Attention.overall.notice_rate",
        "attention.channel.unknown.notice_rate",
        "response.channel.mobile.state.recall_strength.mean_change",
        "response.campaign.bad.id.response_count",
        "response.campaign.-bad.response_count",
        "response.overall.state.recall_strength.unknown",
        "attention.overall.notice_rate.extra",
        1,
        None,
    ),
)
def test_statistics_refuse_invalid_metric_keys(key: Any) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics({key: (0.0, 1.0)}, {key: ATTENTION_SOURCES})


class _DuplicateKeyMapping(Mapping[str, Any]):
    """A Mapping can expose duplicate iteration keys even though dict cannot."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __getitem__(self, key: str) -> Any:
        if key != FIRST_KEY:
            raise KeyError(key)
        return self.value

    def __iter__(self) -> Iterator[str]:
        yield FIRST_KEY
        yield FIRST_KEY

    def __len__(self) -> int:
        return 2


@pytest.mark.parametrize("duplicate_side", ("differences", "sources"))
def test_statistics_refuse_duplicate_iteration_keys(duplicate_side: str) -> None:
    differences: Mapping[str, Any] = {FIRST_KEY: (0.0, 1.0)}
    sources: Mapping[str, Any] = {FIRST_KEY: ATTENTION_SOURCES}
    if duplicate_side == "differences":
        differences = _DuplicateKeyMapping((0.0, 1.0))
    else:
        sources = _DuplicateKeyMapping(ATTENTION_SOURCES)

    with pytest.raises(ValueError):
        spatial_paired_statistics(differences, sources)


@pytest.mark.parametrize(
    "sources",
    (
        {},
        {FIRST_KEY: ATTENTION_SOURCES, LAST_KEY: RESPONSE_SOURCES},
        {LAST_KEY: RESPONSE_SOURCES},
    ),
    ids=("missing", "extra", "same-size-wrong-key"),
)
def test_statistics_require_exact_source_mapping_key_set(
    sources: dict[str, tuple[str, ...]],
) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics({FIRST_KEY: (0.0, 1.0)}, sources)


@pytest.mark.parametrize(
    ("key", "sources"),
    (
        (FIRST_KEY, OPPORTUNITY_SOURCES),
        ("attention.overall.opportunity_count", ATTENTION_SOURCES),
        (LAST_KEY, ATTENTION_SOURCES),
        (FIRST_KEY, ("outputs/unknown.jsonl",)),
        (FIRST_KEY, ()),
        (FIRST_KEY, ATTENTION_SOURCES * 2),
        (FIRST_KEY, (*ATTENTION_SOURCES, *RESPONSE_SOURCES)),
        ("response.overall.state.recall_strength.mean_change", STATE_SOURCES[::-1]),
        ("response.overall.state.recall_strength.mean_change", STATE_SOURCES[:1]),
        ("response.overall.state.recall_strength.mean_change", STATE_SOURCES * 2),
    ),
    ids=(
        "allowed-wrong-attention",
        "allowed-wrong-opportunity",
        "allowed-wrong-response",
        "unknown-artifact",
        "empty-artifacts",
        "duplicate-artifacts",
        "extra-artifact",
        "reversed-state-artifacts",
        "missing-state-artifact",
        "duplicate-state-artifacts",
    ),
)
def test_statistics_require_canonical_artifacts_implied_by_key(
    key: str, sources: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        spatial_paired_statistics({key: (0.0, 1.0)}, {key: sources})


@pytest.mark.parametrize("invalid_kind", ("source", "length", "type", "finite", "overflow"))
def test_all_metrics_are_validated_before_any_bootstrap_resampling(
    monkeypatch: pytest.MonkeyPatch, invalid_kind: str
) -> None:
    def fail_if_resampled(*args: object, **kwargs: object) -> None:
        pytest.fail("bootstrap started before validating every input metric")

    monkeypatch.setattr(spatial_study, "_bootstrap_interval", fail_if_resampled)
    differences: dict[str, Any] = {FIRST_KEY: (0.0, 1.0), LAST_KEY: (0.0, 1.0)}
    sources = {FIRST_KEY: ATTENTION_SOURCES, LAST_KEY: RESPONSE_SOURCES}
    if invalid_kind == "source":
        sources[LAST_KEY] = ATTENTION_SOURCES
    elif invalid_kind == "length":
        differences[LAST_KEY] = (0.0, 1.0, 2.0)
    elif invalid_kind == "type":
        differences[LAST_KEY] = (0.0, True)
    elif invalid_kind == "finite":
        differences[LAST_KEY] = (0.0, float("inf"))
    else:
        differences[LAST_KEY] = (1e308, 1e308)

    with pytest.raises(ValueError):
        spatial_paired_statistics(differences, sources)


class _MisreportedSequence(Sequence[float]):
    """Three iterated values cannot be analyzed using a declared length of two."""

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: Any) -> Any:
        return (1.0, 2.0, 3.0)[index]


def test_statistics_refuse_sequence_length_mismatch_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_resampled(*args: object, **kwargs: object) -> None:
        pytest.fail("bootstrap started for a sequence with inconsistent length")

    monkeypatch.setattr(spatial_study, "_bootstrap_interval", fail_if_resampled)

    with pytest.raises(ValueError):
        spatial_paired_statistics(
            {FIRST_KEY: _MisreportedSequence()}, {FIRST_KEY: ATTENTION_SOURCES}
        )


class _NonterminatingSequence(Sequence[float]):
    """Safety fuse makes an otherwise endless sequence safe to test."""

    def __init__(self) -> None:
        self.read_count = 0

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: Any) -> float:
        self.read_count += 1
        if self.read_count > 101:
            pytest.fail("sequence iteration exceeded the supported sample bound")
        return 1.0


def test_statistics_bound_nonterminating_sequence_iteration_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_resampled(*args: object, **kwargs: object) -> None:
        pytest.fail("bootstrap started for a nonterminating sequence")

    monkeypatch.setattr(spatial_study, "_bootstrap_interval", fail_if_resampled)
    values = _NonterminatingSequence()

    with pytest.raises(ValueError):
        spatial_paired_statistics({FIRST_KEY: values}, {FIRST_KEY: ATTENTION_SOURCES})

    assert values.read_count <= 101


class _OversizedMapping(Mapping[str, Any]):
    def __init__(self, value: Any) -> None:
        self.value = value
        self.iteration_count = 0

    def __len__(self) -> int:
        return 329

    def __iter__(self) -> Iterator[str]:
        self.iteration_count += 1
        yield from _maximum_sources()
        yield "response.campaign.campaign-extra.response_count"

    def __getitem__(self, key: str) -> Any:
        return self.value


@pytest.mark.parametrize("oversized_side", ("differences", "sources"))
def test_statistics_refuse_declared_oversized_mapping_without_iteration(
    oversized_side: str,
) -> None:
    differences: Mapping[str, Any] = {FIRST_KEY: (0.0, 1.0)}
    sources: Mapping[str, Any] = {FIRST_KEY: ATTENTION_SOURCES}
    mapping = _OversizedMapping(
        (0.0, 1.0) if oversized_side == "differences" else ATTENTION_SOURCES
    )
    if oversized_side == "differences":
        differences = mapping
    else:
        sources = mapping

    with pytest.raises(ValueError):
        spatial_paired_statistics(differences, sources)

    assert mapping.iteration_count == 0


class _ChangingSourceMapping(Mapping[str, tuple[str, ...]]):
    def __init__(self) -> None:
        self.lookup_count = 0

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Iterator[str]:
        yield FIRST_KEY

    def __getitem__(self, key: str) -> tuple[str, ...]:
        if key != FIRST_KEY:
            raise KeyError(key)
        self.lookup_count += 1
        return ATTENTION_SOURCES if self.lookup_count == 1 else RESPONSE_SOURCES


def test_statistics_use_validated_source_snapshot_with_one_lookup() -> None:
    sources = _ChangingSourceMapping()

    (result,) = spatial_paired_statistics(
        {FIRST_KEY: (0.0, 1.0)},
        sources,  # type: ignore[arg-type]
    )

    assert sources.lookup_count == 1
    assert result.source_artifacts == ATTENTION_SOURCES
    assert result.mean_paired_difference == 0.5
