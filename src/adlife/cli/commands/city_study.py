"""Expose the read-only verified repeated-seed spatial study contract."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.run_store import CityRunStore
from adlife.city.studies import analyze_stored_spatial_study
from adlife.city.study_loader import SpatialStudyDefinitionError, load_spatial_study_definition
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.experiments.spatial_study import SpatialStudyResult
from adlife.core.ports.run_store import StorageError
from adlife.core.simulation._validation import revalidate_model


def _human_lines(result: SpatialStudyResult) -> list[str]:
    lines = [
        "synthetic spatial study, not observed or causal effects: " + result.definition.study_id,
        f"scope: {result.definition.analysis_scope}; seeds: {len(result.seeds)}; "
        f"tier: {result.evidence_tier}",
        f"opportunity: {result.opportunity_classification} "
        f"(matched {result.opportunity_matched_pair_count}, "
        f"confounded {result.opportunity_confounded_pair_count})",
        f"response assumptions: {result.response_assumption_classification} "
        f"(matched {result.response_assumption_matched_pair_count}, "
        f"confounded {result.response_assumption_confounded_pair_count}); A/A: {result.a_a_status}",
        "Intervals describe deterministic simulator seed variation; "
        "not population confidence or sales.",
    ]
    if result.definition.analysis_scope == "attention-and-response":
        lines.extend(
            [
                "Every notice mechanically produces one rule response; "
                "response counts are not engagement.",
                "Rule deltas are planned terms; state changes are committed bounded changes; "
                "purchase intention is an uncalibrated internal proxy.",
                "A nonzero v1 contrast changes opportunity structure "
                "or numeric response assumptions; "
                "directional stability is not a clean creative effect.",
            ]
        )
    for statistic in result.statistics:
        effect = (
            "null"
            if statistic.paired_standardized_difference is None
            else format(statistic.paired_standardized_difference, "g")
        )
        lines.append(
            f"{statistic.metric_key}: mean {statistic.mean_paired_difference:+g}; "
            f"sample SD {statistic.sample_standard_deviation:g}; "
            f"median {statistic.median_paired_difference:+g}; "
            f"95% bootstrap [{statistic.bootstrap_ci_low:+g}, {statistic.bootstrap_ci_high:+g}]; "
            f"standardized difference {effect}; agreement {statistic.agreement_fraction:g}; "
            f"{statistic.direction}"
        )
    return lines


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing verified city-runs/.")],
    study: Annotated[Path, typer.Argument(help="Explicit bounded spatial-study JSON definition.")],
) -> None:
    """Analyze already saved seed-matched city runs without writing any artifact."""
    try:
        definition = load_spatial_study_definition(study)
        result = revalidate_model(
            analyze_stored_spatial_study(CityRunStore(root), definition),
            SpatialStudyResult,
            label="spatial study result",
        )
        document = result.model_dump(mode="json")
        if output_format() == "human":
            document["_lines"] = _human_lines(result)
    except SpatialStudyDefinitionError:
        raise CommandError("spatial study definition is invalid") from None
    except StorageError:
        raise CommandError(
            "city run artifacts could not be verified", ExitCode.ARTIFACT_ERROR
        ) from None
    except ValueError:
        raise CommandError("spatial study inputs are incompatible") from None
    except Exception:
        raise CommandError("spatial study analysis failed", ExitCode.UNEXPECTED) from None
    emit_result(document)


__all__ = ["command"]
