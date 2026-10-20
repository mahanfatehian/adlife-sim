"""Message references and template functions against tests/unit/i18n/ref_cases.json.

The browser parity test resolves the same fixture with guide-i18n.js; both must give the
expected plain text and the expected tree of text nodes and ``<bdi>`` elements.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from jinja2 import DictLoader, Environment, StrictUndefined
from markupsafe import Markup
from pydantic import ValidationError

from adlife.i18n import (
    Catalog,
    CatalogError,
    IntParam,
    ListParam,
    MessageRef,
    PersonLabel,
    RefContext,
    clock_param,
    id_param,
    int_param,
    list_param,
    load_catalog,
    message_ref,
    num_param,
    pct_param,
    person_param,
    template_functions,
    text_param,
)
from adlife.i18n.refs import MAX_REF_DEPTH, MAX_REF_PARAMS, ref_depth

FIXTURE = Path(__file__).with_name("ref_cases.json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        assert key not in result, f"duplicate fixture key {key}"
        result[key] = value
    return result


def load_ref_fixture() -> dict[str, Any]:
    """Strictly load the shared fixture (also used by the browser parity test)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    assert data["schema_version"] == 1
    return data


REF_FIXTURE = load_ref_fixture()
CASES: list[dict[str, Any]] = REF_FIXTURE["cases"]
ERRORS: list[dict[str, Any]] = REF_FIXTURE["errors"]


def fixture_catalogs() -> dict[str, Catalog]:
    return {
        language: Catalog.from_mapping(language, messages)
        for language, messages in REF_FIXTURE["messages"].items()
    }


def fixture_context(name: str) -> RefContext:
    value = REF_FIXTURE["contexts"][name]
    return RefContext(
        days=value["days"],
        people={
            person["agent_id"]: PersonLabel(
                person["template_key"], person["display_name"], person["number"]
            )
            for person in value["people"]
        },
    )


