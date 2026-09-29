import json
from pathlib import Path

import pytest

from adlife.city.loader import CityPackError, load_city_pack
from tests.unit.city.test_city_pack import pack_data, pack_v2_data


def test_load_json_pack_and_refuse_invalid_inputs(tmp_path: Path) -> None:
    path = tmp_path / "city.json"
    path.write_text(json.dumps(pack_data()), encoding="utf-8")
    assert load_city_pack(path).city_id == "sample-city"

    path.write_bytes(b"\xff")
    with pytest.raises(CityPackError, match="UTF-8"):
        load_city_pack(path)

    path.write_text("{", encoding="utf-8")
    with pytest.raises(CityPackError, match="JSON"):
        load_city_pack(path)


def test_large_pack_refused_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "city.json"
    path.write_bytes(b" " * (4_194_304 + 1))
    with pytest.raises(CityPackError, match="too large"):
        load_city_pack(path)


def test_bundled_demo_is_explicitly_fictional() -> None:
    pack = load_city_pack(None)
    assert "fictional" in pack.name.lower()
    assert "fictional" in pack.attribution.lower()


def test_loads_bounded_v2_pack_without_upgrading_v1(tmp_path: Path) -> None:
    v2_path = tmp_path / "city-v2.json"
    v2_path.write_text(json.dumps(pack_v2_data()), encoding="utf-8")
    v2 = load_city_pack(v2_path)
    v1 = load_city_pack(None)
    assert v2.schema_version == 2
    assert v2.time_zone == "Etc/UTC"
    assert v1.schema_version == 1
    assert not hasattr(v1, "time_zone")


@pytest.mark.parametrize("version", [True, 1.0, 3])
def test_loader_refuses_ambiguous_or_unknown_pack_versions(tmp_path: Path, version: object) -> None:
    data = pack_v2_data()
    data["schema_version"] = version
    path = tmp_path / "city.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CityPackError, match=r"schema|version"):
        load_city_pack(path)
