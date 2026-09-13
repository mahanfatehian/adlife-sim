from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.config.loader import load_app_config


def test_loads_minimal_config(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation:\n  days: 3\n  tick_minutes: 15\n"
        "  population_size: 20\n  seed: 42\nprovider:\n  mode: mock\n",
        encoding="utf-8",
    )
    config = load_app_config(path)
    assert config.simulation.population_size == 20
    assert config.provider.mode == "mock"


def test_rejects_population_above_limit(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation:\n  days: 3\n  tick_minutes: 15\n"
        "  population_size: 31\n  seed: 42\nprovider:\n  mode: mock\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="less than or equal to 30"):
        load_app_config(path)


def test_loads_documented_defaults(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation: {}\nprovider: {}\n",
        encoding="utf-8",
    )

    config = load_app_config(path)

    assert config.model_dump(mode="json") == {
        "schema_version": 1,
        "simulation": {
            "days": 3,
            "tick_minutes": 15,
            "population_size": 20,
            "seed": 42,
            "cognition_mode": "rules",
            "max_cognition_per_agent": 6,
            "max_cognition_total": 180,
        },
        "provider": {
            "mode": "mock",
            "base_url": None,
            "model": "mock-v1",
            "timeout_seconds": 60.0,
            "retries": 2,
        },
    }


def test_rejects_unknown_configuration_fields(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation: {}\nprovider:\n  mode: mock\n  unsupported: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_app_config(path)


def test_local_provider_uses_loopback_default_url(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation: {}\nprovider:\n  mode: local\n",
        encoding="utf-8",
    )

    config = load_app_config(path)

    assert str(config.provider.base_url) == "http://127.0.0.1:11434/v1"


def test_remote_provider_requires_base_url(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation: {}\nprovider:\n  mode: remote\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="remote provider requires base_url"):
        load_app_config(path)


def test_validation_error_includes_source_path(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 2\nsimulation: {}\nprovider: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        load_app_config(path)

    assert str(path) in str(error.value)


def test_rejects_non_mapping_document(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a mapping"):
        load_app_config(path)


def test_rejects_unsafe_yaml_tags(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text("!!python/tuple [1, 2]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="could not be parsed"):
        load_app_config(path)


def test_preserves_environment_variable_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADLIFE_TEST_MODEL", "secret-model")
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation: {}\nprovider:\n"
        "  mode: mock\n  model: ${ADLIFE_TEST_MODEL}\n",
        encoding="utf-8",
    )

    config = load_app_config(path)

    assert config.provider.model == "${ADLIFE_TEST_MODEL}"


def test_loaded_settings_are_immutable(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation: {}\nprovider: {}\n",
        encoding="utf-8",
    )
    config = load_app_config(path)

    with pytest.raises(ValidationError, match="Instance is frozen"):
        config.simulation.days = 4
