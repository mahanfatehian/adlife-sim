from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.config.loader import load_app_config
from adlife.config.models import ProviderSettings, SimulationSettings


@pytest.mark.parametrize("value", [True, "3", 3.0])
def test_simulation_integer_settings_do_not_coerce(value: object) -> None:
    with pytest.raises(ValidationError):
        SimulationSettings(days=value)


def test_configuration_copy_revalidates_limits() -> None:
    with pytest.raises(ValidationError):
        SimulationSettings().model_copy(update={"days": 8})
    with pytest.raises(ValidationError):
        ProviderSettings().model_copy(update={"retries": 100})


@pytest.mark.parametrize(
    "document",
    [
        "schema_version: 1\nsimulation: {}\nprovider: {unexpected: opaque-private-value}\n",
        "schema_version: 1\nsimulation: {}\nprovider: [opaque-private-value\n",
    ],
)
def test_configuration_errors_do_not_echo_input(tmp_path: Path, document: str) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_app_config(path)
    assert "opaque-private-value" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "document",
    [
        "schema_version: 1\nsimulation: &settings {}\nprovider: *settings\n",
        "schema_version: 1\nsimulation: {days: 3, days: 4}\nprovider: {}\n",
        "schema_version: 1\nsimulation: {}\nprovider: {}\n#" + "x" * 1_048_576,
        "schema_version: 1\nsimulation: " + "[" * 100 + "0" + "]" * 100,
    ],
    ids=["alias", "duplicate-key", "oversized", "overdeep"],
)
def test_ambiguous_or_excessive_yaml_is_refused(tmp_path: Path, document: str) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ValueError, match=r"YAML|configuration could not be parsed"):
        load_app_config(path)


def test_invalid_utf8_is_a_sanitized_input_error(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_bytes(b"\xffopaque-private-value")
    with pytest.raises(ValueError, match="UTF-8") as error:
        load_app_config(path)
    assert "opaque-private-value" not in str(error.value)
