"""Analyze verified saved spatial runs and publish one static evidence report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.run_store import CityRunStore
from adlife.city.studies import analyze_stored_spatial_study
from adlife.city.study_loader import SpatialStudyDefinitionError, load_spatial_study_definition
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.ports.run_store import StorageError
from adlife.reporting.spatial_html import (
    SpatialReportConflict,
    SpatialReportPublicationError,
    publish_spatial_study_report,
)


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing verified city-runs/.")],
    study: Annotated[Path, typer.Argument(help="Explicit bounded spatial-study JSON definition.")],
) -> None:
    """Publish a zero-JavaScript spatial study ledger at its fixed no-clobber path."""
    try:
        definition = load_spatial_study_definition(study)
        result = analyze_stored_spatial_study(CityRunStore(root), definition)
        receipt = publish_spatial_study_report(result, root)
        document = receipt.model_dump(mode="json")
        if output_format() == "human":
            document["_lines"] = [
                "synthetic, exploratory, unobserved, non-causal, not sales: " + receipt.study_id,
                f"report: {receipt.report_path}",
                f"SHA-256: {receipt.report_sha256}; bytes: {receipt.report_bytes}",
            ]
    except SpatialStudyDefinitionError:
        raise CommandError("spatial study definition is invalid") from None
    except StorageError:
        raise CommandError(
            "city run artifacts could not be verified", ExitCode.ARTIFACT_ERROR
        ) from None
    except SpatialReportConflict:
        raise CommandError("report destination already exists", ExitCode.CONFLICT) from None
    except SpatialReportPublicationError:
        raise CommandError(
            "spatial study report could not be published", ExitCode.UNEXPECTED
        ) from None
    except ValueError:
        raise CommandError("spatial study inputs are incompatible") from None
    except Exception:
        raise CommandError("spatial study report failed", ExitCode.UNEXPECTED) from None
    emit_result(document)


__all__ = ["command"]
