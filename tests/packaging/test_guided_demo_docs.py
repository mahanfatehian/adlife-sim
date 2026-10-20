"""The presenter guide for the guided view: present, linked, and quoting only shipped copy.

``docs/guided-demo.md`` is read aloud on stage, so every interface label it prints in bold
must be a label the guide actually ships in the English catalog. It also carries the exact
PowerShell start command for the online AI, which must never contain anything that looks
like a real API key. The cross-document sweeps in ``test_documentation.py`` scan this file
too; the guide keeps out of their runtime-derived sentences instead of restating them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
GUIDE = ROOT / "docs" / "guided-demo.md"
ENGLISH_CATALOG = ROOT / "src" / "adlife" / "i18n" / "catalogs" / "en.json"
README_LINK = "[docs/guided-demo.md](docs/guided-demo.md)"
START_COMMAND = (
    "--provider-origin gapgpt=https://api.gapgpt.app/v1 --provider-key-env gapgpt=GAPGPT_API_KEY"
)
# The six rows of the ten-minute script, in stage order (en dashes, as the table prints them).
SCRIPT_TIMES = (
    "0:00\N{EN DASH}0:45",
    "0:45\N{EN DASH}1:30",
    "1:30\N{EN DASH}2:15",
    "2:15\N{EN DASH}6:30",
    "6:30\N{EN DASH}8:30",
    "8:30\N{EN DASH}10:00",
)
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_POWERSHELL_BLOCK = re.compile(r"```powershell\n(.*?)```", flags=re.DOTALL)
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


def _guide() -> str:
    assert GUIDE.is_file(), "docs/guided-demo.md is missing"
    return GUIDE.read_text(encoding="utf-8")


def _english_texts() -> set[str]:
    assert ENGLISH_CATALOG.is_file(), "the English catalog is missing"
    envelope = json.loads(ENGLISH_CATALOG.read_text(encoding="utf-8"))
    texts: set[str] = set()
    for value in envelope["messages"].values():
        if isinstance(value, str):
            texts.add(value)
        else:
            texts.update(value.values())
    return texts


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def test_guided_demo_is_linked_from_the_readme_introduction() -> None:
    _guide()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    introduction = readme.split("\n---\n", maxsplit=1)[0]
    lines = [line for line in introduction.splitlines() if README_LINK in line]
    assert lines, "the README introduction does not link docs/guided-demo.md"
    assert all(line.startswith(">") for line in lines), "the link is not a blockquote line"


def test_every_bold_label_in_the_guide_is_shipped_english_copy() -> None:
    shipped = _english_texts()
    quoted = _BOLD.findall(_guide())
    assert quoted, "the guide quotes no interface labels"
    missing = sorted({label for label in quoted if label not in shipped})
    assert not missing, f"the guide quotes labels the catalog does not ship: {missing}"


def test_guided_demo_carries_the_presenter_start_command() -> None:
    guide = _guide()
    powershell = "\n".join(_POWERSHELL_BLOCK.findall(guide))
    for fragment in (
        'Read-Host "GapGPT API key" -AsSecureString',
        '$env:GAPGPT_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password',
        r"uv run adlife city-workbench --workspace .\demo-guided",
        START_COMMAND,
    ):
        assert fragment in powershell, f"the PowerShell steps omit: {fragment}"
    assert "/guide" in guide
    assert r".\demo-guided" in guide


def test_guided_demo_follows_the_presenter_script_structure() -> None:
    guide = _guide()
    headings = (
        "## Before the audience",
        "## Backups",
        "## The 10-minute script",
        "## If the online AI fails",
        "## Rehearsal checklist",
    )
    positions = [guide.index(heading) for heading in headings]
    assert positions == sorted(positions), "the guide's sections are out of stage order"
    script = guide.split("## The 10-minute script", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    times = [script.index(f"| {time} |") for time in SCRIPT_TIMES]
    assert times == sorted(times), "the ten-minute script rows are missing or out of order"


def test_guided_demo_contains_no_key_like_strings() -> None:
    guide = _guide()
    for pattern in _KEY_LIKE:
        match = pattern.search(guide)
        assert match is None, f"key-like text in the guide: {match.group(0) if match else ''}"


def test_guided_demo_leaves_runtime_derived_sentences_to_the_reference_documents() -> None:
    squashed = _squash(_guide())
    for pattern in _RUNTIME_DERIVED:
        assert pattern.search(squashed) is None, pattern.pattern
