"""Verify a frozen city mobility trace without mutating its source artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.run_store import CityRunStore
from adlife.city.runs import replay_city_run
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved city run ID.")],
) -> None:
    """Recompute every minute frame and refuse a mismatched or partial artifact."""
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    result = replay_city_run(CityRunStore(root).load(run_id))
    document = {
        "run_id": result.run_id,
        "identical": result.identical,
        "city_sha256": result.city_sha256,
        "agents_sha256": result.agents_sha256,
        "trace_sha256": result.trace_sha256,
        "frame_count": result.frame_count,
        "position_count": result.position_count,
    }
    if result.place_set_sha256 is not None:
        document["place_set_sha256"] = result.place_set_sha256
        document["place_assignments_sha256"] = result.place_assignments_sha256
    if result.scenario_sha256 is not None:
        if result.opportunity_counts is None:
            raise RuntimeError("spatial replay result is missing opportunity counts")
        document.update(
            scenario_sha256=result.scenario_sha256,
            opportunity_stream_sha256=result.opportunity_stream_sha256,
            opportunity_summary_sha256=result.opportunity_summary_sha256,
            opportunity_stream_bytes=result.opportunity_stream_bytes,
            opportunity_count=result.opportunity_count,
            opportunity_counts=result.opportunity_counts.model_dump(mode="json"),
            opportunity_claim_scope="synthetic-opportunity-not-impression",
        )
    if result.attention_model_id is not None:
        if result.attention_counts is None or result.attention_claim_scope is None:
            raise RuntimeError("spatial replay result is missing attention evidence")
        document.update(
            attention_model_id=result.attention_model_id,
            attention_claim_scope=result.attention_claim_scope,
            attention_notice_probability=result.attention_notice_probability,
            attention_stream_sha256=result.attention_stream_sha256,
            attention_summary_sha256=result.attention_summary_sha256,
            attention_stream_bytes=result.attention_stream_bytes,
            impression_count=result.impression_count,
            noticed_count=result.noticed_count,
            attention_counts=result.attention_counts.model_dump(mode="json"),
        )
    if result.response_model_id is not None:
        response_counts = result.response_counts
        response_values = (
            result.response_claim_scope,
            result.response_input_sha256,
            result.response_stream_sha256,
            result.response_state_sha256,
            result.response_summary_sha256,
            result.response_stream_bytes,
            result.response_count,
            result.state_update_count,
            result.response_campaign_count,
            result.final_state_count,
            response_counts,
        )
        if any(value is None for value in response_values):
            raise RuntimeError("spatial replay result is missing response evidence")
        if response_counts is None:
            raise RuntimeError("spatial replay result is missing response evidence")
        document.update(
            response_model_id=result.response_model_id,
            response_claim_scope=result.response_claim_scope,
            response_input_sha256=result.response_input_sha256,
            response_stream_sha256=result.response_stream_sha256,
            response_state_sha256=result.response_state_sha256,
            response_summary_sha256=result.response_summary_sha256,
            response_stream_bytes=result.response_stream_bytes,
            response_count=result.response_count,
            state_update_count=result.state_update_count,
            response_campaign_count=result.response_campaign_count,
            final_state_count=result.final_state_count,
            response_counts=response_counts.model_dump(mode="json"),
        )
    if output_format() == "human":
        suffix = ""
        if result.impression_count is not None and result.noticed_count is not None:
            suffix = (
                f", {result.opportunity_count} synthetic opportunities, "
                f"{result.impression_count} impressions, {result.noticed_count} notices"
            )
        elif result.opportunity_count is not None:
            suffix = f", {result.opportunity_count} synthetic opportunities"
        lines = [
            f"city replay identical: {result.run_id} ({result.frame_count} minute frames{suffix})"
        ]
        if result.response_model_id is not None:
            human_response_values = (
                result.response_claim_scope,
                result.response_input_sha256,
                result.response_stream_sha256,
                result.response_state_sha256,
                result.response_summary_sha256,
                result.response_stream_bytes,
                result.response_count,
                result.state_update_count,
                result.response_campaign_count,
                result.final_state_count,
            )
            if any(value is None for value in human_response_values):
                raise RuntimeError("spatial replay result is missing response evidence")
            lines.extend(
                (
                    f"response evidence: {result.response_count} rule responses, "
                    f"{result.state_update_count} state updates, "
                    f"{result.response_campaign_count} campaigns, "
                    f"{result.final_state_count} final campaign states",
                    f"response contract: {result.response_model_id}; {result.response_claim_scope}",
                    f"response input SHA-256: {result.response_input_sha256}",
                    f"response stream SHA-256: {result.response_stream_sha256} "
                    f"({result.response_stream_bytes} bytes)",
                    f"response state SHA-256: {result.response_state_sha256}",
                    f"response summary SHA-256: {result.response_summary_sha256}",
                )
            )
        document["_lines"] = lines
    emit_result(document)


__all__ = ["command"]
