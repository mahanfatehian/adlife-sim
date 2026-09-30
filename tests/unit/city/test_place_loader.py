import importlib
import json
from pathlib import Path

import pytest

from tests.unit.city.test_city_places import place_set_data


def _loader_module():
    try:
        return importlib.import_module("adlife.city.place_loader")
    except ModuleNotFoundError:
        pytest.fail("bounded city place loader is not implemented")


def test_loader_reads_a_valid_local_place_set(tmp_path: Path) -> None:
    path = tmp_path / "places.json"
    path.write_text(json.dumps(place_set_data()), encoding="utf-8")
    loaded = _loader_module().load_city_place_set(path)
    assert loaded.name == "Fictional weekday places"
    assert len(loaded.places) == 4


def test_loader_refuses_oversized_input_without_echoing_contents(tmp_path: Path) -> None:
    loader = _loader_module()
    path = tmp_path / "places.json"
    secret = "api_key=topsecret123"
    path.write_bytes((secret + "x" * loader.MAX_CITY_PLACE_SET_BYTES).encode())
    with pytest.raises(loader.CityPlaceSetError, match="too large") as caught:
        loader.load_city_place_set(path)
    assert secret not in str(caught.value)


@pytest.mark.parametrize("contents", [b"\xff", b"[]", b'{"schema_version":99}'])
def test_loader_refuses_malformed_documents_with_bounded_diagnostics(
    tmp_path: Path, contents: bytes
) -> None:
    loader = _loader_module()
    path = tmp_path / "places.json"
    path.write_bytes(contents)
    with pytest.raises(loader.CityPlaceSetError) as caught:
        loader.load_city_place_set(path)
    assert str(caught.value) in {
        "city place set must be UTF-8",
        "city place set failed schema or version validation",
    }


def test_loader_refuses_missing_file_without_exposing_path(tmp_path: Path) -> None:
    loader = _loader_module()
    missing = tmp_path / "private" / "places.json"
    with pytest.raises(loader.CityPlaceSetError, match="could not be read") as caught:
        loader.load_city_place_set(missing)
    assert str(missing) not in str(caught.value)
