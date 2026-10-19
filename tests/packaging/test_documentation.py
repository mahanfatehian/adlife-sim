"""The documentation contract: what the repository must say about itself.

Documentation drifts silently, so this suite pins the claims Task 17 makes on the
project's behalf: the disclosures a reader must meet before any number, the
machine-editable install block the release process rewrites, the governance files a
university artifact and a public repository both require, and a sweep that no
unsupported accuracy or adoption claim has crept in.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

import pytest
import yaml

from adlife import __version__
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1,
    CITY_COGNITION_DEFAULT_SELECTION_POLICY_V2,
    CITY_COGNITION_V2_MAX_AGGREGATE_OUTPUT_TOKENS,
    CITY_COGNITION_V2_MAX_ATTEMPTS_PER_REQUEST,
    CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
    CityCognitionInputV2,
    CityCognitionResultV2,
)

ROOT = Path(__file__).parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _all_docs() -> dict[Path, str]:
    docs: dict[Path, str] = {}
    readme = ROOT / "README.md"
    docs[readme] = readme.read_text(encoding="utf-8")
    for directory in ("docs", ROOT / "docs" / "methodology"):
        for path in sorted(ROOT.joinpath(directory).glob("*.md")):
            docs[path] = path.read_text(encoding="utf-8")
    return docs


def _squash(text: str) -> str:
    """Normalize prose wrapping while preserving meaningful contract punctuation."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _assert_terms_share_paragraph(text: str, *terms: str) -> None:
    paragraphs = tuple(
        _squash(paragraph) for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()
    )
    expected = tuple(term.lower() for term in terms)
    assert any(all(term in paragraph for term in expected) for paragraph in paragraphs), (
        f"no paragraph associates the required contract terms: {expected}"
    )


# ---------------------------------------------------------------------------
# README: the disclosures a reader meets first
# ---------------------------------------------------------------------------


def test_readme_contains_required_disclosures() -> None:
    text = _read("README.md").lower()
    assert "synthetic and exploratory" in text
    assert "not a representative survey" in text
    assert "adlife demo" in text


def test_readme_carries_machine_editable_install_block() -> None:
    text = _read("README.md")
    start = "<!-- adlife-install:start -->"
    end = "<!-- adlife-install:end -->"
    assert start in text and end in text
    between = text.split(start, 1)[1].split(end, 1)[0]
    assert "uv tool install adlife-sim" in between


def test_readme_status_table_matches_reality() -> None:
    """Capabilities shipped through Task 16 must not still read as planned."""
    text = _read("README.md")
    for shipped in (
        "Cognition ports, mock provider, rules provider, and replay",
        "OpenAI-compatible provider with bounded fallback",
        "Event sinks, SQLite storage, and replayable run artifacts",
        "Full run orchestration",
        "Metrics and paired experiments",
        "Complete command-line interface",
        "Live terminal user interface",
        "Self-contained HTML reports",
    ):
        row = next(
            (line for line in text.splitlines() if shipped in line),
            None,
        )
        assert row is not None, f"status row missing: {shipped}"
        assert "Planned" not in row, f"shipped capability still marked planned: {shipped}"


# ---------------------------------------------------------------------------
# Methodology documents
# ---------------------------------------------------------------------------


def test_methodology_documents_exist() -> None:
    required = [
        "odd-protocol.md",
        "model-card.md",
        "experiment-protocol.md",
        "limitations.md",
    ]
    for name in required:
        assert (ROOT / "docs" / "methodology" / name).stat().st_size > 1000


def test_odd_protocol_records_the_documented_formulas() -> None:
    text = _read("docs", "methodology", "odd-protocol.md")
    for constant in ("0.85", "0.60", "0.25", "0.10", "0.70", "1440"):
        assert constant in text, constant
    for section in (
        "Purpose",
        "Entities",
        "State variables",
        "Process overview",
        "Design concepts",
        "Initialization",
        "Input data",
        "Submodels",
    ):
        assert section.lower() in text.lower(), section


def test_experiment_protocol_pre_registers_the_declared_controls() -> None:
    text = _read("docs", "methodology", "experiment-protocol.md").lower()
    for required in (
        "no-campaign",
        "a/a",
        "common random numbers",
        "50",
        "80%",
        "confound",
        "--seeds 50",
    ):
        assert required in text, required


def test_model_card_states_external_validity_status() -> None:
    text = _read("docs", "methodology", "model-card.md").lower()
    assert "external validity" in text
    assert "prompt-injection" in text or "prompt injection" in text
    assert "stereotype" in text


def test_city_source_qualification_checklist_records_human_decisions() -> None:
    path = ROOT / "docs" / "data" / "city-source-qualification.md"
    assert path.is_file(), "city-source qualification checklist is missing"
    text = path.read_text(encoding="utf-8").lower()
    for required in (
        "jurisdiction",
        "supplier",
        "source date",
        "source version",
        "sha-256",
        "license",
        "attribution",
        "commercial redistribution",
        "retention",
        "public tile",
        "geocoder",
        "reviewer",
        "decision",
        "review date",
        "expiry",
        "re-review",
        "real-city qualification remains pending",
    ):
        assert required in text, required


def test_city_catalog_public_contract_is_fictional_offline_and_content_addressed() -> None:
    readme = _read("README.md").lower()
    cli = _read("docs", "cli-reference.md").lower()
    architecture = _read("docs", "architecture.md").lower()

    for text in (readme, cli):
        assert "city-catalog" in text
        assert "fictional-grid-v2" in text
        assert "fictional-fixture" in text
        assert "content-addressed" in text
        assert "offline" in text
    assert "public tile" in architecture
    assert "geocoder" in architecture
    assert "no network" in architecture


def test_workspace_city_registry_public_contract_is_exact() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    city_pilot = _read("docs", "city-pilot.md")
    cli = _read("docs", "cli-reference.md")
    qualification = _read("docs", "data", "city-source-qualification.md")
    model_card = _read("docs", "methodology", "model-card.md")
    limitations = _read("docs", "methodology", "limitations.md")
    reproducibility = _read("docs", "reproducibility.md")

    _assert_terms_share_paragraph(
        readme,
        "three",
        "bundled",
        "fictional",
        "zero",
        "rights-reviewed",
        "real",
    )
    _assert_terms_share_paragraph(
        cli,
        "POST /api/catalog/cities/upload",
        "4 MiB",
        "128 KiB",
    )
    _assert_terms_share_paragraph(
        cli,
        "city-import",
        "does not register",
        "packaged",
        "--city-id",
    )
    _assert_terms_share_paragraph(
        qualification,
        "workspace",
        "technical validation",
        "does not establish",
        "rights",
        "license",
    )
    _assert_terms_share_paragraph(
        architecture,
        "source URL",
        "inert",
        "not dereferenced",
        "outbound",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "inputs/city.json",
        "workspace registry",
        "replay",
        "metrics",
        "reports",
        "inspection",
        "independent",
    )
    for text in (city_pilot, model_card, limitations):
        _assert_terms_share_paragraph(
            text,
            "operator-supplied",
            "unreviewed",
            "rights",
        )

    public = "\n".join(
        (
            readme,
            architecture,
            city_pilot,
            cli,
            qualification,
            model_card,
            limitations,
            reproducibility,
        )
    ).lower()
    for stale_claim in (
        "only shipped entry is `fictional-grid-v2`",
        "only bundled entry is `fictional-grid-v2`",
        "only bundled catalog pack",
        "sole catalog entry",
        "contains only the `fictional-grid-v2` fixture",
        "currently contains only the `fictional-grid-v2` fixture",
    ):
        assert stale_claim not in public, stale_claim


def test_city_v2_docs_preserve_map_evidence_without_overstating_time_zone() -> None:
    city_pilot = _read("docs", "city-pilot.md").lower()
    model_card = _read("docs", "methodology", "model-card.md").lower()
    limitations = _read("docs", "methodology", "limitations.md").lower()

    for required in ("road geometry", "direction", "source provenance"):
        assert required in city_pilot, required
    assert "odbl" in city_pilot
    assert "commercial redistribution" in city_pilot
    assert "recorded rights review" in city_pilot
    for text in (model_card, limitations):
        assert "time zone" in text
        assert "fixed schedule" in text
        assert "calendar-accurate" in text


def test_spatial_opportunity_docs_describe_c3a_artifacts_without_claiming_outcomes() -> None:
    readme = _read("README.md").lower()
    architecture = _read("docs", "architecture.md").lower()
    cli = _read("docs", "cli-reference.md").lower()
    city_pilot = _read("docs", "city-pilot.md").lower()
    reproducibility = _read("docs", "reproducibility.md").lower()
    model_card = _read("docs", "methodology", "model-card.md").lower()
    limitations = _read("docs", "methodology", "limitations.md").lower()
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )

    for text in (readme, architecture, cli, city_pilot, model_card):
        assert "synthetic-opportunity-not-impression" in text
    for text in (readme, architecture, city_pilot, reproducibility, limitations):
        assert "c3" in text
    for text in (readme, architecture, cli, city_pilot, reproducibility, model_card, limitations):
        assert "city-run" in text
    for text in (readme, cli, city_pilot, reproducibility):
        assert "--spatial-campaign" in text
        assert "schema-v4" in text
    for text in (architecture, cli, reproducibility):
        assert "spatial-opportunities.jsonl" in text
        assert "opportunity-summary.json" in text
    assert "persisted and replayed" in model_card
    assert "synthetic-opportunity-not-impression" in limitations
    assert "splitmix64" in city_pilot
    assert "splitmix64" in reproducibility
    assert "[x] **C2" in roadmap
    assert "[ ] **C3" in roadmap
    assert "c3a" in roadmap.lower()
    for text in (readme, architecture, cli, city_pilot, reproducibility, model_card):
        assert "current-minute opportunity" in text
    combined = "\n".join(
        (readme, architecture, cli, city_pilot, reproducibility, model_card, roadmap.lower())
    )
    for stale_claim in (
        "not shown by the mobility-only viewer",
        "remains a mobility-only observer",
        "does not present opportunity records",
        "still presents only mobility",
        "reports or the read-only mobility viewer",
    ):
        assert stale_claim not in combined


