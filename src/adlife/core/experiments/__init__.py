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
    "paired_statistics",
    "validate_paired_design",
    "validate_paired_manifests",
]
