"""Honest treatment-minus-control deltas for matched synthetic city runs."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.experiments.spatial_metrics import SpatialMetrics, SpatialMetricSeries
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_opportunity import MAX_SPATIAL_OPPORTUNITIES

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"
_METRIC_NAMES = (
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "opportunity_reach",
    "impression_reach",
    "noticed_reach",
    "impression_frequency",
    "notice_rate",
)

SpatialComparisonClassification = Literal["matched-opportunity-structure", "opportunity-confounded"]
SpatialComparisonInterpretation = Literal[
    "Normalized model opportunity structure is identical; deltas remain synthetic and non-causal.",
    (
        "Opportunity volume, timing, channel, agent, placement, or physical/model evidence "
        "differs; deltas are opportunity-confounded."
    ),
]


class SpatialComparisonError(ValueError):
    """Raised when two valid metric documents are not scientifically comparable."""


class SpatialMetricDeltaSeries(DomainModel):
    """Treatment-minus-control values for one fixed metric channel."""

    schema_version: Literal[1] = 1
    channel: Literal["overall", "roadside", "mobile"]
    opportunity_count: float = Field(ge=-MAX_SPATIAL_OPPORTUNITIES, le=MAX_SPATIAL_OPPORTUNITIES)
    impression_count: float = Field(ge=-MAX_SPATIAL_OPPORTUNITIES, le=MAX_SPATIAL_OPPORTUNITIES)
    noticed_count: float = Field(ge=-MAX_SPATIAL_OPPORTUNITIES, le=MAX_SPATIAL_OPPORTUNITIES)
    opportunity_reach: float = Field(ge=-1, le=1)
    impression_reach: float = Field(ge=-1, le=1)
    noticed_reach: float = Field(ge=-1, le=1)
    impression_frequency: float = Field(ge=-MAX_SPATIAL_OPPORTUNITIES, le=MAX_SPATIAL_OPPORTUNITIES)
    notice_rate: float = Field(ge=-1, le=1)


def _delta_series(
    control: SpatialMetricSeries,
    treatment: SpatialMetricSeries,
) -> SpatialMetricDeltaSeries:
    if control.channel != treatment.channel:
        raise SpatialComparisonError("metric channel order differs between the runs")
    return SpatialMetricDeltaSeries(
        channel=control.channel,
        **{
            name: getattr(treatment, name).value - getattr(control, name).value
            for name in _METRIC_NAMES
        },
    )


class SpatialMetricsComparison(DomainModel):
    """Full metric snapshots and auditable raw deltas for one matched run pair."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-metrics-comparison-v1"] = "spatial-metrics-comparison-v1"
    claim_scope: Literal["synthetic-comparison-not-causal-or-observed-effect"] = (
        "synthetic-comparison-not-causal-or-observed-effect"
    )
    classification: SpatialComparisonClassification
    interpretation: SpatialComparisonInterpretation
    control_run_id: str = Field(pattern=_RUN_ID_PATTERN)
    treatment_run_id: str = Field(pattern=_RUN_ID_PATTERN)
    control_scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    treatment_scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    population_size: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    control_opportunity_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    treatment_opportunity_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    control: SpatialMetrics
    treatment: SpatialMetrics
    overall: SpatialMetricDeltaSeries
    channels: tuple[SpatialMetricDeltaSeries, SpatialMetricDeltaSeries]

    @model_validator(mode="after")
    def coherent_comparison(self) -> Self:
        if tuple(item.channel for item in self.channels) != ("roadside", "mobile"):
            raise ValueError("comparison channels must be ordered roadside then mobile")
        if self.overall.channel != "overall":
            raise ValueError("overall comparison must use the overall channel")
        expected_fields = {
            "control_scenario_sha256": self.control.scenario_sha256,
            "treatment_scenario_sha256": self.treatment.scenario_sha256,
            "city_sha256": self.control.city_sha256,
            "agents_sha256": self.control.agents_sha256,
            "trace_sha256": self.control.trace_sha256,
            "seed": self.control.seed,
            "population_size": self.control.population_size,
            "days": self.control.days,
            "control_opportunity_structure_sha256": (self.control.opportunity_structure_sha256),
            "treatment_opportunity_structure_sha256": (self.treatment.opportunity_structure_sha256),
        }
        if any(getattr(self, name) != value for name, value in expected_fields.items()):
            raise ValueError("comparison provenance does not match its source snapshots")
        structure_matches = (
            self.control.opportunity_structure_sha256 == self.treatment.opportunity_structure_sha256
        )
        expected_classification: SpatialComparisonClassification = (
            "matched-opportunity-structure" if structure_matches else "opportunity-confounded"
        )
        expected_interpretation: SpatialComparisonInterpretation = (
            "Normalized model opportunity structure is identical; deltas remain synthetic "
            "and non-causal."
            if structure_matches
            else "Opportunity volume, timing, channel, agent, placement, or physical/model "
            "evidence differs; deltas are opportunity-confounded."
        )
        if self.classification != expected_classification:
            raise ValueError("comparison classification does not match opportunity structure")
        if self.interpretation != expected_interpretation:
            raise ValueError("comparison interpretation does not match its classification")
        expected_deltas = (
            _delta_series(self.control.overall, self.treatment.overall),
            *(
                _delta_series(control, treatment)
                for control, treatment in zip(
                    self.control.channels, self.treatment.channels, strict=True
                )
            ),
        )
        if (self.overall, *self.channels) != expected_deltas:
            raise ValueError("comparison deltas do not match the source snapshots")
        return self


