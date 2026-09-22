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
