from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.city.test_spatial_study_loader import (
    _loader_module,
    _write_study,
    study_document,
)

_SCHEMA_DIAGNOSTIC = "spatial study definition failed schema or version validation"


def _assert_schema_refusal(path: Path, *, forbidden: str | None = None) -> None:
    loader = _loader_module()
    with pytest.raises(loader.SpatialStudyDefinitionError) as caught:
        loader.load_spatial_study_definition(path)
    message = str(caught.value)
    assert message == _SCHEMA_DIAGNOSTIC
    if forbidden is not None:
        assert forbidden not in message


@pytest.mark.parametrize("duplicate", ["schema_version", "seed"])
def test_loader_refuses_duplicate_json_keys_without_choosing_one(
    tmp_path: Path,
    duplicate: str,
) -> None:
    document = json.dumps(study_document())
    if duplicate == "schema_version":
        document = document.replace(
            '"schema_version": 1',
            '"schema_version": 1, "schema_version": 1',
            1,
        )
    else:
        document = document.replace('"seed": 0', '"seed": 0, "seed": 1', 1)
    path = tmp_path / "study.json"
    path.write_text(document, encoding="utf-8")

    _assert_schema_refusal(path)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_loader_refuses_boolean_coerced_and_unknown_schema_versions(
    tmp_path: Path,
    version: object,
) -> None:
    document = study_document()
    document["schema_version"] = version
    path = tmp_path / "study.json"
    _write_study(path, document)

    _assert_schema_refusal(path)


def test_loader_refuses_a_boolean_seed_instead_of_coercing_it(tmp_path: Path) -> None:
    document = study_document()
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    first = pairs[0]
    assert isinstance(first, dict)
    first["seed"] = True
    path = tmp_path / "study.json"
    _write_study(path, document)

    _assert_schema_refusal(path)


@pytest.mark.parametrize("constant", [float("nan"), float("inf"), float("-inf")])
def test_loader_refuses_nonfinite_json_numbers(
    tmp_path: Path,
    constant: float,
) -> None:
    document = study_document()
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    first = pairs[0]
    assert isinstance(first, dict)
    first["seed"] = constant
    path = tmp_path / "study.json"
    path.write_text(json.dumps(document, allow_nan=True), encoding="utf-8")

    _assert_schema_refusal(path)


def test_loader_refuses_excessive_json_nesting_with_a_safe_diagnostic(
    tmp_path: Path,
) -> None:
    secret = "sk-live-abcdefghij"
    nested = "[" * 40 + json.dumps(secret) + "]" * 40
    document = (
        '{"schema_version":1,"study_id":"sample-study",'
        '"design":"paired-contrast","analysis_scope":"attention","pairs":'
        f"{nested}}}"
    )
    path = tmp_path / "study.json"
    path.write_text(document, encoding="utf-8")

    _assert_schema_refusal(path, forbidden=secret)


@pytest.mark.parametrize("field", ["study_id", "control_run_id", "treatment_run_id"])
def test_credential_shaped_identifiers_are_refused_without_echo(
    tmp_path: Path,
    field: str,
) -> None:
    secret = "sk-live-abcdefghij"
    document = study_document()
    if field == "study_id":
        document[field] = secret
    else:
        pairs = document["pairs"]
        assert isinstance(pairs, list)
        first = pairs[0]
        assert isinstance(first, dict)
        first[field] = secret
    path = tmp_path / "study.json"
    _write_study(path, document)

    _assert_schema_refusal(path, forbidden=secret)


def test_explicit_input_symlink_is_read_like_other_local_input_loaders(
    tmp_path: Path,
) -> None:
    target = tmp_path / "study.json"
    _write_study(target)
    link = tmp_path / "study-link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("this account cannot create file symlinks")

    loaded = _loader_module().load_spatial_study_definition(link)

    assert loaded.study_id == "sample-study"


def test_missing_secret_bearing_path_is_never_reflected(tmp_path: Path) -> None:
    loader = _loader_module()
    secret = "sk-live-abcdefghij"
    path = tmp_path / secret / "study.json"

    with pytest.raises(loader.SpatialStudyDefinitionError) as caught:
        loader.load_spatial_study_definition(path)

    assert str(caught.value) == "spatial study definition could not be read"
    assert secret not in str(caught.value)
