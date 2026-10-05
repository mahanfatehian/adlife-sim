from __future__ import annotations

import gc
import tracemalloc
import weakref
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from adlife.city.run_store import CityRunStore
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from tests.integration.test_city_spatial_study import definition
from tests.unit.city.test_city_analysis import _spatial_run


def test_next_run_load_happens_only_after_previous_projection_is_released(
    tmp_path: Path, monkeypatch
):
    from adlife.city import studies

    for seed in range(2):
        _spatial_run(tmp_path, f"control-{seed}", seed=seed)
        _spatial_run(tmp_path, f"treatment-{seed}", seed=seed)
    store = CityRunStore(tmp_path)
    real_load = store.load
    previous = None
    loaded_ids = []

    def tracking_load(run_id):
        nonlocal previous
        gc.collect()
        assert previous is None or previous() is None, "previous full run survives next load"
        stored = real_load(run_id)
        scenario = SpatialCampaignScenario.model_validate(stored.spatial_scenario.model_dump())
        previous = weakref.ref(scenario)
        loaded_ids.append(run_id)
        return replace(stored, spatial_scenario=scenario)

    monkeypatch.setattr(store, "load", tracking_load)
    result = studies.analyze_stored_spatial_study(store, definition(same=False))
    assert result.a_a_status == "exact-zero-verified"
    assert loaded_ids == ["control-0", "treatment-0", "control-1", "treatment-1"]
    gc.collect()
    assert previous() is None


def test_same_run_aa_loads_each_seed_only_once(tmp_path: Path, monkeypatch):
    from adlife.city import studies

    for seed in range(2):
        _spatial_run(tmp_path, f"control-{seed}", seed=seed, response=True)
    store = CityRunStore(tmp_path)
    real_load = store.load
    loaded_ids = []

    def load(run_id):
        assert run_id not in loaded_ids
        loaded_ids.append(run_id)
        return real_load(run_id)

    monkeypatch.setattr(store, "load", load)
    result = studies.analyze_stored_spatial_study(store, definition(response=True))
    assert len(result.statistics) == 62
    assert loaded_ids == ["control-0", "control-1"]


def test_peak_memory_has_constant_factor_for_instrumented_large_loaded_runs(tmp_path, monkeypatch):
    from adlife.city import studies

    for seed in range(8):
        _spatial_run(tmp_path, f"control-{seed}", seed=seed)
    store = CityRunStore(tmp_path)
    real_load = store.load

    def load(run_id):
        stored = real_load(run_id)
        # A contained probe models a large verified run payload and is deliberately
        # absent from model fields/public receipts. Keeping the full source retains it.
        object.__setattr__(stored.spatial_scenario, "_memory_probe", bytearray(8 * 1024 * 1024))
        return stored

    monkeypatch.setattr(store, "load", load)
    peaks = []
    for n in (2, 8):
        gc.collect()
        tracemalloc.start()
        try:
            result = studies.analyze_stored_spatial_study(store, definition(n=n))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(result.pairs) == n
        peaks.append(peak)
        del result
    print(f"study measured peaks: 2 pairs {peaks[0]} bytes; 8 pairs {peaks[1]} bytes")
    assert peaks[1] <= peaks[0] * 1.8 + 2 * 1024 * 1024
    assert peaks[1] < 32 * 1024 * 1024


def test_maximum_100_pair_20_campaign_result_size_and_projection_performance(tmp_path, monkeypatch):
    from adlife.city import studies
    from adlife.city.analysis import (
        metrics_for_stored_city_run,
        response_metrics_for_stored_city_run,
    )
    from adlife.city.runs import create_city_run
    from adlife.core.domain.serialization import canonical_json
    from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
    from adlife.core.experiments._spatial_study_validation import document_sha256
    from adlife.core.experiments.spatial_study import SpatialStudyArmReceipt, SpatialStudyResult
    from adlife.core.simulation._validation import revalidate_model
    from tests.unit.city.test_city_pack import load_pack, pack_data
    from tests.unit.city.test_spatial_opportunity import _phone, _scenario
    from tests.unit.city.test_spatial_response import _response_input

    pack = load_pack(pack_data())
    initial = _scenario(
        pack, [_phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2, probability=1.0)]
    )
    campaign = initial.campaigns[0]
    scenario = SpatialCampaignScenario.model_validate(
        {
            **initial.model_dump(),
            "campaigns": tuple(
                campaign.model_copy(update={"campaign_id": f"fictional-{index:02d}-" + "x" * 60})
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
        from hashlib import sha256

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

    # A structural maximum probe uses real verified scalar evidence and synthetic
    # compact per-seed receipts. It measures result construction, not run generation.
    monkeypatch.setattr(studies, "_load_projection", projection)
    started = perf_counter()
    result = studies.analyze_stored_spatial_study(
        CityRunStore(tmp_path), definition(response=True, n=100)
    )
    elapsed = perf_counter() - started
    serialized_bytes = len(canonical_json(result).encode("utf-8"))
    print(f"maximum spatial result: {serialized_bytes} bytes; {elapsed:.2f}s")
    assert len(result.pairs) == 100
    assert all(len(pair.scalars) == 328 for pair in result.pairs)
    assert len(result.statistics) == 328
    assert result.evidence_tier == "full-protocol-50-or-more-seeds"
    assert serialized_bytes < 33_554_432
    assert elapsed < 60.0
    assert revalidate_model(result, SpatialStudyResult, label="study result") is result
