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
        if result.response_schema_version == 2:
            v2_values = (
                result.baseline_response_count,
                result.applied_response_count,
                result.dual_state_update_count,
                result.final_rule_state_count,
                result.final_cognition_state_count,
                result.rule_state_document_sha256,
                result.cognition_state_document_sha256,
                result.influence_policy_id,
                result.influence_policy_sha256,
                result.workbench_input_schema_version,
                result.workbench_input_sha256,
                result.cognition_effect_scope,
                result.cognition_mode,
                result.cognition_coverage,
                result.cognition_outcomes,
                result.cognition_reasons,
                result.cognition_neutralization,
                result.cognition_attempt_count,
                result.cognition_prompt_tokens,
                result.cognition_completion_tokens,
                result.cognition_input_sha256,
                result.cognition_input_bytes,
                result.cognition_causal_contract_sha256,
                result.cognition_prompt_contract_sha256,
                result.cognition_result_schema_contract_sha256,
                result.cognition_selection_policy_sha256,
                result.cognition_fallback_policy_sha256,
                result.cognition_mock_policy_sha256,
                result.cognition_influence_policy_sha256,
                result.cognition_effective_settings_sha256,
                result.cognition_budget_allocation_sha256,
                result.cognition_terminal_stream_sha256,
                result.cognition_terminal_stream_bytes,
                result.cognition_terminal_record_count,
                result.cognition_summary_sha256,
            )
            if any(value is None for value in v2_values):
                raise RuntimeError("V10 replay result is missing bounded cognition evidence")
            if (
                result.cognition_coverage is None
                or result.cognition_outcomes is None
                or result.cognition_reasons is None
                or result.cognition_neutralization is None
            ):
                raise RuntimeError("V10 replay result is missing bounded cognition evidence")
            document.update(
                response_schema_version=result.response_schema_version,
                baseline_response_count=result.baseline_response_count,
                applied_response_count=result.applied_response_count,
                dual_state_update_count=result.dual_state_update_count,
                final_rule_state_count=result.final_rule_state_count,
                final_cognition_state_count=result.final_cognition_state_count,
                rule_state_document_sha256=result.rule_state_document_sha256,
                cognition_state_document_sha256=result.cognition_state_document_sha256,
                influence_policy_id=result.influence_policy_id,
                influence_policy_sha256=result.influence_policy_sha256,
                workbench_input_schema_version=result.workbench_input_schema_version,
                workbench_input_sha256=result.workbench_input_sha256,
                cognition_effect_scope=result.cognition_effect_scope,
                cognition_mode=result.cognition_mode,
                cognition_coverage=result.cognition_coverage.model_dump(mode="json"),
                cognition_outcomes=result.cognition_outcomes.model_dump(mode="json"),
                cognition_reasons=result.cognition_reasons.model_dump(mode="json"),
                cognition_neutralization=result.cognition_neutralization.model_dump(mode="json"),
                cognition_attempt_count=result.cognition_attempt_count,
                cognition_prompt_tokens=result.cognition_prompt_tokens,
                cognition_completion_tokens=result.cognition_completion_tokens,
                cognition_input_sha256=result.cognition_input_sha256,
                cognition_input_bytes=result.cognition_input_bytes,
                cognition_causal_contract_sha256=result.cognition_causal_contract_sha256,
                cognition_prompt_contract_sha256=result.cognition_prompt_contract_sha256,
                cognition_result_schema_contract_sha256=(
                    result.cognition_result_schema_contract_sha256
                ),
                cognition_selection_policy_sha256=result.cognition_selection_policy_sha256,
                cognition_fallback_policy_sha256=result.cognition_fallback_policy_sha256,
                cognition_mock_policy_sha256=result.cognition_mock_policy_sha256,
                cognition_influence_policy_sha256=result.cognition_influence_policy_sha256,
                cognition_effective_settings_sha256=(result.cognition_effective_settings_sha256),
                cognition_budget_allocation_sha256=(result.cognition_budget_allocation_sha256),
                cognition_terminal_stream_sha256=result.cognition_terminal_stream_sha256,
                cognition_terminal_stream_bytes=result.cognition_terminal_stream_bytes,
                cognition_terminal_record_count=result.cognition_terminal_record_count,
                cognition_summary_sha256=result.cognition_summary_sha256,
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
            if result.response_schema_version == 2:
                coverage = result.cognition_coverage
                if (
                    result.baseline_response_count is None
                    or result.applied_response_count is None
                    or result.dual_state_update_count is None
                    or result.final_rule_state_count is None
                    or result.final_cognition_state_count is None
                    or result.cognition_mode is None
                    or result.cognition_effect_scope is None
                    or coverage is None
                    or result.cognition_terminal_record_count is None
                    or result.rule_state_document_sha256 is None
                    or result.cognition_state_document_sha256 is None
                    or result.cognition_terminal_stream_sha256 is None
                    or result.cognition_terminal_stream_bytes is None
                    or result.cognition_summary_sha256 is None
                ):
                    raise RuntimeError("V10 replay result is missing bounded cognition evidence")
                lines.extend(
                    (
                        f"response evidence: {result.baseline_response_count} rule-owned "
                        f"baseline responses, {result.applied_response_count} bounded "
                        "sentiment/recall applications, "
                        f"{result.dual_state_update_count} dual state updates",
                        f"state evidence: {result.final_rule_state_count} rule states, "
                        f"{result.final_cognition_state_count} cognition states; "
                        "purchase intention remains rule-owned",
                        f"response contract: {result.response_model_id}; "
                        f"{result.response_claim_scope}",
                        "cognition contract: bounded synthetic cognition "
                        f"(mode: {result.cognition_mode}; effect scope: "
                        f"{result.cognition_effect_scope}); frozen offline replay",
                        f"cognition evidence: {coverage.selected_request_count} selected "
                        f"({coverage.funded_request_count} funded, "
                        f"{coverage.budget_skipped_count} budget-skipped), "
                        f"{coverage.unselected_response_count} unselected; "
                        f"{result.cognition_terminal_record_count} frozen terminals",
                        f"response input SHA-256: {result.response_input_sha256}",
                        f"response stream SHA-256: {result.response_stream_sha256} "
                        f"({result.response_stream_bytes} bytes)",
                        f"response state SHA-256: {result.response_state_sha256}",
                        f"rule state SHA-256: {result.rule_state_document_sha256}",
                        f"cognition state SHA-256: {result.cognition_state_document_sha256}",
                        f"response summary SHA-256: {result.response_summary_sha256}",
                        f"cognition terminal SHA-256: "
                        f"{result.cognition_terminal_stream_sha256} "
                        f"({result.cognition_terminal_stream_bytes} bytes)",
                        f"cognition summary SHA-256: {result.cognition_summary_sha256}",
                    )
                )
            else:
                lines.extend(
                    (
                        f"response evidence: {result.response_count} rule responses, "
                        f"{result.state_update_count} state updates, "
                        f"{result.response_campaign_count} campaigns, "
                        f"{result.final_state_count} final campaign states",
                        f"response contract: {result.response_model_id}; "
                        f"{result.response_claim_scope}",
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
