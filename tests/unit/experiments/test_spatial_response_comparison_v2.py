"""V10 comparisons subtract the rule baseline and only classify cognition."""

from __future__ import annotations

import importlib
import math
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_response_metrics import SpatialResponseMetrics
from tests.unit.experiments.test_spatial_response_comparison import _response_metrics
from tests.unit.experiments.test_spatial_response_metrics_v2 import (
    _arguments,
    _derive,
    _finalize_with_vector,
    _keys,
    _metric_inputs,
)

FORBIDDEN_COGNITION_TOKENS = frozenset({"purchase", "delta", "effect", "lift"})
_OTHER_HASH = sha256(b"other-v10-provenance").hexdigest()


def _module():
    return importlib.import_module("adlife.core.experiments.spatial_response_comparison_v2")


def _pair(root: Path, **options):
    _, prepared, arguments = _metric_inputs(root, **options)
    metrics = _derive(arguments)
    return prepared, metrics


def _signalled_pair(root: Path):
    staged, _, _ = _metric_inputs(
        root,
        mode="local",
        source="provider",
        agents=3,
        run_id="signal-run",
    )
    positive = _finalize_with_vector(
        staged,
        affect="positive",
        relevance="high",
        credibility="high",
        salience="high",
    )
    negative = _finalize_with_vector(
        staged,
        affect="annoyed",
        relevance="low",
        credibility="low",
        salience="low",
    )
    return _derive(_arguments(positive)), _derive(_arguments(negative))


def _baseline_scalars(comparison) -> dict[str, float]:
    values: dict[str, float] = {}
    baseline = comparison.rule_baseline
    slices = [("overall", baseline.overall)]
    slices.extend((f"channel.{series.channel}", series) for series in baseline.channels)
    slices.extend((f"campaign.{series.campaign_id}", series) for series in baseline.campaigns)
    event_names = (
        "response_count",
        "response_reach",
        "response_frequency",
        "mean_rule_sentiment_delta",
        "mean_rule_recall_delta",
    )
    for key, series in slices:
        for name in event_names:
            values[f"{key}.{name}"] = float(getattr(series, name))
        if not key.startswith("channel."):
            for name in ("brand_sentiment", "recall_strength", "purchase_intention_proxy"):
                state = getattr(series, name)
                for aggregate in ("initial_mean", "final_mean", "mean_change"):
                    values[f"{key}.state.{name}.{aggregate}"] = float(getattr(state, aggregate))
    return values


def _compare(control, treatment, *, control_run_id="control-run", treatment_run_id="treatment-run"):
    return _module().compare_spatial_response_metrics_v2(
        control,
        treatment,
        control_run_id=control_run_id,
        treatment_run_id=treatment_run_id,
    )


def test_v10_comparison_has_no_cognition_effect_delta(tmp_path: Path) -> None:
    control, treatment = _signalled_pair(tmp_path)

    comparison = _compare(control, treatment)

    assert comparison.schema_version == 2
    assert comparison.model_id == "spatial-response-metrics-comparison-v2"
    assert (
        comparison.rule_baseline.purchase_intention_proxy_scope == "uncalibrated-rule-owned-proxy"
    )
    assert comparison.rule_baseline.overall.purchase_intention_proxy.mean_change == 0.0
    cognition = comparison.cognition_provenance.model_dump(mode="json")
    assert (
        cognition["control"]["claim_scope"] == "provider-dependent-exploratory-not-campaign-effect"
    )
    assert cognition["treatment"]["claim_scope"] == cognition["control"]["claim_scope"]
    tokens = {token for key in _keys(cognition) for token in key.split("_")}
    assert not tokens & FORBIDDEN_COGNITION_TOKENS
    assert "purchase" not in canonical_json(cognition)
    assert "delta" not in cognition
    assert "effect" not in cognition
    assert "lift" not in cognition
    dumped = comparison.model_dump(mode="json")
    assert "overall" not in dumped
    assert dumped["rule_baseline"]["overall"]["response_count"] == 0.0


