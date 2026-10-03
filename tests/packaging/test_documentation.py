"""The documentation contract: what the repository must say about itself.

Documentation drifts silently, so this suite pins the claims Task 17 makes on the
project's behalf: the disclosures a reader must meet before any number, the
machine-editable install block the release process rewrites, the governance files a
university artifact and a public repository both require, and a sweep that no
unsupported accuracy or adoption claim has crept in.
"""

from __future__ import annotations

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
