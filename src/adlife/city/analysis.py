"""Read-only projections from fully verified saved city run artifacts."""

from __future__ import annotations

from adlife.city.run_store import StoredCityRun
from adlife.core.domain.city_run import CityRunManifestV5, CityRunManifestV6, CityRunManifestV7
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
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch


def metrics_for_stored_city_run(stored: StoredCityRun) -> SpatialMetrics:
    """Project attention-only metrics from one verified v5/v6/v7 run without writing."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    if not isinstance(
        stored.manifest,
        (CityRunManifestV5, CityRunManifestV6, CityRunManifestV7),
    ):
        raise SchemaVersionMismatch("spatial metrics require a schema-v5, v6 or v7 city run")
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
    """Compare two already verified runs; never rerun or mutate either artifact."""
    control_metrics = metrics_for_stored_city_run(control)
    treatment_metrics = metrics_for_stored_city_run(treatment)
    return compare_spatial_metrics(
        control_metrics,
        treatment_metrics,
        control_run_id=control.manifest.run_id,
        treatment_run_id=treatment.manifest.run_id,
    )


def response_metrics_for_stored_city_run(stored: StoredCityRun) -> SpatialResponseMetrics:
    """Project response metrics from one verified schema-v6/v7 run without writing."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    if not isinstance(stored.manifest, (CityRunManifestV6, CityRunManifestV7)):
        raise SchemaVersionMismatch("spatial response metrics require a schema-v6 or v7 city run")
    if (
        stored.spatial_scenario is None
        or stored.opportunity_evaluation is None
        or stored.attention_evaluation is None
        or stored.response_input is None
        or stored.response_evaluation is None
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


def compare_stored_city_response_runs(
    control: StoredCityRun,
    treatment: StoredCityRun,
) -> SpatialResponseMetricsComparison:
    """Compare response metrics from two verified schema-v6/v7 runs without writing."""
    control_metrics = response_metrics_for_stored_city_run(control)
    treatment_metrics = response_metrics_for_stored_city_run(treatment)
    return compare_spatial_response_metrics(
        control_metrics,
        treatment_metrics,
        control_run_id=control.manifest.run_id,
        treatment_run_id=treatment.manifest.run_id,
    )


__all__ = [
    "compare_stored_city_response_runs",
    "compare_stored_city_runs",
    "metrics_for_stored_city_run",
    "response_metrics_for_stored_city_run",
]