def _validate_matched_provenance(control: SpatialMetrics, treatment: SpatialMetrics) -> None:
    checks = (
        ("city fingerprint", control.city_sha256, treatment.city_sha256),
        ("mobility trace", control.trace_sha256, treatment.trace_sha256),
        ("agent assignment", control.agents_sha256, treatment.agents_sha256),
        ("seed", control.seed, treatment.seed),
        ("duration", control.days, treatment.days),
        ("population", control.population_size, treatment.population_size),
    )
    for label, control_value, treatment_value in checks:
        if control_value != treatment_value:
            raise SpatialComparisonError(f"{label} differs between the spatial runs")


def compare_spatial_metrics(
    control: SpatialMetrics,
    treatment: SpatialMetrics,
    *,
    control_run_id: str,
    treatment_run_id: str,
) -> SpatialMetricsComparison:
    """Compare two matched metric snapshots without labeling a raw delta causal."""
    validated_control = revalidate_model(
        control,
        SpatialMetrics,
        label="control spatial metrics",
    )
    validated_treatment = revalidate_model(
        treatment,
        SpatialMetrics,
        label="treatment spatial metrics",
    )
    _validate_matched_provenance(validated_control, validated_treatment)
    structure_matches = (
        validated_control.opportunity_structure_sha256
        == validated_treatment.opportunity_structure_sha256
    )
    classification: SpatialComparisonClassification = (
        "matched-opportunity-structure" if structure_matches else "opportunity-confounded"
    )
    interpretation: SpatialComparisonInterpretation = (
        "Normalized model opportunity structure is identical; deltas remain synthetic and "
        "non-causal."
        if structure_matches
        else "Opportunity volume, timing, channel, agent, placement, or physical/model "
        "evidence differs; deltas are opportunity-confounded."
    )
    channel_deltas = tuple(
        _delta_series(control_channel, treatment_channel)
        for control_channel, treatment_channel in zip(
            validated_control.channels,
            validated_treatment.channels,
            strict=True,
        )
    )
    return SpatialMetricsComparison(
        classification=classification,
        interpretation=interpretation,
        control_run_id=control_run_id,
        treatment_run_id=treatment_run_id,
        control_scenario_sha256=validated_control.scenario_sha256,
        treatment_scenario_sha256=validated_treatment.scenario_sha256,
        city_sha256=validated_control.city_sha256,
        agents_sha256=validated_control.agents_sha256,
        trace_sha256=validated_control.trace_sha256,
        seed=validated_control.seed,
        population_size=validated_control.population_size,
        days=validated_control.days,
        control_opportunity_structure_sha256=(validated_control.opportunity_structure_sha256),
        treatment_opportunity_structure_sha256=(validated_treatment.opportunity_structure_sha256),
        control=validated_control,
        treatment=validated_treatment,
        overall=_delta_series(validated_control.overall, validated_treatment.overall),
        channels=(channel_deltas[0], channel_deltas[1]),
    )


__all__ = [
    "SpatialComparisonError",
    "SpatialMetricDeltaSeries",
    "SpatialMetricsComparison",
    "compare_spatial_metrics",
]
