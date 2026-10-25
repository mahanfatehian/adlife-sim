"""Bundled persona starting points use the schema-2 authoring boundary unchanged."""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.city.workbench_input import WorkbenchPersonaDraftV2
from adlife.core.domain.serialization import canonical_json

IDS = [f"persona-{index:03d}" for index in range(1, 11)]
PERSONA_FIELDS = [
    "persona_id",
    "count",
    "fictional",
    "display_name",
    "age",
    "occupation",
    "background",
    "mbti",
    "interests",
    "traits",
    "initial_state",
]
TRAIT_FIELDS = [
    "price_sensitivity",
    "novelty_seeking",
    "advertising_skepticism",
    "mobile_recall_encoding",
    "roadside_recall_encoding",
    "impulsivity",
]
STATE_FIELDS = ["brand_sentiment", "recall_strength", "purchase_intention"]


def _personas():
    name = "adlife.city.workbench_personas"
    assert importlib.util.find_spec(name) is not None, "persona catalog loader is missing"
    return importlib.import_module(name)


def catalog_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "templates": [
            {
                "persona_id": identifier,
                "count": 1,
                "fictional": True,
                "display_name": f"Fictional character {index}",
                "age": 28,
                "occupation": "Invented maker",
                "background": "An invented character for a synthetic scenario.",
                "mbti": "INTJ",
                "interests": ["technology", "crafts"],
                "traits": {
                    "price_sensitivity": 0.7,
                    "novelty_seeking": 0.4,
                    "advertising_skepticism": 0.6,
                    "mobile_recall_encoding": 0.5,
                    "roadside_recall_encoding": 0.3,
                    "impulsivity": 0.2,
                },
                "initial_state": {
                    "brand_sentiment": -0.1,
                    "recall_strength": 0.0,
                    "purchase_intention": 0.1,
                },
            }
            for index, identifier in enumerate(IDS, start=1)
        ],
    }


def _load(tmp_path: Path, payload: object):
    (tmp_path / "persona_templates.json").write_text(json.dumps(payload), encoding="utf-8")
    return _personas().load_persona_template_catalog(root=tmp_path)


def test_packaged_catalog_contains_ten_flat_validated_fictional_starting_points() -> None:
    resource = files("adlife.city").joinpath("persona_templates.json")
    assert resource.is_file(), "persona catalog resource is missing"
    raw = json.loads(resource.read_bytes())
    assert set(raw) == {"schema_version", "templates"}
    assert type(raw["schema_version"]) is int and raw["schema_version"] == 1
    assert [item["persona_id"] for item in raw["templates"]] == IDS
    catalog = _personas().load_persona_template_catalog()
    assert isinstance(catalog, _personas().PersonaTemplateCatalog)
    assert len(canonical_json(catalog).encode("utf-8")) <= 32_768
    for item, template in zip(raw["templates"], catalog.templates, strict=True):
        # The resource groups descriptive context before explicit causal assumptions.
        assert list(item) == PERSONA_FIELDS
        assert set(item["traits"]) == set(TRAIT_FIELDS)
        assert set(item["initial_state"]) == set(STATE_FIELDS)
        assert isinstance(template, WorkbenchPersonaDraftV2)
        assert template.fictional is True
        assert template.count == 1
        assert len(canonical_json(template).encode("utf-8")) <= 3_072
        assert template == WorkbenchPersonaDraftV2.model_validate_json(json.dumps(item))
    assert len({item.display_name for item in catalog.templates}) == 10
    assert len({canonical_json(item.traits) for item in catalog.templates}) == 10


# Prompt contract v3 sends a template's background to the AI as the life the person
# answers from, so the packaged text tells a short, whole fictional life (daily life and
# hobbies, one dream or goal, one current worry or circumstance) and carries no note
# meant for the editor. The character bound keeps every request small.
MAX_TEMPLATE_BACKGROUND_CHARACTERS = 600
_GOAL_WORDS = ("dream", "hope", "goal")
_CIRCUMSTANCE_WORDS = ("lately", "right now", "this year", "this month", "this season", "soon")
_EDITOR_NOTES = (
    "assumption",
    "causal",
    "response value",
    "mobility",
    "personality label",
    "mbti",
    "below",
    "scenario",
    "descriptive",
)