def test_spatial_attention_docs_describe_c3b_without_claiming_observed_behavior() -> None:
    readme = _read("README.md").lower()
    architecture = _read("docs", "architecture.md").lower()
    cli = _read("docs", "cli-reference.md").lower()
    city_pilot = _read("docs", "city-pilot.md").lower()
    reproducibility = _read("docs", "reproducibility.md").lower()
    model_card = _read("docs", "methodology", "model-card.md").lower()
    limitations = _read("docs", "methodology", "limitations.md").lower()
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    ).lower()

    public = (readme, architecture, cli, city_pilot, reproducibility, model_card, limitations)
    for text in public:
        assert "synthetic-attention-not-observed-behavior" in text
        assert "0.5" in text
    for text in (readme, cli, city_pilot, reproducibility):
        assert "schema-v5" in text
        assert "attention-summary.json" in text
        assert "spatial-attention.jsonl" in text
    for text in (readme, architecture, cli, city_pilot, reproducibility, model_card):
        assert "current-minute attention" in text
    for text in (readme, architecture, city_pilot, reproducibility, model_card, limitations):
        assert "not calibrated" in text or "uncalibrated" in text
        assert "purchase" in text
        assert "cognition" in text
    assert "c3b evidence" in roadmap
    assert "[ ] **c3" in roadmap
    assert "[ ] **c4" in roadmap
    assert "schema-v5" in roadmap

    combined = "\n".join(public)
    for stale_claim in (
        "schema-v4 artifact that freezes",
        "schema-v4 spatial runs additionally persist",
        "they are persisted and replayed as evidence, not impressions, attention",
        "does not infer impressions",
        "does not convert it into impressions/notice",
    ):
        assert stale_claim not in combined


def test_spatial_metrics_docs_describe_c4a_receipts_and_confounding_honestly() -> None:
    readme = _read("README.md").lower()
    architecture = _read("docs", "architecture.md").lower()
    cli = _read("docs", "cli-reference.md").lower()
    city_pilot = _read("docs", "city-pilot.md").lower()
    reproducibility = _read("docs", "reproducibility.md").lower()
    model_card = _read("docs", "methodology", "model-card.md").lower()
    limitations = _read("docs", "methodology", "limitations.md").lower()
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    ).lower()

    public = (readme, architecture, cli, city_pilot, reproducibility, model_card, limitations)
    for text in public:
        assert "synthetic-metrics-not-observed-outcomes" in text
        assert "numerator" in text and "denominator" in text
        assert "uncalibrated" in text or "not calibrated" in text
    for text in (readme, cli, city_pilot, reproducibility):
        assert "city-metrics" in text
        assert "city-compare" in text
    for text in (readme, architecture, cli, city_pilot, reproducibility, limitations):
        assert "opportunity-confounded" in text
        assert "synthetic-comparison-not-causal-or-observed-effect" in text
    assert "c4a evidence" in roadmap
    assert "[ ] **c4" in roadmap
    assert "c4b" in roadmap

    combined = "\n".join(public)
    for stale_claim in (
        "cannot affect cognition, state, budget, movement, purchase probability, "
        "metrics or reports",
        "cannot reach cognition, agent state, purchase logic, metrics or reports",
        "neither evidence layer affects cognition, agent state, budget, movement, "
        "purchase probability, metrics or reports",
        "c4 artifact-backed metrics are required",
        "c4 are responsible for causal downstream outcomes and metrics",
    ):
        assert stale_claim not in combined


