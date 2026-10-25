"""A new draft is refused up front by the very screens its planning applies later.

Planning builds the cognition context models only after the whole deterministic
simulation, and the job re-parses its canonical frozen input, in which interest sets are
sorted. ``POST /api/scenarios/validate`` and ``POST /api/jobs`` both call
``construct_workbench_run`` before any job is reserved, so it applies the same screens and
refuses by field. Saved runs are never re-screened: the stored-input models are unchanged.
"""

from __future__ import annotations

import copy
import json
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from adlife.city import runs, workbench_validation
from adlife.city.run_store import CityRunStore
from adlife.city.workbench_input import (
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
    WorkbenchRunDraftV4,
    WorkbenchRunInputV2,
    WorkbenchRunInputV4,
    parse_workbench_run_input_json,
)
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_validation import WorkbenchValidationError, construct_workbench_run
from adlife.city.workbench_web import _WORKBENCH_FIELD_COMPONENTS
from adlife.core.domain import city_cognition_v2 as domain
from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10, _save_prepared
from tests.unit.city.run_story_fixtures import default_study_draft
from tests.unit.city.test_city_run_preparation import _v2_arguments
from tests.unit.city.test_workbench_input import draft_v2_data, draft_v3_data
from tests.unit.city.test_workbench_web import _artifact_snapshot, _client, _secure_post

CURRENT = domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT
OFFLINE_LIMIT = domain.CITY_COGNITION_SELECTION_LIMITS[
    domain.CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION
].offline_funded_response_limit
ROUTES = ("/api/scenarios/validate", "/api/jobs")
PERSONA_REFUSAL = "Rewrite this persona text without path-like slashes or credential labels."
CAMPAIGN_REFUSAL = (
    "Rewrite this campaign text without path-like slashes, credential labels or line breaks."
)
# Ordinary prose to a person, path-shaped to the cognition context screens.
REPORTED_TEXT = ("Node.js/React developer", "B.Sc./M.Sc.", "reads /r/gardening", "4.5/5")
OCCUPATION = ("population", "personas", 0, "occupation")
BACKGROUND = ("population", "personas", 0, "background")
DISPLAY_NAME = ("population", "personas", 0, "display_name")
INTERESTS = ("population", "personas", 0, "interests")
CAMPAIGN_ID = ("scenario", "campaign", "campaign_id")
CAMPAIGN_NAME = ("scenario", "campaign", "name")
TARGET_INTERESTS = ("scenario", "campaign", "target_interests")
FieldPath = tuple[str | int, ...]


def _study(*, run_id: str = "acceptance-run") -> dict[str, Any]:
    """The guided default study as a No-AI (rules) draft."""
    return default_study_draft(run_id=run_id, mode="rules", funded_response_limit=OFFLINE_LIMIT)


def _http(document: dict[str, Any]) -> dict[str, Any]:
    """The draft as the browser sends it: the seed travels as a decimal string."""
    payload = copy.deepcopy(document)
    payload["settings"]["seed"] = str(payload["settings"]["seed"])
    return payload


def _changed(
    document: dict[str, Any], path: FieldPath, value: object, *, append: bool = False
) -> dict[str, Any]:
    changed = copy.deepcopy(document)
    target: Any = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = [*target[path[-1]], value] if append else value
    return changed


def _field(path: FieldPath) -> str:
    return ".".join(str(component) for component in path)


def _refusal(path: FieldPath) -> dict[str, object]:
    persona = path[0] == "population"
    return {
        "schema_version": 1,
        "error": {
            "code": "invalid-persona-text" if persona else "invalid-campaign-text",
            "message": "The scenario could not be validated.",
            "fields": {_field(path): PERSONA_REFUSAL if persona else CAMPAIGN_REFUSAL},
        },
    }


def _assert_refused_before_any_job(
    tmp_path: Path, payload: dict[str, Any], path: FieldPath
) -> None:
    client, root = _client(tmp_path)
    before = _artifact_snapshot(root)
    with client:
        for route in ROUTES:
            response = _secure_post(client, route, payload)
            assert (response.status_code, response.json()) == (400, _refusal(path)), route
        assert client.get("/api/workbench").json()["active_job"] is None
    assert _artifact_snapshot(root) == before


