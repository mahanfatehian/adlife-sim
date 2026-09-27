import pytest
from pydantic import ValidationError

from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ExposureCount


def test_manifest_parameters_cannot_change_after_validation(run_manifest: RunManifest) -> None:
    parameters = {"notice_scale": 1.0}
    manifest = run_manifest.model_copy(update={"parameters": parameters})
    before = manifest.model_dump_json()
    parameters["notice_scale"] = 2.0
    assert manifest.parameters["notice_scale"] == 1.0
    with pytest.raises(TypeError):
        manifest.parameters["notice_scale"] = 2.0
    assert manifest.model_dump_json() == before
    assert RunManifest.model_validate_json(before) == manifest


@pytest.mark.parametrize("value", [True, "1.0", float("nan"), float("inf")])
def test_manifest_parameters_do_not_coerce_invalid_values(
    run_manifest: RunManifest, value: object
) -> None:
    with pytest.raises(ValidationError):
        run_manifest.model_copy(update={"parameters": {"notice_scale": value}})


@pytest.mark.parametrize("field", ["aware_campaign_ids", "exposure_counts", "memories"])
def test_initial_state_campaign_references_must_exist(valid_scenario: Scenario, field: str) -> None:
    from adlife.core.domain.state import Memory

    values = {
        "aware_campaign_ids": frozenset({"missing-campaign"}),
        "exposure_counts": (
            ExposureCount(campaign_id="missing-campaign", channel="mobile-feed", count=1),
        ),
        "memories": (
            Memory(
                memory_id="old-memory",
                created_minute=0,
                kind="advertising",
                summary="A fictional advertisement",
                salience=0.5,
                campaign_id="missing-campaign",
                caused_by_event_ids=("old-event",),
            ),
        ),
    }
    state = valid_scenario.initial_states[0].model_copy(update={field: values[field]})
    with pytest.raises(ValidationError, match="campaign"):
        valid_scenario.model_copy(
            update={"initial_states": (state, *valid_scenario.initial_states[1:])}
        )