def test_spatial_response_docs_describe_exact_schema_v6_artifacts_and_interfaces() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    city_pilot = _read("docs", "city-pilot.md")
    reproducibility = _read("docs", "reproducibility.md")
    model_card = _read("docs", "methodology", "model-card.md")
    limitations = _read("docs", "methodology", "limitations.md")

    public = (readme, architecture, cli, city_pilot, reproducibility, model_card, limitations)
    for text in public:
        lowered = text.lower()
        assert "schema-v6" in lowered
        assert "synthetic-response-not-observed-behavior" in lowered

    for text in (readme, cli, city_pilot, reproducibility):
        assert "--spatial-response" in text

    for text in (cli, city_pilot):
        _assert_terms_share_paragraph(
            text,
            "--spatial-response",
            "requires",
            "--spatial-campaign",
        )
        _assert_terms_share_paragraph(text, "--spatial-campaign", "schema-v5")
        _assert_terms_share_paragraph(text, "--spatial-response", "schema-v6")

    artifact_docs = (architecture, cli, city_pilot, reproducibility)
    for text in artifact_docs:
        for artifact in (
            "inputs/spatial-response.json",
            "outputs/spatial-responses.jsonl",
            "outputs/response-state.json",
            "outputs/response-summary.json",
        ):
            assert artifact in text, artifact

    identity_docs = (architecture, city_pilot, reproducibility)
    for text in identity_docs:
        for model_id in (
            "illustrative-road-spatial-response-study-v1",
            "spatial-response-v1",
            "spatial-response-artifact-v1",
            "spatial-response-state-v1",
        ):
            assert model_id in text, model_id

    for receipt in (
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
        assert receipt in cli, receipt

    for route in (
        "/api/response-summary",
        "/api/response-events",
        "/api/response-state",
    ):
        assert route in cli, route
    _assert_terms_share_paragraph(cli, "/api/response-state", "final", "scrub")


def test_spatial_response_docs_pin_rule_semantics_and_remaining_limitations() -> None:
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    city_pilot = _read("docs", "city-pilot.md")
    reproducibility = _read("docs", "reproducibility.md")
    model_card = _read("docs", "methodology", "model-card.md")
    limitations = _read("docs", "methodology", "limitations.md")

    formula_contract = _squash(city_pilot)
    for formula in (
        "interest_match = jaccard(profile interests, campaign target interests)",
        "affordability = clamp(1.25 - relative_price * price_sensitivity, 0, 1)",
        "value_match = 0.55 * interest_match + 0.25 * novelty_seeking + 0.20 * affordability",
        "frequency_fatigue = min(1, prior_notices_today / frequency_cap_per_agent_per_day)",
        "0.18 * value_match - 0.12 * advertising_skepticism - 0.06 * frequency_fatigue",
        "0.22 * channel_recall_encoding + 0.12 * novelty_seeking - 0.08 * frequency_fatigue",
        "sentiment_after = clamp(sentiment_before + sum(sentiment_delta), -1, 1)",
        "recall_after = 1 - (1 - recall_before) * product(1 - recall_delta)",
        "0.40 * ((sentiment_after + 1) / 2) + 0.25 * value_match + "
        "0.20 * recall_after + 0.15 * impulsivity",
    ):
        assert formula in formula_contract, formula

    for text in (architecture, city_pilot, reproducibility):
        lowered = _squash(text)
        assert "campaign-scoped" in lowered
        assert "immutable pre-minute state" in lowered
        _assert_terms_share_paragraph(
            text,
            "same",
            "minute",
            "immutable pre-minute state",
            "atomic",
            "state update",
        )

    for text in (cli, reproducibility, model_card, limitations):
        _assert_terms_share_paragraph(text, "schema-v5", "schema-v6", "attention-only")

    _assert_terms_share_paragraph(
        limitations,
        "schema-v6",
        "cognition",
        "memory",
        "social",
        "budget",
        "purchase event",
    )
    _assert_terms_share_paragraph(
        limitations,
        "spatial",
        "report",
        "zero-javascript",
        "8 mib",
    )
    for text in (model_card, limitations):
        lowered = _squash(text)
        assert "purchase intention" in lowered
        _assert_terms_share_paragraph(text, "purchase intention", "purchase probability", "sales")
        assert "not purchase probability" in lowered or "not a purchase probability" in lowered
        assert "not calibrated" in lowered or "uncalibrated" in lowered


def test_spatial_response_public_input_contract_is_complete_and_unambiguous() -> None:
    city_pilot = _read("docs", "city-pilot.md")
    cli = _read("docs", "cli-reference.md")

    _assert_terms_share_paragraph(
        city_pilot,
        "relative_price",
        "advertised price",
        "category reference price",
        "(0, 100]",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "channel_recall_encoding",
        "mobile-feed",
        "mobile_recall_encoding",
        "roadside-billboard",
        "roadside_recall_encoding",
    )

    match = re.search(
        r"<!-- spatial-response-input:start -->\s*```json\s*(.*?)\s*```\s*"
        r"<!-- spatial-response-input:end -->",
        city_pilot,
        flags=re.DOTALL,
    )
    assert match is not None, "public city guide must contain the exact response-input example"
    assert json.loads(match.group(1)) == {
        "schema_version": 1,
        "city_sha256": "0" * 64,
        "scenario_sha256": "1" * 64,
        "profiles": [
            {
                "agent_id": "person-001",
                "fictional": True,
                "interests": ["coffee"],
                "traits": {
                    "price_sensitivity": 0.5,
                    "novelty_seeking": 0.5,
                    "advertising_skepticism": 0.5,
                    "mobile_recall_encoding": 0.5,
                    "roadside_recall_encoding": 0.5,
                    "impulsivity": 0.5,
                },
            }
        ],
        "campaigns": [
            {
                "campaign_id": "demo-campaign",
                "creative_sha256": "2" * 64,
                "target_interests": ["coffee"],
                "relative_price": 1.0,
            }
        ],
        "initial_states": [
            {
                "agent_id": "person-001",
                "campaign_id": "demo-campaign",
                "brand_sentiment": 0.0,
                "recall_strength": 0.0,
                "purchase_intention": 0.0,
            }
        ],
    }
    _assert_terms_share_paragraph(cli, "schema-v1", "exact json shape", "city-pilot.md")


def test_spatial_response_docs_distinguish_v5_attention_from_v6_state_causality() -> None:
    model_card = _read("docs", "methodology", "model-card.md")
    lowered = _squash(model_card)

    assert "cannot reach cognition, agent state, or purchase logic" not in lowered
    _assert_terms_share_paragraph(
        model_card,
        "attention evaluator itself",
        "does not mutate",
        "schema-v5",
        "stops",
    )
    _assert_terms_share_paragraph(
        model_card,
        "schema-v6",
        "persisted noticed",
        "separate response boundary",
        "campaign-scoped state",
    )


def test_spatial_response_reproducibility_docs_distinguish_permutations_from_corruption() -> None:
    reproducibility = _read("docs", "reproducibility.md")
    lowered = _squash(reproducibility)

    assert "source notice ordering cannot change the result" not in lowered
    _assert_terms_share_paragraph(
        reproducibility,
        "response-input collection permutations",
        "canonicalized",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "noncanonical persisted notice order",
        "corruption",
        "refused",
    )


def test_spatial_response_docs_state_exact_agent_binding_and_hash_boundaries() -> None:
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    city_pilot = _read("docs", "city-pilot.md")
    reproducibility = _read("docs", "reproducibility.md")
    model_card = _read("docs", "methodology", "model-card.md")

    for text in (architecture, cli, model_card):
        assert "exact agent-id set" in _squash(text)
    _assert_terms_share_paragraph(
        reproducibility,
        "response input",
        "not bound",
        "mobility assignment",
        "separately freezes",
    )
    for text in (cli, city_pilot, reproducibility):
        _assert_terms_share_paragraph(
            text,
            "response_input_sha256",
            "excluding",
            "trailing newline",
            "response_stream_sha256",
            "response_state_sha256",
            "response_summary_sha256",
            "persisted bytes",
            "including",
            "trailing newline",
        )


def test_readme_describes_response_routing_and_claim_scope_precisely() -> None:
    readme = _read("README.md")
    lowered = _squash(readme)

    assert (
        "campaign copy, identity, creative content and provider configuration are not inputs "
        "to the attention draw or the response formulas"
    ) not in lowered
    _assert_terms_share_paragraph(
        readme,
        "campaign id",
        "routes",
        "campaign assumptions",
        "campaign-scoped state",
    )
    _assert_terms_share_paragraph(
        readme,
        "campaign name",
        "copy",
        "creative hash",
        "response assumptions remain fixed",
        "numeric response values",
    )
    _assert_terms_share_paragraph(
        readme,
        "spatial.response",
        "spatial.state-updated",
        "stream record",
        "claim scope",
        "final-state document",
        "top level",
        "state entry",
    )


def test_spatial_response_roadmap_design_and_changelog_record_partial_c3c_status() -> None:
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )
    design = _read("docs", "superpowers", "specs", "2026-09-28-production-city-platform-design.md")
    changelog = _read("CHANGELOG.md")
    unreleased = changelog.split("## [0.1.0]", maxsplit=1)[0]

    roadmap_lower = roadmap.lower()
    assert "c3c evidence" in roadmap_lower
    assert "schema-v6" in roadmap_lower
    assert "synthetic-response-not-observed-behavior" in roadmap_lower
    assert "[ ] **c3" in roadmap_lower
    assert "[ ] **c4" in roadmap_lower
    _assert_terms_share_paragraph(
        roadmap,
        "C4b evidence",
        "spatial-paired-study-v1",
        "city-report",
        "source artifacts unchanged",
    )
    _assert_terms_share_paragraph(
        roadmap,
        "Current ledger",
        "C4b repeated-seed analysis/static reporting",
        "unchecked parent gates",
    )
    _assert_terms_share_paragraph(
        roadmap,
        "C3 remains open",
        "cognition",
        "memory",
        "social",
        "purchase",
    )

    design_lower = design.lower()
    assert "partially implemented" in design_lower
    assert "schema-v6" in design_lower
    assert "synthetic-response-not-observed-behavior" in design_lower
    for still_open in (
        "rights-reviewed real",
        "authentication",
        "external validity",
    ):
        assert still_open in design_lower

    unreleased_lower = unreleased.lower()
    for term in (
        "schema-v6",
        "--spatial-response",
        "spatial-response-v1",
        "synthetic-response-not-observed-behavior",
        "city-study",
        "city-report",
        "spatial-paired-study-v1",
    ):
        assert term in unreleased_lower


def test_spatial_response_metrics_are_an_additive_schema_v6_public_contract() -> None:
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    city_pilot = _read("docs", "city-pilot.md")
    reproducibility = _read("docs", "reproducibility.md")
    model_card = _read("docs", "methodology", "model-card.md")

    for text in (architecture, cli, city_pilot, reproducibility, model_card):
        assert "spatial-response-metrics-v1" in text
        assert "synthetic-response-metrics-not-observed-outcomes" in text

    _assert_terms_share_paragraph(cli, "city-metrics", "--layer", "attention", "default")
    _assert_terms_share_paragraph(cli, "city-metrics", "--layer response", "schema-v6")
    _assert_terms_share_paragraph(cli, "city-compare", "--layer response", "schema-v6")
    _assert_terms_share_paragraph(
        cli,
        "/api/spatial-metrics",
        "attention-only",
        "/api/spatial-response-metrics",
        "schema-v6",
        "get-only",
    )

    formulas = _squash(city_pilot)
    for formula in (
        "response_count = len(r)",
        "response_reach = unique responding agents / population_size",
        "response_frequency = len(r) / unique responding agents",
        "mean_rule_sentiment_delta = fsum(response.sentiment_delta) / len(r)",
        "mean_rule_recall_delta = fsum(response.recall_delta) / len(r)",
        "change_total = final_total - initial_total",
        "mean_change = change_total / len(s)",
    ):
        assert formula in formulas, formula
    _assert_terms_share_paragraph(
        city_pilot,
        "mean_rule_recall_delta",
        "planned",
        "recall_strength.mean_change",
        "committed nonlinear",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "one",
        "notice",
        "one",
        "response",
        "not engagement",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "event-only",
        "channel",
        "state",
        "not",
        "attributed",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "direct-response receipts",
        "outputs/spatial-responses.jsonl",
        "spatial.response",
        "state receipts",
        "inputs/spatial-response.json",
        "outputs/response-state.json",
    )


def test_saved_run_response_ledger_is_documented_as_read_only_incremental_workbench_progress() -> (
    None
):
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    city_pilot = _read("docs", "city-pilot.md")
    limitations = _read("docs", "methodology", "limitations.md")
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )

    _assert_terms_share_paragraph(
        readme,
        "city-view",
        "full-run response ledger",
        "planned rule deltas",
        "committed state",
    )
    _assert_terms_share_paragraph(
        architecture,
        "city-view",
        "/api/spatial-response-metrics",
        "read-only",
        "full-run",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "/api/spatial-response-metrics",
        "full-run response ledger",
        "not written back",
        "channel",
    )
    _assert_terms_share_paragraph(
        limitations,
        "saved-run browser",
        "full-run",
        "purchase intention",
        "not purchase probability",
    )
    _assert_terms_share_paragraph(
        roadmap,
        "D3 progress",
        "scenario operation",
        "D3 remains open",
        "provider",
        "report download",
    )
    _assert_terms_share_paragraph(
        roadmap,
        "D4 evidence",
        "local deterministic profile",
        "maximum fixture",
        "one peak in-flight timeline read",
        "complete",
    )