@pytest.mark.parametrize(
    ("path", "text", "append"),
    [
        *((OCCUPATION, text, False) for text in REPORTED_TEXT),
        *((BACKGROUND, text, False) for text in REPORTED_TEXT),
        (INTERESTS, "Node.js/React", True),
        (TARGET_INTERESTS, "Node.js/React", True),
    ],
)
def test_reported_text_is_refused_by_field_on_both_routes_before_any_job(
    tmp_path: Path, path: FieldPath, text: str, append: bool
) -> None:
    payload = _http(_changed(_study(), path, text, append=append))
    _assert_refused_before_any_job(tmp_path, payload, path)


@pytest.mark.parametrize(
    ("path", "text"),
    [(CAMPAIGN_NAME, "Node.js/React launch"), (OCCUPATION, "B.Sc./M.Sc.")],
)
def test_a_schema3_draft_is_refused_by_the_schema3_context_screens(
    tmp_path: Path, path: FieldPath, text: str
) -> None:
    payload = _http(_changed(draft_v3_data(), path, text))
    _assert_refused_before_any_job(tmp_path, payload, path)


def test_a_persona_refusal_names_the_personas_position_in_the_request(tmp_path: Path) -> None:
    document = _study()
    document["population"]["personas"].reverse()
    assert document["population"]["personas"][1]["persona_id"] == "persona-001"
    document = _changed(document, ("population", "personas", 1, "occupation"), REPORTED_TEXT[0])
    # A parsed draft holds its personas in canonical ID order, where this one comes first.
    with pytest.raises(WorkbenchValidationError) as refused:
        construct_workbench_run(
            WorkbenchRunDraftV4.model_validate_json(json.dumps(document)), causal_contract=CURRENT
        )
    assert refused.value.field == "population.personas.0.occupation"
    # The HTTP diagnostic names the array position the client sent, like pydantic's.
    _assert_refused_before_any_job(
        tmp_path, _http(document), ("population", "personas", 1, "occupation")
    )


@pytest.mark.parametrize(
    ("schema", "path", "value"),
    [
        (4, OCCUPATION, "password"),
        (4, DISPLAY_NAME, "Passwords"),
        (4, INTERESTS, ["secrets"]),
        (4, CAMPAIGN_ID, "password"),
        (4, TARGET_INTERESTS, ["technology", "line\u2028break"]),
        (3, DISPLAY_NAME, "password"),
        (3, INTERESTS, ["secrets"]),
        (3, CAMPAIGN_NAME, "password"),
        (3, CAMPAIGN_NAME, "line\u2028break"),
    ],
)
def test_labels_and_line_breaks_are_refused_by_the_field_that_holds_them(
    schema: int, path: FieldPath, value: object
) -> None:
    document = _changed(_study() if schema == 4 else draft_v3_data(), path, value)
    model = WorkbenchRunDraftV4 if schema == 4 else WorkbenchRunDraftV3
    draft = model.model_validate_json(json.dumps(document))
    with pytest.raises(WorkbenchValidationError) as refused:
        construct_workbench_run(draft, causal_contract=CURRENT)
    persona = path[0] == "population"
    assert refused.value.field == _field(path)
    assert refused.value.code == ("invalid-persona-text" if persona else "invalid-campaign-text")
    assert refused.value.safe_message == (PERSONA_REFUSAL if persona else CAMPAIGN_REFUSAL)


@pytest.mark.parametrize(
    ("schema", "interests"),
    [
        *(
            (schema, interests)
            for schema in (2, 4)
            for interests in (
                ("tea", "baking", "cookies"),
                ("baking", "cookies"),
                ("travel", "secrets"),
                ("commuting", "technology"),
            )
        ),
        # A label sorted last: the request's walk refuses it only when sent first, and the
        # schema-4 campaign screen refuses it in any order. Schema 2 plans no cognition, so
        # there the order sent still decides (the request walk reads it), which is a refusal
        # up front, never an accepted draft that fails later.
        (4, ("music", "secrets")),
    ],
)
def test_the_order_of_target_interests_does_not_change_the_verdict(
    tmp_path: Path, schema: int, interests: tuple[str, ...]
) -> None:
    # The job re-parses the canonical input, in which the set is sorted.
    document = _study() if schema == 4 else draft_v2_data()
    client, root = _client(tmp_path)
    before = _artifact_snapshot(root)
    with client:
        verdicts = {
            _secure_post(
                client,
                "/api/scenarios/validate",
                _http(_changed(document, TARGET_INTERESTS, list(order))),
            ).status_code
            == 200
            for order in permutations(interests)
        }
        assert client.get("/api/workbench").json()["active_job"] is None
    assert len(verdicts) == 1, interests
    assert _artifact_snapshot(root) == before


