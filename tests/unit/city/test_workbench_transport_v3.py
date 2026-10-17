"""Exact schema-3 browser projection of already verified V9 city evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from adlife.city import workbench_transport as transport
from adlife.city.run_store import StoredCityRun
from tests.integration.test_city_cognition_artifacts import _evaluation, _manifest, _prepared
from tests.unit.city.test_workbench_transport import _stored_workbench_run


@pytest.fixture(scope="module")
def stored_v9(tmp_path_factory: pytest.TempPathFactory) -> StoredCityRun:
    prepared = _prepared(mode="mock")
    evaluation = _evaluation(prepared)
    return StoredCityRun(
        manifest=_manifest(prepared, evaluation),
        pack=prepared.pack,
        mobility=prepared.mobility,
        directory=tmp_path_factory.mktemp("v9-transport"),
        spatial_scenario=prepared.spatial_scenario,
        response_input=prepared.response_input,
        workbench_input=prepared.workbench_input,
        cognition_input=prepared.cognition_input,
        cognition_evaluation=evaluation,
    )


def _v3_model():
    model = getattr(transport, "WorkbenchRunInputViewV3", None)
    assert model is not None, "V9 browser input projection is not implemented"
    return model


def test_v9_input_projection_preserves_population_and_complete_frozen_cognition(stored_v9) -> None:
    model = _v3_model()
    view = transport.build_workbench_run_input_view(stored_v9)
    assert type(view) is model
    document = view.model_dump(mode="json")
    source = stored_v9.workbench_input.draft
    assert document["run_schema_version"] == 9
    assert document["workbench_input_schema_version"] == 3
    assert document["population"] == source.population.model_dump(mode="json")
    assert document["cognition"] == source.cognition.model_dump(mode="json")
    assert document["settings"]["seed"] == str(source.settings.seed)
    assert "cohort" not in document
    assert "provider_profile" not in document
    assert str(stored_v9.directory) not in view.model_dump_json()
    assert (
        TypeAdapter(transport.WorkbenchRunInputViewDocument).validate_json(view.model_dump_json())
        == view
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("run_schema_version", True),
        ("run_schema_version", 9.0),
        ("run_schema_version", "9"),
        ("run_schema_version", 10),
        ("workbench_input_schema_version", True),
        ("workbench_input_schema_version", 3.0),
        ("workbench_input_schema_version", "3"),
        ("workbench_input_schema_version", 4),
    ],
)
def test_v3_view_union_refuses_coerced_and_future_versions(stored_v9, field, invalid) -> None:
    _v3_model()
    document = transport.build_workbench_run_input_view(stored_v9).model_dump(mode="python")
    document[field] = invalid
    with pytest.raises(ValidationError):
        TypeAdapter(transport.WorkbenchRunInputViewDocument).validate_python(document)


@pytest.mark.parametrize("mutation", ["missing-cognition", "wrong-mode", "wrong-input-version"])
def test_v9_projection_refuses_incomplete_or_mismatched_cognition_binding(
    stored_v9, mutation
) -> None:
    _v3_model()
    if mutation == "missing-cognition":
        candidate = replace(stored_v9, cognition_input=None)
    elif mutation == "wrong-mode":
        candidate = replace(
            stored_v9,
            cognition_input=BaseModel.model_copy(
                stored_v9.cognition_input, update={"mode": "rules"}
            ),
        )
    else:
        candidate = replace(
            stored_v9,
            manifest=BaseModel.model_copy(
                stored_v9.manifest, update={"workbench_input_schema_version": 2}
            ),
        )
    with pytest.raises(ValueError, match=r"workbench|cognition"):
        transport.build_workbench_run_input_view(candidate)


def test_v7_projection_document_stays_unchanged_and_remains_in_exact_union(tmp_path: Path) -> None:
    _v3_model()
    view = transport.build_workbench_run_input_view(_stored_workbench_run(tmp_path))
    assert type(view) is transport.WorkbenchRunInputView
    assert "cognition" not in view.model_dump()
    assert (
        TypeAdapter(transport.WorkbenchRunInputViewDocument).validate_json(view.model_dump_json())
        == view
    )