def test_packaged_backgrounds_tell_a_short_whole_fictional_life() -> None:
    catalog = _personas().load_persona_template_catalog()
    names = {
        item.persona_id: item.display_name.removeprefix("Fictional ") for item in catalog.templates
    }
    for template in catalog.templates:
        background = template.background
        assert background is not None, template.persona_id
        assert 300 <= len(background) <= MAX_TEMPLATE_BACKGROUND_CHARACTERS, template.persona_id
        # The fictional disclosure opens every life story, as it did before enrichment.
        assert background.startswith("A wholly invented character who "), template.persona_id
        assert background.count(". ") >= 3 and background.endswith("."), template.persona_id
        lowered = background.lower()
        assert any(word in lowered for word in _GOAL_WORDS), template.persona_id
        assert any(word in lowered for word in _CIRCUMSTANCE_WORDS), template.persona_id
        assert not [note for note in _EDITOR_NOTES if note in lowered], template.persona_id
        # A story names only its own person, never another template's.
        assert names[template.persona_id] in background, template.persona_id
        others = [name for key, name in names.items() if key != template.persona_id]
        assert not [name for name in others if name in background], template.persona_id
        assert not any(character.isdigit() for character in background), template.persona_id
        # Gender is never part of a template, so no story assumes one.
        words = set(re.findall(r"[a-z]+", lowered))
        assert not words & {"he", "she", "him", "his", "her", "hers"}, template.persona_id


def test_loader_orders_templates_by_fixed_id_and_preserves_explicit_assumptions(tmp_path: Path):
    payload = catalog_data()
    payload["templates"].reverse()
    catalog = _load(tmp_path, payload)
    assert [item.persona_id for item in catalog.templates] == IDS
    assert catalog.templates[0].traits.price_sensitivity == 0.7
    assert catalog.templates[0].initial_state.brand_sentiment == -0.1
    assert catalog.templates[0].interests == frozenset({"technology", "crafts"})


def test_descriptive_edits_never_infer_or_replace_explicit_causal_values(tmp_path: Path) -> None:
    payload = catalog_data()
    before = _load(tmp_path, payload).templates[0]
    payload["templates"][0].update(
        display_name="Another fictional character",
        age=65,
        occupation="Invented archivist",
        background="A different invented story.",
        mbti="ESFP",
    )
    after = _load(tmp_path, payload).templates[0]
    assert (after.interests, after.traits, after.initial_state) == (
        before.interests,
        before.traits,
        before.initial_state,
    )


@pytest.mark.parametrize("mutation", ["empty", "missing", "extra", "duplicate", "wrong-id"])
def test_catalog_refuses_any_set_other_than_the_ten_fixed_ids(tmp_path: Path, mutation: str):
    payload = catalog_data()
    if mutation == "empty":
        payload["templates"] = []
    elif mutation == "missing":
        payload["templates"].pop()
    elif mutation == "extra":
        payload["templates"].append(deepcopy(payload["templates"][0]))
    elif mutation == "duplicate":
        payload["templates"][1]["persona_id"] = "persona-001"
    else:
        payload["templates"][0]["persona_id"] = "persona-011"
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _load(tmp_path, payload)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2, None])
def test_catalog_refuses_inexact_or_unknown_schema_versions(tmp_path: Path, version: object):
    payload = catalog_data()
    payload["schema_version"] = version
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _load(tmp_path, payload)


