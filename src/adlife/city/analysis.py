"""Read-only projections from fully verified saved city run artifacts."""

from __future__ import annotations

from adlife.city.run_store import StoredCityRun
from adlife.core.domain.city_run import CityRunManifestV5
from adlife.core.experiments.spatial_comparison import (
    SpatialMetricsComparison,
    compare_spatial_metrics,
)
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch


def metrics_for_stored_city_run(stored: StoredCityRun) -> SpatialMetrics:
    """Project metrics from one store-verified schema-v5 run without writing to it."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    if not isinstance(stored.manifest, CityRunManifestV5):
        raise SchemaVersionMismatch("spatial metrics require a schema-v5 city run")
    if stored.opportunity_evaluation is None or stored.attention_evaluation is None:
        raise CorruptRunArtifact("schema-v5 city run has incomplete spatial evidence")
    return derive_spatial_metrics(
        stored.opportunity_evaluation,
        stored.attention_evaluation,
        agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
        agents_sha256=stored.manifest.agents_sha256,
        trace_sha256=stored.manifest.trace_sha256,
        days=stored.manifest.days,
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


__all__ = ["compare_stored_city_runs", "metrics_for_stored_city_run"]