class _BdiTree(HTMLParser):
    """Parse renderer markup into the fixture's node tree; only <bdi dir> is allowed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[list[Any]] = [[]]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        assert tag == "bdi", tag
        assert len(attrs) == 1 and attrs[0][0] == "dir", attrs
        node: dict[str, Any] = {"bdi": attrs[0][1], "children": []}
        self.stack[-1].append(node)
        self.stack.append(node["children"])

    def handle_endtag(self, tag: str) -> None:
        assert tag == "bdi", tag
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        children = self.stack[-1]
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            children.append(data)


def markup_tree(markup: str) -> list[Any]:
    parser = _BdiTree()
    parser.feed(markup)
    parser.close()
    assert len(parser.stack) == 1, "unbalanced <bdi> markup"
    return parser.stack[0]


def test_fixture_covers_every_parameter_kind_and_both_directions() -> None:
    kinds: set[str] = set()

    def walk(ref: dict[str, Any]) -> None:
        for param in (ref.get("params") or {}).values():
            walk_param(param)

    def walk_param(param: dict[str, Any]) -> None:
        kind = next(
            iter(set(param) & {"text", "id", "key", "int", "num", "pct", "clock", "person", "list"})
        )
        kinds.add(kind)
        if kind == "key":
            walk(param)
        if kind == "list":
            for item in param["list"]:
                walk_param(item)

    for case in CASES:
        walk(case["ref"])
    assert kinds == {"text", "id", "key", "int", "num", "pct", "clock", "person", "list"}
    assert {case["language"] for case in CASES} == {"en", "fa"}
    assert len({case["id"] for case in CASES + ERRORS}) == len(CASES) + len(ERRORS)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_python_resolves_the_shared_reference_cases(case: dict[str, Any]) -> None:
    catalog = fixture_catalogs()[case["language"]]
    context = fixture_context(case["context"])
    ref = MessageRef.model_validate(case["ref"])

    markup = catalog.markup(ref, context)

    assert isinstance(markup, Markup)
    assert catalog.plain(ref, context) == case["plain"]
    assert markup_tree(str(markup)) == case["nodes"]
    # The wire form round-trips exactly, so the story JSON and this fixture agree.
    assert ref.model_dump(mode="json") == case["ref"]
    assert MessageRef.model_validate(ref.model_dump()) == ref
    assert MessageRef.model_validate_json(ref.model_dump_json()) == ref


@pytest.mark.parametrize("case", ERRORS, ids=[case["id"] for case in ERRORS])
def test_python_refuses_the_shared_error_cases(case: dict[str, Any]) -> None:
    catalog = fixture_catalogs()[case["language"]]
    context = fixture_context(case["context"])
    with pytest.raises((CatalogError, ValidationError)):
        catalog.plain(MessageRef.model_validate(case["ref"]), context)


def test_hostile_text_is_escaped_inside_an_isolate() -> None:
    catalog = fixture_catalogs()["en"]
    context = fixture_context("one_day")
    ref = message_ref(
        "test.noticed",
        time=clock_param(5),
        name=person_param("person-009"),
        product=text_param('</bdi><script>alert("x")</script>'),
        where=message_ref("test.where"),
    )
    markup = str(catalog.markup(ref, context))
    assert "<script" not in markup
    assert '<bdi dir="auto">&lt;/bdi&gt;&lt;script&gt;alert(&#34;x&#34;)' in markup
    assert '<bdi dir="auto">Sam &lt;/bdi&gt;&lt;script&gt;window.pwned=1' in markup
    assert markup.count("<bdi") == markup.count("</bdi>")


def test_a_missing_parameter_or_person_raises() -> None:
    catalog = fixture_catalogs()["en"]
    with pytest.raises(CatalogError, match="absent"):
        catalog.plain(message_ref("test.missing"), fixture_context("one_day"))
    with pytest.raises(CatalogError, match="person-007"):
        catalog.plain(
            message_ref("test.judged", count=2, model=person_param("person-007")),
            fixture_context("one_day"),
        )


def test_parameter_count_limit() -> None:
    params = {f"p{index}": int_param(index) for index in range(MAX_REF_PARAMS)}
    assert len(message_ref("test.total", **params).params or {}) == MAX_REF_PARAMS
    params["p99"] = int_param(99)
    with pytest.raises(ValidationError):
        message_ref("test.total", **params)


def test_nesting_depth_limit() -> None:
    inner = message_ref("flag.unsafe-content")
    two = message_ref("test.warning", flags=inner)
    three = message_ref("test.warning", flags=list_param([inner]))
    assert ref_depth(inner) == 1
    assert ref_depth(two) == 2
    assert ref_depth(three) == MAX_REF_DEPTH == 3
    with pytest.raises(ValidationError):
        message_ref("test.warning", flags=three)
    with pytest.raises(ValidationError):
        message_ref("test.warning", flags=list_param([list_param([inner])]))
    with pytest.raises(ValidationError):
        list_param([int_param(index) for index in range(9)])


def test_person_resolution_for_template_and_saved_personas() -> None:
    english = load_catalog("en")
    context = RefContext(
        days=1,
        people={
            "person-001": PersonLabel("lumo", "Lumo", 1),
            "person-002": PersonLabel(None, "Kim Example", 2),
            "person-003": PersonLabel("nuvin", "Ignored display name", 3),
        },
    )
    assert (
        english.plain(message_ref("story.person.follow", name=person_param("person-001")), context)
        == "Follow Lumo #1"
    )
    assert (
        english.plain(message_ref("story.person.follow", name=person_param("person-002")), context)
        == "Follow Kim Example #2"
    )
    assert (
        english.plain(message_ref("story.person.follow", name=person_param("person-003")), context)
        == "Follow Nuvin #3"
    )
    template = str(
        english.markup(message_ref("story.filter.person", name=person_param("person-001")), context)
    )
    saved = str(
        english.markup(message_ref("story.filter.person", name=person_param("person-002")), context)
    )
    assert '<bdi dir="ltr"><bdi dir="ltr">Lumo</bdi> #<bdi dir="ltr">1</bdi></bdi>' in template
    assert '<bdi dir="auto">Kim Example</bdi>' in saved
    unknown = RefContext(days=1, people={"person-004": PersonLabel("unknown", "X", 4)})
    with pytest.raises(CatalogError):
        english.plain(message_ref("story.person.follow", name=person_param("person-004")), unknown)


def test_typed_constructors_build_the_wire_shapes() -> None:
    ref = message_ref(
        "test.everything",
        count=3,
        a=text_param("x"),
        b=id_param("model-1"),
        c=int_param(-4),
        d=num_param(0.5, 2, signed=True),
        e=num_param(0.5, 2),
        f=pct_param(0.25),
        g=clock_param(60),
        h=person_param("person-001"),
        i=list_param([message_ref("flag.unsafe-content")]),
    )
    assert ref.model_dump(mode="json") == {
        "key": "test.everything",
        "count": 3,
        "params": {
            "a": {"text": "x"},
            "b": {"id": "model-1"},
            "c": {"int": -4},
            "d": {"num": 0.5, "decimals": 2, "signed": True},
            "e": {"num": 0.5, "decimals": 2},
            "f": {"pct": 0.25, "decimals": 0},
            "g": {"clock": 60},
            "h": {"person": "person-001"},
            "i": {"list": [{"key": "flag.unsafe-content"}]},
        },
    }
    assert message_ref("a.b").model_dump() == {"key": "a.b"}
    assert IntParam.model_validate({"int": 3}).value == 3
    assert ListParam.model_validate({"list": []}).items == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"key": "a.b", "count": -1},
        {"key": "a.b", "count": True},
        {"key": "a.b", "count": 1.0},
        {"key": "a.b", "extra": 1},
        {"key": "a"},
        {"key": "a.b", "params": {"Bad": {"int": 1}}},
        {"key": "a.b", "params": {"x": {"text": "y" * 241}}},
        {"key": "a.b", "params": {"x": {"id": ""}}},
        {"key": "a.b", "params": {"x": {"num": float("nan"), "decimals": 2}}},
        {"key": "a.b", "params": {"x": {"num": 1e21, "decimals": 2}}},
        {"key": "a.b", "params": {"x": {"pct": 0.5, "decimals": 2}}},
        {"key": "a.b", "params": {"x": {"clock": -1}}},
        {"key": "a.b", "params": {"x": {"int": 9_007_199_254_740_992}}},
        {"key": "a.b", "params": {"x": "plain string"}},
        {"key": "a.b", "params": {"x": {}}},
    ],
)
def test_reference_models_are_strict(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MessageRef.model_validate(payload)


def test_references_are_frozen() -> None:
    ref = message_ref("a.b", count=1)
    with pytest.raises(ValidationError):
        ref.count = 2  # type: ignore[misc]


def _render(
    source: str, catalog: Catalog, context: RefContext | None = None, **variables: object
) -> str:
    environment = Environment(
        loader=DictLoader({"page": source}),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    environment.globals.update(template_functions(catalog, context))
    return environment.get_template("page").render(**variables)


def test_template_functions_translate_with_isolated_parameters() -> None:
    english = load_catalog("en")
    assert _render('{{ tr("common.skip") }}', english) == "Skip to content"
    assert (
        _render('{{ tr("progress.answered", resolved=3, total=10) }}', english)
        == '<bdi dir="ltr">3</bdi> of <bdi dir="ltr">10</bdi> answered'
    )
    assert (
        _render('{{ tr("progress.run", run_id=run_id) }}', english, run_id="<b>x</b>")
        == 'Run <bdi dir="auto">&lt;b&gt;x&lt;/b&gt;</bdi>'
    )
    assert (
        _render('{{ tr("progress.run", run_id=bdi_id(run_id)) }}', english, run_id="r-1")
        == 'Run <bdi dir="ltr">r-1</bdi>'
    )
    assert (
        _render('{{ tr("common.people", count=n) }}', english, n=10240)
        == '<bdi dir="ltr">10,240</bdi> people'
    )
    assert (
        _render('<title>{{ tr_text("progress.run", run_id=x) }}</title>', english, x="a&b")
        == "<title>Run a&amp;b</title>"
    )
    assert _render("{{ tr_text('common.days', count=1) }}", english) == "1 simulated day"
    # A text count may fill {count} in a message that is not plural.
    assert (
        _render("{{ tr_text('story.map.label', count='', name='') }}", english)
        == "Map of the fictional town with  people. The highlighted person is ."
    )
    with pytest.raises(CatalogError):
        _render("{{ tr('common.people', count='3') }}", english)
    with pytest.raises(CatalogError):
        _render("{{ tr('common.people') }}", english)
    with pytest.raises(CatalogError):
        _render("{{ tr('progress.answered', resolved=3) }}", english)
    with pytest.raises(CatalogError):
        _render("{{ tr('no.such_key') }}", english)
    with pytest.raises(TypeError):
        _render("{{ tr('progress.run', run_id=1.5) }}", english)


def test_template_functions_resolve_references_and_format_numbers() -> None:
    persian = fixture_catalogs()["fa"]
    context = fixture_context("three_days")
    ref = {"key": "test.total", "params": {"total": {"int": 14800}}}
    assert _render("{{ ref(r) }}", persian, context, r=ref) == (
        '<bdi dir="ltr">\u06f1\u06f4\u066c\u06f8\u06f0\u06f0</bdi> '
        + persian.message("test.total").replace("{total} ", "")  # type: ignore[union-attr]
    )
    assert _render("{{ ref_text(r) }}", persian, context, r=MessageRef.model_validate(ref)) == (
        persian.text("test.total", total=14800)
    )
    english = load_catalog("en")
    functions = template_functions(english, RefContext(days=2, people={}))
    assert functions["fmt_int"](10240) == "10,240"
    assert functions["fmt_dec"](-0.2213, 2) == "\u22120.22"
    assert functions["fmt_dec"](0.04, 2, True) == "+0.04"
    assert functions["fmt_pct"](0.5) == "50%"
    assert functions["fmt_clock"](1877) == "Day 2 \u00b7 07:17"
    assert functions["fmt_clock"](437, 1) == "07:17"
    assert functions["fmt_duration"](61) == "1 min 1 s"
    assert functions["bdi_text"]("<x>") == Markup('<bdi dir="auto">&lt;x&gt;</bdi>')
    assert functions["bdi_id"]("abc123") == Markup('<bdi dir="ltr">abc123</bdi>')
    assert set(functions) == {
        "tr",
        "tr_text",
        "ref",
        "ref_text",
        "fmt_int",
        "fmt_dec",
        "fmt_pct",
        "fmt_clock",
        "fmt_duration",
        "bdi_text",
        "bdi_id",
    }
    with pytest.raises(TypeError):
        template_functions("en")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        functions["ref"]("story.title")
    with pytest.raises(TypeError):
        functions["bdi_id"](3)