@pytest.mark.parametrize("mutation", ["wrapper-extra", "persona-extra", "nested-context", "array"])
def test_loader_refuses_competing_or_unknown_resource_structure(tmp_path: Path, mutation: str):
    payload = catalog_data()
    if mutation == "wrapper-extra":
        payload["extra"] = "unexpected"
    elif mutation == "persona-extra":
        payload["templates"][0]["extra"] = "unexpected"
    elif mutation == "nested-context":
        payload["templates"][0]["context"] = {"display_name": "Fictional context"}
        payload["templates"][0].pop("display_name")
    else:
        payload = payload["templates"]
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _load(tmp_path, payload)


@pytest.mark.parametrize(
    "group,field",
    [("traits", f) for f in TRAIT_FIELDS] + [("initial_state", f) for f in STATE_FIELDS],
)
@pytest.mark.parametrize(
    "value", [True, "0.5", -1.01, 1.01, float("nan"), float("inf"), float("-inf")]
)
def test_loader_validates_every_explicit_causal_number(tmp_path, group, field, value):
    payload = catalog_data()
    payload["templates"][0][group][field] = value
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _load(tmp_path, payload)


@pytest.mark.parametrize(
    "group,field",
    [("traits", f) for f in TRAIT_FIELDS] + [("initial_state", f) for f in STATE_FIELDS],
)
def test_loader_never_defaults_a_missing_causal_number(tmp_path, group, field):
    payload = catalog_data()
    del payload["templates"][0][group][field]
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _load(tmp_path, payload)


@pytest.mark.parametrize("value", [-0.01, 0.0, 1.0])
@pytest.mark.parametrize(
    "group,field",
    [("traits", field) for field in TRAIT_FIELDS]
    + [("initial_state", "recall_strength"), ("initial_state", "purchase_intention")],
)
def test_causal_unit_interval_lower_bound_and_endpoints(tmp_path, group, field, value):
    payload = catalog_data()
    payload["templates"][0][group][field] = value
    if value < 0:
        with pytest.raises(_personas().PersonaTemplateCatalogError):
            _load(tmp_path, payload)
    else:
        catalog = _load(tmp_path, payload)
        assert getattr(getattr(catalog.templates[0], group), field) == value


@pytest.mark.parametrize("value", [-1.0, 1.0])
def test_causal_sentiment_accepts_both_signed_endpoints(tmp_path, value):
    payload = catalog_data()
    payload["templates"][0]["initial_state"]["brand_sentiment"] = value
    assert _load(tmp_path, payload).templates[0].initial_state.brand_sentiment == value


@pytest.mark.parametrize(
    "field", ["persona_id", "display_name", "occupation", "background", "mbti", "interests"]
)
@pytest.mark.parametrize(
    "text",
    [
        "person@example.org",
        "+1 415 555 0134",
        "national-id-1234567890",
        "Authorization: Bearer private-marker-1234567890",
        "sk-" + "a" * 32,
        "unsafe\ntext",
        "unsafe\u202etext",
        "unsafe\u2028text",
        "unsafe\ud800text",
    ],
)
def test_loader_screens_every_raw_persona_string_without_reflecting_it(tmp_path, field, text):
    payload = catalog_data()
    payload["templates"][0][field] = [text] if field == "interests" else text
    with pytest.raises(_personas().PersonaTemplateCatalogError) as captured:
        _load(tmp_path, payload)
    assert text not in str(captured.value)
    assert captured.value.__suppress_context__


@pytest.mark.parametrize(
    "field,value",
    [
        ("display_name", "a" * 81),
        ("display_name", ""),
        ("occupation", "a" * 81),
        ("occupation", ""),
        ("background", "a" * 801),
        ("interests", ["a" * 81]),
        ("interests", []),
        ("interests", ["crafts", "crafts"]),
        ("interests", [f"interest-{i}" for i in range(13)]),
        ("age", 17),
        ("age", 66),
        ("age", True),
        ("count", 0),
        ("count", 31),
        ("count", True),
        ("fictional", False),
    ],
)
def test_loader_enforces_persona_text_collection_and_context_bounds(tmp_path, field, value):
    payload = catalog_data()
    payload["templates"][0][field] = value
    with pytest.raises(_personas().PersonaTemplateCatalogError):
        _load(tmp_path, payload)