def test_spatial_study_docs_pin_seed_statistics_and_provenance_contract() -> None:
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    city_pilot = _read("docs", "city-pilot.md")
    reproducibility = _read("docs", "reproducibility.md")
    model_card = _read("docs", "methodology", "model-card.md")
    limitations = _read("docs", "methodology", "limitations.md")

    for text in (architecture, cli, city_pilot, reproducibility, model_card, limitations):
        assert "city-study" in text
        assert "synthetic-study-not-observed-or-causal-effect" in text
    for text in (architecture, city_pilot, reproducibility, model_card):
        assert "spatial-paired-study-v1" in text

    _assert_terms_share_paragraph(
        city_pilot,
        "seed pair",
        "experimental unit",
        "never pools",
        "agents",
        "events",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "assignment",
        "trace",
        "place",
        "common random numbers",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "attention",
        "all-v5",
        "all-v6",
        "attention-and-response",
        "schema-v6",
    )
    _assert_terms_share_paragraph(
        city_pilot,
        "opportunity",
        "matched-opportunity-structure",
        "opportunity-confounded",
        "response assumptions",
        "matched-response-assumptions",
        "response-assumption-confounded",
    )

    statistics = _squash(reproducibility)
    for required in (
        "sample standard deviation",
        "median",
        "nullable paired standardized difference",
        "0.8",
        "no p-value",
        "10,000",
        "splitmix64",
        "249",
        "9749",
        "simulator seed variation",
    ):
        assert required in statistics, required
    _assert_terms_share_paragraph(
        city_pilot,
        "each metric",
        "independently keyed SplitMix64 stream",
        "10,000 resamples",
    )
    assert "10,000 independently keyed SplitMix64 resamples" not in city_pilot
    _assert_terms_share_paragraph(
        reproducibility,
        "constant",
        "sample deviation",
        "0.0",
        "standardized difference",
        "null",
        "bootstrap endpoints",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "50",
        "full-protocol-50-or-more-seeds",
        "not registered",
        "not representative",
        "not validated",
        "not statistically powered",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "city manifests",
        "package",
        "python",
        "model",
        "do not prove",
        "git",
        "lockfile",
    )


def test_spatial_study_hash_size_and_report_publication_boundaries_are_explicit() -> None:
    cli = _read("docs", "cli-reference.md")
    reproducibility = _read("docs", "reproducibility.md")
    limitations = _read("docs", "methodology", "limitations.md")

    _assert_terms_share_paragraph(
        reproducibility,
        "study_definition_sha256",
        "study_result_sha256",
        "without a trailing lf",
        "manifest_sha256",
        "including",
        "trailing lf",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "report_sha256",
        "report_bytes",
        "exact",
        "utf-8/lf",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "32 mib",
        "o(largest source run + bounded result)",
        "8 mib",
    )
    _assert_terms_share_paragraph(
        cli,
        "city-reports/<study_id>.html",
        "zero-javascript",
        "no network",
        "csp",
    )
    _assert_terms_share_paragraph(
        cli,
        "atomic no-clobber",
        "hard link",
        "best effort",
        "directory fsync",
        "power loss",
    )
    for receipt_field in (
        "schema_version",
        "format_id",
        "claim_scope",
        "study_id",
        "study_definition_sha256",
        "study_result_sha256",
        "report_path",
        "report_sha256",
        "report_bytes",
    ):
        assert receipt_field in cli, receipt_field
    assert "city-study" in limitations
    assert "city-report" in limitations


def test_city_pilot_repeated_seed_example_uses_campaign_backed_schema_v6_runs() -> None:
    city_pilot = _read("docs", "city-pilot.md")

    for required in (
        "--run-id response-0",
        "--seed 0",
        "--run-id response-1",
        "--seed 1",
        "--spatial-campaign spatial-campaign.json",
        "--spatial-response spatial-response.json",
        "city-metrics ./city-output response-0 --layer response",
        "city-compare ./city-output response-0 response-0 --layer response",
        "city-study ./city-output spatial-study.json",
        "city-report ./city-output spatial-study.json",
    ):
        assert required in city_pilot, required

    match = re.search(
        r"<!-- spatial-study-definition:start -->\s*```json\s*(.*?)\s*```\s*"
        r"<!-- spatial-study-definition:end -->",
        city_pilot,
        flags=re.DOTALL,
    )
    assert match is not None, "city guide must include the exact two-seed A/A definition"
    assert json.loads(match.group(1)) == {
        "schema_version": 1,
        "study_id": "response-aa",
        "design": "a-a",
        "analysis_scope": "attention-and-response",
        "pairs": [
            {
                "seed": 0,
                "control_run_id": "response-0",
                "treatment_run_id": "response-0",
            },
            {
                "seed": 1,
                "control_run_id": "response-1",
                "treatment_run_id": "response-1",
            },
        ],
    }


def test_spatial_public_status_is_complete_only_for_bounded_c4b() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )
    design = _read("docs", "superpowers", "specs", "2026-09-28-production-city-platform-design.md")

    for text in (readme, architecture, roadmap, design):
        assert "C4b evidence" in text
        assert "city-study" in text
        assert "city-report" in text
    _assert_terms_share_paragraph(
        roadmap,
        "C4 remains open",
        "social",
        "job orchestration",
        "workbench",
        "authentication",
        "calibration",
        "external validity",
    )
    _assert_terms_share_paragraph(
        design,
        "bounded C4b",
        "complete",
        "broader C3",
        "C4",
        "open",
    )

    assert "attribute the difference in outcome to that change alone" not in readme.lower()
    spatial_evidence_section = roadmap.split("**C3c evidence", maxsplit=1)[1].split(
        "## D. Analyst web workbench", maxsplit=1
    )[0]
    assert "inspect the causal event on that road/time" not in spatial_evidence_section.lower()
    assert "inspect persisted evidence on that road/time" in spatial_evidence_section.lower()
    _assert_terms_share_paragraph(
        readme,
        "zone campaign engine",
        "shared per-person",
        "spatial",
        "campaign-scoped",
    )


def test_persona_authoring_docs_publish_version_and_scientific_boundaries() -> None:
    paths = (
        "README.md",
        "CHANGELOG.md",
        "docs/architecture.md",
        "docs/city-pilot.md",
        "docs/cli-reference.md",
        "docs/reproducibility.md",
        "docs/methodology/model-card.md",
        "docs/methodology/limitations.md",
        "docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md",
    )
    for path in paths:
        text = _squash(_read(path)).lower()
        for term in (
            "schema-v8",
            "schema-2",
            "schema-v7",
            "schema-1",
            "fictional",
            "mbti",
            "noncausal",
        ):
            assert term in text, (path, term)
    cli = _read("docs/cli-reference.md")
    assert "GET /api/persona-templates" in cli
    assert "3,072" in cli and "98,304" in cli and "131,072" in cli
    assert "32,768" in cli
    reproducibility = _squash(_read("docs/reproducibility.md")).lower()
    for term in ("reordering", "persona id", "template", "assignments", "descriptive"):
        assert term in reproducibility


@pytest.mark.parametrize(
    "path,anchor,versions",
    [
        (
            "docs/architecture.md",
            "Saved city studies use separate strict",
            "v1/v2/v3/v4/v5/v6/v7/v8/v9/v10",
        ),
        (
            "docs/architecture.md",
            "validates before serving one immutable mobility projection",
            "V4 through V10",
        ),
        (
            "docs/architecture.md",
            "The adapter-free core exposes the original read-only",
            "V5, V6, V7, V8, V9, and V10",
        ),
        ("docs/architecture.md", "Schema V6", "V6, V7, V8, and V9"),
        ("docs/city-pilot.md", "Use `city-metrics --layer response`", "V6/V7/V8/V9"),
        (
            "docs/city-pilot.md",
            "`city-view` validates the full trace before opening",
            "v4, v5, v6, v7, v8, v9, and v10",
        ),
        (
            "docs/city-pilot.md",
            "`/api/response-summary`, paged",
            "V6, V7, V8, V9, and V10",
        ),
        (
            "docs/methodology/model-card.md",
            "City-pack v2 can preserve real-world road geometry",
            "v3/v4/v5/v6/v7/v8/v9",
        ),
    ],
)
def test_current_city_consumer_and_replay_descriptions_include_v9(path, anchor, versions):
    paragraphs = re.split(r"\n\s*\n", _read(path))
    paragraph = next(value for value in paragraphs if anchor in value)
    assert versions in paragraph, (path, anchor, versions)
    assert re.search(r"\bv8\b", paragraph, re.IGNORECASE), (path, anchor)
    for stale in ("through V7", "V6 or V7", "V6/V7 endpoint", "all three"):
        assert stale not in paragraph, (path, anchor, stale)


def test_city_pilot_allows_descriptive_mbti_but_refuses_predictive_claims() -> None:
    text = _read("docs/city-pilot.md")
    _assert_terms_share_paragraph(
        text, "MBTI", "descriptive", "noncausal", "predictive", "not implemented"
    )
    assert "adding mbti labels without" not in _squash(text).lower()


@pytest.mark.parametrize("path", ["README.md", "docs/methodology/model-card.md"])
def test_fictional_only_policy_does_not_claim_identity_enforcement(path) -> None:
    text = _read(path)
    _assert_terms_share_paragraph(text, "fictional-only", "policy", "editable", "optional")
    _assert_terms_share_paragraph(text, "best effort", "cannot prove", "fictional identity")
    lowered = _squash(text).lower()
    for overclaim in (
        "every agent in this simulator is fictional and generated from templates",
        "this constraint is enforced in code, not merely documented",
        "the model contains none",
    ):
        assert overclaim not in lowered


