"""Reconstruct the compact study receipt graph without adapters or raw source data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import TYPE_CHECKING

from adlife.core.domain.city_run import CityRunManifestV5, CityRunManifestV6
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import SpatialStudyDefinition
from adlife.core.experiments._spatial_study_receipts import (
    OpportunityClassification,
    ResponseClassification,
    SpatialStudyArmReceipt,
    SpatialStudyCityProvenance,
    SpatialStudyPairReceipt,
)
from adlife.core.experiments.spatial_metrics import (
    _METRIC_NAMES,
    _SOURCES,
    SpatialMetrics,
)
from adlife.core.experiments.spatial_observations import SpatialMetricObservation
from adlife.core.experiments.spatial_response_metrics import (
    _EVENT_NAMES,
    _STATE_NAMES,
    SpatialResponseMetrics,
)
from adlife.core.simulation._validation import revalidate_model

if TYPE_CHECKING:
    from adlife.core.experiments.spatial_study import SpatialStudyResult


def document_sha256(value: object) -> str:
    # All callers pass a typed model; keeping the signature typed prevents raw inputs
    # from becoming retained source material in the public receipt graph.
    from pydantic import BaseModel

    if not isinstance(value, BaseModel):
        raise TypeError("study digest requires a typed document")
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def pair_classifications(
    control: SpatialStudyArmReceipt,
    treatment: SpatialStudyArmReceipt,
    *,
    response_scope: bool,
) -> tuple[OpportunityClassification, ResponseClassification]:
    opportunity: OpportunityClassification = (
        "matched-opportunity-structure"
        if control.opportunity_structure_sha256 == treatment.opportunity_structure_sha256
        else "opportunity-confounded"
    )
    response: ResponseClassification = "not-applicable"
    if response_scope:
        if control.response is None or treatment.response is None:
            raise ValueError("response study arms require response receipts")
        response = (
            "matched-response-assumptions"
            if control.response.response_assumption_structure_sha256
            == treatment.response.response_assumption_structure_sha256
            else "response-assumption-confounded"
        )
    return opportunity, response


def reconstruct_manifest(
    result: SpatialStudyResult,
    pair: SpatialStudyPairReceipt,
    arm: SpatialStudyArmReceipt,
) -> CityRunManifestV5 | CityRunManifestV6:
    data: dict[str, object] = {
        "schema_version": result.source_run_schema_version,
        "run_id": arm.run_id,
        "status": "completed",
        "model_id": result.source_run_model_id,
        "package_version": result.package_version,
        "python_version": result.python_version,
        "city_sha256": result.city.city_sha256,
        "city_schema_version": result.city.schema_version,
        "agents_sha256": pair.agents_sha256,
        "trace_sha256": pair.trace_sha256,
        "seed": pair.seed,
        "agent_count": result.population_size,
        "days": result.days,
        "frame_count": result.days * 1_440,
        "position_count": result.days * 1_440 * result.population_size,
        "spatial_scenario_schema_version": 1,
        "spatial_opportunity_schema_version": 1,
        "spatial_attention_schema_version": 1,
        "spatial_attention_model_id": result.attention_model_id,
        "place_schema_version": result.place_schema_version,
        "place_set_sha256": result.place_set_sha256,
        "place_assignments_sha256": pair.place_assignments_sha256,
    }
    for name in (
        "scenario_sha256",
        "opportunity_stream_sha256",
        "opportunity_summary_sha256",
        "opportunity_stream_bytes",
        "opportunity_count",
        "attention_stream_sha256",
        "attention_summary_sha256",
        "attention_stream_bytes",
        "impression_count",
        "noticed_count",
    ):
        data[name] = getattr(arm, name)
    if result.source_run_schema_version == 5:
        if arm.response is not None or result.response_model_id is not None:
            raise ValueError("v5 study cannot carry response evidence")
        return CityRunManifestV5.model_validate(data)
    if arm.response is None or result.response_model_id != "spatial-response-v1":
        raise ValueError("v6 study requires response artifact receipts and model identity")
    data.update(
        {
            "spatial_response_schema_version": 1,
            "spatial_response_model_id": result.response_model_id,
        }
    )
    for name in (
        "response_input_sha256",
        "response_stream_sha256",
        "response_state_sha256",
        "response_summary_sha256",
        "response_stream_bytes",
        "response_count",
        "state_update_count",
        "response_campaign_count",
        "final_state_count",
    ):
        data[name] = getattr(arm.response, name)
    return CityRunManifestV6.model_validate(data)


def _event_reach_bounds(count: float, reach: float, population: int) -> None:
    if count != int(count) or reach != int(reach) or not 0 <= reach <= min(count, population):
        raise ValueError("study event count/reach is outside its integer population bounds")
    if (count == 0.0) != (reach == 0.0):
        raise ValueError("study event reach must be zero exactly when count is zero")


def _union_reach_bounds(overall: float, parts: Sequence[float]) -> None:
    if not max(parts) <= overall <= sum(parts):
        raise ValueError("study overall reach cannot be the union of its slice reaches")


def _attention_document(
    result: SpatialStudyResult,
    pair: SpatialStudyPairReceipt,
    arm: SpatialStudyArmReceipt,
    observations: Mapping[str, SpatialMetricObservation],
) -> SpatialMetrics:
    series: list[dict[str, object]] = []
    for scope, channel in (
        ("overall", "overall"),
        ("channel.roadside", "roadside"),
        ("channel.mobile", "mobile"),
    ):
        row: dict[str, object] = {"channel": channel}
        for name in _METRIC_NAMES:
            observation = observations[f"attention.{scope}.{name}"]
            if observation.numerator != int(observation.numerator) or observation.numerator < 0:
                raise ValueError("study attention receipts require nonnegative integer numerators")
            events, artifacts = _SOURCES[name]
            row[name] = {
                "name": name,
                "numerator": int(observation.numerator),
                "denominator": observation.denominator,
                "value": observation.value,
                "source_event_types": events,
                "source_artifacts": artifacts,
            }
        series.append(row)
    document = SpatialMetrics.model_validate(
        {
            "source_run_schema_version": result.source_run_schema_version,
            "scenario_sha256": arm.scenario_sha256,
            "city_sha256": result.city.city_sha256,
            "agents_sha256": pair.agents_sha256,
            "trace_sha256": pair.trace_sha256,
            "opportunity_structure_sha256": arm.opportunity_structure_sha256,
            "seed": pair.seed,
            "population_size": result.population_size,
            "days": result.days,
            "overall": series[0],
            "channels": tuple(series[1:]),
        }
    )
    for item in (document.overall, *document.channels):
        for count, reach in (
            (item.opportunity_count, item.opportunity_reach),
            (item.impression_count, item.impression_reach),
            (item.noticed_count, item.noticed_reach),
        ):
            _event_reach_bounds(
                float(count.numerator), float(reach.numerator), result.population_size
            )
        if item.opportunity_reach.numerator != item.impression_reach.numerator:
            raise ValueError("study opportunity and impression reach must match")
        if item.noticed_reach.numerator > item.impression_reach.numerator:
            raise ValueError("study noticed reach cannot exceed impression reach")
    for name in ("opportunity_reach", "impression_reach", "noticed_reach"):
        _union_reach_bounds(
            float(getattr(document.overall, name).numerator),
            tuple(float(getattr(item, name).numerator) for item in document.channels),
        )
    for name in ("opportunity_count", "impression_count", "noticed_count"):
        if getattr(document.overall, name).numerator != getattr(arm, name):
            raise ValueError("study attention count does not match manifest receipt")
    if document_sha256(document) != arm.attention_metrics_sha256:
        raise ValueError("study attention metric digest does not match its scalar receipts")
    return document


def _response_document(
    result: SpatialStudyResult,
    pair: SpatialStudyPairReceipt,
    arm: SpatialStudyArmReceipt,
    observations: Mapping[str, SpatialMetricObservation],
    attention: SpatialMetrics,
) -> SpatialResponseMetrics:
    receipt = arm.response
    if receipt is None:
        raise ValueError("study response receipt is missing")
    scopes = (
        "overall",
        "channel.roadside",
        "channel.mobile",
        *(f"campaign.{item}" for item in receipt.campaign_ids),
    )
    series: list[dict[str, object]] = []
    for scope in scopes:
        row: dict[str, object] = {}
        if scope.startswith("channel."):
            row["channel"] = scope.split(".")[1]
        if scope.startswith("campaign."):
            row["campaign_id"] = scope.split(".")[1]
        for name in _EVENT_NAMES:
            observation = observations[f"response.{scope}.{name}"]
            numerator: int | float = observation.numerator
            if name in {"response_count", "response_reach", "response_frequency"}:
                if numerator != int(numerator) or numerator < 0:
                    raise ValueError("study response count receipts require integer numerators")
                numerator = int(numerator)
            row[name] = {
                "name": name,
                "numerator": numerator,
                "denominator": observation.denominator,
                "value": observation.value,
            }
        if not scope.startswith("channel."):
            for state_name in _STATE_NAMES:
                states = {
                    aggregate: observations[f"response.{scope}.state.{state_name}.{aggregate}"]
                    for aggregate in ("initial_mean", "final_mean", "mean_change")
                }
                if len({item.denominator for item in states.values()}) != 1:
                    raise ValueError("study state aggregates have different denominators")
                row[state_name] = {
                    "name": state_name,
                    "denominator": states["initial_mean"].denominator,
                    "initial_total": states["initial_mean"].numerator,
                    "final_total": states["final_mean"].numerator,
                    "change_total": states["mean_change"].numerator,
                    "initial_mean": states["initial_mean"].value,
                    "final_mean": states["final_mean"].value,
                    "mean_change": states["mean_change"].value,
                }
        series.append(row)
    document = SpatialResponseMetrics.model_validate(
        {
            "scenario_sha256": arm.scenario_sha256,
            "city_sha256": result.city.city_sha256,
            "agents_sha256": pair.agents_sha256,
            "trace_sha256": pair.trace_sha256,
            "opportunity_structure_sha256": arm.opportunity_structure_sha256,
            "response_input_sha256": receipt.response_input_sha256,
            "response_assumption_structure_sha256": receipt.response_assumption_structure_sha256,
            "response_stream_sha256": receipt.response_stream_sha256,
            "response_state_sha256": receipt.response_state_sha256,
            "seed": pair.seed,
            "population_size": result.population_size,
            "days": result.days,
            "campaign_count": receipt.response_campaign_count,
            "overall": series[0],
            "channels": tuple(series[1:3]),
            "campaigns": tuple(series[3:]),
        }
    )
    for item in (document.overall, *document.channels, *document.campaigns):
        _event_reach_bounds(
            float(item.response_count.numerator),
            float(item.response_reach.numerator),
            result.population_size,
        )
        if (
            not -0.2 <= item.mean_rule_sentiment_delta.value <= 0.2
            or not 0.0 <= item.mean_rule_recall_delta.value <= 0.3
        ):
            raise ValueError("study direct response means exceed the rule bounds")
        if item.response_count.numerator == 0 and (
            item.mean_rule_sentiment_delta.numerator != 0.0
            or item.mean_rule_recall_delta.numerator != 0.0
        ):
            raise ValueError("study zero-response direct numerators must be zero")
    for slices in (document.channels, document.campaigns):
        _union_reach_bounds(
            float(document.overall.response_reach.numerator),
            tuple(float(item.response_reach.numerator) for item in slices),
        )
    for responses, notices in zip(
        (document.overall, *document.channels),
        (attention.overall, *attention.channels),
        strict=True,
    ):
        if (
            responses.response_count.numerator != notices.noticed_count.numerator
            or responses.response_reach.numerator != notices.noticed_reach.numerator
        ):
            raise ValueError("study rule response must exactly process every notice")
    for item in (document.overall, *document.campaigns):
        if item.response_count.numerator == 0 and any(
            getattr(item, name).change_total != 0.0 for name in _STATE_NAMES
        ):
            raise ValueError("study zero-response campaign state must be unchanged")
    if document.overall.response_count.numerator != receipt.response_count:
        raise ValueError("study response count does not match manifest receipt")
    if document_sha256(document) != receipt.response_metrics_sha256:
        raise ValueError("study response metric digest does not match scalar receipts")
    return document


def expected_observation_keys(campaign_ids: tuple[str, ...]) -> tuple[str, ...]:
    keys = [
        f"attention.{scope}.{name}"
        for scope in ("overall", "channel.roadside", "channel.mobile")
        for name in _METRIC_NAMES
    ]
    if campaign_ids:
        scopes = (
            "overall",
            "channel.roadside",
            "channel.mobile",
            *(f"campaign.{item}" for item in campaign_ids),
        )
        keys.extend(f"response.{scope}.{name}" for scope in scopes for name in _EVENT_NAMES)
        keys.extend(
            f"response.{scope}.state.{name}.{aggregate}"
            for scope in ("overall", *(f"campaign.{item}" for item in campaign_ids))
            for name in _STATE_NAMES
            for aggregate in ("initial_mean", "final_mean", "mean_change")
        )
    return tuple(sorted(keys))


def validate_study_result(result: SpatialStudyResult) -> None:
    from adlife.core.experiments.spatial_study import (
        SpatialPairedStatistic,
        SpatialStudyAAInvariantError,
        SpatialStudyCompatibilityError,
        spatial_paired_statistics,
    )

    definition = revalidate_model(
        result.definition, SpatialStudyDefinition, label="study definition"
    )
    city = revalidate_model(result.city, SpatialStudyCityProvenance, label="study city provenance")
    if document_sha256(city) != result.city_provenance_sha256:
        raise ValueError("study city provenance digest does not match its public metadata")
    n = len(definition.pairs)
    response_scope = definition.analysis_scope == "attention-and-response"
    if result.study_definition_sha256 != definition.fingerprint or result.seeds != tuple(range(n)):
        raise ValueError("study definition fingerprint or exact seed protocol is inconsistent")
    if len(result.pairs) != n or tuple(pair.seed for pair in result.pairs) != result.seeds:
        raise ValueError("study pair receipts do not match the canonical seed protocol")
    expected_tier = "exploratory-under-50-seeds" if n < 50 else "full-protocol-50-or-more-seeds"
    if result.evidence_tier != expected_tier:
        raise ValueError("study evidence tier does not match its seed count")
    if (result.place_schema_version is None) != (result.place_set_sha256 is None):
        raise ValueError("study place set binding is incomplete")
    response_identities = (
        result.control_response_input_sha256,
        result.treatment_response_input_sha256,
        result.control_response_assumption_structure_sha256,
        result.treatment_response_assumption_structure_sha256,
    )
    if response_scope:
        if (
            result.source_run_schema_version != 6
            or not result.campaign_ids
            or any(value is None for value in response_identities)
        ):
            raise ValueError("response study identity is incomplete")
        if result.campaign_ids != tuple(sorted(set(result.campaign_ids))):
            raise ValueError("study campaigns must be unique and canonical")
    elif result.campaign_ids or any(value is not None for value in response_identities):
        raise ValueError("attention-only study cannot carry analyzed response identities")
    keys = expected_observation_keys(result.campaign_ids)
    differences: dict[str, list[float]] = {key: [] for key in keys}
    sources = {}
    opportunity_matched = response_matched = 0
    for declared, raw_pair in zip(definition.pairs, result.pairs, strict=True):
        pair = revalidate_model(raw_pair, SpatialStudyPairReceipt, label="study pair receipt")
        if (
            pair.control.run_id != declared.control_run_id
            or pair.treatment.run_id != declared.treatment_run_id
        ):
            raise ValueError("study pair run identities differ from the definition")
        if tuple(row.control.key for row in pair.scalars) != keys:
            raise ValueError("study scalar keys do not match its complete observation shape")
        if (pair.place_assignments_sha256 is None) != (result.place_schema_version is None):
            raise ValueError("study pair place assignments are incomplete")
        for arm, scenario, response_input, assumption in (
            (
                pair.control,
                result.control_scenario_sha256,
                result.control_response_input_sha256,
                result.control_response_assumption_structure_sha256,
            ),
            (
                pair.treatment,
                result.treatment_scenario_sha256,
                result.treatment_response_input_sha256,
                result.treatment_response_assumption_structure_sha256,
            ),
        ):
            if arm.scenario_sha256 != scenario:
                raise SpatialStudyCompatibilityError(
                    "study arm scenario identity varies across seeds"
                )
            if response_scope and (
                arm.response is None
                or arm.response.response_input_sha256 != response_input
                or arm.response.response_assumption_structure_sha256 != assumption
                or arm.response.campaign_ids != result.campaign_ids
            ):
                raise SpatialStudyCompatibilityError(
                    "study arm response assumptions vary across seeds"
                )
            manifest = reconstruct_manifest(result, pair, arm)
            if (
                sha256((canonical_json(manifest) + "\n").encode("utf-8")).hexdigest()
                != arm.manifest_sha256
            ):
                raise ValueError("study manifest digest does not match its compact receipt")
            observations = {
                row.control.key: row.control if arm is pair.control else row.treatment
                for row in pair.scalars
            }
            attention = _attention_document(result, pair, arm, observations)
            if response_scope:
                _response_document(result, pair, arm, observations, attention)
            elif arm.response is not None and arm.response.response_metrics_sha256 is not None:
                raise ValueError("attention-only study cannot carry a response metric digest")
        opportunity, response = pair_classifications(
            pair.control, pair.treatment, response_scope=response_scope
        )
        if (
            pair.opportunity_classification != opportunity
            or pair.response_assumption_classification != response
        ):
            raise ValueError("study pair classifications do not match their evidence")
        opportunity_matched += opportunity == "matched-opportunity-structure"
        response_matched += response == "matched-response-assumptions"
        if definition.design == "a-a":
            control = pair.control.model_dump(exclude={"run_id", "manifest_sha256"})
            treatment = pair.treatment.model_dump(exclude={"run_id", "manifest_sha256"})
            if control != treatment or any(
                row.control != row.treatment or row.delta != 0.0 for row in pair.scalars
            ):
                raise SpatialStudyAAInvariantError("study A/A evidence must be exactly equal")
        elif pair.control.scenario_sha256 == pair.treatment.scenario_sha256 and (
            not response_scope
            or (
                pair.control.response is not None
                and pair.treatment.response is not None
                and pair.control.response.response_assumption_structure_sha256
                == pair.treatment.response.response_assumption_structure_sha256
            )
        ):
            raise SpatialStudyCompatibilityError(
                "paired contrast must change scenario or response assumptions"
            )
        for row in pair.scalars:
            differences[row.control.key].append(row.delta)
            sources[row.control.key] = row.control.source_artifacts
    expected_opportunity = (
        "matched-opportunity-structure" if opportunity_matched == n else "opportunity-confounded"
    )
    expected_response = (
        "not-applicable"
        if not response_scope
        else "matched-response-assumptions"
        if response_matched == n
        else "response-assumption-confounded"
    )
    if (
        result.opportunity_classification,
        result.opportunity_matched_pair_count,
        result.opportunity_confounded_pair_count,
    ) != (expected_opportunity, opportunity_matched, n - opportunity_matched):
        raise ValueError("study aggregate opportunity verdict does not match its pairs")
    if (
        result.response_assumption_classification,
        result.response_assumption_matched_pair_count,
        result.response_assumption_confounded_pair_count,
    ) != (expected_response, response_matched, n - response_matched if response_scope else 0):
        raise ValueError("study aggregate response verdict does not match its pairs")
    if result.a_a_status != (
        "exact-zero-verified" if definition.design == "a-a" else "not-applicable"
    ):
        raise ValueError("study A/A status does not match its definition")
    statistics = tuple(
        revalidate_model(item, SpatialPairedStatistic, label="study paired statistic")
        for item in result.statistics
    )
    if statistics != spatial_paired_statistics(differences, sources):
        raise ValueError("study statistics do not match its seed-paired scalar receipts")
    # Keep the validated provenance in view: the city digest used for every manifest
    # was validated from the same typed public metadata, without copying its graph.
    if city != result.city:
        raise ValueError("study city provenance is inconsistent")