@pytest.mark.parametrize(
    "nested",
    [
        {"api_key": "opaque-private-marker"},
        ["api_key", "opaque-private-marker"],
        ["opaque-private-marker", "x-api-key"],
    ],
)
@pytest.mark.parametrize("field", ["background", "interests", "traits", "initial_state"])
def test_loader_refuses_nested_secret_labels_without_reflecting_values(tmp_path, field, nested):
    payload = catalog_data()
    payload["templates"][0][field] = nested
    with pytest.raises(_personas().PersonaTemplateCatalogError) as captured:
        _load(tmp_path, payload)
    assert "opaque-private-marker" not in str(captured.value)


@pytest.mark.parametrize(
    "document",
    [
        b'{"schema_version":1,"schema_version":1,"templates":[]}',
        b'{"schema_version":1,"templates":[{"display_name":"one","display_name":"two"}]}',
        b"{",
        b"\xff",
        b"[]",
    ],
)
def test_loader_refuses_ambiguous_or_malformed_json(tmp_path, document):
    (tmp_path / "persona_templates.json").write_bytes(document)
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _personas().load_persona_template_catalog(root=tmp_path)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_loader_requires_utf8_resource_bytes(tmp_path, encoding):
    (tmp_path / "persona_templates.json").write_bytes(json.dumps(catalog_data()).encode(encoding))
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _personas().load_persona_template_catalog(root=tmp_path)


def test_loader_byte_limit_is_inclusive_and_read_failure_is_value_free(tmp_path):
    module = _personas()
    with pytest.raises(module.PersonaTemplateCatalogError, match="missing or unreadable"):
        module.load_persona_template_catalog(root=tmp_path)
    document = json.dumps(catalog_data()).encode("utf-8")
    exact = document + b" " * (32_768 - len(document))
    resource = tmp_path / "persona_templates.json"
    resource.write_bytes(exact)
    assert len(module.load_persona_template_catalog(root=tmp_path).templates) == 10
    resource.write_bytes(exact + b" ")
    with pytest.raises(module.PersonaTemplateCatalogError, match="size"):
        module.load_persona_template_catalog(root=tmp_path)


def test_loader_applies_per_persona_canonical_utf8_limit(tmp_path):
    payload = catalog_data()
    persona = payload["templates"][0]
    persona["background"] = ""
    overhead = len(canonical_json(persona).encode("utf-8"))
    quotient, remainder = divmod(3_072 - overhead, 4)
    persona["background"] = "\U0001f600" * quotient + "a" * remainder
    assert len(canonical_json(persona).encode("utf-8")) == 3_072
    assert _load(tmp_path, payload).templates[0].background == persona["background"]
    persona["background"] += "a"
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="schema"):
        _load(tmp_path, payload)


def test_catalog_and_nested_values_are_immutable_and_reloaded_independently(tmp_path):
    catalog = _load(tmp_path, catalog_data())
    assert isinstance(catalog.templates, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        catalog.templates = ()
    with pytest.raises(ValidationError, match="frozen"):
        catalog.templates[0].display_name = "Changed"
    with pytest.raises(ValidationError, match="frozen"):
        catalog.templates[0].traits.impulsivity = 0.9
    with pytest.raises(ValidationError, match="frozen"):
        catalog.templates[0].initial_state.brand_sentiment = 0.9
    assert isinstance(catalog.templates[0].interests, frozenset)
    assert _personas().load_persona_template_catalog(root=tmp_path) == catalog


def test_loader_refuses_symlinked_resource(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(catalog_data()), encoding="utf-8")
    try:
        (tmp_path / "persona_templates.json").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(_personas().PersonaTemplateCatalogError, match="unsafe"):
        _personas().load_persona_template_catalog(root=tmp_path)
