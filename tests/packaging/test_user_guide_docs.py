"""The user guide for the guided app: present, linked, and quoting only shipped copy.

``docs/user-guide.md`` is what a new user follows to install, start and use the app, so
every interface label it prints in bold must be a label the app actually ships in the
English catalog, its quick start must be the real command, and it must never contain
anything that looks like a real API key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
GUIDE = ROOT / "docs" / "user-guide.md"
ENGLISH_CATALOG = ROOT / "src" / "adlife" / "i18n" / "catalogs" / "en.json"
README_LINK = "[docs/user-guide.md](docs/user-guide.md)"
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_KEY_LIKE = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b[A-Za-z0-9]{32,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*[\"'][^\"'\s$]{6,}[\"']"),
    re.compile(r"\$env:[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\"']"),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
)
# Sentences whose figures test_documentation.py derives from the runtime; the guide leaves
# them to the reference documents rather than restating them.
_RUNTIME_DERIVED = (
    re.compile(r"funded requests? reserves?"),
    re.compile(r"noticed responses per agent"),
)
# The guide's sections, in the order a new user needs them.
_SECTIONS = (
    "## Install",
    "## Start the app",
    "## The start page",
    "## Connect an AI",
    "## Set up a simulation",
    "## Watch it run",
    "## The story",
    "## The report",
    "## Troubleshooting",
    "## What the results mean",
)


def _guide() -> str:
    assert GUIDE.is_file(), "docs/user-guide.md is missing"
    return GUIDE.read_text(encoding="utf-8")


def _english_texts() -> set[str]:
    envelope = json.loads(ENGLISH_CATALOG.read_text(encoding="utf-8"))
    texts: set[str] = set()
    for value in envelope["messages"].values():
        if isinstance(value, str):
            texts.add(value)
        else:
            texts.update(value.values())
    return texts


def test_user_guide_is_linked_from_the_readme_introduction() -> None:
    _guide()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    introduction = readme.split("\n---\n", maxsplit=1)[0]
    assert README_LINK in introduction


def test_every_bold_label_in_the_guide_is_shipped_english_copy() -> None:
    shipped = _english_texts()
    quoted = _BOLD.findall(_guide())
    assert quoted, "the guide quotes no interface labels"
    missing = sorted({label for label in quoted if label not in shipped})
    assert not missing, f"the guide quotes labels the catalog does not ship: {missing}"


def test_user_guide_starts_the_app_with_the_real_command() -> None:
    guide = _guide()
    assert "uv sync" in guide
    assert "uv run adlife app" in guide
    assert "http://127.0.0.1:8765/guide" in guide
    assert "adlife-workspace" in guide
    for option in ("--port", "--workspace", "--no-open", "--provider-origin", "--provider-key-env"):
        assert option in guide, option


def test_user_guide_follows_a_new_users_order() -> None:
    guide = _guide()
    positions = [guide.index(f"\n{heading}\n") for heading in _SECTIONS]
    assert positions == sorted(positions), "the guide's sections are out of order"


def test_user_guide_explains_every_ai_source_and_never_names_a_default_service() -> None:
    guide = _guide()
    for label in ("Online AI", "AI on this computer", "Practice answers", "No AI"):
        assert f"**{label}**" in guide, label
    for address in ("127.0.0.1:11434/v1", "127.0.0.1:1234/v1", "openrouter.ai/api/v1"):
        assert address in guide, address
    lowered = guide.lower()
    assert "in memory only" in lowered and "credential store" in lowered
    assert "never written to a file" in lowered


def test_user_guide_contains_no_key_like_strings() -> None:
    guide = _guide()
    for pattern in _KEY_LIKE:
        assert pattern.search(guide) is None, pattern.pattern


def test_user_guide_leaves_runtime_derived_sentences_to_the_reference_documents() -> None:
    squashed = re.sub(r"\s+", " ", _guide()).strip().lower()
    for pattern in _RUNTIME_DERIVED:
        assert pattern.search(squashed) is None, pattern.pattern
