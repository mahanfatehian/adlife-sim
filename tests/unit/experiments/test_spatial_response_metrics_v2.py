"""Version-two metrics keep rule authority separate from bounded cognition."""

from __future__ import annotations

import importlib
import json
import math
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from adlife.city import runs
from adlife.city.run_store import CityRunStore
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionAffectV2,
    CityCognitionCredibilityV2,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    CityCognitionRelevanceV2,
    CityCognitionSalienceV2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseMetrics,
    derive_spatial_response_metrics,
)
from adlife.core.simulation.city_cognition_v2 import (
    build_city_budget_skipped_result_v2,
    build_city_cognition_terminal_record_v2,
    build_city_rules_result_v2,
    validate_city_cognition_evaluation_v2,
)
from adlife.core.simulation.spatial_response_v2 import (
    SpatialAppliedResponseV2,
    SpatialResponseEvaluationV2,
)
from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10, _save_prepared
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_attention import _evaluate_attention
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)
from tests.unit.city.test_spatial_response import _response_input

MODE_MATRIX = (
    pytest.param("rules", None, id="rules"),
    pytest.param("mock", None, id="mock"),
    pytest.param("local", "provider", id="local-provider"),
    pytest.param("local", "cache", id="local-cache"),
    pytest.param("local", "fallback", id="local-fallback"),
    pytest.param("remote", "provider", id="remote-provider"),
)
FORBIDDEN_COGNITION_TOKENS = frozenset({"purchase", "delta", "effect", "lift", "probability"})


def _module():
    return importlib.import_module("adlife.core.experiments.spatial_response_metrics_v2")


def _arguments(prepared) -> dict[str, Any]:
    agent_ids = tuple(agent.agent_id for agent in prepared.mobility.agents)
    return {
        "response_input": prepared.response_input,
        "response": prepared.response_evaluation,
        "scenario": prepared.spatial_scenario,
        "opportunities": prepared.opportunity_evaluation,
        "attention": prepared.attention_evaluation,
        "agent_ids": agent_ids,
        "attention_metrics": derive_spatial_metrics(
            prepared.opportunity_evaluation,
            prepared.attention_evaluation,
            agent_ids=agent_ids,
            agents_sha256=prepared.manifest.agents_sha256,
            trace_sha256=prepared.manifest.trace_sha256,
            days=prepared.manifest.days,
            source_run_schema_version=10,
        ),
        "cognition_input": prepared.cognition_input,
        "cognition": prepared.cognition_evaluation,
    }


def _metric_inputs(root: Path, **options):
    staged, prepared = _prepared_v10(root, **options)
    return staged, prepared, _arguments(prepared)


def _derive(arguments):
    return _module().derive_spatial_response_metrics_v2(**arguments)


def _options(mode: str, source: str | None) -> dict[str, Any]:
    # Eight agents exceed the ten-request provider funding prefix, so provider modes
    # also carry budget-skipped terminals.
    return {"mode": mode} if source is None else {"mode": mode, "source": source, "agents": 8}


def _dense(document) -> None:
    # More than four notices per agent leave eligible responses unselected.
    document["scenario"]["phone"]["frequency_cap_per_agent_per_day"] = 12
    document["scenario"]["phone"]["opportunity_probability_per_minute"] = 0.5


def _finalize_with_vector(
    staged,
    *,
    affect,
    relevance,
    credibility,
    salience,
    safety_flags=(),
):
    frozen = staged.cognition_input
    records = []
    for index, request in enumerate(staged.cognition_requests):
        if frozen.reservations[index].funded:
            source, reason, attempts, usage = "provider", None, 1, (20, 10)
            result = build_city_rules_result_v2(request).model_copy(
                update={
                    "affect": affect,
                    "relevance": relevance,
                    "credibility": credibility,
                    "salience": salience,
                    "safety_flags": tuple(safety_flags),
                }
            )
        else:
            source, reason, attempts, usage = "budget-skipped", "budget-skipped", 0, (None, None)
            result = build_city_budget_skipped_result_v2(request)
        records.append(
            build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=index,
                source=source,
                result=result,
                attempt_count=attempts,
                reason=reason,
                prompt_tokens=usage[0],
                completion_tokens=usage[1],
            )
        )
    cognition = validate_city_cognition_evaluation_v2(
        frozen,
        staged.cognition_requests,
        tuple(records),
        baseline_evaluation=staged.baseline_evaluation,
    )
    return runs.finalize_city_run_v2(
        staged,
        cognition_input=frozen,
        cognition_evaluation=cognition,
    )


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, (list, tuple)):
        return {key for item in value for key in _keys(item)}
    return set()


def _rehash_receipt(receipt: dict[str, Any]) -> None:
    receipt["value"] = receipt["numerator"] / receipt["denominator"]


def test_v10_metrics_separate_rule_and_cognition_families(tmp_path: Path) -> None:
    module = _module()
    _, _, arguments = _metric_inputs(tmp_path, mode="mock")

    metrics = module.derive_spatial_response_metrics_v2(**arguments)

    assert metrics.schema_version == 2
    assert metrics.source_run_schema_version == 10
    assert metrics.rule_baseline.purchase_intention_proxy.name == "purchase_intention_proxy"
    cognition = metrics.provider_dependent_cognition.model_dump(mode="json")
    assert "purchase" not in canonical_json(cognition)
    assert cognition["claim_scope"] == "provider-dependent-exploratory-not-campaign-effect"
    assert (
        metrics.rule_baseline.claim_scope == "rule-owned-synthetic-baseline-not-observed-outcomes"
    )
    assert metrics.rule_baseline.purchase_intention_proxy_scope == "uncalibrated-rule-owned-proxy"
    assert metrics.model_id == "spatial-response-metrics-v2"
    assert metrics.response_model_id == "spatial-response-v2"
    assert cognition["disclosure"] == "synthetic-bounded-cognition-not-observed-behavior"
    assert type(metrics) is module.SpatialResponseMetricsV2
    assert type(metrics.rule_baseline) is module.SpatialRuleBaselineMetricsV2
    assert type(metrics.provider_dependent_cognition) is (
        module.SpatialProviderDependentCognitionMetricsV2
    )


