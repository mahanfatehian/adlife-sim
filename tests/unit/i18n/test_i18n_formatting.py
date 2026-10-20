"""The shared formatting algorithm: Python against tests/unit/i18n/format_cases.json.

The browser test runs the same fixture through guide-i18n.js, so both implementations
are pinned to one set of expected strings. Expected strings live only in the JSON file;
this module contains no Persian, minus-sign or no-break-space literals.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from adlife.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    Catalog,
    CatalogError,
    load_catalog,
    parse_language,
    plural_category,
    text_direction,
)
from adlife.i18n.formatting import (
    MAX_SAFE_INTEGER,
    format_decimal,
    format_integer,
    format_time_of_day,
    localize_digits,
)

FIXTURE = Path(__file__).with_name("format_cases.json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        assert key not in result, f"duplicate fixture key {key}"
        result[key] = value
    return result


def load_format_fixture() -> dict[str, Any]:
    """Strictly load the shared fixture (also used by the browser parity test)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    assert data["schema_version"] == 1
    return data


FORMAT_FIXTURE = load_format_fixture()
CASES: list[dict[str, Any]] = FORMAT_FIXTURE["cases"]


def fixture_catalogs() -> dict[str, Catalog]:
    return {
        language: Catalog.from_mapping(language, messages)
        for language, messages in FORMAT_FIXTURE["messages"].items()
    }


def run_case(catalog: Catalog, case: dict[str, Any]) -> str:
    """Apply one fixture case to a Python catalog, mirroring the JS harness."""
    call = case["call"]
    args = case["args"]
    if call == "int":
        return catalog.int(args[0])
    if call == "dec":
        return catalog.dec(args[0], args[1], case.get("signed", False))
    if call == "pct":
        return catalog.pct(*args)
    if call == "clock":
        return catalog.clock(*args)
    if call == "duration":
        return catalog.duration(*args)
    if call == "t":
        return catalog.text(args[0], **args[1])
    if call == "tn":
        return catalog.text(args[0], count=args[1], **(args[2] if len(args) > 2 else {}))
    raise AssertionError(f"unknown fixture call {call}")


def test_fixture_has_enough_unique_cases_for_every_language_and_call() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) >= 30
    assert len(set(ids)) == len(ids)
    assert {case["language"] for case in CASES} == set(LANGUAGES)
    assert {case["call"] for case in CASES} == {"int", "dec", "pct", "clock", "duration", "t", "tn"}
    assert set(FORMAT_FIXTURE["messages"]) == set(LANGUAGES)


def test_fixture_contains_the_cases_the_spec_requires() -> None:
    required = {
        ("en", "dec", (0.125, 2)): "0.13",
        ("en", "dec", (2.675, 2)): "2.67",
        ("en", "dec", (-0.001, 2)): "0.00",
        ("en", "int", (10240,)): "10,240",
        ("de", "int", (10240,)): "10.240",
        ("en", "clock", (437, 1)): "07:17",
        ("en", "duration", (59,)): "59 seconds",
        ("en", "duration", (61,)): "1 min 1 s",
    }
    present = {
        (case["language"], case["call"], tuple(case["args"])): case
        for case in CASES
        if case["call"] not in {"t", "tn"}
    }
    for signature, expected in required.items():
        assert present[signature]["expected"] == expected
    assert ("fa", "int", (10240,)) in present
    assert ("fa", "clock", (437, 1)) in present
    assert any(case.get("signed") and case["expected"] == "+0.04" for case in CASES)
    assert {case["language"] for case in CASES if case["call"] == "pct"} == set(LANGUAGES)
    assert any(case["call"] == "clock" and case["args"][1] == 2 for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_python_formatting_matches_the_shared_fixture(case: dict[str, Any]) -> None:
    catalogs = fixture_catalogs()
    assert run_case(catalogs[case["language"]], case) == case["expected"]


def test_fixture_english_messages_are_the_packaged_english_messages() -> None:
    packaged = load_catalog("en")
    for key, message in FORMAT_FIXTURE["messages"]["en"].items():
        if key.startswith("test."):
            continue
        expected = packaged.message(key)
        if isinstance(message, dict):
            assert (message["one"], message["other"]) == tuple(expected)
        else:
            assert message == expected


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["language"] == "en" and case["call"] not in {"t", "tn"}],
    ids=lambda case: case["id"],
)
def test_packaged_english_catalog_formats_like_the_fixture(case: dict[str, Any]) -> None:
    assert run_case(load_catalog("en"), case) == case["expected"]