def test_different_terminal_signals_leave_every_baseline_delta_positive_zero(
    tmp_path: Path,
) -> None:
    control, treatment = _signalled_pair(tmp_path)
    assert control.provider_dependent_cognition != treatment.provider_dependent_cognition

    comparison = _compare(control, treatment)

    values = _baseline_scalars(comparison)
    assert values
    assert set(values.values()) == {0.0}
    assert all(math.copysign(1.0, value) == 1.0 for value in values.values())
    assert comparison.cognition_provenance.classification in {
        "matched-provider-dependent-exploratory",
        "incompatible-provider-provenance",
    }
    assert comparison.cognition_provenance.control == control.provider_dependent_cognition
    assert comparison.cognition_provenance.treatment == treatment.provider_dependent_cognition


def test_provenance_matches_only_the_frozen_contract_fields(tmp_path: Path) -> None:
    _, metrics = _pair(tmp_path, mode="local", source="provider", agents=3, run_id="provider-run")

    matched = _compare(
        metrics, metrics, control_run_id="provider-run", treatment_run_id="provider-run"
    )
    assert matched.cognition_provenance.classification == "matched-provider-dependent-exploratory"

    cognition = metrics.provider_dependent_cognition.model_copy(
        update={"effective_settings_sha256": _OTHER_HASH}
    )
    drifted = metrics.model_copy(update={"provider_dependent_cognition": cognition})
    incompatible = _compare(metrics, drifted)
    assert incompatible.cognition_provenance.classification == "incompatible-provider-provenance"
    assert incompatible.cognition_provenance.control == metrics.provider_dependent_cognition
    assert incompatible.cognition_provenance.treatment == drifted.provider_dependent_cognition
    assert incompatible.cognition_provenance.treatment.effective_settings_sha256 == _OTHER_HASH


def test_comparison_refuses_unmatched_scientific_identity(tmp_path: Path) -> None:
    error = _module().SpatialResponseComparisonError
    _, metrics = _pair(tmp_path / "base", mode="rules", run_id="rules-run")
    _, other_population = _pair(
        tmp_path / "population",
        mode="rules",
        agents=4,
        run_id="other-population",
    )

    with pytest.raises(error, match=r"population|mobility trace|agent assignment"):
        _compare(metrics, other_population)
    shifted_days = metrics.model_copy(update={"days": 1 if metrics.days != 1 else 2})
    with pytest.raises(error, match="duration"):
        _compare(metrics, shifted_days)
    shifted_seed = metrics.model_copy(update={"seed": metrics.seed + 1})
    with pytest.raises(error, match="seed"):
        _compare(metrics, shifted_seed)
    campaign = metrics.rule_baseline.campaigns[0]
    renamed = campaign.model_copy(update={"campaign_id": "alternate-campaign"})
    campaigns = tuple(
        sorted((renamed, *metrics.rule_baseline.campaigns[1:]), key=lambda item: item.campaign_id)
    )
    renamed_metrics = metrics.model_copy(
        update={"rule_baseline": metrics.rule_baseline.model_copy(update={"campaigns": campaigns})}
    )
    with pytest.raises(error, match="campaign"):
        _compare(metrics, renamed_metrics)


def test_comparison_refuses_other_versions_and_non_integer_schema(tmp_path: Path) -> None:
    _, metrics = _pair(tmp_path, mode="mock", run_id="mock-run")
    legacy = _response_metrics()

    with pytest.raises(TypeError, match="SpatialResponseMetricsV2"):
        _compare(legacy, legacy)
    with pytest.raises(ValidationError):
        SpatialResponseMetrics.model_validate(metrics.model_dump(mode="python"))
    document = _compare(metrics, metrics).model_dump(mode="python")
    for version in (True, 2.0, 1, 10):
        with pytest.raises(ValidationError):
            _module().SpatialResponseMetricsComparisonV2.model_validate(
                document | {"schema_version": version}
            )
    swapped = document | {"control_scenario_sha256": _OTHER_HASH}
    with pytest.raises(ValidationError, match="provenance"):
        _module().SpatialResponseMetricsComparisonV2.model_validate(swapped)


def test_v1_comparison_still_rejects_a_v2_snapshot(tmp_path: Path) -> None:
    from adlife.core.experiments.spatial_response_comparison import compare_spatial_response_metrics

    _, metrics = _pair(tmp_path, mode="rules")
    with pytest.raises(TypeError, match="SpatialResponseMetrics"):
        compare_spatial_response_metrics(
            metrics,  # type: ignore[arg-type]
            metrics,  # type: ignore[arg-type]
            control_run_id="control-run",
            treatment_run_id="treatment-run",
        )
