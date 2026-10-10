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
from pathlib import Path

import yaml

from adlife import __version__

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
        "D3/D4 progress",
        "response ledger",
        "D remains open",
        "authentication",
        "jobs",
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