def test_a_label_the_canonical_order_exposes_is_refused_before_any_job(
    tmp_path: Path,
) -> None:
    # Sent last, "secrets" passes the request's own walk; sorted first, the job's refuses it.
    document = _changed(draft_v2_data(), TARGET_INTERESTS, ["travel", "secrets"])
    client, root = _client(tmp_path)
    before = _artifact_snapshot(root)
    with client:
        for route in ROUTES:
            response = _secure_post(client, route, _http(document))
            # The transport walks the canonical order too, so either order gets this answer.
            assert response.status_code == 422, route
            assert response.json()["error"]["fields"] == {
                "request": "Request data cannot contain credential fields."
            }, route
        assert client.get("/api/workbench").json()["active_job"] is None
    assert _artifact_snapshot(root) == before
    # A caller that skips the HTTP transport is still refused, by field, before any job.
    with pytest.raises(WorkbenchValidationError) as refused:
        construct_workbench_run(WorkbenchRunDraftV2.model_validate_json(json.dumps(document)))
    assert refused.value.field == _field(TARGET_INTERESTS)


def _slash_persona(document: dict[str, Any]) -> dict[str, Any]:
    persona = document["population"]["personas"][0]
    persona["occupation"] = REPORTED_TEXT[0]
    persona["background"] = "Holds a B.Sc./M.Sc., reads /r/gardening and rates cafes 4.5/5."
    return document


def test_a_saved_schema2_run_holding_such_text_still_loads_and_replays(tmp_path: Path) -> None:
    document = _changed(_slash_persona(draft_v2_data()), INTERESTS, "Node.js/React", append=True)
    document = _changed(document, TARGET_INTERESTS, "Node.js/React", append=True)
    # Schema 2 has no cognition planning, so new schema-2 drafts keep this text too.
    validated = construct_workbench_run(
        WorkbenchRunDraftV2.model_validate_json(json.dumps(document))
    )
    settings = validated.workbench_input.draft.settings
    runs.create_city_run(
        validated.pack,
        root=tmp_path,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )
    stored = CityRunStore(tmp_path).load(settings.run_id)
    assert runs.replay_city_run(stored).identical is True
    sidecar = tmp_path / "city-runs" / settings.run_id / "inputs" / "workbench.json"
    saved = parse_workbench_run_input_json(sidecar.read_bytes())
    assert isinstance(saved, WorkbenchRunInputV2)
    persona = saved.draft.population.personas[0]
    assert persona.occupation == REPORTED_TEXT[0]
    assert "Node.js/React" in persona.interests


def test_a_saved_run_under_the_prompt_v2_contract_holding_such_text_loads_and_replays(
    tmp_path: Path,
) -> None:
    document = _slash_persona(_study(run_id="older-contract"))
    # Prompt v2 carries no persona card, so its runs never screened this text.
    validated = construct_workbench_run(
        WorkbenchRunDraftV4.model_validate_json(json.dumps(document)),
        causal_contract=domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3,
    )
    _staged, prepared = _prepared_v10(tmp_path, mode="rules", validated=validated)
    _save_prepared(tmp_path, prepared)
    stored = CityRunStore(tmp_path).load("older-contract")
    assert runs.replay_city_run(stored).identical is True
    assert isinstance(stored.workbench_input, WorkbenchRunInputV4)
    persona = stored.workbench_input.draft.population.personas[0]
    assert persona.occupation == REPORTED_TEXT[0]
    sidecar = tmp_path / "city-runs" / "older-contract" / "inputs" / "workbench.json"
    assert parse_workbench_run_input_json(sidecar.read_bytes()) == stored.workbench_input