@pytest.mark.parametrize(("mode", "source"), MODE_MATRIX)
def test_v10_metrics_bind_complete_provenance_counts_and_denominators(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    _, prepared, arguments = _metric_inputs(tmp_path, **_options(mode, source))

    metrics = _derive(arguments)
    manifest = prepared.manifest
    receipt = manifest.cognition
    cognition = metrics.provider_dependent_cognition
    evaluation = prepared.cognition_evaluation
    records = evaluation.records

    for field in ("scenario_sha256", "city_sha256", "agents_sha256", "trace_sha256", "seed"):
        assert getattr(metrics, field) == getattr(manifest, field)
    for field in (
        "response_input_sha256",
        "response_stream_sha256",
        "response_state_sha256",
        "response_summary_sha256",
        "rule_state_document_sha256",
        "cognition_state_document_sha256",
        "influence_policy_sha256",
    ):
        assert getattr(metrics, field) == getattr(manifest, field), field
    assert metrics.population_size == manifest.agent_count
    assert metrics.days == manifest.days
    assert metrics.campaign_count == manifest.response_campaign_count
    assert metrics.cognition_input_sha256 == prepared.cognition_input.fingerprint
    assert metrics.cognition_input_sha256 == receipt.cognition_input_sha256
    assert metrics.cognition_terminal_stream_sha256 == evaluation.summary.terminal_stream_sha256
    assert metrics.cognition_terminal_stream_sha256 == receipt.terminal_stream_sha256
    assert metrics.cognition_summary_sha256 == receipt.summary_sha256
    assert metrics.influence_policy_sha256 == (
        prepared.cognition_input.causal_contract.influence_policy_sha256
    )
    for field in (
        "mode",
        "causal_contract_sha256",
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "mock_policy_sha256",
        "influence_policy_sha256",
        "provider_revision_sha256",
        "provider_profile_snapshot_sha256",
        "effective_settings_sha256",
        "funded_response_limit",
        "coverage",
    ):
        assert getattr(cognition, field) == getattr(receipt, field), field
    assert cognition.source_counts == receipt.outcomes == evaluation.summary.outcomes
    assert cognition.fallback_counts == receipt.reasons == evaluation.summary.reasons
    assert cognition.neutralization_counts == receipt.neutralization
    assert cognition.influence_policy_id == manifest.influence_policy_id

    coverage = cognition.coverage
    applied = [
        record
        for record in prepared.response_evaluation.records
        if isinstance(record, SpatialAppliedResponseV2)
    ]
    assert coverage.eligible_response_count == len(applied) == manifest.applied_response_count
    assert coverage.eligible_response_count == metrics.rule_baseline.response_count.numerator
    assert coverage.eligible_response_count == (
        coverage.selected_request_count + coverage.unselected_response_count
    )
    assert coverage.selected_request_count == (
        coverage.funded_request_count + coverage.budget_skipped_count
    )
    assert (
        coverage.selected_request_count
        == len(records)
        == len(prepared.cognition_input.reservations)
    )
    assert coverage.funded_request_count == min(
        coverage.selected_request_count, prepared.cognition_input.funded_response_limit
    )
    sources = Counter(record.source.replace("-", "_") for record in records)
    assert cognition.source_counts.model_dump(exclude={"schema_version"}) == {
        name: sources[name]
        for name in ("rules", "mock", "provider", "cache", "fallback", "budget_skipped")
    }
    assert cognition.mean_applied_sentiment.denominator == coverage.eligible_response_count
    assert cognition.mean_applied_recall.denominator == coverage.eligible_response_count
    assert cognition.mean_applied_sentiment.numerator == math.fsum(
        record.applied_sentiment_delta for record in applied
    )
    assert cognition.mean_applied_recall.numerator == math.fsum(
        record.applied_recall_delta for record in applied
    )
    assert cognition.funded_coverage.numerator == coverage.funded_request_count
    assert cognition.funded_coverage.denominator == coverage.eligible_response_count
    assert cognition.synthetic_sentiment.denominator == manifest.final_cognition_state_count
    assert cognition.synthetic_recall.denominator == manifest.final_cognition_state_count
    assert cognition.synthetic_sentiment.final_total == math.fsum(
        state.synthetic_sentiment for state in prepared.response_evaluation.final_cognition_states
    )

    expected = {
        field: Counter(getattr(record.result, field) for record in records)
        for field in ("affect", "relevance", "credibility", "salience")
    }
    categories = cognition.category_counts.model_dump(mode="json", exclude={"schema_version"})
    expected_orders = {
        "affect": ("annoyed", "skeptical", "neutral", "curious", "positive", "uncertain"),
        "relevance": ("none", "low", "medium", "high", "uncertain"),
        "credibility": ("low", "medium", "high", "uncertain"),
        "salience": ("none", "low", "medium", "high", "uncertain"),
    }
    assert tuple(categories) == tuple(expected_orders)
    for field, counts in categories.items():
        assert tuple(counts) == expected_orders[field]
        assert sum(counts.values()) == coverage.selected_request_count
        assert counts == {name: expected[field][name] for name in counts}


@pytest.mark.parametrize(("mode", "source"), [("mock", None), ("local", "provider")])
def test_v10_metrics_reproduce_the_saved_artifact_bytes(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    _, prepared = _prepared_v10(tmp_path, **_options(mode, source))
    directory = _save_prepared(tmp_path, prepared)
    stored = CityRunStore(tmp_path).load(prepared.manifest.run_id)

    metrics = _derive(
        {
            "response_input": stored.response_input,
            "response": stored.response_evaluation,
            "scenario": stored.spatial_scenario,
            "opportunities": stored.opportunity_evaluation,
            "attention": stored.attention_evaluation,
            "agent_ids": tuple(agent.agent_id for agent in stored.mobility.agents),
            "attention_metrics": derive_spatial_metrics(
                stored.opportunity_evaluation,
                stored.attention_evaluation,
                agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
                agents_sha256=stored.manifest.agents_sha256,
                trace_sha256=stored.manifest.trace_sha256,
                days=stored.manifest.days,
                source_run_schema_version=10,
            ),
            "cognition_input": stored.cognition_input,
            "cognition": stored.cognition_evaluation,
        }
    )

    def file_sha256(relative: str) -> str:
        return sha256((directory / relative).read_bytes()).hexdigest()

    def lf_sha256(document: object) -> str:
        return sha256((canonical_json(document) + "\n").encode("utf-8")).hexdigest()

    state = json.loads((directory / "outputs/response-state.json").read_bytes())
    assert metrics.response_stream_sha256 == file_sha256("outputs/spatial-responses.jsonl")
    assert metrics.response_state_sha256 == file_sha256("outputs/response-state.json")
    assert metrics.response_summary_sha256 == file_sha256("outputs/response-summary.json")
    assert metrics.rule_state_document_sha256 == lf_sha256(state["rule"])
    assert metrics.cognition_state_document_sha256 == lf_sha256(state["cognition"])
    assert metrics.cognition_terminal_stream_sha256 == file_sha256("outputs/cognition.jsonl")
    assert metrics.cognition_summary_sha256 == file_sha256("outputs/cognition-summary.json")
    assert (
        metrics.cognition_input_sha256
        == sha256(
            (directory / "inputs/cognition.json").read_bytes().removesuffix(b"\n")
        ).hexdigest()
    )
    assert (
        metrics.response_input_sha256
        == sha256(
            (directory / "inputs/spatial-response.json").read_bytes().removesuffix(b"\n")
        ).hexdigest()
    )
    assert metrics == _derive(_arguments(prepared))


def test_unselected_and_budget_skipped_responses_keep_exact_cardinality_equations(
    tmp_path: Path,
) -> None:
    _, prepared, arguments = _metric_inputs(
        tmp_path, mode="local", source="provider", mutation=_dense
    )

    metrics = _derive(arguments)
    cognition = metrics.provider_dependent_cognition
    coverage = cognition.coverage

    assert coverage.unselected_response_count > 0
    assert coverage.budget_skipped_count > 0
    assert coverage.selected_request_count <= 4 * metrics.population_size
    assert coverage.eligible_response_count == (
        coverage.funded_request_count
        + coverage.budget_skipped_count
        + coverage.unselected_response_count
    )
    assert coverage.applied_response_count == coverage.eligible_response_count
    assert cognition.neutralization_counts.neutralized_response_count == (
        coverage.unselected_response_count + coverage.budget_skipped_count
    )
    assert cognition.funded_coverage.value == (
        coverage.funded_request_count / coverage.eligible_response_count
    )
    assert cognition.funded_coverage.value < 1.0
    identity = [
        record
        for record in prepared.response_evaluation.records
        if isinstance(record, SpatialAppliedResponseV2)
        and record.neutralization_status in {"unselected", "budget-skipped"}
    ]
    assert len(identity) == coverage.unselected_response_count + coverage.budget_skipped_count
    assert all(
        (record.applied_sentiment_delta, record.applied_recall_delta)
        == (record.baseline.sentiment_delta, record.baseline.recall_delta)
        for record in identity
    )


@pytest.mark.parametrize(
    ("mode", "source"),
    [("rules", None), ("mock", None), ("local", "provider"), ("local", "fallback")],
)
def test_rule_baseline_receipts_equal_v1_derivation_from_identical_rule_evidence(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    staged, prepared, arguments = _metric_inputs(tmp_path, **_options(mode, source))
    metrics = _derive(arguments)
    legacy_attention = derive_spatial_metrics(
        prepared.opportunity_evaluation,
        prepared.attention_evaluation,
        agent_ids=arguments["agent_ids"],
        agents_sha256=prepared.manifest.agents_sha256,
        trace_sha256=prepared.manifest.trace_sha256,
        days=prepared.manifest.days,
        source_run_schema_version=9,
    )
    legacy = derive_spatial_response_metrics(
        prepared.response_input,
        staged.baseline_evaluation,
        scenario=prepared.spatial_scenario,
        opportunities=prepared.opportunity_evaluation,
        attention=prepared.attention_evaluation,
        agent_ids=arguments["agent_ids"],
        attention_metrics=legacy_attention,
    )

    baseline = metrics.rule_baseline
    for field in (
        "response_count",
        "response_reach",
        "response_frequency",
        "mean_rule_sentiment_delta",
        "mean_rule_recall_delta",
        "brand_sentiment",
        "recall_strength",
        "purchase_intention_proxy",
    ):
        assert canonical_json(getattr(baseline, field)) == canonical_json(
            getattr(legacy.overall, field)
        ), field
    assert baseline.channels == legacy.channels
    assert baseline.campaigns == legacy.campaigns
    assert [canonical_json(item) for item in (*baseline.channels, *baseline.campaigns)] == [
        canonical_json(item) for item in (*legacy.channels, *legacy.campaigns)
    ]
    for field in (
        "scenario_sha256",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "opportunity_structure_sha256",
        "response_input_sha256",
        "response_assumption_structure_sha256",
        "seed",
        "population_size",
        "days",
        "campaign_count",
    ):
        assert getattr(metrics, field) == getattr(legacy, field), field


def test_provider_signals_cannot_change_rule_baseline_or_purchase_proxy(tmp_path: Path) -> None:
    staged, _, _ = _metric_inputs(tmp_path, mode="local", source="provider", agents=8)
    low = _finalize_with_vector(
        staged,
        affect="annoyed",
        relevance="low",
        credibility="low",
        salience="none",
    )
    high = _finalize_with_vector(
        staged,
        affect="positive",
        relevance="high",
        credibility="high",
        salience="high",
    )

    low_metrics = _derive(_arguments(low))
    high_metrics = _derive(_arguments(high))

    assert low_metrics.rule_baseline == high_metrics.rule_baseline
    assert canonical_json(low_metrics.rule_baseline) == canonical_json(high_metrics.rule_baseline)
    assert canonical_json(low_metrics.rule_baseline.purchase_intention_proxy) == canonical_json(
        high_metrics.rule_baseline.purchase_intention_proxy
    )
    assert low_metrics.provider_dependent_cognition != high_metrics.provider_dependent_cognition
    left = low_metrics.model_dump(mode="json")
    right = high_metrics.model_dump(mode="json")
    assert {field for field in left if left[field] != right[field]} == {
        "response_stream_sha256",
        "response_state_sha256",
        "response_summary_sha256",
        "cognition_state_document_sha256",
        "cognition_terminal_stream_sha256",
        "cognition_summary_sha256",
        "provider_dependent_cognition",
    }
    left_cognition = left["provider_dependent_cognition"]
    right_cognition = right["provider_dependent_cognition"]
    assert {
        field for field in left_cognition if left_cognition[field] != right_cognition[field]
    } == {
        "cognition_terminal_stream_sha256",
        "cognition_summary_sha256",
        "category_counts",
        "mean_applied_sentiment",
        "mean_applied_recall",
        "synthetic_sentiment",
        "synthetic_recall",
    }


@pytest.mark.parametrize(
    "vector",
    [
        pytest.param({"affect": "uncertain"}, id="uncertain"),
        pytest.param({"safety_flags": ("unsafe-content",)}, id="safety"),
        pytest.param({"salience": "uncertain", "safety_flags": ("stereotype-risk",)}, id="both"),
    ],
)
def test_uncertain_and_safety_signals_neutralize_without_touching_the_rule_baseline(
    tmp_path: Path, vector: dict[str, Any]
) -> None:
    staged, _, _ = _metric_inputs(tmp_path, mode="local", source="provider")
    signals = {
        "affect": "positive",
        "relevance": "high",
        "credibility": "high",
        "salience": "high",
    } | vector
    neutralized = _derive(_arguments(_finalize_with_vector(staged, **signals)))
    applied = _derive(
        _arguments(
            _finalize_with_vector(
                staged, affect="positive", relevance="high", credibility="high", salience="high"
            )
        )
    )

    cognition = neutralized.provider_dependent_cognition
    counts = cognition.neutralization_counts
    assert neutralized.rule_baseline == applied.rule_baseline
    assert counts.neutralized_response_count == cognition.coverage.eligible_response_count
    assert counts.uncertain_result_count == (
        cognition.coverage.funded_request_count if "uncertain" in signals.values() else 0
    )
    assert counts.safety_neutralized_result_count == (
        cognition.coverage.funded_request_count if signals.get("safety_flags") else 0
    )
    assert cognition.synthetic_sentiment.final_total == (
        neutralized.rule_baseline.brand_sentiment.final_total
    )
    assert cognition.synthetic_recall.final_total == (
        neutralized.rule_baseline.recall_strength.final_total
    )
    assert applied.provider_dependent_cognition.synthetic_recall.final_total != (
        applied.rule_baseline.recall_strength.final_total
    )


@pytest.mark.parametrize(("mode", "source"), [("rules", None), ("local", "fallback")])
def test_neutral_cognition_is_an_exact_identity_over_the_rule_baseline(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    _, _, arguments = _metric_inputs(tmp_path, **_options(mode, source))

    metrics = _derive(arguments)
    baseline = metrics.rule_baseline
    cognition = metrics.provider_dependent_cognition

    assert cognition.neutralization_counts.neutralized_response_count == (
        cognition.coverage.eligible_response_count
    )
    assert (
        cognition.mean_applied_sentiment.numerator == baseline.mean_rule_sentiment_delta.numerator
    )
    assert cognition.mean_applied_sentiment.value == baseline.mean_rule_sentiment_delta.value
    assert cognition.mean_applied_recall.numerator == baseline.mean_rule_recall_delta.numerator
    for cognitive, rule in (
        (cognition.synthetic_sentiment, baseline.brand_sentiment),
        (cognition.synthetic_recall, baseline.recall_strength),
    ):
        for field in (
            "initial_total",
            "final_total",
            "change_total",
            "denominator",
            "initial_mean",
            "final_mean",
            "mean_change",
        ):
            assert getattr(cognitive, field) == getattr(rule, field), field


@pytest.mark.parametrize("mode", ["rules", "mock", "local"])
def test_empty_v10_metrics_have_positive_zero_and_complete_state_denominators(
    tmp_path: Path, mode: str
) -> None:
    _, prepared, arguments = _metric_inputs(tmp_path, mode=mode, empty=True)

    metrics = _derive(arguments)
    cognition = metrics.provider_dependent_cognition

    assert metrics.rule_baseline.response_count.value == 0.0
    assert math.copysign(1.0, metrics.rule_baseline.response_count.value) == 1.0
    assert cognition.mean_applied_sentiment.value == 0.0
    assert cognition.mean_applied_recall.value == 0.0
    assert cognition.funded_coverage.value == 0.0
    assert all(
        math.copysign(1.0, value) == 1.0
        for value in (
            cognition.mean_applied_sentiment.value,
            cognition.mean_applied_sentiment.numerator,
            cognition.mean_applied_recall.value,
            cognition.mean_applied_recall.numerator,
            cognition.funded_coverage.value,
            cognition.synthetic_sentiment.change_total,
            cognition.synthetic_sentiment.mean_change,
            cognition.synthetic_recall.change_total,
            cognition.synthetic_recall.mean_change,
        )
    )
    assert cognition.mean_applied_sentiment.denominator == 0
    assert cognition.funded_coverage.denominator == 0
    assert cognition.synthetic_sentiment.denominator == prepared.manifest.final_state_count
    assert cognition.synthetic_recall.denominator == prepared.manifest.final_state_count
    assert cognition.synthetic_sentiment.final_total == cognition.synthetic_sentiment.initial_total
    assert cognition.funded_response_limit == (60 if mode in {"rules", "mock"} else 10)
    serialized = canonical_json(metrics)
    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    assert "-0.0" not in serialized
    category_values = cognition.category_counts.model_dump(mode="json")
    assert all(
        value == 0
        for family, counts in category_values.items()
        if family != "schema_version"
        for value in counts.values()
    )
    assert set(cognition.coverage.model_dump(exclude={"schema_version"}).values()) == {0}
    assert set(cognition.source_counts.model_dump(exclude={"schema_version"}).values()) == {0}
    assert set(cognition.fallback_counts.model_dump(exclude={"schema_version"}).values()) == {0}
    assert set(cognition.neutralization_counts.model_dump(exclude={"schema_version"}).values()) == {
        0
    }


@pytest.mark.parametrize(
    "vector",
    [
        pytest.param(("positive", "high", "high", "high"), id="strongest"),
        pytest.param(("annoyed", "none", "low", "none"), id="weakest"),
    ],
)
def test_cognition_values_stay_within_frozen_policy_bounds(
    tmp_path: Path, vector: tuple[str, str, str, str]
) -> None:
    staged, _, _ = _metric_inputs(tmp_path, mode="local", source="provider", mutation=_dense)
    affect, relevance, credibility, salience = vector
    prepared = _finalize_with_vector(
        staged, affect=affect, relevance=relevance, credibility=credibility, salience=salience
    )

    cognition = _derive(_arguments(prepared)).provider_dependent_cognition

    assert -0.2 <= cognition.mean_applied_sentiment.value <= 0.2
    assert 0.0 <= cognition.mean_applied_recall.value <= 0.3
    assert -1.0 <= cognition.synthetic_sentiment.final_mean <= 1.0
    assert 0.0 <= cognition.synthetic_recall.final_mean <= 1.0
    assert 0.0 <= cognition.funded_coverage.value <= 1.0
    assert all(
        math.isfinite(value)
        for receipt in (
            cognition.mean_applied_sentiment,
            cognition.mean_applied_recall,
            cognition.funded_coverage,
        )
        for value in (float(receipt.numerator), receipt.value)
    )


@pytest.mark.parametrize("version", [True, 10.0, "10", 9, 11, 6])
def test_v2_metric_document_requires_exact_source_version(tmp_path: Path, version: object) -> None:
    _, _, arguments = _metric_inputs(tmp_path, mode="rules")
    metrics = _derive(arguments)
    document = metrics.model_dump(mode="python")
    document["source_run_schema_version"] = version

    with pytest.raises(ValidationError, match="source run schema version"):
        _module().SpatialResponseMetricsV2.model_validate(document)


@pytest.mark.parametrize(("mode", "source"), [("mock", None), ("local", "fallback")])
def test_v2_metric_documents_round_trip_through_canonical_json(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    _, _, arguments = _metric_inputs(tmp_path, **_options(mode, source))
    metrics = _derive(arguments)

    restored = _module().SpatialResponseMetricsV2.model_validate_json(canonical_json(metrics))

    assert restored == metrics
    assert canonical_json(restored) == canonical_json(metrics)
    with pytest.raises(ValidationError, match="frozen"):
        metrics.__setattr__("seed", 1)


def test_v1_and_v2_metric_derivations_refuse_the_other_response_version(tmp_path: Path) -> None:
    from tests.integration.test_city_cognition_artifacts import _evaluation, _prepared

    staged, prepared, arguments = _metric_inputs(tmp_path, mode="rules")
    v1_arguments = {
        name: arguments[name]
        for name in ("response_input", "scenario", "opportunities", "attention", "agent_ids")
    }
    with pytest.raises(TypeError, match="SpatialResponseEvaluation"):
        derive_spatial_response_metrics(
            response=arguments["response"],
            attention_metrics=arguments["attention_metrics"],
            **v1_arguments,
        )
    with pytest.raises(ValueError, match="source run schema 6, 7, 8 or 9"):
        derive_spatial_response_metrics(
            response=staged.baseline_evaluation,
            attention_metrics=arguments["attention_metrics"],
            **v1_arguments,
        )

    invalid = dict(arguments)
    invalid["response"] = staged.baseline_evaluation
    with pytest.raises(TypeError, match="SpatialResponseEvaluationV2"):
        _derive(invalid)
    invalid = dict(arguments)
    invalid["attention_metrics"] = arguments["attention_metrics"].model_copy(
        update={"source_run_schema_version": 9}
    )
    with pytest.raises(ValueError, match="source run schema 10"):
        _derive(invalid)

    legacy = _prepared(mode="rules")
    for field, value in (
        ("cognition_input", legacy.cognition_input),
        ("cognition", _evaluation(legacy)),
        ("cognition", prepared.cognition_evaluation.summary),
    ):
        invalid = dict(arguments)
        invalid[field] = value
        with pytest.raises(TypeError, match="CityCognition"):
            _derive(invalid)

    v2_document = _derive(arguments).model_dump(mode="python")
    with pytest.raises(ValidationError):
        SpatialResponseMetrics.model_validate(v2_document)
    v1_document = derive_spatial_response_metrics(
        response=staged.baseline_evaluation,
        attention_metrics=arguments["attention_metrics"].model_copy(
            update={"source_run_schema_version": 9}
        ),
        **v1_arguments,
    ).model_dump(mode="python")
    with pytest.raises(ValidationError):
        _module().SpatialResponseMetricsV2.model_validate(v1_document)


def _forged_summary(cognition: CityCognitionEvaluationV2, **changes: Any):
    forged = cognition.summary.model_construct(
        **(cognition.summary.model_dump(mode="python") | changes)
    )
    return CityCognitionEvaluationV2.model_construct(
        **(cognition.model_dump(mode="python") | {"summary": forged})
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "policy",
        "terminal-stream",
        "summary",
        "foreign-cognition",
        "foreign-response",
        "foreign-signals",
        "foreign-response-input",
        "attention-metrics",
        "population",
        "cognition-input-contract",
    ],
)
def test_v2_derivation_revalidates_cross_bound_response_and_cognition_evidence(
    tmp_path: Path,
    tamper: str,
) -> None:
    staged, _, arguments = _metric_inputs(tmp_path, mode="local", source="provider")
    invalid = dict(arguments)
    response = arguments["response"]
    cognition = arguments["cognition"]
    assert isinstance(response, SpatialResponseEvaluationV2)
    assert isinstance(cognition, CityCognitionEvaluationV2)
    if tamper == "policy":
        invalid["response"] = SpatialResponseEvaluationV2.model_construct(
            **(response.model_dump(mode="python") | {"influence_policy_sha256": "0" * 64})
        )
    elif tamper == "terminal-stream":
        invalid["cognition"] = _forged_summary(cognition, terminal_stream_sha256="0" * 64)
    elif tamper == "summary":
        coverage = cognition.summary.coverage
        invalid["cognition"] = _forged_summary(
            cognition,
            coverage=coverage.model_construct(
                **(
                    coverage.model_dump(mode="python")
                    | {"eligible_response_count": coverage.eligible_response_count + 1}
                )
            ),
        )
    elif tamper == "foreign-cognition":
        _, _, other = _metric_inputs(
            tmp_path / "other", mode="local", source="provider", run_id="other-run"
        )
        invalid["cognition_input"] = other["cognition_input"]
        invalid["cognition"] = other["cognition"]
    elif tamper == "foreign-response":
        _, _, other = _metric_inputs(tmp_path / "other", mode="local", source="cache")
        invalid["response"] = other["response"]
    elif tamper == "foreign-signals":
        other = _finalize_with_vector(
            staged, affect="annoyed", relevance="low", credibility="low", salience="none"
        )
        invalid["cognition"] = other.cognition_evaluation
    elif tamper == "foreign-response-input":
        _, _, other = _metric_inputs(tmp_path / "other", mode="mock", mutation=_dense)
        invalid["response_input"] = other["response_input"]
    elif tamper == "attention-metrics":
        # Agents/trace hashes are copied provenance that the analysis layer binds to the
        # manifest; the opportunity structure is re-derived and must match exactly.
        invalid["attention_metrics"] = arguments["attention_metrics"].model_copy(
            update={"opportunity_structure_sha256": "e" * 64}
        )
    elif tamper == "population":
        invalid["agent_ids"] = arguments["agent_ids"][:-1]
    else:
        frozen = arguments["cognition_input"]
        assert isinstance(frozen, CityCognitionInputV2)
        invalid["cognition_input"] = CityCognitionInputV2.model_construct(
            **(frozen.model_dump(mode="python") | {"causal_contract_sha256": "0" * 64})
        )

    with pytest.raises(ValueError):
        _derive(invalid)


def _zero_rule_sentiment(document) -> None:
    # No interest overlap, zero novelty/skepticism, no affordability and one notice per
    # day make every baseline sentiment step exactly 0.0 for agents that respond.
    persona = document["population"]["personas"][0]
    persona["interests"] = ["commuting"]
    persona["traits"].update(novelty_seeking=0.0, advertising_skepticism=0.0, price_sensitivity=1.0)
    document["scenario"]["campaign"]["target_interests"] = ["technology"]
    document["scenario"]["campaign"]["relative_price"] = 2.0
    document["scenario"]["phone"]["frequency_cap_per_agent_per_day"] = 1


def test_v2_derivation_refuses_a_signed_zero_final_state_hidden_by_model_equality(
    tmp_path: Path,
) -> None:
    from adlife.core.simulation.spatial_response_v2 import summarize_spatial_response_artifact_v2

    _, _, arguments = _metric_inputs(tmp_path, mode="rules", mutation=_zero_rule_sentiment)
    response = arguments["response"]
    states = list(response.final_rule_states)
    index = next(
        position
        for position, state in enumerate(states)
        if state.response_count and state.brand_sentiment == 0.0
    )
    states[index] = type(states[index]).model_construct(
        **(states[index].model_dump(mode="python") | {"brand_sentiment": -0.0})
    )
    forged = SpatialResponseEvaluationV2.model_validate(
        response.model_dump(mode="python") | {"final_rule_states": tuple(states)}
    )
    # The forged evidence is model-valid and numerically equal, yet its bytes differ.
    assert forged == response
    assert summarize_spatial_response_artifact_v2(forged).state_document_sha256 != (
        summarize_spatial_response_artifact_v2(response).state_document_sha256
    )

    with pytest.raises(ValueError, match="baseline and frozen signals"):
        _derive(dict(arguments, response=forged))
    assert _derive(arguments).response_state_sha256 == (
        summarize_spatial_response_artifact_v2(response).state_document_sha256
    )


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_cognition_metrics_copy_only_safe_metadata_not_provider_transport(
    tmp_path: Path, mode: str
) -> None:
    _, prepared, arguments = _metric_inputs(tmp_path, mode=mode, source="provider")
    profile = prepared.cognition_input.provider_profile
    settings = prepared.cognition_input.effective_settings
    assert profile is not None and settings is not None

    document = _derive(arguments).provider_dependent_cognition.model_dump(mode="json")
    serialized = canonical_json(document)

    assert document["provider_profile_id"] == profile.profile_id == "provider-main"
    assert document["provider_model_id"] == profile.model_id == "fictional-explainer"
    assert document["provider_revision_sha256"] == profile.revision_sha256
    assert document["provider_profile_snapshot_sha256"] == profile.fingerprint
    assert document["effective_settings_sha256"] == settings.fingerprint
    for value in (
        profile.base_url,
        profile.credential_slot_id,
        profile.display_label,
        profile.origin_approval_id,
    ):
        # A fixture may reuse the copied profile ID as its origin approval ID.
        if value not in {profile.profile_id, profile.model_id}:
            assert value not in serialized
    keys = _keys(document)
    for key in (
        "base_url",
        "credential_slot_id",
        "display_label",
        "origin_approval_id",
        "raw_response",
        "prompt_tokens",
        "completion_tokens",
        "attempt_count",
        "repair_context",
    ):
        assert key not in keys
    assert "authorization" not in serialized.casefold()
    assert "http" not in serialized.casefold()
    assert (
        document["cognition_summary_sha256"]
        == sha256(
            (canonical_json(arguments["cognition"].summary) + "\n").encode("utf-8")
        ).hexdigest()
    )


@pytest.mark.parametrize(("mode", "source"), MODE_MATRIX)
def test_cognition_family_has_no_purchase_delta_effect_or_lift_member(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    _, _, arguments = _metric_inputs(tmp_path, **_options(mode, source))

    document = _derive(arguments).provider_dependent_cognition.model_dump(mode="json")

    tokens = {token for key in _keys(document) for token in key.split("_")}
    assert not tokens & FORBIDDEN_COGNITION_TOKENS
    assert "purchase" not in canonical_json(document)


def test_category_count_families_follow_the_result_contract_order() -> None:
    module = _module()
    for family, categories in (
        (module.SpatialCognitionAffectCountsV2, CityCognitionAffectV2),
        (module.SpatialCognitionRelevanceCountsV2, CityCognitionRelevanceV2),
        (module.SpatialCognitionCredibilityCountsV2, CityCognitionCredibilityV2),
        (module.SpatialCognitionSalienceCountsV2, CityCognitionSalienceV2),
    ):
        assert tuple(family.model_fields) == get_args(categories)
    assert tuple(module.SpatialCognitionCategoryCountsV2.model_fields) == (
        "schema_version",
        "affect",
        "relevance",
        "credibility",
        "salience",
    )


def test_category_count_contract_discloses_fixed_neutral_terminals() -> None:
    # The count keys stay exact (saved-run inspectors validate them), so the inclusion of
    # AdLife's fixed neutral vectors is documented in the published JSON schema instead.
    schema = _module().SpatialProviderDependentCognitionMetricsV2.model_json_schema()

    description = schema["properties"]["category_counts"]["description"]

    assert description.startswith("Categories of every selected terminal result")
    assert "fixed neutral vectors of rules, fallback, and budget-skipped terminals" in description
    assert "source_counts" in description


@pytest.mark.parametrize(
    ("case", "mode", "source", "message"),
    [
        ("category-total", "mock", None, "same terminals"),
        ("source-total", "mock", None, "source, fallback, and coverage"),
        ("fallback-source", "mock", None, "source, fallback, and coverage"),
        ("funded-coverage", "local", "provider", "funded"),
        ("applied-denominator", "mock", None, "every eligible response"),
        ("state-denominator", "mock", None, "complete-grid denominator"),
        ("partial-provider", "local", "provider", "complete non-secret provenance"),
        ("offline-provider", "mock", None, "resolve locally"),
        ("offline-settings", "rules", None, "resolve locally"),
        ("policy-hash", "mock", None, "frozen V2 causal contract"),
        ("outer-provenance", "mock", None, "outer provenance"),
        ("outer-influence", "mock", None, "outer provenance"),
        ("eligible-baseline", "mock", None, "rule baseline responses"),
        ("initial-state", "mock", None, "one initial state"),
        ("neutral-identity", "rules", None, "exact identity"),
        ("category-uncertain", "local", "provider", "terminal sources"),
        ("neutral-floor", "local", "fallback", "terminal sources"),
        ("neutralization", "mock", None, "neutralization counts"),
        ("funded-limit-bool", "mock", None, "funded response limit"),
        ("funded-limit-float", "mock", None, "funded response limit"),
        ("funded-prefix", "local", "provider", "funded prefix"),
        ("negative-zero", "rules-empty", None, "positive quotient"),
        ("integer-scalar", "rules-empty", None, "exact floats"),
        ("channel-order", "mock", None, "roadside then mobile"),
        ("population", "mock", None, "population"),
        ("campaign-count", "mock", None, "population and campaigns"),
        ("unsafe-model", "local", "provider", "provider_model_id"),
    ],
)
def test_v2_metric_model_rejects_internally_inconsistent_receipts(
    tmp_path: Path,
    case: str,
    mode: str,
    source: str | None,
    message: str,
) -> None:
    options = {"mode": "rules", "empty": True} if mode == "rules-empty" else _options(mode, source)
    _, _, arguments = _metric_inputs(tmp_path, **options)
    metrics = _derive(arguments)
    document = metrics.model_dump(mode="python")
    cognition = document["provider_dependent_cognition"]

    if case == "category-total":
        cognition["category_counts"]["affect"]["positive"] += 1
    elif case == "source-total":
        cognition["source_counts"]["mock"] += 1
    elif case == "fallback-source":
        cognition["fallback_counts"]["invalid_result"] += 1
    elif case == "funded-coverage":
        receipt = cognition["funded_coverage"]
        receipt["numerator"] -= 1
        _rehash_receipt(receipt)
    elif case == "applied-denominator":
        receipt = cognition["mean_applied_sentiment"]
        receipt["denominator"] += 1
        _rehash_receipt(receipt)
    elif case == "state-denominator":
        receipt = cognition["synthetic_recall"]
        receipt["denominator"] += 1
        receipt["initial_mean"] = receipt["initial_total"] / receipt["denominator"]
        receipt["final_mean"] = receipt["final_total"] / receipt["denominator"]
        receipt["mean_change"] = receipt["change_total"] / receipt["denominator"]
    elif case == "partial-provider":
        cognition["provider_model_id"] = None
    elif case == "offline-provider":
        cognition["provider_profile_id"] = "provider-main"
    elif case == "offline-settings":
        cognition["effective_settings_sha256"] = "e" * 64
    elif case == "policy-hash":
        cognition["prompt_contract_sha256"] = "e" * 64
    elif case == "outer-provenance":
        document["cognition_input_sha256"] = "e" * 64
    elif case == "outer-influence":
        document["influence_policy_sha256"] = "e" * 64
    elif case == "eligible-baseline":
        coverage = cognition["coverage"]
        for field in (
            "eligible_response_count",
            "unselected_response_count",
            "applied_response_count",
        ):
            coverage[field] += 1
        cognition["neutralization_counts"]["neutralized_response_count"] += 1
        for name in ("funded_coverage", "mean_applied_sentiment", "mean_applied_recall"):
            cognition[name]["denominator"] += 1
            _rehash_receipt(cognition[name])
    elif case == "initial-state":
        receipt = cognition["synthetic_sentiment"]
        receipt["initial_total"] += 0.25
        receipt["change_total"] = receipt["final_total"] - receipt["initial_total"]
        receipt["initial_mean"] = receipt["initial_total"] / receipt["denominator"]
        receipt["mean_change"] = receipt["change_total"] / receipt["denominator"]
    elif case == "neutral-identity":
        receipt = cognition["synthetic_recall"]
        receipt["final_total"] = receipt["final_total"] * 0.5
        receipt["change_total"] = receipt["final_total"] - receipt["initial_total"]
        receipt["final_mean"] = receipt["final_total"] / receipt["denominator"]
        receipt["mean_change"] = receipt["change_total"] / receipt["denominator"]
    elif case == "category-uncertain":
        affect = cognition["category_counts"]["affect"]
        affect["positive"] -= 1
        affect["uncertain"] += 1
    elif case == "neutral-floor":
        affect = cognition["category_counts"]["affect"]
        affect["neutral"] -= 1
        affect["curious"] += 1
    elif case == "neutralization":
        cognition["neutralization_counts"]["neutralized_response_count"] += 1
    elif case == "funded-limit-bool":
        cognition["funded_response_limit"] = True
    elif case == "funded-limit-float":
        cognition["funded_response_limit"] = 60.0
    elif case == "funded-prefix":
        cognition["funded_response_limit"] = 20
    elif case == "negative-zero":
        cognition["mean_applied_sentiment"]["value"] = -0.0
    elif case == "integer-scalar":
        cognition["mean_applied_recall"]["numerator"] = 0
    elif case == "channel-order":
        channels = document["rule_baseline"]["channels"]
        document["rule_baseline"]["channels"] = (channels[1], channels[0])
    elif case == "population":
        document["population_size"] += 1
    elif case == "campaign-count":
        document["campaign_count"] += 1
    else:
        cognition["provider_model_id"] = "model‮explainer"

    with pytest.raises(ValidationError, match=re.escape(message)):
        _module().SpatialResponseMetricsV2.model_validate(document)


def _two_campaign_v1_metrics() -> SpatialResponseMetrics:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                campaign_id="phone-campaign",
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=2,
            ),
        ],
    )
    opportunities = _evaluate(_mobility(pack), scenario)
    attention = _evaluate_attention(opportunities, seed=42)
    response_input = _response_input(scenario)
    agent_ids = ("person-001",)
    from adlife.core.simulation.spatial_response import evaluate_spatial_responses

    return derive_spatial_response_metrics(
        response_input,
        evaluate_spatial_responses(
            response_input, scenario, opportunities, attention, agent_ids=agent_ids
        ),
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        agent_ids=agent_ids,
        attention_metrics=derive_spatial_metrics(
            opportunities,
            attention,
            agent_ids=agent_ids,
            agents_sha256="a" * 64,
            trace_sha256="b" * 64,
            days=1,
            source_run_schema_version=6,
        ),
    )


def test_rule_baseline_model_reuses_v1_receipts_and_refuses_noncanonical_slices() -> None:
    model = _module().SpatialRuleBaselineMetricsV2
    legacy = _two_campaign_v1_metrics()
    fields = {
        name: getattr(legacy.overall, name)
        for name in (
            "response_count",
            "response_reach",
            "response_frequency",
            "mean_rule_sentiment_delta",
            "mean_rule_recall_delta",
            "brand_sentiment",
            "recall_strength",
            "purchase_intention_proxy",
        )
    }

    baseline = model(**fields, channels=legacy.channels, campaigns=legacy.campaigns)

    assert tuple(item.campaign_id for item in baseline.campaigns) == (
        "fictional-launch",
        "phone-campaign",
    )
    assert baseline.purchase_intention_proxy == legacy.overall.purchase_intention_proxy
    first, second = legacy.campaigns
    for campaigns, error in (
        ((second, first), "unique and canonical"),
        ((first, first), "unique and canonical"),
        ((second,), "slice counts"),
        ((first,), "every agent/campaign pair"),
    ):
        with pytest.raises(ValidationError, match=error):
            model(**fields, channels=legacy.channels, campaigns=campaigns)
    with pytest.raises(ValidationError, match="roadside then mobile"):
        model(
            **fields, channels=(legacy.channels[1], legacy.channels[0]), campaigns=legacy.campaigns
        )
    forged = type(fields["response_count"]).model_construct(
        **(fields["response_count"].model_dump(mode="python") | {"numerator": True})
    )
    with pytest.raises(ValidationError, match="integer"):
        model(
            **(fields | {"response_count": forged}),
            channels=legacy.channels,
            campaigns=legacy.campaigns,
        )
    with pytest.raises(ValidationError, match="wrong field"):
        model(
            **(fields | {"response_reach": fields["response_count"]}),
            channels=legacy.channels,
            campaigns=legacy.campaigns,
        )


def test_cognition_receipt_models_are_strict_and_bounded() -> None:
    module = _module()
    with pytest.raises(ValidationError, match="positive quotient"):
        module.SpatialCognitionMetricReceiptV2(
            name="mean_applied_sentiment", numerator=0.1, denominator=2, value=0.1
        )
    with pytest.raises(ValidationError, match="zero numerator"):
        module.SpatialCognitionMetricReceiptV2(
            name="mean_applied_recall", numerator=0.1, denominator=0, value=0.0
        )
    with pytest.raises(ValidationError, match="cannot be negative"):
        module.SpatialCognitionMetricReceiptV2(
            name="mean_applied_recall", numerator=-0.1, denominator=1, value=-0.1
        )
    with pytest.raises(ValidationError, match="sources are not canonical"):
        module.SpatialCognitionMetricReceiptV2(
            name="mean_applied_recall",
            numerator=0.1,
            denominator=1,
            value=0.1,
            source_artifacts=(),
        )
    with pytest.raises(ValidationError, match="funded requests over eligible"):
        module.SpatialCognitionCoverageReceiptV2(numerator=2, denominator=1, value=1.0)
    with pytest.raises(ValidationError):
        module.SpatialCognitionCoverageReceiptV2(numerator=True, denominator=1, value=1.0)
    with pytest.raises(ValidationError, match="complete-grid totals"):
        module.SpatialCognitionStateReceiptV2(
            name="synthetic_sentiment",
            initial_total=0.5,
            final_total=0.25,
            change_total=0.0,
            denominator=1,
            initial_mean=0.5,
            final_mean=0.25,
            mean_change=0.0,
        )
    with pytest.raises(ValidationError, match="cannot be negative"):
        module.SpatialCognitionStateReceiptV2(
            name="synthetic_recall",
            initial_total=-0.5,
            final_total=-0.5,
            change_total=0.0,
            denominator=1,
            initial_mean=-0.5,
            final_mean=-0.5,
            mean_change=0.0,
        )
    receipt = module.SpatialCognitionCoverageReceiptV2(numerator=0, denominator=0, value=0.0)
    assert receipt.model_dump(mode="json")["source_artifacts"] == ["outputs/cognition-summary.json"]