@pytest.mark.parametrize(
    "path,anchor",
    [
        ("docs/cli-reference.md", "Load the frozen inputs, regenerate"),
        ("docs/methodology/model-card.md", "workbench artifacts retain that entire"),
    ],
)
def test_workbench_replay_provenance_paragraph_covers_v7_v8_and_v9(path, anchor) -> None:
    paragraph = next(value for value in re.split(r"\n\s*\n", _read(path)) if anchor in value)
    for version in ("schema-v7", "schema-v8", "schema-v9"):
        assert version in paragraph.lower(), (path, version)
    assert "inputs/workbench.json" in paragraph


def test_d1b_workbench_contract_is_documented_without_claiming_later_gates() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    reproducibility = _read("docs", "reproducibility.md")
    city_pilot = _read("docs", "city-pilot.md")
    model_card = _read("docs", "methodology", "model-card.md")
    limitations = _read("docs", "methodology", "limitations.md")
    changelog = _read("CHANGELOG.md")
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )

    public = (
        readme,
        architecture,
        cli,
        reproducibility,
        city_pilot,
        model_card,
        limitations,
        changelog,
    )
    for text in public:
        lowered = text.lower()
        assert "schema-v7" in lowered
        assert "inputs/workbench.json" in lowered

    _assert_terms_share_paragraph(
        architecture,
        "process-local",
        "one",
        "worker",
        "evaluating",
        "persisting",
        "verifying",
    )
    for text in (architecture, cli):
        _assert_terms_share_paragraph(
            text,
            "accepted_settings",
            "every",
            "phase",
            "verified",
            "result",
        )
    _assert_terms_share_paragraph(
        reproducibility,
        "schema-v7",
        "canonical",
        "workbench_input_sha256",
        "replay",
    )
    for route in (
        "POST /api/jobs",
        "GET /api/jobs/{job_id}",
        "POST /api/jobs/{job_id}/cancel",
        "GET /api/runs?offset=&limit=",
        "GET /runs/{run_id}",
    ):
        assert route in cli, route

    _assert_terms_share_paragraph(
        limitations,
        "process-local",
        "restart",
        "authentication",
        "provider",
        "report download",
        "real city",
    )
    assert "[x] **D1b" in roadmap
    assert "[x] **D2" in roadmap
    assert "[ ] **D3" in roadmap
    assert "[x] **D4" in roadmap

    current_claims = "\n".join(public).lower()
    for stale_claim in (
        "does **not** start simulation jobs",
        "automatic workbench jobs and persistence",
        "jobs, persistence and run discovery are explicitly false",
    ):
        assert stale_claim not in current_claims


def test_d1b_plan_records_implemented_tasks_without_preclaiming_certification() -> None:
    plan = _read("docs", "superpowers", "plans", "2026-10-10-local-city-workbench-jobs.md")
    for task in range(1, 8):
        start = plan.split(f"### Task {task}:", maxsplit=1)[1]
        section = start.split(f"### Task {task + 1}:", maxsplit=1)[0]
        assert "- [ ]" not in section, f"Task {task} still has an unchecked implementation step"
    certification = plan.split("### Task 8:", maxsplit=1)[1]
    assert "- [ ]" in certification


def test_browser_operation_plan_records_completed_product_tasks_before_final_gates() -> None:
    plan = _read(
        "docs",
        "superpowers",
        "plans",
        "2026-10-10-local-city-workbench-browser-operation.md",
    )
    for task in range(1, 10):
        start = plan.split(f"### Task {task}:", maxsplit=1)[1]
        section = start.split(f"### Task {task + 1}:", maxsplit=1)[0]
        assert "- [ ]" not in section, f"Task {task} still has an unchecked product step"
    final_gates = plan.split("### Task 10:", maxsplit=1)[1]
    assert "- [ ]" in final_gates


def test_local_browser_workbench_is_documented_without_enterprise_overclaim() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    reproducibility = _read("docs", "reproducibility.md")
    city_pilot = _read("docs", "city-pilot.md")
    model_card = _read("docs", "methodology", "model-card.md")
    limitations = _read("docs", "methodology", "limitations.md")
    changelog = _read("CHANGELOG.md")
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )

    for text in (readme, architecture, cli, city_pilot, changelog):
        assert "CITY -> CAMPAIGN -> RUN -> INSPECT" in text

    for term in (
        "http://127.0.0.1:8765",
        "workspace",
        "exact decimal",
        "verified library",
    ):
        assert term in readme
    for route in (
        "GET /api/catalog/cities/{city_id}",
        "POST /api/scenarios/validate",
        "GET /api/runs/{run_id}/workbench-input",
        "GET /runs/{run_id}",
    ):
        assert route in cli, route
    _assert_terms_share_paragraph(
        architecture,
        "browser",
        "unsaved draft",
        "server",
        "authoritative",
        "schema-v7",
    )
    _assert_terms_share_paragraph(
        reproducibility,
        "canonical decimal string",
        "python integer",
        "presentation state",
        "artifact",
    )
    _assert_terms_share_paragraph(
        model_card,
        "homogeneous",
        "cohort",
        "not",
        "calibration",
    )
    _assert_terms_share_paragraph(
        limitations,
        "provider",
        "oauth",
        "authentication",
        "real city",
        "report download",
        "comparison",
    )

    combined = "\n".join(
        (readme, architecture, cli, city_pilot, model_card, limitations, changelog)
    ).lower()
    for stale_claim in (
        "does not yet ship the four-stage browser campaign editor",
        "current shell is not yet the complete four-stage campaign editor",
        "packaged shell does not yet provide the four-stage campaign editor",
        "four-stage browser scenario editor and job controls",
        "full browser editor, authentication",
    ):
        assert stale_claim not in combined

    assert "[x] **D2" in roadmap
    assert "[ ] **D3" in roadmap
    assert "[x] **D4" in roadmap
    _assert_terms_share_paragraph(
        roadmap,
        "D2",
        "local deterministic",
        "worldwide",
        "month",
        "not",
    )
    _assert_terms_share_paragraph(
        roadmap,
        "D3",
        "scenario",
        "provider",
        "report download",
        "remains open",
    )
    _assert_terms_share_paragraph(
        roadmap,
        "D4",
        "local deterministic profile",
        "maximum fixture",
        "timeline read",
        "complete",
    )


def test_workbench_docs_cover_v7_and_late_failure_boundaries() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    reproducibility = _read("docs", "reproducibility.md")
    city_pilot = _read("docs", "city-pilot.md")
    limitations = _read("docs", "methodology", "limitations.md")
    roadmap = _read(
        "docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md"
    )

    public = "\n".join((readme, architecture, cli, reproducibility, city_pilot)).lower()
    for stale_schema_scope in (
        "schema-v5 and schema-v6 runs",
        "requires schema-v6 throughout",
        "attention-and-response requires v6",
    ):
        assert stale_schema_scope not in public, stale_schema_scope

    assert "workbench input and control seed values" in readme.lower()
    assert "<workspace>/city-runs/<run-id>/" in readme
    for text in (readme, architecture, cli):
        _assert_terms_share_paragraph(
            text,
            "late cancellation",
            "refused",
            "publication",
            "verification",
            "fail safely",
        )
    _assert_terms_share_paragraph(
        limitations,
        "observed",
        "calibrated",
        "attention",
        "claim",
    )

    assert "play/pause/speed/scrub" not in roadmap
    _assert_terms_share_paragraph(
        roadmap,
        "D4 evidence",
        "local deterministic profile",
        "timeline read",
        "complete",
    )


def test_workbench_docs_describe_one_verified_timeline_projection() -> None:
    readme = _read("README.md")
    architecture = _read("docs", "architecture.md")
    cli = _read("docs", "cli-reference.md")
    reproducibility = _read("docs", "reproducibility.md")

    for text in (readme, architecture, reproducibility):
        _assert_terms_share_paragraph(
            text,
            "/timeline",
            "one",
            "verified",
            "individual",
        )
    assert "GET /api/runs/{run_id}/timeline?minute=&agent_id=&..." in cli


def test_optional_city_explanations_are_documented_as_a_noncausal_v9_layer() -> None:
    paths = (
        "README.md",
        "CHANGELOG.md",
        "docs/architecture.md",
        "docs/city-pilot.md",
        "docs/cli-reference.md",
        "docs/reproducibility.md",
        "docs/methodology/model-card.md",
        "docs/methodology/limitations.md",
        "docs/methodology/experiment-protocol.md",
        "docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md",
    )
    for path in paths:
        text = _squash(_read(path))
        assert "schema-v9" in text, path
        assert "noncausal" in text, path

    public = _squash(
        "\n".join(
            _read(path)
            for path in (
                "README.md",
                "docs/architecture.md",
                "docs/city-pilot.md",
                "docs/cli-reference.md",
                "docs/reproducibility.md",
                "docs/methodology/model-card.md",
                "docs/methodology/limitations.md",
            )
        )
    )
    for artifact in (
        "inputs/cognition.json",
        "outputs/cognition.jsonl",
        "outputs/cognition-summary.json",
    ):
        assert artifact in public, artifact
    for prohibited_effect in (
        "movement",
        "attention",
        "response",
        "state",
        "budget",
        "purchase probability",
        "metrics",
    ):
        assert prohibited_effect in public, prohibited_effect


