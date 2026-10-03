"""The experiments layer: event-derived metrics, paired comparisons, sensitivity.

This package consumes runs - through the ports the runner already speaks - and never
touches the engine's internals: the metrics fold reads events, boundary states and
usage records handed to it; the comparison and sensitivity runners drive whole runs
through :class:`~adlife.core.simulation.runner.SimulationRunner`. Nothing here draws
randomness of its own except the fixed-seed bootstrap, so a comparison is as
reproducible as the runs it is built from.
"""

from __future__ import annotations

from adlife.core.experiments.design import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    FULL_SEED_COUNT,
    QUICK_SEED_COUNT,
    STABLE_DIRECTION_THRESHOLD,
    ComparisonResult,
    ExperimentDesign,
    ExperimentDesignError,
    PairedStatistic,
    paired_statistics,
    validate_paired_design,
    validate_paired_manifests,
)
from adlife.core.experiments.metrics import (
    METRIC_NAMES,
    MetricsCalculator,
    MetricValue,
    RunMetrics,
)
from adlife.core.experiments.spatial_comparison import (
    SpatialComparisonError,
    SpatialMetricDeltaSeries,
    SpatialMetricsComparison,
    compare_spatial_metrics,
)
from adlife.core.experiments.spatial_metrics import (
    SpatialMetricReceipt,
    SpatialMetrics,
    SpatialMetricSeries,
    derive_spatial_metrics,
    spatial_opportunity_structure_sha256,
)
from adlife.core.experiments.spatial_observations import (
    SpatialMetricObservation,
    SpatialStudyMetricArtifact,
    spatial_metric_observations,
)
from adlife.core.experiments.spatial_response_comparison import (
    SpatialResponseAggregateDeltaSeries,
    SpatialResponseCampaignDeltaSeries,
    SpatialResponseChannelDeltaSeries,
    SpatialResponseComparisonError,
    SpatialResponseEventDeltaSeries,
    SpatialResponseMetricsComparison,
    SpatialResponseStateDelta,
    compare_spatial_response_metrics,
)
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseCampaignSeries,
    SpatialResponseChannelSeries,
    SpatialResponseMetricReceipt,
    SpatialResponseMetrics,
    SpatialResponseStateReceipt,
    derive_spatial_response_metrics,
    spatial_response_assumption_structure_sha256,
)

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "FULL_SEED_COUNT",
    "METRIC_NAMES",
    "QUICK_SEED_COUNT",
    "STABLE_DIRECTION_THRESHOLD",
    "ComparisonResult",
    "ExperimentDesign",
    "ExperimentDesignError",
    "MetricValue",
    "MetricsCalculator",
    "PairedStatistic",
    "RunMetrics",
    "SpatialComparisonError",
    "SpatialMetricDeltaSeries",
    "SpatialMetricObservation",
    "SpatialMetricReceipt",
    "SpatialMetricSeries",
    "SpatialMetrics",
    "SpatialMetricsComparison",
    "SpatialResponseAggregateDeltaSeries",
    "SpatialResponseCampaignDeltaSeries",
    "SpatialResponseCampaignSeries",
    "SpatialResponseChannelDeltaSeries",
    "SpatialResponseChannelSeries",
    "SpatialResponseComparisonError",
    "SpatialResponseEventDeltaSeries",
    "SpatialResponseMetricReceipt",
    "SpatialResponseMetrics",
    "SpatialResponseMetricsComparison",
    "SpatialResponseStateDelta",
    "SpatialResponseStateReceipt",
    "SpatialStudyMetricArtifact",
    "compare_spatial_metrics",
    "compare_spatial_response_metrics",
    "derive_spatial_metrics",
    "derive_spatial_response_metrics",
    "paired_statistics",
    "spatial_metric_observations",
    "spatial_opportunity_structure_sha256",
    "spatial_response_assumption_structure_sha256",
    "validate_paired_design",
    "validate_paired_manifests",
]
