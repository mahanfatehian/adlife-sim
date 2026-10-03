from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType

import pytest


def _loader_module() -> ModuleType:
    try:
        return importlib.import_module("adlife.city.study_loader")
    except ModuleNotFoundError:
        pytest.fail("bounded spatial study definition loader is not implemented")


def study_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "study_id": "sample-study",
        "design": "paired-contrast",
        "analysis_scope": "attention",
        "pairs": [
            {
                "seed": 0,
                "control_run_id": "control-0",
                "treatment_run_id": "treatment-0",
            },
            {
                "seed": 1,
                "control_run_id": "control-1",
                "treatment_run_id": "treatment-1",
            },
        ],
    }


def _write_study(path: Path, document: object | None = None) -> None:
    path.write_text(
        json.dumps(study_document() if document is None else document),
        encoding="utf-8",
    )


def test_loader_reads_one_valid_local_definition_in_canonical_seed_order(
    tmp_path: Path,
) -> None:
    document = study_document()
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    pairs.reverse()
    path = tmp_path / "مطالعه.json"
    _write_study(path, document)

    loaded = _loader_module().load_spatial_study_definition(path)

    assert loaded.schema_version == 1
    assert loaded.study_id == "sample-study"
    assert loaded.design == "paired-contrast"
    assert loaded.analysis_scope == "attention"
    assert tuple(pair.seed for pair in loaded.pairs) == (0, 1)


def test_loader_accepts_an_exactly_64_kib_valid_document(tmp_path: Path) -> None:
    loader = _loader_module()
    assert loader.MAX_SPATIAL_STUDY_BYTES == 65_536
    encoded = json.dumps(study_document()).encode("utf-8")
    assert len(encoded) < loader.MAX_SPATIAL_STUDY_BYTES
    path = tmp_path / "study.json"
    path.write_bytes(encoded + b" " * (loader.MAX_SPATIAL_STUDY_BYTES - len(encoded)))

    loaded = loader.load_spatial_study_definition(path)

    assert loaded.study_id == "sample-study"


def test_loader_refuses_oversized_input_before_parsing_or_echoing_it(
    tmp_path: Path,
) -> None:
    loader = _loader_module()
    secret = "=".join(("_".join(("api", "key")), "".join(("top", "secret", "123"))))
    path = tmp_path / "study.json"
    path.write_bytes((secret + "x" * loader.MAX_SPATIAL_STUDY_BYTES).encode("utf-8"))

    with pytest.raises(loader.SpatialStudyDefinitionError, match="too large") as caught:
        loader.load_spatial_study_definition(path)

    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    ("contents", "diagnostic"),
    [
        (b"\xff", "spatial study definition must be UTF-8"),
        (b"{", "spatial study definition failed schema or version validation"),
        (b"[]", "spatial study definition failed schema or version validation"),
        (
            b'{"schema_version":99}',
            "spatial study definition failed schema or version validation",
        ),
    ],
)
def test_loader_refuses_malformed_documents_with_bounded_diagnostics(
    tmp_path: Path,
    contents: bytes,
    diagnostic: str,
) -> None:
    loader = _loader_module()
    path = tmp_path / "study.json"
    path.write_bytes(contents)

    with pytest.raises(loader.SpatialStudyDefinitionError) as caught:
        loader.load_spatial_study_definition(path)

    assert str(caught.value) == diagnostic


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_loader_refuses_unreadable_paths_without_exposing_them(
    tmp_path: Path,
    kind: str,
) -> None:
    loader = _loader_module()
    secret = "sk-live-abcdefghij"
    path = tmp_path / secret / "study.json"
    if kind == "directory":
        path.mkdir(parents=True)

    with pytest.raises(
        loader.SpatialStudyDefinitionError,
        match="could not be read",
    ) as caught:
        loader.load_spatial_study_definition(path)

    message = str(caught.value)
    assert secret not in message
    assert str(path) not in message