def test_provider_setup_docs_pin_the_supported_compatible_endpoint_contract() -> None:
    readme = _read("README.md")
    architecture = _read("docs/architecture.md")
    city_pilot = _read("docs/city-pilot.md")
    cli = _read("docs/cli-reference.md")

    for text in (readme, city_pilot, cli):
        assert "--provider-origin ID=URL" in text
        assert "--provider-key-env ID=ENVIRONMENT_VARIABLE" in text
        assert "local-ollama" in text
    for text in (architecture, city_pilot, cli):
        _assert_terms_share_paragraph(
            text,
            "OpenAI-compatible",
            "Chat Completions",
            "non-streaming",
            "/chat/completions",
        )
    _assert_terms_share_paragraph(
        cli,
        "ChatGPT",
        "Codex",
        "subscription",
        "OAuth",
        "not accepted",
    )
    _assert_terms_share_paragraph(
        architecture,
        "adlife.city-provider.v1",
        "keyring",
        "environment-variable name",
        "credential value",
    )
    for route in (
        "GET /api/providers",
        "POST /api/providers",
        "POST /api/providers/{profile_id}/revisions/{revision_id}/credential",
        "POST /api/providers/{profile_id}/revisions/{revision_id}/check",
        "GET /api/runs/{run_id}/cognition-summary",
        "GET /api/runs/{run_id}/cognition-records",
    ):
        assert route in cli, route


def test_provider_replay_docs_distinguish_live_variation_from_frozen_reproduction() -> None:
    readme = _read("README.md")
    reproducibility = _read("docs/reproducibility.md")
    model_card = _read("docs/methodology/model-card.md")
    limitations = _read("docs/methodology/limitations.md")

    for text in (readme, reproducibility, model_card, limitations):
        _assert_terms_share_paragraph(
            text,
            "live",
            "nondeterministic",
            "frozen",
            "replay",
            "no provider call",
        )
    _assert_terms_share_paragraph(
        readme,
        "rules",
        "mock",
        "offline",
        "default",
        "no credential",
    )

    combined = _squash("\n".join((readme, reproducibility, model_card, limitations)))
    for stale_claim in (
        "does not configure providers or oauth",
        "does not provide provider or oauth configuration",
        "provider/oauth configuration is not implemented",
    ):
        assert stale_claim not in combined, stale_claim


def test_spatial_study_plan_records_completed_tasks_and_exact_evidence() -> None:
    plan = _read("docs", "superpowers", "plans", "2026-10-03-spatial-study-analysis.md")
    task_3 = plan.split("### Task 3:", maxsplit=1)[1].split("### Task 4:", maxsplit=1)[0]
    task_4 = plan.split("### Task 4:", maxsplit=1)[1].split("### Task 5:", maxsplit=1)[0]
    task_5 = plan.split("### Task 5:", maxsplit=1)[1]

    assert "- [ ]" not in task_3
    assert "controller review and push remain pending" not in task_3.lower()
    assert "805e333" in task_3
    assert "eb1d0cd" in task_3
    assert "origin/main" in task_3

    assert "- [ ]" not in task_4
    assert "1cab490" in task_4
    assert "bb3fb58" in task_4
    assert "independent scoped re-review" in task_4.lower()
    assert "origin/main" in task_4

    assert "- [ ]" not in task_5
    for evidence in (
        "4,592 tests",
        "90.69%",
        "60,061-byte static spatial report",
        "14.02 seconds",
        "windows x86-64 pyinstaller build",
        "108 tests",
        "4,597 tests",
        "exact `city-run` json receipt",
        "recorded jsonl/state evidence",
    ):
        assert evidence in task_5.lower()


def test_spatial_response_docs_remove_obsolete_pre_c3c_claims() -> None:
    public = _squash(
        "\n".join(
            _read(path)
            for path in (
                "README.md",
                "docs/architecture.md",
                "docs/city-pilot.md",
                "docs/cli-reference.md",
                "docs/reproducibility.md",
                "docs/methodology/model-card.md",
                "docs/methodology/limitations.md",
            )
        )
    )
    roadmap = _squash(
        _read("docs", "superpowers", "plans", "2026-09-28-production-city-platform-roadmap.md")
    )
    design = _squash(
        _read("docs", "superpowers", "specs", "2026-09-28-production-city-platform-design.md")
    )

    for stale_claim in (
        "the remaining c3 response/state bridge",
        "the c3 response/state bridge",
        "remaining c3 response/state work",
        "validate a schema-v5 saved run and derive",
        "compare two fully verified schema-v5 runs",
        "v1/v2/v3/v4/v5 `cityrunmanifest`",
        "it is not joined to impressions",
        "not a spatial extension of the advertising results",
    ):
        assert stale_claim not in public, stale_claim

    assert "c3 remains open for that response/state bridge" not in roadmap
    assert "a1\u2013g5 are unchecked" not in roadmap
    assert "status: proposed target, **not implemented**" not in design


# ---------------------------------------------------------------------------
# Bounded city cognition: workbench schema 4 / manifest V10
# ---------------------------------------------------------------------------

# Every limit below is read from the runtime contract, so changing a frozen budget cannot
# leave a stale figure in the public documents without failing this suite.
_FUNDED_RESPONSE_LIMITS: tuple[int, ...] = typing.get_args(
    CityCognitionInputV2.model_fields["funded_response_limit"].annotation
)
_RESERVATION_SENTENCE = re.compile(
    r"each funded request reserves ([\d,]+) output tokens: at most (\d+) http attempts of up "
    r"to ([\d,]+) output tokens each"
)
_AGGREGATE_SENTENCE = re.compile(r"(\d+) funded requests reserve ([\d,]+) output tokens")
_SELECTION_SENTENCE = re.compile(r"at most (\d+) noticed responses per agent and (\d+) per run")


def _integer(text: str) -> int:
    return int(text.replace(",", ""))


