"""Read-only, streaming analysis of explicit verified spatial city run pairs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.city_run import CityRunManifestV5, CityRunManifestV6, CityRunManifestV7
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import SpatialStudyDefinition
from adlife.core.experiments._spatial_study_validation import document_sha256, pair_classifications
from adlife.core.experiments.spatial_observations import (
    SpatialMetricObservation,
    spatial_metric_observations,
)
from adlife.core.experiments.spatial_response_metrics import (
    spatial_response_assumption_structure_sha256,
)
from adlife.core.experiments.spatial_study import (
    SpatialStudyAAInvariantError,
    SpatialStudyArmReceipt,
    SpatialStudyCityProvenance,
    SpatialStudyCompatibilityError,
    SpatialStudyPairReceipt,
    SpatialStudyResponseReceipt,
    SpatialStudyResult,
    SpatialStudyScalarPair,
    spatial_paired_statistics,
)
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch
from adlife.core.simulation._validation import revalidate_model


@dataclass(frozen=True, slots=True)
class _RunProjection:
    shared: dict[str, object]
    seed: int
    agents_sha256: str
    trace_sha256: str
    place_assignments_sha256: str | None
    arm: SpatialStudyArmReceipt
    observations: tuple[SpatialMetricObservation, ...]


def _project(stored: StoredCityRun, *, response_scope: bool) -> _RunProjection:
    manifest = stored.manifest
    if not isinstance(manifest, (CityRunManifestV5, CityRunManifestV6, CityRunManifestV7)):
        raise SchemaVersionMismatch("spatial study requires a schema-v5, v6 or v7 city run")
    if response_scope and not isinstance(manifest, (CityRunManifestV6, CityRunManifestV7)):
        raise SchemaVersionMismatch("spatial response study requires a schema-v6 or v7 city run")
    pack = stored.pack
    if isinstance(pack, CityPackV2):
        city = SpatialStudyCityProvenance(
            schema_version=2,
            city_id=pack.city_id,
            name=pack.name,
            city_sha256=manifest.city_sha256,
            time_zone=pack.time_zone,
            source=pack.source,
            known_omissions=pack.known_omissions,
        )
    else:
        city = SpatialStudyCityProvenance(
            schema_version=1,
            city_id=pack.city_id,
            name=pack.name,
            city_sha256=manifest.city_sha256,
            source_url=pack.source_url,
            license=pack.license,
            attribution=pack.attribution,
        )
    shared: dict[str, object] = {
        "source_run_schema_version": manifest.schema_version,
        "source_run_model_id": manifest.model_id,
        "package_version": manifest.package_version,
        "python_version": manifest.python_version,
        "city": city,
        "city_provenance_sha256": document_sha256(city),
        "days": manifest.days,
        "population_size": manifest.agent_count,
        "place_schema_version": manifest.place_schema_version,
        "place_set_sha256": manifest.place_set_sha256,
        "response_model_id": "spatial-response-v1"
        if isinstance(manifest, (CityRunManifestV6, CityRunManifestV7))
        else None,
    }
    attention = metrics_for_stored_city_run(stored)
    response_metrics = response_metrics_for_stored_city_run(stored) if response_scope else None
    response_receipt = None
    if isinstance(manifest, (CityRunManifestV6, CityRunManifestV7)):
        if stored.response_input is None or stored.spatial_scenario is None:
            raise CorruptRunArtifact("response city run has incomplete response inputs")
        response_fields: dict[str, object] = {
            name: getattr(manifest, name)
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
            )
        }
        response_fields.update(
            {
                "response_assumption_structure_sha256": (
                    response_metrics.response_assumption_structure_sha256
                    if response_metrics is not None
                    else spatial_response_assumption_structure_sha256(
                        stored.response_input, stored.spatial_scenario
                    )
                ),
                "response_metrics_sha256": document_sha256(response_metrics)
                if response_metrics is not None
                else None,
                "campaign_ids": tuple(
                    campaign.campaign_id for campaign in stored.response_input.campaigns
                ),
            }
        )
        response_receipt = SpatialStudyResponseReceipt.model_validate(response_fields)
    arm_fields: dict[str, object] = {
        name: getattr(manifest, name)
        for name in (
            "run_id",
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
        )
    }
    arm_fields.update(
        {
            "manifest_sha256": sha256(
                (canonical_json(manifest) + "\n").encode("utf-8")
            ).hexdigest(),
            "opportunity_structure_sha256": attention.opportunity_structure_sha256,
            "attention_metrics_sha256": document_sha256(attention),
            "response": response_receipt,
            "workbench_input_schema_version": (
                manifest.workbench_input_schema_version
                if isinstance(manifest, CityRunManifestV7)
                else None
            ),
            "workbench_input_sha256": (
                manifest.workbench_input_sha256 if isinstance(manifest, CityRunManifestV7) else None
            ),
        }
    )
    return _RunProjection(
        shared,
        manifest.seed,
        manifest.agents_sha256,
        manifest.trace_sha256,
        manifest.place_assignments_sha256,
        SpatialStudyArmReceipt.model_validate(arm_fields),
        spatial_metric_observations(attention, response_metrics),
    )


def _load_projection(store: CityRunStore, run_id: str, *, response_scope: bool) -> _RunProjection:
    # This function's frame owns the only full loaded run. Every retained return value
    # is a bounded public receipt; the frame is gone before the next store.load call.
    stored = store.load(run_id)
    try:
        return _project(stored, response_scope=response_scope)
    except (SchemaVersionMismatch, CorruptRunArtifact):
        raise
    except ValueError:
        raise CorruptRunArtifact(
            "verified city run could not be projected to study receipts"
        ) from None


def analyze_stored_spatial_study(
    store: CityRunStore,
    definition: SpatialStudyDefinition,
) -> SpatialStudyResult:
    """Analyze seed-sorted saved runs without discovery, rerunning or source writes."""
    checked = revalidate_model(definition, SpatialStudyDefinition, label="spatial study definition")
    response_scope = checked.analysis_scope == "attention-and-response"
    shared: dict[str, object] | None = None
    identities: dict[str, object] | None = None
    receipts: list[SpatialStudyPairReceipt] = []
    for declared in checked.pairs:
        control = _load_projection(store, declared.control_run_id, response_scope=response_scope)
        treatment = (
            control
            if declared.control_run_id == declared.treatment_run_id
            else _load_projection(store, declared.treatment_run_id, response_scope=response_scope)
        )
        if control.seed != declared.seed or treatment.seed != declared.seed:
            raise SpatialStudyCompatibilityError("study pair seeds do not match their manifests")
        if control.shared != treatment.shared or (
            control.agents_sha256,
            control.trace_sha256,
            control.place_assignments_sha256,
        ) != (treatment.agents_sha256, treatment.trace_sha256, treatment.place_assignments_sha256):
            raise SpatialStudyCompatibilityError("study arms do not share matched provenance")
        if shared is None:
            shared = control.shared
        elif shared != control.shared:
            raise SpatialStudyCompatibilityError("study shared provenance varies across seeds")
        if tuple(item.key for item in control.observations) != tuple(
            item.key for item in treatment.observations
        ):
            raise SpatialStudyCompatibilityError("study arms have different observation keys")
        current_identities: dict[str, object] = {
            "control_scenario_sha256": control.arm.scenario_sha256,
            "treatment_scenario_sha256": treatment.arm.scenario_sha256,
        }
        if response_scope:
            if control.arm.response is None or treatment.arm.response is None:
                raise CorruptRunArtifact("study response receipt is incomplete")
            if control.arm.response.campaign_ids != treatment.arm.response.campaign_ids:
                raise SpatialStudyCompatibilityError(
                    "study arms have different campaign identities"
                )
            current_identities.update(
                {
                    "control_response_input_sha256": control.arm.response.response_input_sha256,
                    "treatment_response_input_sha256": treatment.arm.response.response_input_sha256,
                    "control_response_assumption_structure_sha256": (
                        control.arm.response.response_assumption_structure_sha256
                    ),
                    "treatment_response_assumption_structure_sha256": (
                        treatment.arm.response.response_assumption_structure_sha256
                    ),
                    "campaign_ids": control.arm.response.campaign_ids,
                }
            )
        if identities is None:
            identities = current_identities
        elif identities != current_identities:
            raise SpatialStudyCompatibilityError("study arm identities vary across seeds")
        opportunity, response = pair_classifications(
            control.arm, treatment.arm, response_scope=response_scope
        )
        scalars = tuple(
            SpatialStudyScalarPair(
                control=left,
                treatment=right,
                delta=0.0 if right.value == left.value else right.value - left.value,
            )
            for left, right in zip(control.observations, treatment.observations, strict=True)
        )
        if checked.design == "a-a":
            if control.arm.model_dump(
                exclude={
                    "run_id",
                    "manifest_sha256",
                    "workbench_input_schema_version",
                    "workbench_input_sha256",
                }
            ) != treatment.arm.model_dump(
                exclude={
                    "run_id",
                    "manifest_sha256",
                    "workbench_input_schema_version",
                    "workbench_input_sha256",
                }
            ) or any(row.control != row.treatment or row.delta != 0.0 for row in scalars):
                raise CorruptRunArtifact("verified spatial study A/A invariant failed")
        elif control.arm.scenario_sha256 == treatment.arm.scenario_sha256 and (
            not response_scope
            or current_identities["control_response_assumption_structure_sha256"]
            == current_identities["treatment_response_assumption_structure_sha256"]
        ):
            raise SpatialStudyCompatibilityError(
                "paired contrast must change scenario or response assumptions"
            )
        receipts.append(
            SpatialStudyPairReceipt(
                seed=declared.seed,
                agents_sha256=control.agents_sha256,
                trace_sha256=control.trace_sha256,
                place_assignments_sha256=control.place_assignments_sha256,
                control=control.arm,
                treatment=treatment.arm,
                opportunity_classification=opportunity,
                response_assumption_classification=response,
                scalars=scalars,
            )
        )
    assert shared is not None and identities is not None  # definition proves at least two pairs
    keys = tuple(row.control.key for row in receipts[0].scalars)
    differences = {
        key: tuple(pair.scalars[index].delta for pair in receipts) for index, key in enumerate(keys)
    }
    sources = {row.control.key: row.control.source_artifacts for row in receipts[0].scalars}
    n = len(receipts)
    opportunity_matched = sum(
        pair.opportunity_classification == "matched-opportunity-structure" for pair in receipts
    )
    response_matched = sum(
        pair.response_assumption_classification == "matched-response-assumptions"
        for pair in receipts
    )
    fields: dict[str, object] = {
        **shared,
        **identities,
        "definition": checked,
        "study_definition_sha256": checked.fingerprint,
        "seeds": tuple(range(n)),
        "evidence_tier": "exploratory-under-50-seeds"
        if n < 50
        else "full-protocol-50-or-more-seeds",
        "opportunity_classification": "matched-opportunity-structure"
        if opportunity_matched == n
        else "opportunity-confounded",
        "opportunity_matched_pair_count": opportunity_matched,
        "opportunity_confounded_pair_count": n - opportunity_matched,
        "response_assumption_classification": "not-applicable"
        if not response_scope
        else "matched-response-assumptions"
        if response_matched == n
        else "response-assumption-confounded",
        "response_assumption_matched_pair_count": response_matched,
        "response_assumption_confounded_pair_count": n - response_matched if response_scope else 0,
        "a_a_status": "exact-zero-verified" if checked.design == "a-a" else "not-applicable",
        "pairs": tuple(receipts),
        "statistics": spatial_paired_statistics(differences, sources),
    }
    # A bypass-constructed intermediate carries no trust. The public return crosses the
    # same full validation and identity cache boundary as every detached result.
    try:
        return revalidate_model(
            SpatialStudyResult.model_construct(_fields_set=set(fields), **fields),
            SpatialStudyResult,
            label="spatial study result",
        )
    except SpatialStudyAAInvariantError:
        raise CorruptRunArtifact("verified spatial study A/A invariant failed") from None


__all__ = ["analyze_stored_spatial_study"]
