"""Maximum report shape from real verified scalar evidence and compact seed receipts."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from adlife.city import studies
from adlife.city.analysis import metrics_for_stored_city_run, response_metrics_for_stored_city_run
from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.experiments._spatial_study_validation import document_sha256
from adlife.core.experiments.spatial_study import SpatialStudyArmReceipt
from tests.integration.test_city_spatial_study import definition
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input


def maximum_report_result(tmp_path, monkeypatch):
    pack = load_pack(pack_data())
    initial = _scenario(pack, [_phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2)])
    campaign = initial.campaigns[0]
    scenario = SpatialCampaignScenario.model_validate(
        {
            **initial.model_dump(),
            "campaigns": tuple(
                campaign.model_copy(
                    update={
                        "campaign_id": f"fictional-{index:02d}-" + "x" * 60,
                    }
                )
                for index in range(20)
            ),
            "placements": tuple(
                initial.placements[0].model_copy(
                    update={
                        "placement_id": f"phone-{index:02d}",
                        "campaign_id": f"fictional-{index:02d}-" + "x" * 60,
                        "opportunity_probability_per_minute": 1.0 if index == 0 else 0.0,
                    }
                )
                for index in range(20)
            ),
        }
    )
    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="control-0",
        seed=0,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=_response_input(scenario, agent_ids=("person-001", "person-002")),
    )
    projected = studies._project(stored, response_scope=True)
    attention = metrics_for_stored_city_run(stored)
    response = response_metrics_for_stored_city_run(stored)
    manifest = stored.manifest
    del stored

    def projection(store, run_id, *, response_scope):
        assert response_scope
        seed = int(run_id.removeprefix("control-"))
        arm = projected.arm.model_dump(mode="python")
        arm["run_id"] = run_id
        changed_manifest = manifest.model_copy(update={"seed": seed, "run_id": run_id})
        arm["manifest_sha256"] = sha256(
            (canonical_json(changed_manifest) + "\n").encode("utf-8")
        ).hexdigest()
        arm["attention_metrics_sha256"] = document_sha256(
            attention.model_copy(update={"seed": seed})
        )
        arm["response"]["response_metrics_sha256"] = document_sha256(
            response.model_copy(update={"seed": seed})
        )
        return replace(projected, seed=seed, arm=SpatialStudyArmReceipt.model_validate(arm))

    with monkeypatch.context() as patch:
        patch.setattr(studies, "_load_projection", projection)
        return studies.analyze_stored_spatial_study(
            CityRunStore(tmp_path), definition(response=True, n=100)
        )