def _table_row(text: str, first_cell: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.strip("|").split("|")[0].strip() == first_cell:
            return stripped
    return None


def _public_documents() -> dict[str, str]:
    documents = {path.relative_to(ROOT).as_posix(): text for path, text in _all_docs().items()}
    documents["CHANGELOG.md"] = _read("CHANGELOG.md")
    return documents


def test_bounded_output_budget_figures_match_the_runtime_contract() -> None:
    """Every published V10 reservation figure is the runtime's own, never a stale copy."""
    reservation_documents: set[str] = set()
    aggregate_maximum_documented = False
    for name, text in _public_documents().items():
        squashed = _squash(text)
        for reserved, attempts, per_attempt in _RESERVATION_SENTENCE.findall(squashed):
            reservation_documents.add(name)
            assert _integer(reserved) == CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST, name
            assert int(attempts) == CITY_COGNITION_V2_MAX_ATTEMPTS_PER_REQUEST, name
            assert _integer(per_attempt) == CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT, name
        for count, reserved in _AGGREGATE_SENTENCE.findall(squashed):
            assert int(count) in _FUNDED_RESPONSE_LIMITS, (name, count)
            assert _integer(reserved) == (
                int(count) * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
            ), (name, count)
            aggregate_maximum_documented |= (
                int(count) == max(_FUNDED_RESPONSE_LIMITS)
                and _integer(reserved) == CITY_COGNITION_V2_MAX_AGGREGATE_OUTPUT_TOKENS
            )
    for required in (
        "CHANGELOG.md",
        "README.md",
        "docs/cli-reference.md",
        "docs/architecture.md",
        "docs/city-pilot.md",
        "docs/investor-demo.md",
    ):
        assert required in reservation_documents, f"{required} omits the V10 output reservation"
    assert aggregate_maximum_documented, "the maximum aggregate V10 reservation is undocumented"


def _literal(field: str) -> int:
    (value,) = typing.get_args(CityCognitionInputV2.model_fields[field].annotation)
    return typing.cast(int, value)


def test_cli_reference_documents_the_v10_operation_matrix_and_commands() -> None:
    from adlife.city import workbench_providers

    cli = _read("docs", "cli-reference.md")
    for operation, pair in (
        ("`explanation-only`", "schema 3 / V9"),
        ("`bounded-signals-v1`", "schema 4 / V10"),
    ):
        rows = [line for line in cli.splitlines() if line.startswith(f"| {operation}")]
        assert len(rows) == 2, f"operation matrix rows for {operation} are missing"
        assert all(pair in row for row in rows), operation
    assert "(4,10)" in cli
    for phase in ("planning-cognition", "requesting-cognition", "applying-cognition"):
        assert f"`{phase}`" in cli, phase
    assert '`{"effect_scope": "bounded-signals-v1"}`' in cli
    assert "`provider-compatibility-required`" in cli

    squashed = _squash(cli)
    transport = re.search(
        r"bounded to (\d+) concurrent requests, at most (\d+) seconds per attempt, and (\d+) "
        r"seconds in aggregate",
        squashed,
    )
    assert transport is not None, "bounded provider transport limits are undocumented"
    assert tuple(int(value) for value in transport.groups()) == (
        _literal("concurrency_limit"),
        _literal("attempt_timeout_seconds_ceiling"),
        _literal("aggregate_deadline_seconds"),
    )
    checks = re.search(
        r"a local check waits up to (\d+) seconds .* and a remote check up to (\d+) seconds",
        squashed,
    )
    assert checks is not None, "compatibility-check deadlines are undocumented"
    assert (int(checks.group(1)), int(checks.group(2))) == (
        workbench_providers._LOCAL_CHECK_DEADLINE_SECONDS,
        workbench_providers._REMOTE_CHECK_DEADLINE_SECONDS,
    )

    _assert_terms_share_paragraph(
        cli,
        "schema-v10",
        "spatial-response-metrics-v2",
        "rule-owned-synthetic-baseline-not-observed-outcomes",
        "purchase-intention proxy",
        "provider-dependent-exploratory-not-campaign-effect",
        "no purchase, delta, effect, or lift member",
    )
    _assert_terms_share_paragraph(
        cli, "a schema-v10 run compares only with another schema-v10 run", "refused"
    )
    _assert_terms_share_paragraph(
        cli, "city-compare --layer response", "refuses any schema-v10 run for now"
    )
    _assert_terms_share_paragraph(
        cli, "attention-and-response", "schema-v10 runs are refused for now", "city-report"
    )
    _assert_terms_share_paragraph(
        cli,
        "load the frozen inputs",
        "schema-v10 requires input schema 4",
        "city-cognition-influence-v1",
        "without a provider, cache, keyring, environment variable, profile registry, or network",
    )
    _assert_terms_share_paragraph(
        cli,
        "schema-v10 run opens in the same viewer",
        "/api/workbench-input",
        "/api/cognition-summary",
        "/api/cognition-records",
        "final-end-of-run-not-scrubbed-minute",
    )


def test_architecture_documents_the_v10_data_flow_and_adapter_boundary() -> None:
    architecture = _read("docs", "architecture.md")
    heading = "## The bounded city cognition seam (schema-v10)"
    assert heading in architecture, "V10 cognition seam section is missing"
    section = architecture.split(heading, maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    flow = re.search(r"```text\n(.*?)```", section, flags=re.DOTALL)
    assert flow is not None, "V10 data flow diagram is missing"
    steps = flow.group(1)
    ordered = [
        "deterministic baseline proposals",
        "city-cognition-selection-v2",
        "one terminal record per selected request",
        "the whole applied vector becomes neutral",
        "city-cognition-influence-v1",
        "one atomic dual-state update",
        "manifest-V10 publication last",
    ]
    positions = [steps.index(step) for step in ordered]
    assert positions == sorted(positions), "V10 data flow steps are out of order"
    for source in ("rules", "mock", "provider", "cache", "fallback", "budget-skipped"):
        assert source in steps, source
    policy = CITY_COGNITION_DEFAULT_SELECTION_POLICY_V2
    assert (
        f"at most {policy.selected_requests_per_agent} per agent and "
        f"{policy.selected_requests_per_run} per run"
    ) in steps

    _assert_terms_share_paragraph(
        section,
        "core/simulation/city_cognition_v2.py",
        "core/simulation/spatial_response_v2.py",
        "none imports a provider",
        "adapters/cognition",
    )
    _assert_terms_share_paragraph(
        section,
        "name, age, occupation, background, mbti, and purchase intention are excluded",
        "completion order cannot change",
        "finish_reason",
    )
    _assert_terms_share_paragraph(
        section,
        "city-replay",
        "without a provider, cache, keyring, environment variable, profile registry, or network",
        "capabilities.bounded_cognition",
        "app-cognition-v2.js",
    )
    assert "(4,10)" in architecture


def test_city_pilot_documents_the_frozen_bounded_cognition_science() -> None:
    city_pilot = _read("docs", "city-pilot.md")
    heading = "### Bounded AI cognition (schema-v10)"
    assert heading in city_pilot, "bounded cognition science section is missing"
    section = city_pilot.split(heading, maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    for field in ("affect", "relevance", "credibility", "salience"):
        row = _table_row(section, f"`{field}`")
        assert row is not None, f"closed {field} categories are undocumented"
        for value in typing.get_args(CityCognitionResultV2.model_fields[field].annotation):
            assert f"`{value}`" in row, (field, value)
    flags_row = _table_row(section, "`safety_flags`")
    assert flags_row is not None
    (flag_literal, _) = typing.get_args(
        CityCognitionResultV2.model_fields["safety_flags"].annotation
    )
    for flag in typing.get_args(flag_literal):
        assert f"`{flag}`" in flags_row, flag

    policy = CITY_COGNITION_DEFAULT_INFLUENCE_POLICY_V1
    for rows in (
        policy.affect_adjustments,
        policy.relevance_factors,
        policy.credibility_factors,
        policy.salience_factors,
    ):
        for row in rows:
            assert f"| {row.category} | {row.weight:.2f} |" in section, (row.category, row.weight)
    assert (
        f"* credibility_factor, {policy.sentiment_minimum:.2f}, {policy.sentiment_maximum:.2f})"
        in section
    )
    assert (
        f"* salience_factor, {policy.recall_minimum:.2f}, {policy.recall_maximum:.2f})" in section
    )
    assert "affect=neutral, relevance=medium, credibility=medium, salience=medium" in section

    _assert_terms_share_paragraph(
        section,
        "name, age, occupation, background, and mbti",
        "omitted from the causal prompt",
        "purchase intention is never sent",
    )
    _assert_terms_share_paragraph(
        section,
        "uncertain",
        "safety flag",
        "neutral vector to the whole result",
        "cannot be applied partially",
        "city-cognition-mock-signal-v1",
    )
    _assert_terms_share_paragraph(
        section,
        "immutable pre-minute rule state",
        "immutable pre-minute cognition state",
        "one atomic dual-state update",
    )
    _assert_terms_share_paragraph(
        section,
        "rule-baseline family",
        "purchase-intention proxy",
        "exploratory evidence, not a campaign effect",
        "refuse v10 runs for now",
    )


def _section(text: str, heading: str) -> str:
    """Return the body under ``heading`` up to the next heading of the same or higher level."""
    assert heading in text, f"section is missing: {heading}"
    level = len(heading) - len(heading.lstrip("#"))
    body = text.split(heading, maxsplit=1)[1]
    following = re.search(rf"\n#{{1,{level}}} ", body)
    return body if following is None else body[: following.start()]


def test_investor_demo_is_the_two_part_bounded_cognition_script() -> None:
    demo = _read("docs", "investor-demo.md")
    setup = _section(demo, "## Part 1 — SETUP")
    simulation = _section(demo, "## Part 2 — SIMULATION")
    assert demo.index("## Part 1 — SETUP") < demo.index("## Part 2 — SIMULATION")
    assert "```powershell" in demo and "```bash" not in demo

    for setup_step in (
        "ollama pull qwen2.5:7b-instruct",
        "uv sync",
        "uv run adlife doctor --offline",
        "8/8 checks passed",
        "ollama run qwen2.5:7b-instruct --keepalive 30m hello",
        r"uv run adlife city-workbench --workspace .\demo-workbench --port 50050",
        "http://127.0.0.1:50050",
        "`local-ollama`",
        "http://127.0.0.1:11434/v1",
        "--provider-origin ID=URL",
        "--provider-key-env ID=ENVIRONMENT_VARIABLE",
        "**Save profile revision**",
        "**Check compatibility**",
        "Bounded-signal contract confirmed.",
    ):
        assert setup_step in setup, setup_step
    _assert_terms_share_paragraph(
        setup, "chatgpt or codex subscription", "oauth token", "not accepted"
    )

    ordered_simulation = (
        "`Fictional Grid City V2`",
        "`Fictional Nuvin`",
        "Operation `Bounded AI cognition`, Source `Local compatible model`",
        "Funded responses `10 requests`",
        "**Validate study**",
        "**Start bounded cognition run**",
        "Planning cognition",
        "Requesting cognition",
        "Applying cognition",
        "**Inspect run**",
        "**Saved signal timeline.**",
        r"uv run adlife city-metrics .\demo-workbench demo-v10-001 --layer response",
        r"uv run adlife city-replay .\demo-workbench demo-v10-001",
    )
    positions = [simulation.index(step) for step in ordered_simulation]
    assert positions == sorted(positions), "the simulation steps are out of live order"
    assert "Workbench schema 4 → run V10" in simulation
    assert "frozen offline replay" in simulation

    fallback = _section(demo, "## Without a working model: mock and rules")
    assert "`Mock — reproducible demo`" in fallback
    assert "makes no network call" in _squash(fallback)
    troubleshooting = _section(demo, "## Troubleshooting")
    for symptom in (
        "endpoint resolution refused",
        "protocol incompatible",
        "did not answer within the check's time limit",
        "Check bounded-signal compatibility for this profile revision to enable the run.",
        "**Fallback**",
        "Set aside · safety flag",
        "**Cache**",
        "Errno 10048",
        "winerror 10013",
    ):
        assert symptom in troubleshooting, symptom
    talking_points = _squash(_section(demo, "## Talking points"))
    for claim in (
        "chooses only closed categories",
        "city-cognition-influence-v1",
        "purchase-intention proxy stay rule-owned",
        "names, ages, occupations, biographies, and mbti never reach the model",
        "live model answers are not reproducible",
        "city-replay",
        "not validated psychology",
        "not a sales forecast or campaign effect",
        "unsigned",
    ):
        assert claim in talking_points, claim
    assert _RESERVATION_SENTENCE.search(talking_points), "talking points omit the reservation"

    lowered = demo.lower()
    for superseded in (
        "adlife demo --headless",
        "adlife compare",
        "shares",
        "five-minute",
        "purchase count is structurally zero",
    ):
        assert superseded not in lowered, superseded


def test_reproducibility_separates_live_v10_signals_from_frozen_offline_replay() -> None:
    reproducibility = _read("docs", "reproducibility.md")
    section = _section(reproducibility, "### Bounded AI cognition reproducibility (schema-v10)")
    for artifact in (
        "inputs/workbench.json",
        "inputs/cognition.json",
        "outputs/cognition.jsonl",
        "outputs/cognition-summary.json",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
    ):
        assert artifact in section, artifact
    _assert_terms_share_paragraph(
        section,
        "live provider signals are nondeterministic",
        "temperature 0 with a fixed seed",
        "never a reproducibility check",
        "frozen schema-v10 replay",
        "city-cognition-influence-v1",
        "makes no provider call",
        "byte-identical before and after replay",
    )
    _assert_terms_share_paragraph(
        section,
        "completion order",
        "pythonhashseed",
        "name, age, occupation, background, or mbti",
        "causal request ids",
        "source `cache`",
    )
    _assert_terms_share_paragraph(
        section,
        "rule-baseline metrics",
        "exactly unchanged",
        "unsigned",
        "recomputes the whole hash chain",
        "earlier development builds",
    )
    _assert_terms_share_paragraph(reproducibility, "all-v9", "schema-v10", "refused", "city-report")


def test_bounded_selection_caps_match_the_frozen_selection_policy() -> None:
    policy = CITY_COGNITION_DEFAULT_SELECTION_POLICY_V2
    documented: set[str] = set()
    for name, text in _public_documents().items():
        for per_agent, per_run in _SELECTION_SENTENCE.findall(_squash(text)):
            documented.add(name)
            assert int(per_agent) == policy.selected_requests_per_agent, name
            assert int(per_run) == policy.selected_requests_per_run, name
    for required in ("README.md", "docs/city-pilot.md"):
        assert required in documented, f"{required} omits the V10 selection caps"


def test_readme_presents_the_three_mode_matrix_and_the_live_demo() -> None:
    readme = _read("README.md")
    rows = {
        label: _table_row(readme, f"**{label}**")
        for label in ("Rules", "AI explanation", "Bounded AI cognition")
    }
    assert all(rows.values()), "bounded cognition mode matrix is missing"
    rules, explanation, bounded = (
        typing.cast(str, rows[label])
        for label in ("Rules", "AI explanation", "Bounded AI cognition")
    )
    assert "schema 3 / V9" in explanation and "noncausal" in explanation
    assert "schema 4 / V10" in bounded
    assert "city-cognition-influence-v1" in bounded and "compatibility check" in bounded
    assert "separate synthetic cognition state" in bounded
    assert "no model is called" in rules

    # The demo script is linked prominently, before the first section break.
    introduction = readme.split("\n---\n", maxsplit=1)[0]
    assert "[docs/investor-demo.md](docs/investor-demo.md)" in introduction
    assert "SETUP" in introduction and "SIMULATION" in introduction

    status = next(line for line in readme.splitlines() if "manifest V10" in line)
    assert status.startswith("|") and "Planned" not in status
    _assert_terms_share_paragraph(
        readme,
        *(str(limit) for limit in _FUNDED_RESPONSE_LIMITS),
        "budget-skipped",
        "input tokens and vendor charges are not included",
    )
    _assert_terms_share_paragraph(
        readme,
        "uncertain",
        "safety flag",
        "exact neutral vector",
        "name, age, occupation, background, and mbti never reach the causal prompt",
    )
    _assert_terms_share_paragraph(
        readme,
        "live provider signals are nondeterministic",
        "frozen schema-v10 replay",
        "no provider call",
    )
    _assert_terms_share_paragraph(
        readme,
        "city-compare --layer response",
        "city-study",
        "city-report",
        "refuse v10 runs for now",
        "v6-v9 runs keep those commands",
    )


def test_changelog_records_bounded_cognition_without_overclaiming() -> None:
    unreleased = _read("CHANGELOG.md").split("## [0.1.0]", maxsplit=1)[0]
    _assert_terms_share_paragraph(
        unreleased,
        "bounded ai cognition",
        "workbench schema 4",
        "manifest v10",
        "closed categories",
        "city-cognition-influence-v1",
        "separate synthetic cognition state",
        "purchase-intention proxy stay rule-owned",
        "excluded from the causal prompt",
    )
    _assert_terms_share_paragraph(
        unreleased,
        "live provider signals are nondeterministic",
        "city-replay",
        "no provider, cache, keyring, environment or network access",
    )
    _assert_terms_share_paragraph(
        unreleased, "chatgpt", "codex", "subscription", "oauth", "not accepted"
    )
    _assert_terms_share_paragraph(
        unreleased,
        "city-compare --layer response",
        "city-study",
        "city-report",
        "refuse v10",
        "v6-v9 keep them",
    )
    assert "docs/investor-demo.md" in unreleased


# ---------------------------------------------------------------------------
# Governance files
# ---------------------------------------------------------------------------


def test_governance_files_exist_and_are_substantive() -> None:
    for name in (
        "CITATION.cff",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
    ):
        assert (ROOT / name).stat().st_size > 400, name


def test_license_is_agpl_v3_only() -> None:
    text = _read("LICENSE")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3" in text


def test_citation_cff_matches_package_version() -> None:
    metadata = yaml.safe_load(_read("CITATION.cff"))
    assert metadata["cff-version"].startswith("1.")
    assert metadata["title"]
    assert metadata["version"] == __version__
    assert metadata["type"] == "software"
    assert metadata["date-released"] is not None


def test_changelog_has_a_release_section() -> None:
    text = _read("CHANGELOG.md")
    assert "## [0.1.0]" in text


def test_security_md_offers_private_reporting_without_inventing_contacts() -> None:
    text = _read("SECURITY.md")
    lowered = text.lower()
    assert "private vulnerability reporting" in lowered
    # The spec forbids inventing an email address; route through GitHub instead.
    assert not re.search(r"[\w.]+@[\w.]+\.\w+", text), "SECURITY.md must not list an email"


def test_contributing_requires_dco_signoff() -> None:
    text = _read("CONTRIBUTING.md")
    assert "Signed-off-by" in text
    assert "DCO" in text or "Developer Certificate of Origin" in text


# ---------------------------------------------------------------------------
# Cross-document consistency
# ---------------------------------------------------------------------------


def test_cli_reference_covers_the_registered_command_tree() -> None:
    from adlife.cli.app import app

    registered: set[str] = set()
    for group in app.registered_groups:
        registered.add(group.name)
    for command in app.registered_commands:
        registered.add(command.name)

    text = _read("docs", "cli-reference.md")
    for name in sorted(registered):
        assert f"`adlife {name}`" in text or f"adlife {name}" in text, name


def test_readme_links_every_published_doc() -> None:
    text = _read("README.md")
    for relative in (
        "docs/quickstart.md",
        "docs/cli-reference.md",
        "docs/architecture.md",
        "docs/reproducibility.md",
        "docs/investor-demo.md",
        "docs/methodology/odd-protocol.md",
        "docs/methodology/model-card.md",
        "docs/methodology/experiment-protocol.md",
        "docs/methodology/limitations.md",
    ):
        assert relative in text, relative


def test_no_unsupported_accuracy_or_adoption_claims() -> None:
    forbidden = [
        "state-of-the-art",
        "state of the art",
        "validated on real consumers",
        "validated against real consumers",
        "predicts real",
        "market-proven",
        "guaranteed roi",
    ]
    for path, text in _all_docs().items():
        lowered = text.lower()
        for claim in forbidden:
            assert claim not in lowered, f"{path.name} claims: {claim}"


def test_tick_duration_documentation_matches_the_implementation() -> None:
    """The engine advances 15 simulated minutes per tick; docs must say exactly that.

    The clock is authoritative (``SimClock`` refuses any ``tick_minutes`` other than 15
    and the configuration pins ``Literal[15]``), so 96 ticks drive a 1,440-minute
    simulated day. A timestamp, however, remains an absolute simulated minute - which
    is why "minute 1440" is correct while "one minute per tick" is not. The forbidden
    phrasings below are the ways this contradiction has actually drifted before.
    """
    forbidden = [
        "one simulated minute per tick",
        "1,440 ticks",
        "1440 ticks",
        "minute-by-minute clock",
        "one tick is one simulated minute",
        "one minute per tick",
    ]
    for path, text in _all_docs().items():
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"{path.name} states: {phrase}"

    for name in (
        "README.md",
        "docs/quickstart.md",
        "docs/architecture.md",
        "docs/reproducibility.md",
        "docs/methodology/odd-protocol.md",
    ):
        text = _read(name).lower()
        assert "15" in text and "96" in text, f"{name} must state the 15-minute tick scale"
        assert "simulated minute" in text, f"{name} must distinguish ticks from minutes"
