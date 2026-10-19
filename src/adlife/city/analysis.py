"""Read-only projections from fully verified saved city run artifacts."""

from __future__ import annotations

from typing import TypeAlias

from adlife.city.run_store import StoredCityRun
from adlife.core.domain.city_cognition_v2 import CityCognitionEvaluationV2, CityCognitionInputV2
from adlife.core.domain.city_run import (
    CityRunManifestV5,
    CityRunManifestV6,
    CityRunManifestV7,
    CityRunManifestV8,
    CityRunManifestV9,
    CityRunManifestV10,
)
from adlife.core.experiments.spatial_comparison import (
    SpatialMetricsComparison,
    compare_spatial_metrics,
)
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.experiments.spatial_response_comparison import (
    SpatialResponseMetricsComparison,
    compare_spatial_response_metrics,
)
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseMetrics,
    derive_spatial_response_metrics,
)
from adlife.core.experiments.spatial_response_metrics_v2 import (
    SpatialResponseMetricsV2,
    derive_spatial_response_metrics_v2,
)
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch
from adlife.core.simulation.spatial_response import SpatialResponseEvaluation
from adlife.core.simulation.spatial_response_v2 import SpatialResponseEvaluationV2

StoredSpatialResponseMetrics: TypeAlias = SpatialResponseMetrics | SpatialResponseMetricsV2


def metrics_for_stored_city_run(stored: StoredCityRun) -> SpatialMetrics:
    """Project attention-only metrics from one verified v5-v10 run without writing."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    if not isinstance(
        stored.manifest,
        (
            CityRunManifestV5,
            CityRunManifestV6,
            CityRunManifestV7,
            CityRunManifestV8,
            CityRunManifestV9,
            CityRunManifestV10,
        ),
    ):
        raise SchemaVersionMismatch(
            "spatial metrics require a schema-v5, v6, v7, v8, v9 or v10 city run"
        )
    if stored.opportunity_evaluation is None or stored.attention_evaluation is None:
        raise CorruptRunArtifact("spatial metrics city run has incomplete evidence")
    return derive_spatial_metrics(
        stored.opportunity_evaluation,
        stored.attention_evaluation,
        agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
        agents_sha256=stored.manifest.agents_sha256,
        trace_sha256=stored.manifest.trace_sha256,
        days=stored.manifest.days,
        source_run_schema_version=stored.manifest.schema_version,
    )


def compare_stored_city_runs(
    control: StoredCityRun,
    treatment: StoredCityRun,
) -> SpatialMetricsComparison:
    """Compare two already verified runs; never rerun or mutate either artifact.

    Schema-v5-v9 runs keep their cross-version attention comparison, and a V10 run
    compares with another V10 run. A V10 run is never paired with an earlier schema:
    that mix is refused before either run is projected, as before V10 support.
    """
    for stored in (control, treatment):
        if not isinstance(stored, StoredCityRun):
            raise TypeError("stored must be a StoredCityRun")
    if isinstance(control.manifest, CityRunManifestV10) != isinstance(
        treatment.manifest, CityRunManifestV10
    ):
        raise SchemaVersionMismatch(
            "spatial attention comparison cannot mix a schema-v10 city run with a "
            "schema-v5, v6, v7, v8 or v9 city run"
        )
    control_metrics = metrics_for_stored_city_run(control)
    treatment_metrics = metrics_for_stored_city_run(treatment)
    return compare_spatial_metrics(
        control_metrics,
        treatment_metrics,
        control_run_id=control.manifest.run_id,
        treatment_run_id=treatment.manifest.run_id,
    )


def _response_metrics_v1(stored: StoredCityRun) -> SpatialResponseMetrics:
    if (
        stored.spatial_scenario is None
        or stored.opportunity_evaluation is None
        or stored.attention_evaluation is None
        or stored.response_input is None
        or not isinstance(stored.response_evaluation, SpatialResponseEvaluation)
    ):
        raise CorruptRunArtifact("response city run has incomplete spatial response evidence")
    attention_metrics = metrics_for_stored_city_run(stored)
    return derive_spatial_response_metrics(
        stored.response_input,
        stored.response_evaluation,
        scenario=stored.spatial_scenario,
        opportunities=stored.opportunity_evaluation,
        attention=stored.attention_evaluation,
        agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
        attention_metrics=attention_metrics,
    )


def _require_v10_manifest_binding(
    metrics: SpatialResponseMetricsV2,
    manifest: CityRunManifestV10,
) -> None:
    """Bind every projected metric hash and count to the run's own verified manifest."""
    cognition = metrics.provider_dependent_cognition
    receipt = manifest.cognition
    bindings: tuple[tuple[object, object], ...] = (
        (metrics.scenario_sha256, manifest.scenario_sha256),
        (metrics.city_sha256, manifest.city_sha256),
        (metrics.agents_sha256, manifest.agents_sha256),
        (metrics.trace_sha256, manifest.trace_sha256),
        (metrics.seed, manifest.seed),
        (metrics.population_size, manifest.agent_count),
        (metrics.days, manifest.days),
        (metrics.campaign_count, manifest.response_campaign_count),
        (metrics.response_input_sha256, manifest.response_input_sha256),
        (metrics.response_stream_sha256, manifest.response_stream_sha256),
        (metrics.response_state_sha256, manifest.response_state_sha256),
        (metrics.response_summary_sha256, manifest.response_summary_sha256),
        (metrics.rule_state_document_sha256, manifest.rule_state_document_sha256),
        (metrics.cognition_state_document_sha256, manifest.cognition_state_document_sha256),
        (metrics.influence_policy_sha256, manifest.influence_policy_sha256),
        (metrics.rule_baseline.response_count.numerator, manifest.baseline_response_count),
        (cognition.influence_policy_id, manifest.influence_policy_id),
        (cognition.mode, receipt.mode),
        (cognition.cognition_input_sha256, receipt.cognition_input_sha256),
        (cognition.cognition_terminal_stream_sha256, receipt.terminal_stream_sha256),
        (cognition.cognition_summary_sha256, receipt.summary_sha256),
        (cognition.causal_contract_sha256, receipt.causal_contract_sha256),
        (cognition.prompt_contract_sha256, receipt.prompt_contract_sha256),
        (cognition.result_schema_contract_sha256, receipt.result_schema_contract_sha256),
        (cognition.selection_policy_sha256, receipt.selection_policy_sha256),
        (cognition.fallback_policy_sha256, receipt.fallback_policy_sha256),
        (cognition.mock_policy_sha256, receipt.mock_policy_sha256),
        (cognition.influence_policy_sha256, receipt.influence_policy_sha256),
        (cognition.provider_revision_sha256, receipt.provider_revision_sha256),
        (cognition.provider_profile_snapshot_sha256, receipt.provider_profile_snapshot_sha256),
        (cognition.effective_settings_sha256, receipt.effective_settings_sha256),
        (cognition.funded_response_limit, receipt.funded_response_limit),
        (cognition.coverage, receipt.coverage),
        (cognition.source_counts, receipt.outcomes),
        (cognition.fallback_counts, receipt.reasons),
        (cognition.neutralization_counts, receipt.neutralization),
    )
    if any(left != right for left, right in bindings):
        raise CorruptRunArtifact("V10 response metrics do not match the verified run manifest")