def _agrees_with_planning(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch, *, schema: int = 4
) -> bool:
    """Acceptance takes the draft exactly when rules-mode planning prepares it."""
    model = WorkbenchRunDraftV4 if schema == 4 else WorkbenchRunDraftV3
    draft = model.model_validate_json(json.dumps(document))
    prepare = (
        runs.prepare_city_scientific_run_v2 if schema == 4 else runs.prepare_city_scientific_run
    )

    def prepared(validated: workbench_validation.ValidatedWorkbenchRun) -> object:
        return prepare(validated.pack, **_v2_arguments(validated))

    try:
        validated = construct_workbench_run(draft, causal_contract=CURRENT)
    except WorkbenchValidationError:
        with monkeypatch.context() as patch:
            patch.setattr(workbench_validation, "_screen_new_input", lambda _input: None)
            unscreened = construct_workbench_run(draft, causal_contract=CURRENT)
        with pytest.raises(ValueError):
            prepared(unscreened)
        return False
    assert prepared(validated) is not None
    return True


def _template_studies() -> list[Any]:
    studies = [pytest.param(_study(), id="default-study")]
    for template in load_persona_template_catalog().model_dump(mode="json")["templates"]:
        document = _study(run_id="template-run")
        document["population"]["personas"] = [{**template, "count": 8}]
        studies.append(pytest.param(document, id=template["persona_id"]))
    return studies


@pytest.mark.parametrize("document", _template_studies())
def test_the_default_study_and_every_packaged_template_are_accepted_and_prepare(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _agrees_with_planning(document, monkeypatch) is True


@pytest.mark.parametrize(
    ("schema", "path", "text", "append", "accepted"),
    [
        *((4, OCCUPATION, text, False, False) for text in REPORTED_TEXT),
        (4, OCCUPATION, "Ph.D./postdoc researcher", False, False),
        (4, OCCUPATION, "password", False, False),
        (4, OCCUPATION, "Food/retail planner", False, True),
        (4, OCCUPATION, "24/7 and/or on call", False, True),
        (4, BACKGROUND, "Works nights (/weekends too).", False, False),
        (4, BACKGROUND, "Blogs at tastynotes.example/recipes on Sundays.", False, False),
        (4, BACKGROUND, "Balances home/work routines and/or weekend plans.", False, True),
        (4, DISPLAY_NAME, "Ana B./C.", False, False),
        (4, DISPLAY_NAME, "Ana and/or Mina", False, True),
        (4, INTERESTS, "Node.js/React", True, False),
        (4, INTERESTS, "home/work", True, True),
        (4, TARGET_INTERESTS, "Node.js/React", True, False),
        (4, TARGET_INTERESTS, "password", True, False),
        (4, TARGET_INTERESTS, "food/retail", True, True),
        (3, OCCUPATION, "Node.js/React developer", False, False),
        (3, OCCUPATION, "Food/retail planner", False, True),
        (3, CAMPAIGN_NAME, "Node.js/React launch", False, False),
        (3, CAMPAIGN_NAME, "Food/retail launch", False, True),
    ],
)
def test_acceptance_takes_exactly_what_rules_mode_planning_takes(
    schema: int,
    path: FieldPath,
    text: str,
    append: bool,
    accepted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _study() if schema == 4 else draft_v3_data()
    document = _changed(source, path, text, append=append)
    assert _agrees_with_planning(document, monkeypatch, schema=schema) is accepted


def test_every_refused_field_is_one_the_error_machinery_accepts() -> None:
    persona_fields = workbench_validation._PERSONA_TEXT_FIELDS.values()
    campaign_fields = workbench_validation._CAMPAIGN_TEXT_FIELDS.values()
    fields = {
        *(f"population.personas.0.{name}" for name in persona_fields),
        *(f"scenario.campaign.{name}" for name in campaign_fields),
        "population.personas.0",
        "scenario.campaign",
    }
    for field in fields:
        assert all(
            part.isdecimal() or part in _WORKBENCH_FIELD_COMPONENTS for part in field.split(".")
        ), field
