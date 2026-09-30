"""Version gates reject equal-valued JSON booleans and floats, without coercion."""

import json

import pytest
from pydantic import ValidationError

from adlife.config.models import AppConfig, ProviderSettings, SimulationSettings
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import DomainModel
from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from tests.unit.city.test_city_pack import pack_data, pack_v2_data


@pytest.fixture
def versioned_models(
    valid_scenario: Scenario, run_manifest: RunManifest
) -> tuple[DomainModel, ...]:
    return (
        AppConfig(
            schema_version=1,
            simulation=SimulationSettings(),
            provider=ProviderSettings(),
        ),
        valid_scenario,
        run_manifest,
        DomainEvent(
            event_id="run-schema:event-00000000",
            run_id="run-schema",
            simulated_minute=0,
            sequence=0,
            event_type=EventType.RUN_STARTED,
            source=EventSource.RULE,
            payload={},
        ),
    )


@pytest.mark.parametrize("index", range(4), ids=["config", "scenario", "manifest", "event"])
@pytest.mark.parametrize("value", [True, 1.0], ids=["boolean", "float"])
@pytest.mark.parametrize("wire", [False, True], ids=["copy", "json"])
def test_schema_versions_do_not_coerce_equal_valued_wrong_types(
    versioned_models: tuple[DomainModel, ...], index: int, value: object, wire: bool
) -> None:
    model = versioned_models[index]
    with pytest.raises(ValidationError, match="schema_version"):
        if wire:
            data = model.model_dump(mode="json") | {"schema_version": value}
            type(model).model_validate_json(json.dumps(data))
        else:
            model.model_copy(update={"schema_version": value})


def test_integer_schema_version_and_defaults_still_round_trip(
    versioned_models: tuple[DomainModel, ...],
) -> None:
    for model in versioned_models:
        assert type(model.schema_version) is int
        assert model.schema_version == 1
        assert model.model_copy(update={"schema_version": 1}) == model
        assert type(model).model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("value", [True, 1.0], ids=["boolean", "float"])
def test_city_pack_dispatch_never_coerces_schema_versions(value: object) -> None:
    from adlife.core.domain import city

    parser = getattr(city, "parse_city_pack_json", None)
    assert parser is not None, "parse_city_pack_json is not implemented"
    for document in (pack_data(), pack_v2_data()):
        document["schema_version"] = value
        with pytest.raises((ValidationError, ValueError), match="schema_version"):
            parser(json.dumps(document))


@pytest.mark.parametrize("value", [True, 1.0], ids=["boolean", "float"])
def test_city_place_set_dispatch_never_coerces_schema_versions(value: object) -> None:
    try:
        module = __import__("adlife.core.domain.city_places", fromlist=["unused"])
    except ModuleNotFoundError:
        pytest.fail("city place-set contract is not implemented")
    from tests.unit.city.test_city_places import place_set_data

    document = place_set_data()
    document["schema_version"] = value
    with pytest.raises((ValidationError, ValueError), match="schema_version"):
        module.parse_city_place_set_json(json.dumps(document))


@pytest.mark.parametrize("value", [True, 1.0], ids=["boolean", "float"])
def test_spatial_campaign_dispatch_never_coerces_schema_versions(value: object) -> None:
    try:
        module = __import__("adlife.core.domain.spatial_campaign", fromlist=["unused"])
    except ModuleNotFoundError:
        pytest.fail("spatial campaign contract is not implemented")
    from tests.unit.city.test_spatial_campaign_contract import spatial_scenario_data

    document = spatial_scenario_data()
    document["schema_version"] = value
    with pytest.raises((ValidationError, ValueError), match="schema_version"):
        module.parse_spatial_campaign_scenario_json(json.dumps(document))