def _response_metrics_v2(
    stored: StoredCityRun,
    manifest: CityRunManifestV10,
) -> SpatialResponseMetricsV2:
    response_evaluation = stored.response_evaluation
    cognition_input = stored.cognition_input
    cognition_evaluation = stored.cognition_evaluation
    if (
        stored.spatial_scenario is None
        or stored.opportunity_evaluation is None
        or stored.attention_evaluation is None
        or stored.response_input is None
        or not isinstance(response_evaluation, SpatialResponseEvaluationV2)
        or not isinstance(cognition_input, CityCognitionInputV2)
        or not isinstance(cognition_evaluation, CityCognitionEvaluationV2)
    ):
        raise CorruptRunArtifact(
            "V10 response city run has incomplete dual-state or cognition evidence"
        )
    attention_metrics = metrics_for_stored_city_run(stored)
    try:
        metrics = derive_spatial_response_metrics_v2(
            stored.response_input,
            response_evaluation,
            scenario=stored.spatial_scenario,
            opportunities=stored.opportunity_evaluation,
            attention=stored.attention_evaluation,
            agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
            attention_metrics=attention_metrics,
            cognition_input=cognition_input,
            cognition=cognition_evaluation,
        )
    except ValueError:
        # Loaded V10 evidence was already verified; any disagreement is corruption.
        raise CorruptRunArtifact(
            "verified V10 city run could not be projected to response metrics"
        ) from None
    _require_v10_manifest_binding(metrics, manifest)
    return metrics


def response_metrics_for_stored_city_run(
    stored: StoredCityRun,
) -> StoredSpatialResponseMetrics:
    """Project response metrics from one verified schema-v6-v10 run without writing.

    The manifest class alone selects the V1 rule-only or V2 rule-plus-cognition
    projection; no evidence is trial-parsed against the other version.
    """
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    manifest = stored.manifest
    if isinstance(manifest, CityRunManifestV10):
        return _response_metrics_v2(stored, manifest)
    if isinstance(
        manifest,
        (CityRunManifestV6, CityRunManifestV7, CityRunManifestV8, CityRunManifestV9),
    ):
        return _response_metrics_v1(stored)
    raise SchemaVersionMismatch(
        "spatial response metrics require a schema-v6, v7, v8, v9 or v10 city run"
    )


def compare_stored_city_response_runs(
    control: StoredCityRun,
    treatment: StoredCityRun,
) -> SpatialResponseMetricsComparison:
    """Compare response metrics from two verified schema-v6-v9 runs without writing."""
    for stored in (control, treatment):
        # The V1 comparison contract has no V2 cognition family. Refuse V10 (alone or
        # mixed with V6-V9) before projecting either run.
        if isinstance(stored, StoredCityRun) and isinstance(stored.manifest, CityRunManifestV10):
            raise SchemaVersionMismatch(
                "spatial response comparison requires schema-v6, v7, v8 or v9 city runs"
            )
    control_metrics = response_metrics_for_stored_city_run(control)
    treatment_metrics = response_metrics_for_stored_city_run(treatment)
    if not isinstance(control_metrics, SpatialResponseMetrics) or not isinstance(
        treatment_metrics, SpatialResponseMetrics
    ):
        raise SchemaVersionMismatch(
            "spatial response comparison requires schema-v6, v7, v8 or v9 city runs"
        )
    return compare_spatial_response_metrics(
        control_metrics,
        treatment_metrics,
        control_run_id=control.manifest.run_id,
        treatment_run_id=treatment.manifest.run_id,
    )


__all__ = [
    "StoredSpatialResponseMetrics",
    "compare_stored_city_response_runs",
    "compare_stored_city_runs",
    "metrics_for_stored_city_run",
    "response_metrics_for_stored_city_run",
]
