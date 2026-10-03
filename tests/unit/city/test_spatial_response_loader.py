from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.unit.city.test_spatial_response_contract import spatial_response_data


def _loader_module():
    try:
        return importlib.import_module("adlife.city.response_loader")
    except ModuleNotFoundError:
        pytest.fail("bounded spatial response loader is not implemented")


def test_loader_reads_a_valid_local_response_input(tmp_path: Path) -> None:
    path = tmp_path / "spatial-response.json"
    path.write_text(json.dumps(spatial_response_data()), encoding="utf-8")

    loaded = _loader_module().load_spatial_response_input(path)

    assert loaded.schema_version == 1
    assert len(loaded.profiles) == 2
    assert len(loaded.campaigns) == 2
    assert len(loaded.initial_states) == 4


def test_loader_refuses_oversized_input_without_echoing_contents(tmp_path: Path) -> None:
    loader = _loader_module()
    path = tmp_path / "spatial-response.json"
    secret = "api_key=topsecret123"
    path.write_bytes((secret + "x" * loader.MAX_SPATIAL_RESPONSE_INPUT_BYTES).encode())

    with pytest.raises(loader.SpatialResponseInputError, match="too large") as caught:
        loader.load_spatial_response_input(path)

    assert secret not in str(caught.value)


@pytest.mark.parametrize("contents", [b"\xff", b"[]", b'{"schema_version":99}'])
def test_loader_refuses_malformed_documents_with_bounded_diagnostics(
    tmp_path: Path, contents: bytes
) -> None:
    loader = _loader_module()
    path = tmp_path / "spatial-response.json"
    path.write_bytes(contents)

    with pytest.raises(loader.SpatialResponseInputError) as caught:
        loader.load_spatial_response_input(path)

    assert str(caught.value) in {
        "spatial response input must be UTF-8",
        "spatial response input failed schema or version validation",
    }


def test_loader_refuses_missing_file_without_exposing_path(tmp_path: Path) -> None:
    loader = _loader_module()
    missing = tmp_path / "private" / "spatial-response.json"

    with pytest.raises(loader.SpatialResponseInputError, match="could not be read") as caught:
        loader.load_spatial_response_input(missing)

    assert str(missing) not in str(caught.value)