@pytest.mark.parametrize("count", range(0, 32))
def test_plural_selection_follows_cldr_for_each_language(count: int) -> None:
    assert plural_category("en", count) == ("one" if count == 1 else "other")
    assert plural_category("de", count) == ("one" if count == 1 else "other")
    assert plural_category("fa", count) == ("one" if count in (0, 1) else "other")


def test_plural_selection_refuses_negative_and_non_integer_counts() -> None:
    with pytest.raises(ValueError):
        plural_category("en", -1)
    with pytest.raises(TypeError):
        plural_category("en", True)
    with pytest.raises(TypeError):
        plural_category("fa", 1.0)  # type: ignore[arg-type]


def test_language_parsing_defaults_to_english_and_refuses_everything_else() -> None:
    assert DEFAULT_LANGUAGE == "en"
    assert LANGUAGES == ("en", "de", "fa")
    assert parse_language(None) == "en"
    for language in LANGUAGES:
        assert parse_language(language) == language
    for invalid in ("", "FA", "En", "de-DE", "fa ", "xx", "english"):
        with pytest.raises(ValueError):
            parse_language(invalid)
    assert [text_direction(language) for language in LANGUAGES] == ["ltr", "ltr", "rtl"]
    with pytest.raises(ValueError):
        text_direction("ar")  # type: ignore[arg-type]


def test_persian_digits_are_the_extended_arabic_indic_digits() -> None:
    assert localize_digits("0123456789", "fa") == "".join(chr(0x06F0 + i) for i in range(10))
    assert localize_digits("0123456789", "de") == "0123456789"
    assert format_time_of_day(1440, "fa") == localize_digits("24:00", "fa")


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, 1e21, -1e21, 1e300],
)
def test_decimals_refuse_values_outside_the_shared_range(value: float) -> None:
    with pytest.raises(ValueError):
        format_decimal(value, 2, "en")


def test_decimals_refuse_bad_types_and_precisions() -> None:
    with pytest.raises(TypeError):
        format_decimal(True, 2, "en")
    with pytest.raises(TypeError):
        format_decimal("1.5", 2, "en")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        format_decimal(1.5, 2.0, "en")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        format_decimal(1.5, -1, "en")
    with pytest.raises(ValueError):
        format_decimal(1.5, 7, "en")
    with pytest.raises(TypeError):
        format_decimal(1.5, 2, "en", signed=1)  # type: ignore[arg-type]
    assert format_decimal(999_999_999_999_999_999_999 / 1e3, 0, "en").count(",") == 6


def test_integers_stay_inside_the_javascript_safe_range() -> None:
    assert format_integer(MAX_SAFE_INTEGER, "en") == "9,007,199,254,740,991"
    with pytest.raises(ValueError):
        format_integer(MAX_SAFE_INTEGER + 1, "en")
    with pytest.raises(TypeError):
        format_integer(True, "en")
    with pytest.raises(TypeError):
        format_integer(3.0, "en")  # type: ignore[arg-type]


def test_catalog_formatting_refuses_invalid_times_and_durations() -> None:
    catalog = fixture_catalogs()["en"]
    with pytest.raises(ValueError):
        catalog.clock(-1, 1)
    with pytest.raises(ValueError):
        catalog.clock(10, 0)
    with pytest.raises(TypeError):
        catalog.clock(10.5, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        catalog.duration(-1)
    with pytest.raises(TypeError):
        catalog.duration(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        catalog.pct("0.5")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        format_time_of_day(1441, "en")


def test_text_localizes_integers_and_refuses_other_parameter_types() -> None:
    catalog = fixture_catalogs()["fa"]
    english = fixture_catalogs()["en"]
    assert english.text("test.answered", resolved=3, total=10) == "3 of 10 answered"
    assert catalog.text("test.answered", resolved="3", total="10").startswith("3 ")
    with pytest.raises(TypeError):
        english.text("test.answered", resolved=True, total=10)
    with pytest.raises(TypeError):
        english.text("test.answered", resolved=0.5, total=10)  # type: ignore[arg-type]
    with pytest.raises(CatalogError):
        english.text("test.answered", resolved=3)
    with pytest.raises(CatalogError):
        english.text("test.plural")
    with pytest.raises(CatalogError):
        english.text("test.unknown")
    with pytest.raises(TypeError):
        english.text("test.plural", count="1")  # type: ignore[arg-type]
