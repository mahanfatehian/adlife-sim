"""Reviewed, offline V10 scientific evidence through public core interfaces only.

Object identities hash canonical JSON without LF; persisted streams, state
documents, response summaries, and manifest receipts hash canonical JSON plus LF.
This fixture has deliberately no update or regeneration path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from adlife.core.domain.city import CityPack
from adlife.core.domain.city_cognition import (
    CityCognitionCreativeSnapshotV1,
    CityCognitionProviderProfileSnapshotV1,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionCausalAgentV2,
    CityCognitionCausalCampaignV2,
    CityCognitionInitialRuleStateV2,
    CityCognitionResultV2,
    CityCognitionRunContextV2,
)
from adlife.core.domain.city_places import parse_city_place_set_json
from adlife.core.domain.city_run import build_city_cognition_manifest_receipt_v2
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from adlife.core.domain.spatial_response import parse_spatial_response_input_json
from adlife.core.simulation import city_cognition_v2 as cognition
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from adlife.core.simulation.spatial_response import evaluate_spatial_responses
from adlife.core.simulation.spatial_response_v2 import (
    apply_spatial_cognition_v2,
    spatial_cognition_state_document_v2,
    spatial_response_state_document_v2,
    spatial_response_v2_lines,
    spatial_rule_state_document_v2,
    summarize_spatial_response_artifact_v2,
)

GOLDEN = Path(__file__).parent / "scenarios" / "cognition-v2-small" / "record.json"


def _object_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _document_hash(value: Any) -> str:
    return sha256((canonical_json(value) + "\n").encode("utf-8")).hexdigest()


def _fixed_baseline():
    """One fictional agent crosses two left-side placements in one model minute."""
    pack = CityPack.model_validate_json(
        canonical_json(
            {
                "schema_version": 1,
                "city_id": "sample-city",
                "name": "Fictional Sample City",
                "source_url": "https://example.org/fictional-map",
                "license": "CC0-1.0",
                "attribution": "Fictional streets for demonstration",
                "nodes": [
                    {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
                    {"node_id": "b", "longitude": 0.01, "latitude": 0.0},
                    {"node_id": "c", "longitude": 0.01, "latitude": 0.01},
                ],
                "roads": [
                    {
                        "road_id": "ab",
                        "source_node": "a",
                        "target_node": "b",
                        "kind": "residential",
                    },
                    {"road_id": "bc", "source_node": "b", "target_node": "c", "kind": "primary"},
                    {
                        "road_id": "ca",
                        "source_node": "c",
                        "target_node": "a",
                        "kind": "residential",
                    },
                ],
            }
        )
    )
    places = parse_city_place_set_json(
        canonical_json(
            {
                "schema_version": 1,
                "city_id": pack.city_id,
                "city_sha256": pack.fingerprint,
                "name": "Forced opportunity places",
                "places": [
                    {
                        "place_id": place_id,
                        "kind": kind,
                        "node_id": node,
                        "label": label,
                        "provenance": {"method": "operator-authored-fictional"},
                    }
                    for place_id, kind, node, label in (
                        ("home-place-1", "home", "a", "Fictional home 1"),
                        ("work-place", "workplace", "b", "Fictional work"),
                        ("leisure-place", "leisure", "b", "Fictional leisure"),
                    )
                ],
            }
        )
    )
    mobility = CityMobility(pack, seed=42, agent_count=1, days=1, places=places)
    creative = CityCognitionCreativeSnapshotV1(
        template_id="demo",
        template_version=1,
        product_name="Fictional tea",
        product_category="tea",
        message="Fictional tea for a synthetic city.",
        call_to_action="Explore",
        disclosure="Fictional creative for synthetic simulation only.",
    )
    scenario = parse_spatial_campaign_scenario_json(
        canonical_json(
            {
                "schema_version": 1,
                "scenario_id": "opportunity-golden",
                "name": "Fictional opportunity golden",
                "days": 1,
                "city_id": pack.city_id,
                "city_sha256": pack.fingerprint,
                "campaigns": [
                    {
                        "campaign_id": "fictional-launch",
                        "name": "Campaign 1",
                        "creative_sha256": creative.fingerprint,
                    }
                ],
                "placements": [
                    {
                        "placement_id": placement_id,
                        "campaign_id": "fictional-launch",
                        "channel": "roadside-billboard",
                        "active_windows": [{"start_minute": 0, "end_minute": 1440}],
                        "frequency_cap_per_agent_per_day": 3,
                        "road_id": "ab",
                        "travel_direction": "forward",
                        "road_fraction": fraction,
                        "longitude": longitude,
                        "latitude": 0.0,
                        "side": "left",
                        "orientation_degrees": 270.0,
                        "max_view_distance_meters": 100.0,
                    }
                    for placement_id, fraction, longitude in (
                        ("a-later", 0.5, 0.005),
                        ("z-earlier", 0.4, 0.004),
                    )
                ],
            }
        )
    )
    inputs = parse_spatial_response_input_json(
        canonical_json(
            {
                "schema_version": 1,
                "city_sha256": pack.fingerprint,
                "scenario_sha256": scenario.fingerprint,
                "profiles": [
                    {
                        "agent_id": "person-001",
                        "fictional": True,
                        "interests": ["coffee", "outdoors"],
                        "traits": {
                            "price_sensitivity": 0.8,
                            "novelty_seeking": 0.3,
                            "advertising_skepticism": 0.6,
                            "mobile_recall_encoding": 0.7,
                            "roadside_recall_encoding": 0.4,
                            "impulsivity": 0.2,
                        },
                    }
                ],
                "campaigns": [
                    {
                        "campaign_id": "fictional-launch",
                        "creative_sha256": creative.fingerprint,
                        "target_interests": ["coffee", "work"],
                        "relative_price": 1.25,
                    }
                ],
                "initial_states": [
                    {
                        "agent_id": "person-001",
                        "campaign_id": "fictional-launch",
                        "brand_sentiment": 0.1,
                        "recall_strength": 0.1,
                        "purchase_intention": 0.2,
                    }
                ],
            }
        )
    )
    opportunities = evaluate_spatial_opportunities(mobility, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=5)
    baseline = evaluate_spatial_responses(
        inputs, scenario, opportunities, attention, agent_ids=("person-001",)
    )
    assert baseline.counts.response_count == 2
    assert baseline.counts.state_update_count == 1
    context = CityCognitionRunContextV2(
        schema_version=2,
        run_input_sha256="b" * 64,
        response_input_sha256=inputs.fingerprint,
        scenario_sha256=scenario.fingerprint,
        city_sha256=pack.fingerprint,
        agents=(
            CityCognitionCausalAgentV2(
                schema_version=2,
                agent_id="person-001",
                interests=("coffee", "outdoors"),
                traits=inputs.profiles[0].traits,
            ),
        ),
        campaigns=(
            CityCognitionCausalCampaignV2(
                schema_version=2,
                campaign_id="fictional-launch",
                creative=creative,
                creative_sha256=creative.fingerprint,
                target_interests=("coffee", "work"),
                relative_price=1.25,
            ),
        ),
        initial_states=(
            CityCognitionInitialRuleStateV2(
                schema_version=2,
                agent_id="person-001",
                campaign_id="fictional-launch",
                brand_sentiment=0.1,
                recall_strength=0.1,
            ),
        ),
    )
    return context, mobility, baseline


def _recorded_profile() -> CityCognitionProviderProfileSnapshotV1:
    facts = {
        "schema_version": 1,
        "profile_id": "fictional-provider",
        "credential_slot_id": "fictional-provider",
        "display_label": "Fictional recorded provider",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "fictional-model",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.0,
        "top_p": 1.0,
        "seed_policy": "omit",
        "seed": None,
        "max_output_tokens": 256,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "fictional-origin",
    }
    return CityCognitionProviderProfileSnapshotV1.model_validate(
        facts | {"revision_sha256": city_cognition_provider_profile_revision_sha256(facts)}
    )


def build_v10_scientific_document() -> dict[str, Any]:
    """Build all cases from one shared fixed baseline, without filesystem writes."""
    context, mobility, baseline = _fixed_baseline()
    requests = cognition.build_city_cognition_requests_v2(context, mobility, baseline)
    assert len(requests) == 2
    cases = {}
    for label, mode in (("rules", "rules"), ("mock", "mock"), ("recorded-provider", "local")):
        frozen = cognition.build_city_cognition_input_v2(
            context,
            requests,
            mode=mode,
            funded_response_limit=10 if mode == "local" else 60,
            provider_profile=_recorded_profile() if mode == "local" else None,
        )
        if mode == "local":
            assert [r.reserved_output_tokens for r in frozen.reservations] == [512, 512]
        terminals = []
        for index, request in enumerate(requests):
            if mode == "rules":
                result = cognition.build_city_rules_result_v2(request)
            elif mode == "mock":
                result = cognition.build_city_mock_result_v2(request)
            else:
                result = CityCognitionResultV2(
                    schema_version=2,
                    request_id=request.request_id,
                    affect="positive",
                    relevance="high",
                    credibility="medium",
                    salience="high",
                    safety_flags=(),
                    disclosure="synthetic-bounded-cognition-not-observed-behavior",
                )
            terminals.append(
                cognition.build_city_cognition_terminal_record_v2(
                    request,
                    frozen,
                    selection_index=index,
                    source="provider" if mode == "local" else mode,
                    result=result,
                    attempt_count=1 if mode == "local" else 0,
                )
            )
        signals = cognition.validate_city_cognition_evaluation_v2(
            frozen, requests, tuple(terminals), baseline_evaluation=baseline
        )
        assert signals.summary.coverage.model_dump(mode="json") == {
            "schema_version": 2,
            "eligible_response_count": 2,
            "selected_request_count": 2,
            "funded_request_count": 2,
            "budget_skipped_count": 0,
            "unselected_response_count": 0,
            "applied_response_count": 2,
        }
        assert signals.summary.neutralization.neutralized_response_count == (
            2 if mode == "rules" else 0
        )
        evaluation = apply_spatial_cognition_v2(baseline, frozen, signals)
        assert evaluation.counts.model_dump(mode="json") == {
            "schema_version": 2,
            "baseline_response_count": 2,
            "applied_response_count": 2,
            "dual_state_update_count": 1,
            "roadside_response_count": 2,
            "phone_response_count": 0,
            "campaign_count": 1,
            "final_rule_state_count": 1,
            "final_cognition_state_count": 1,
        }
        terminal_stream = b"".join(cognition.city_cognition_terminal_lines_v2(signals))
        response_stream = b"".join(spatial_response_v2_lines(evaluation))
        summary = summarize_spatial_response_artifact_v2(evaluation)
        receipt = build_city_cognition_manifest_receipt_v2(frozen, signals)
        case = {
            "mode": mode,
            "causal_request_id": requests[0].request_id,
            "request_record_sha256": requests[0].request_record_sha256,
            "validated_result": signals.records[0].result.model_dump(mode="json"),
            "cognition_input_sha256": _object_hash(frozen),
            "cognition_terminal_stream_sha256": sha256(terminal_stream).hexdigest(),
            "cognition_terminal_stream_bytes": len(terminal_stream),
            "response_stream_sha256": sha256(response_stream).hexdigest(),
            "response_stream_bytes": len(response_stream),
            "dual_state_document_sha256": _document_hash(
                spatial_response_state_document_v2(evaluation)
            ),
            "rule_state_document_sha256": _document_hash(
                spatial_rule_state_document_v2(evaluation)
            ),
            "cognition_state_document_sha256": _document_hash(
                spatial_cognition_state_document_v2(evaluation)
            ),
            "response_summary_sha256": _document_hash(summary),
            "manifest_cognition_receipt_sha256": _document_hash(receipt),
        }
        case["case_scientific_sha256"] = _object_hash(case)
        cases[label] = case
    document = {"schema_version": 1, "cases": cases}
    document["combined_scientific_sha256"] = _object_hash(document)
    return document


def test_v10_core_science_matches_reviewed_cross_platform_golden() -> None:
    actual = build_v10_scientific_document()
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
    assert set(actual["cases"]) == {"rules", "mock", "recorded-provider"}
    assert all(
        case["case_scientific_sha256"]
        == _object_hash(
            {key: value for key, value in case.items() if key != "case_scientific_sha256"}
        )
        for case in actual["cases"].values()
    )
    assert actual["combined_scientific_sha256"] == _object_hash(
        {key: value for key, value in actual.items() if key != "combined_scientific_sha256"}
    )


def test_v10_core_missing_golden_fails_without_creating_evidence(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "absent-evidence" / "record.json"
    monkeypatch.setattr(sys.modules[__name__], "GOLDEN", missing)
    with pytest.raises(FileNotFoundError):
        test_v10_core_science_matches_reviewed_cross_platform_golden()
    assert not missing.exists()
    assert not missing.parent.exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_v10_core_golden_is_identical_across_hash_seeds() -> None:
    command = (
        "from tests.golden.test_city_cognition_v2_science import build_v10_scientific_document; "
        "print(build_v10_scientific_document()['combined_scientific_sha256'])"
    )
    for seed in ("0", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ.copy() | {"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.stdout == (
            "7dcf107caaf4cef44075c2cc03218e04cb429001f567de925fce74fde45e5b52\n"
        )
        assert result.stderr == ""
