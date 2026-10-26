"""The packaged guide catalogs: format, parity, refusals, coverage and key usage (spec 5.1).

Rules checked here:
1. en, de and fa have identical keys, the same kind (string or plural) per key and the
   same placeholders; a plural ``one`` form may use only placeholders of ``other``.
2-5. Key pattern, placeholder syntax, refused characters and Persian letters.
6. Every closed vocabulary the guide shows has a message (enumerations via get_args).
7. Every key the guide JS, the templates and the story builder use exists.
8. Every catalog key is used literally somewhere or belongs to a dynamic family.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Iterable
from importlib import import_module
from importlib.resources import files
from pathlib import Path
from typing import Any, get_args

import pytest

import adlife
from adlife.i18n import (
    LANGUAGES,
    Catalog,
    CatalogError,
    PluralMessage,
    catalog_bytes,
    catalog_parity_problems,
    load_catalog,
)
from adlife.i18n.catalog import (
    MAX_CATALOG_BYTES,
    PLACEHOLDER_PATTERN,
    parse_catalog_messages,
    placeholders,
)

PACKAGE = Path(adlife.__file__).resolve().parent
CATALOG_DIRECTORY = PACKAGE / "i18n" / "catalogs"
GUIDE_STATIC = PACKAGE / "city" / "static" / "guide"
GUIDE_TEMPLATES = PACKAGE / "city" / "templates" / "guide"
REPORT_TEMPLATE = PACKAGE / "reporting" / "templates" / "run-report.html.j2"
GUIDE_SCRIPTS = (
    "guide-core.js",
    "guide-i18n.js",
    "guide-connection.js",
    "guide-start.js",
    "guide-setup.js",
    "guide-progress.js",
    "guide-story.js",
    "guide-map.js",
)
GUIDE_TEMPLATE_NAMES = (
    "base.html.j2",
    "error.html.j2",
    "start.html.j2",
    "new.html.j2",
    "job.html.j2",
    "story.html.j2",
)
# Python modules that name catalog keys as plain string literals (titles, errors, refs).
KEY_USING_MODULES = (
    PACKAGE / "city" / "guide_web.py",
    PACKAGE / "city" / "run_story.py",
    PACKAGE / "reporting" / "run_report.py",
    PACKAGE / "reporting" / "run_story_model.py",
    PACKAGE / "i18n" / "catalog.py",
)
DYNAMIC_FAMILIES = (
    "affect.",
    "relevance.",
    "credibility.",
    "salience.",
    "flag.",
    "status.",
    "mode.",
    "activity.",
    "channel.",
    "compare.",
    "trait.",
    "interest.",
    "persona.",
    "creative.",
    "city.",
    "reason.",
    "hint.",
    "failure.",
    "lang.",
    "story.where.",
    "story.ai.",
    "story.effect.",
    "story.headline.",
    "story.person.",
    "report.ai.",
    "progress.step.",
)
SPEC_STATUSES = (
    "answered",
    "reused",
    "practice",
    "rules",
    "unsure",
    "warning",
    "failed",
    "over_limit",
    "not_chosen",
    "not_noticed",
)
SPEC_COMPARES = ("lower", "slightly_lower", "about_same", "slightly_higher", "higher")
TRAITS = (
    "advertising_skepticism",
    "price_sensitivity",
    "novelty_seeking",
    "mobile_recall_encoding",
    "roadside_recall_encoding",
    "impulsivity",
)
# The story builder emits these only for people who were shown the ad at least once.
COUNT_IS_NEVER_ZERO = frozenset({"story.person.never_noticed", "story.person.noticed"})
PERSONA_KEYS = {
    "persona-001": "lumo",
    "persona-002": "veyra",
    "persona-003": "orilo",
    "persona-004": "nuvin",
    "persona-005": "pimo",
    "persona-006": "zarel",
    "persona-007": "ketra",
    "persona-008": "tavi",
    "persona-009": "selun",
    "persona-010": "halvi",
}


def _ordered_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        assert key not in result, f"duplicate key {key}"
        result[key] = value
    return result


def _raw_messages(language: str) -> dict[str, Any]:
    document = json.loads(catalog_bytes(language).decode("utf-8"), object_pairs_hook=_ordered_pairs)
    messages: dict[str, Any] = document["messages"]
    return messages


def _is_untranslated(language: str) -> bool:
    return language != "en" and _raw_messages(language) == _raw_messages("en")


# Packaged files ----------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_packaged_catalog_files_are_strict_sorted_utf8(language: str) -> None:
    path = CATALOG_DIRECTORY / f"{language}.json"
    data = path.read_bytes()
    assert catalog_bytes(language) == data
    assert files("adlife.i18n").joinpath("catalogs", f"{language}.json").read_bytes() == data
    assert not data.startswith(b"\xef\xbb\xbf")
    assert len(data) <= MAX_CATALOG_BYTES
    document = json.loads(data.decode("utf-8"), object_pairs_hook=_ordered_pairs)
    assert set(document) == {"schema_version", "language", "direction", "messages"}
    assert document["schema_version"] == 1 and type(document["schema_version"]) is int
    assert document["language"] == language
    assert document["direction"] == ("rtl" if language == "fa" else "ltr")
    keys = list(document["messages"])
    assert keys == sorted(keys), "catalog message keys must be sorted"
    for value in document["messages"].values():
        if isinstance(value, dict):
            assert list(value) == ["one", "other"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_packaged_catalogs_load_and_are_cached(language: str) -> None:
    catalog = load_catalog(language)
    assert catalog is load_catalog(language)
    assert catalog.language == language
    assert catalog.direction == ("rtl" if language == "fa" else "ltr")
    assert catalog.message_keys() == frozenset(_raw_messages(language))


def test_english_catalog_is_the_normative_appendix_a_key_set() -> None:
    english = load_catalog("en")
    # Appendix A's 501 keys plus the finished and stopped progress titles, the three story map
    # place labels (home, work, free time), and the rehearsal review's additions: six
    # direction-specific liking effects in place of "higher" and "lower", two "new values"
    # arithmetic lines, the opening counter, the practice-run note, the over-limit note and the
    # once-each label summary. The content extension adds six personas (name and tagline),
    # four creatives (category and message), two towns and the four town-picker messages.
    # Connecting any OpenAI-compatible service adds the address, remember-key and status
    # messages, and the removed technical view and GapGPT-only setup take theirs away.
    # Covering the whole simulated period adds twelve: the spread not-chosen clause, the
    # day counter, the closing step (counter, title, lead), the day-by-day table (title,
    # column, row) and the setup's every-noticed-ad option, its note, the spread wording
    # and the upper-bound token estimate. The AI answering as the person (prompt v3) adds
    # six: the headline and moment line, and in-character versions of the how-it-works
    # box, the reaction glossary line and the names and feelings limits. Inner thoughts add
    # six: the setup's title, help and cost lines, and the story's title, other-languages
    # toggle and note.
    assert len(english.message_keys()) == 571
    assert english.text("progress.title.done") == "Simulation finished"
    assert english.text("progress.answered", resolved=3, total=10) == "3 of 10 answered"
    assert english.text("common.app") == "AdLife Lab"
    assert english.text("setup.run_with_test") == "Test the AI and run"
    plural_keys = {
        key for key in english.message_keys() if isinstance(english.message(key), PluralMessage)
    }
    assert {"common.people", "common.days", "story.headline.shown", "report.person.more"} <= (
        plural_keys
    )
    assert len(plural_keys) == 25


@pytest.mark.parametrize("language", ["de", "fa"])
def test_translations_match_english_keys_kinds_and_placeholders(language: str) -> None:
    assert catalog_parity_problems(load_catalog("en"), load_catalog(language)) == []


def test_language_names_are_identical_in_every_catalog() -> None:
    english = load_catalog("en")
    for language in LANGUAGES:
        catalog = load_catalog(language)
        for name in LANGUAGES:
            assert catalog.message(f"lang.{name}") == english.message(f"lang.{name}")
    assert english.message("lang.en") == "English"
    assert english.message("lang.de") == "Deutsch"


def test_persian_one_forms_show_the_count_because_they_also_cover_zero() -> None:
    if _is_untranslated("fa"):
        pytest.skip("fa.json is still an untranslated copy of en.json")
    english = load_catalog("en")
    persian = load_catalog("fa")
    missing = []
    for key in sorted(english.message_keys() - COUNT_IS_NEVER_ZERO):
        message = english.message(key)
        if isinstance(message, PluralMessage) and "count" in placeholders(message.other):
            persian_message = persian.message(key)
            assert isinstance(persian_message, PluralMessage)
            if "count" not in placeholders(persian_message.one):
                missing.append(key)
    assert missing == [], "fa one forms are also used for 0, so they must show {count}"


def test_translated_number_formats_follow_the_spec() -> None:
    expected = {"de": "{value}\u00a0%", "fa": "{value}\u066a"}
    for language, percent in expected.items():
        if _is_untranslated(language):
            continue
        assert load_catalog(language).message("common.percent") == percent
    assert load_catalog("en").message("common.percent") == "{value}%"


def test_persian_messages_never_type_digits() -> None:
    # Spec 5.9: Persian numbers come only through placeholders, so they are always
    # formatted with Persian digits and separators by the shared algorithm.
    if _is_untranslated("fa"):
        pytest.skip("fa.json is still an untranslated copy of en.json")
    digits = re.compile(r"[0-9\u0660-\u0669\u06f0-\u06f9]")
    typed = []
    for key, value in _raw_messages("fa").items():
        forms = [value] if isinstance(value, str) else [value["one"], value["other"]]
        if any(digits.search(PLACEHOLDER_PATTERN.sub("", form)) for form in forms):
            typed.append(key)
    assert typed == []


# Refused catalogs ----------------------------------------------------------------------


def _document(language: str, messages: dict[str, object], /, **envelope: object) -> bytes:
    document: dict[str, object] = {
        "schema_version": 1,
        "language": language,
        "direction": "rtl" if language == "fa" else "ltr",
        "messages": messages,
    }
    document.update(envelope)
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema_version":1,"language":"en","direction":"ltr","messages":{"a.b":"x","a.b":"y"}}',
        b'{"schema_version":1,"schema_version":1,"language":"en","direction":"ltr","messages":{}}',
        b"\xef\xbb\xbf" + _document("en", {"a.b": "x"}),
        _document("en", {"a.b": "x"}).replace(b'"x"', b"NaN"),
        b'{"schema_version":1,"language":"en","direction":"ltr","messages":{"a.b":Infinity}}',
        b"\xff\xfe",
        b"[]",
        _document("en", {"a.b": "x"}, schema_version=True),
        _document("en", {"a.b": "x"}, schema_version=1.0),
        _document("en", {"a.b": "x"}, schema_version=2),
        _document("en", {"a.b": "x"}, language="de"),
        _document("en", {"a.b": "x"}, direction="rtl"),
        _document("en", {"a.b": "x"}, extra=True),
        _document("en", []),  # type: ignore[arg-type]
        b" " * (MAX_CATALOG_BYTES + 1),
    ],
    ids=[
        "duplicate-message-key",
        "duplicate-envelope-key",
        "byte-order-mark",
        "nan",
        "infinity",
        "not-utf8",
        "not-an-object",
        "boolean-schema-version",
        "float-schema-version",
        "unknown-schema-version",
        "wrong-language",
        "wrong-direction",
        "extra-envelope-member",
        "messages-not-an-object",
        "too-large",
    ],
)
def test_refused_catalog_documents(data: bytes) -> None:
    with pytest.raises(CatalogError):
        parse_catalog_messages(data, "en")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("a.b", "\u202eoverride"),
        ("a.b", "isolate \u2067x\u2069"),
        ("a.b", "mark \u200f"),
        ("a.b", "mark \u200e"),
        ("a.b", "arabic letter mark \u061c"),
        ("a.b", "line\nbreak"),
        ("a.b", "tab\there"),
        ("a.b", "c1 \u0085 control"),
        ("a.b", "line separator \u2028"),
        ("a.b", "paragraph separator \u2029"),
        ("a.b", "markup <b>bold</b>"),
        ("a.b", "arrow > here"),
        ("a.b", "see https://example.invalid"),
        ("a.b", "see HTTP://example.invalid"),
        ("a.b", ""),
        ("a.b", "x" * 601),
        ("a.b", "{Name}"),
        ("a.b", "{_name}"),
        ("a.b", "{name"),
        ("a.b", "name}"),
        ("a.b", "{{name}}"),
        ("a.b", "{na-me}"),
        ("a.b", 3),
        ("a.b", None),
        ("a.b", ["list"]),
        ("a.b", {"one": "x"}),
        ("a.b", {"one": "x", "other": "y", "few": "z"}),
        ("a.b", {"one": "x", "many": "y"}),
        ("a.b", {"one": "{count} x", "other": "y"}),
        ("a.b", {"one": 1, "other": "y"}),
        ("A.b", "x"),
        ("a", "x"),
        ("a.", "x"),
        ("a..b", "x"),
        ("a.B", "x"),
        ("a b.c", "x"),
        ("1a.b", "x"),
        ("a." + "b" * 95, "x"),
    ],
)
def test_refused_messages(key: str, value: object) -> None:
    with pytest.raises(CatalogError):
        Catalog.from_mapping("en", {key: value})


def test_persian_catalogs_refuse_arabic_yeh_and_kaf() -> None:
    with pytest.raises(CatalogError):
        Catalog.from_mapping("fa", {"a.b": "\u0641\u0627\u0631\u0633\u064a"})
    with pytest.raises(CatalogError):
        Catalog.from_mapping("fa", {"a.b": "\u0643\u062a\u0627\u0628"})
    persian = Catalog.from_mapping("fa", {"a.b": "\u0641\u0627\u0631\u0633\u06cc \u06a9"})
    assert persian.has("a.b")
    # The same letters are not refused in other languages' catalogs.
    assert Catalog.from_mapping("en", {"a.b": "\u064a"}).has("a.b")


def test_zero_width_joiners_and_ordinary_symbols_are_allowed() -> None:
    catalog = Catalog.from_mapping(
        "fa",
        {
            "a.zwnj": "\u0645\u06cc\u200c\u0634\u0648\u062f",
            "a.zwj": "x\u200dy",
            "a.symbols": "{a} \u2192 {b} \u00b7 \u00d7 \u00b1 \u2026 \u00ab\u00bb \u201c\u201d",
            "a.dash": "{from}\u2013{until}",
            "a.nbsp": "{value}\u00a0%",
        },
    )
    assert len(catalog.message_keys()) == 5


def test_unknown_languages_are_refused() -> None:
    with pytest.raises(CatalogError):
        Catalog.from_mapping("ar", {"a.b": "x"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        load_catalog("EN")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        catalog_bytes("../en")  # type: ignore[arg-type]


def test_parity_reports_every_structural_difference() -> None:
    english = Catalog.from_mapping(
        "en",
        {
            "a.same": "{x} y",
            "a.kind": "text",
            "a.placeholder": "{x}",
            "a.plural": {"one": "once", "other": "{count} times"},
            "a.missing": "m",
        },
    )
    other = Catalog.from_mapping(
        "de",
        {
            "a.same": "y {x}",
            "a.kind": {"one": "a", "other": "b"},
            "a.placeholder": "{y}",
            "a.plural": {"one": "einmal", "other": "oft"},
            "a.extra": "e",
        },
    )
    assert catalog_parity_problems(english, other) == [
        "missing key a.missing",
        "extra key a.extra",
        "key a.kind differs in kind",
        "key a.placeholder differs in placeholders",
        "key a.plural other form differs in placeholders",
    ]
    # A one form may show {count} where English says "once".
    persian = Catalog.from_mapping(
        "fa",
        {"a.plural": {"one": "{count} \u0628\u0627\u0631", "other": "{count} \u0628\u0627\u0631"}},
    )
    reference = Catalog.from_mapping("en", {"a.plural": {"one": "once", "other": "{count} times"}})
    assert catalog_parity_problems(reference, persian) == []


# Enumeration coverage --------------------------------------------------------------


def _literal_values(annotation: object) -> tuple[str, ...]:
    values: list[str] = []
    for argument in get_args(annotation):
        if isinstance(argument, str):
            values.append(argument)
        elif argument is not type(None):
            values.extend(_literal_values(argument))
    return tuple(values)


def _story_literal(candidates: Iterable[str], fallback: tuple[str, ...]) -> tuple[str, ...]:
    try:
        module = import_module("adlife.reporting.run_story_model")
    except ImportError:
        return fallback
    for name in candidates:
        if hasattr(module, name):
            values = _literal_values(getattr(module, name))
            assert set(values) == set(fallback), f"{name} differs from the spec"
            return values
    return fallback


def _require(prefix: str, values: Iterable[str], suffix: str = "") -> None:
    english = load_catalog("en")
    missing = [f"{prefix}{value}{suffix}" for value in values]
    missing = [key for key in missing if not english.has(key)]
    assert missing == []


def test_cognition_vocabularies_have_messages() -> None:
    from adlife.city.provider_compatibility import CompatibilityReason
    from adlife.core.domain.city_cognition import CityCognitionSafetyFlag
    from adlife.core.domain.city_cognition_v2 import (
        CityCognitionAffectV2,
        CityCognitionBaselineProposalV2,
        CityCognitionCredibilityV2,
        CityCognitionFallbackReasonV2,
        CityCognitionModeV2,
        CityCognitionRelevanceV2,
        CityCognitionRuntimeFallbackReasonV2,
        CityCognitionSalienceV2,
    )
    from adlife.core.domain.spatial_campaign import SpatialActivity

    _require("affect.", get_args(CityCognitionAffectV2))
    _require("relevance.", get_args(CityCognitionRelevanceV2))
    _require("credibility.", get_args(CityCognitionCredibilityV2))
    _require("salience.", get_args(CityCognitionSalienceV2))
    _require("flag.", get_args(CityCognitionSafetyFlag))
    _require("mode.", get_args(CityCognitionModeV2))
    _require("reason.", get_args(CityCognitionFallbackReasonV2))
    _require("reason.", _literal_values(CompatibilityReason))
    _require("hint.", get_args(CityCognitionRuntimeFallbackReasonV2))
    _require("hint.", _literal_values(CompatibilityReason))
    _require("activity.", get_args(SpatialActivity))
    _require("story.where.mobile-feed.", get_args(SpatialActivity))
    channels = _literal_values(CityCognitionBaselineProposalV2.model_fields["channel"].annotation)
    assert set(channels) == {"mobile-feed", "roadside-billboard"}
    _require("channel.", channels)
    _require("story.ai.", ("answered", "reused", "practice", "unsure", "warning", "failed"))
    _require("story.ai.", ("over_limit", "not_chosen", "rules"))


def test_job_failures_story_statuses_and_comparisons_have_messages() -> None:
    from adlife.city.workbench_jobs import WorkbenchJobFailure

    _require("failure.", _literal_values(WorkbenchJobFailure.model_fields["code"].annotation))
    statuses = _story_literal(("Status", "StoryStatus", "StoryMomentStatus"), SPEC_STATUSES)
    compares = _story_literal(("Compare", "StoryCompare", "CompareBucket"), SPEC_COMPARES)
    _require("status.", statuses)
    _require("compare.", compares)
    _require("story.headline.compare.liking.", compares)
    _require("story.headline.compare.memory.", compares)
    _require(
        "story.effect.liking.",
        (
            "rose_more",
            "fell_less",
            "rose_not_fell",
            "rose_less",
            "fell_more",
            "fell_not_rose",
            "same",
        ),
    )
    _require("story.effect.memory.", ("higher", "lower", "same"))
    _require("progress.step.", ("simulate", "ask", "ask.mock", "ask.rules", "save", "done"))
    _require("progress.state.", ("waiting", "active", "done", "stopped"))


def test_packaged_content_vocabularies_have_messages() -> None:
    from adlife.city.catalog import load_city_catalog
    from adlife.city.workbench_creatives import load_creative_template_catalog
    from adlife.city.workbench_personas import load_persona_template_catalog

    personas = load_persona_template_catalog().templates
    keys = {persona.persona_id: persona.display_name.split()[-1].lower() for persona in personas}
    assert keys == PERSONA_KEYS
    _require("persona.", PERSONA_KEYS.values(), ".name")
    _require("persona.", PERSONA_KEYS.values(), ".tagline")
    interests = {interest for persona in personas for interest in persona.interests}
    interests |= {"commuting", "technology", "food", "leisure"}
    _require("interest.", interests)
    assert (
        len({key for key in load_catalog("en").message_keys() if key.startswith("interest.")}) == 9
    )
    for trait in TRAITS:
        _require(f"trait.{trait}.", ("high", "low"))
    templates = [template.template_id for template in load_creative_template_catalog().templates]
    _require("creative.", templates, ".category")
    _require("creative.", templates, ".message")
    _require("city.", [entry.city_id for entry in load_city_catalog().entries])


# Key usage ---------------------------------------------------------------------------

# A literal key in t("..."), tn('...') or t(`...`) (a template literal without ${}).
_JS_CALL = re.compile(r"\b(?:t|tn)\(\s*([\"'`])([^\"'`\\\n$]+)\1")
_TEMPLATE_CALL = re.compile(r"\b(?:tr|tr_text)\(\s*([\"'])([^\"'\\\n]+)\1")
_QUOTED = re.compile(r"([\"'`])([a-z][a-z0-9_]*(?:\.[a-z0-9][a-z0-9_-]*)+)\1")
_KEY_SHAPE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9][a-z0-9_-]*)+")
# The namespaces of spec 5.2; a key-shaped literal in one of them must be a catalog key.
NAMESPACES = frozenset(
    {
        "lang",
        "common",
        "start",
        "connection",
        "reason",
        "hint",
        "setup",
        "progress",
        "failure",
        "story",
        "label",
        "limits",
        "report",
        "error",
        "affect",
        "relevance",
        "credibility",
        "salience",
        "flag",
        "status",
        "mode",
        "activity",
        "channel",
        "compare",
        "trait",
        "interest",
        "persona",
        "creative",
        "city",
    }
)
# File names such as "story.html.j2" look like keys but name packaged resources.
_RESOURCE_SUFFIXES = (".j2", ".js", ".css", ".json", ".svg", ".html")
# Appendix A defines these two keys, but no screen in the spec shows them yet. They stay
# in all three catalogs (the key set is normative) and are the only unused keys allowed;
# a screen may start using them at any time.
APPENDIX_A_UNASSIGNED = frozenset({"common.show_more", "common.times"})


def _existing(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if path.is_file()]


def _guide_sources() -> tuple[list[Path], list[Path]]:
    scripts = _existing(GUIDE_STATIC / name for name in GUIDE_SCRIPTS)
    templates = _existing(GUIDE_TEMPLATES / name for name in GUIDE_TEMPLATE_NAMES)
    templates += _existing([REPORT_TEMPLATE])
    return scripts, templates


def _story_message_keys() -> frozenset[str] | None:
    try:
        module = import_module("adlife.city.run_story")
    except ImportError:
        return None
    keys = getattr(module, "STORY_MESSAGE_KEYS", None)
    return None if keys is None else frozenset(keys)


def _python_string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _called_keys() -> dict[str, set[str]]:
    scripts, templates = _guide_sources()
    used: dict[str, set[str]] = {}
    for path in scripts:
        for match in _JS_CALL.finditer(path.read_text(encoding="utf-8")):
            used.setdefault(match.group(2), set()).add(path.name)
    for path in templates:
        for match in _TEMPLATE_CALL.finditer(path.read_text(encoding="utf-8")):
            used.setdefault(match.group(2), set()).add(path.name)
    story_keys = _story_message_keys()
    for key in story_keys or ():
        used.setdefault(key, set()).add("run_story.STORY_MESSAGE_KEYS")
    return used


def test_every_key_the_guide_uses_exists() -> None:
    english = load_catalog("en")
    used = _called_keys()
    assert "common.skip" in used and "common.footer" in used, "the base template is scanned"
    unknown = {key: sorted(sources) for key, sources in used.items() if not english.has(key)}
    assert unknown == {}


def _is_namespaced_key(value: str) -> bool:
    return (
        _KEY_SHAPE.fullmatch(value) is not None
        and value.split(".", 1)[0] in NAMESPACES
        and not value.endswith(_RESOURCE_SUFFIXES)
    )


def _namespaced_literals(text: str) -> set[str]:
    return {
        match.group(2) for match in _QUOTED.finditer(text) if _is_namespaced_key(match.group(2))
    }


def test_every_namespaced_literal_in_guide_sources_is_a_catalog_key() -> None:
    # Keys also travel inside data: {key: "common.people", count: n} references, label
    # tables and Python title or error keys. A misspelled one would only show up as
    # a bracketed key at run time, so every key-shaped literal in a catalog namespace
    # must name a real message.
    english = load_catalog("en")
    scripts, templates = _guide_sources()
    unknown: dict[str, list[str]] = {}
    for path in [*scripts, *templates]:
        for key in _namespaced_literals(path.read_text(encoding="utf-8")):
            if not english.has(key):
                unknown.setdefault(key, []).append(path.name)
    for path in _existing(KEY_USING_MODULES):
        for key in _python_string_literals(path):
            if _is_namespaced_key(key) and not english.has(key):
                unknown.setdefault(key, []).append(path.name)
    assert unknown == {}, "use an existing key, or ask for the new key to be added to en.json"


def test_every_catalog_key_uses_a_spec_namespace() -> None:
    namespaces = {key.split(".", 1)[0] for key in load_catalog("en").message_keys()}
    assert namespaces <= NAMESPACES, sorted(namespaces - NAMESPACES)


def test_every_catalog_key_is_used_or_belongs_to_a_dynamic_family() -> None:
    scripts, templates = _guide_sources()
    story_keys = _story_message_keys()
    missing_sources = [name for name in GUIDE_SCRIPTS if not (GUIDE_STATIC / name).is_file()] + [
        name for name in GUIDE_TEMPLATE_NAMES if not (GUIDE_TEMPLATES / name).is_file()
    ]
    if not REPORT_TEMPLATE.is_file():
        missing_sources.append(REPORT_TEMPLATE.name)
    if story_keys is None:
        missing_sources.append("run_story.STORY_MESSAGE_KEYS")
    if missing_sources:
        pytest.skip("guide sources are not all present yet: " + ", ".join(missing_sources))
    used: set[str] = set(story_keys or ())
    for path in [*scripts, *templates]:
        used.update(match.group(2) for match in _QUOTED.finditer(path.read_text(encoding="utf-8")))
    for path in _existing(KEY_USING_MODULES):
        used.update(_python_string_literals(path))
    unused = sorted(
        key
        for key in load_catalog("en").message_keys() - APPENDIX_A_UNASSIGNED
        if key not in used and not key.startswith(DYNAMIC_FAMILIES)
    )
    assert unused == [], "use these keys literally, or remove them from all three catalogs"


_I18N_ALLOWED_IMPORTS = ("adlife.i18n", "adlife.core.domain.person", "pydantic", "markupsafe")


def test_the_i18n_package_imports_only_stdlib_pydantic_and_markupsafe() -> None:
    # Spec 9.2: adlife.i18n depends on the standard library, pydantic and markupsafe
    # (plus the core DomainModel base) and never on web, storage or adapter layers.
    imported: dict[str, set[str]] = {}
    for path in sorted((PACKAGE / "i18n").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path.name} uses a relative import"
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".", 1)[0]
                allowed = top in sys.stdlib_module_names or name.startswith(_I18N_ALLOWED_IMPORTS)
                if not allowed:
                    imported.setdefault(path.name, set()).add(name)
    assert imported == {}


def test_story_message_keys_exist_and_render_their_placeholders() -> None:
    story_keys = _story_message_keys()
    if story_keys is None:
        pytest.skip("adlife.city.run_story is not present yet")
    english = load_catalog("en")
    assert story_keys, "STORY_MESSAGE_KEYS must not be empty"
    assert sorted(key for key in story_keys if not english.has(key)) == []
    for key in story_keys:
        for name in placeholders(str(english.message(key))):
            assert PLACEHOLDER_PATTERN.fullmatch("{" + name + "}")
