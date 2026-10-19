import json
import warnings

import httpx
import pytest
from starlette.exceptions import StarletteDeprecationWarning

from adlife.city.web import create_city_app, create_city_view_context
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.domain.spatial_response import SpatialResponseInput
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import derive_spatial_response_metrics
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
)
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from adlife.core.simulation.spatial_response import (
    MAX_SPATIAL_RESPONSE_RECORDS,
    SpatialResponseEvaluation,
    evaluate_spatial_responses,
    spatial_response_state_document,
    summarize_spatial_response_artifact,
)
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)
from tests.unit.city.test_spatial_response import _response_input


def _response_view_case(
    *,
    seed: int = 42,
) -> tuple[
    CityMobility,
    SpatialCampaignScenario,
    SpatialOpportunityEvaluation,
    SpatialAttentionEvaluation,
    SpatialResponseInput,
    SpatialResponseEvaluation,
]:
    pack = load_pack(pack_data())
    fixture_mobility = _mobility(pack, agent_count=2)
    simulation = CityMobility(
        pack,
        seed=seed,
        agent_count=2,
        days=1,
        places=fixture_mobility.places,
    )
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(
                windows=[{"start_minute": 0, "end_minute": 3}],
                probability=1.0,
                cap=3,
            ),
        ],
    )
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=seed)
    response_input = _response_input(
        scenario,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
    )
    responses = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
    )
    return simulation, scenario, opportunities, attention, response_input, responses


def _response_view_metrics(
    simulation: CityMobility,
    scenario: SpatialCampaignScenario,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    response_input: SpatialResponseInput,
    responses: SpatialResponseEvaluation,
    *,
    source_run_schema_version: int = 6,
):
    attention_metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=simulation.days,
        source_run_schema_version=source_run_schema_version,
    )
    response_metrics = derive_spatial_response_metrics(
        response_input,
        responses,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=attention_metrics,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
    )
    return attention_metrics, response_metrics


def client() -> httpx.AsyncClient:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=3, days=2)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url="http://127.0.0.1",
    )


@pytest.mark.asyncio
async def test_viewer_validation_errors_never_echo_query_values() -> None:
    secret = "Authorization: Bearer sk-never-echo-viewer-query-1234567890"
    async with client() as web:
        response = await web.get("/api/frame", params={"minute": secret})

    assert response.status_code == 422
    assert response.json() == {"detail": "request validation failed"}
    assert secret not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "forwarded_host"),
    [
        ("attacker.example", None),
        ("attacker.example:8765", None),
        ("localhost.attacker.example", None),
        ("attacker.example, localhost", None),
        ("", None),
        ("attacker.example", "localhost"),
    ],
)
async def test_city_api_refuses_untrusted_host_header_without_exposing_metadata(
    host: str,
    forwarded_host: str | None,
) -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=1, days=1)
    headers = {"Host": host}
    if forwarded_host is not None:
        headers["X-Forwarded-Host"] = forwarded_host
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url="http://127.0.0.1",
    ) as web:
        response = await web.get("/api/meta", headers=headers)

    assert response.status_code == 400
    assert simulation.pack.city_id not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://[::1]:8765",
    ],
)
async def test_city_api_accepts_loopback_host_headers_with_ports(base_url: str) -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=1, days=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url=base_url,
    ) as web:
        response = await web.get("/api/meta")

    assert response.status_code == 200
    assert response.json()["city_id"] == simulation.pack.city_id


def test_city_app_remains_usable_with_testclient_on_loopback() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient

    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=1, days=1)
    with TestClient(
        create_city_app(simulation),
        base_url="http://127.0.0.1:8765",
    ) as web:
        response = web.get("/api/meta")

    assert response.status_code == 200
    assert response.json()["city_id"] == simulation.pack.city_id


@pytest.mark.asyncio
async def test_api_exposes_exact_core_trace_and_pack_hash() -> None:
    async with client() as web:
        metadata = await web.get("/api/meta")
        city = await web.get("/api/city")
        frame = await web.get("/api/frame?minute=480")
    assert metadata.status_code == city.status_code == frame.status_code == 200
    assert metadata.json()["city_sha256"] == load_pack(pack_data()).fingerprint
    assert city.json()["city_id"] == "sample-city"
    assert frame.json() == CityMobility(
        load_pack(pack_data()), seed=42, agent_count=3, days=2
    ).frame_document(480)


@pytest.mark.asyncio
async def test_api_exposes_v2_geometry_timezone_and_provenance() -> None:
    simulation = CityMobility(load_pack_v2(pack_v2_data()), seed=42, agent_count=2, days=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        city = (await web.get("/api/city")).json()
    assert metadata["city_schema_version"] == 2
    assert metadata["time_zone"] == "Etc/UTC"
    assert metadata["source"]["dataset"] == "Fictional Grid"
    assert city["schema_version"] == 2
    assert city["roads"][0]["shape"] == [{"longitude": 0.005, "latitude": 0.004}]


@pytest.mark.asyncio
async def test_selected_route_is_core_computed_and_unknown_agents_are_refused() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=3, days=2)
    selected = simulation.agents[0].agent_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url="http://127.0.0.1",
    ) as web:
        route = await web.get(f"/api/frame?minute=480&agent_id={selected}")
        missing = await web.get("/api/frame?minute=480&agent_id=person-999")
    assert route.status_code == 200
    assert route.json() == simulation.frame_document(480, selected_agent_id=selected)
    assert missing.status_code == 400


@pytest.mark.asyncio
async def test_api_is_read_only_and_never_serves_arbitrary_paths() -> None:
    async with client() as web:
        assert (await web.post("/api/frame")).status_code == 405
        assert (await web.get("/api/frame?minute=2880")).status_code == 400
        assert (await web.get("/static/../../pyproject.toml")).status_code == 404
        assert (await web.get("/api/pack?path=pyproject.toml")).status_code == 404


@pytest.mark.asyncio
async def test_place_api_exposes_exact_core_inputs_and_assignments_read_only() -> None:
    places = mobility_place_set()
    simulation = CityMobility(
        load_pack_v2(pack_v2_data()),
        seed=42,
        agent_count=2,
        days=7,
        places=places,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url="http://127.0.0.1",
    ) as web:
        place_response = await web.get("/api/places")
        assignment_response = await web.get("/api/place-assignments")
        write = await web.post("/api/places", json={})
    assert place_response.status_code == assignment_response.status_code == 200
    assert place_response.json() == places.model_dump(mode="json")
    assert assignment_response.json() == simulation.place_assignment_document()
    assert write.status_code == 405


@pytest.mark.asyncio
async def test_place_api_is_explicitly_absent_for_legacy_mobility() -> None:
    async with client() as web:
        places = await web.get("/api/places")
        assignments = await web.get("/api/place-assignments")
    assert places.status_code == assignments.status_code == 404
    assert places.json() == assignments.json() == {"detail": "synthetic places not configured"}


@pytest.mark.asyncio
async def test_dashboard_is_offline_and_discloses_model_limitations() -> None:
    async with client() as web:
        html = await web.get("/")
        js = await web.get("/static/app.js")
        css = await web.get("/static/app.css")
    assert html.status_code == js.status_code == css.status_code == 200
    assert "Synthetic model" in html.text
    assert "not a prediction or forecast of real people, traffic, behavior, or sales" in html.text
    assert "Run inspector" in html.text
    assert "Current model minute" in html.text
    assert "Mobility observatory" not in html.text
    assert "A city in motion" not in html.text
    assert "1-MINUTE MOBILITY FRAMES" not in html.text
    assert "15-MIN ENGINE" not in html.text
    assert 'id="license-label"' in html.text
    assert 'byId("license-label").textContent = meta.license' in js.text
    assert 'id="route-summary"' in html.text
    assert "state.frame.route.node_ids" in js.text
    assert "road.shape" in js.text
    assert "state.frame.route.geometry" in js.text
    assert 'id="place-evidence" hidden' in html.text
    assert 'id="home-place"' in html.text
    assert 'id="work-place"' in html.text
    assert 'id="leisure-place"' in html.text
    assert 'id="model-label"' in html.text
    assert 'id="workbench-back-link" href="/" hidden' in html.text
    assert 'id="assumptions-panel"' in html.text
    assert 'id="assumptions-drawer"' in html.text
    assert 'id="assumptions-status"' in html.text
    assert 'id="assumptions-content" hidden' in html.text
    assert 'id="assumptions-seed"' in html.text
    assert 'id="assumptions-placements"' in html.text
    assert 'id="assumptions-workbench-input-sha256"' in html.text
    assert 'id="attention-panel"' in html.text
    assert 'id="attention-claim"' in html.text
    for list_id in (
        "opportunity-list",
        "attention-list",
        "response-list",
        "response-state-list",
    ):
        assert f'id="{list_id}" aria-live=' not in html.text
    assert 'id="metrics-panel"' in html.text
    assert 'id="metrics-claim"' in html.text
    assert 'id="metrics-overall-notice-rate-receipt"' in html.text
    assert 'fetchJson("/places")' in js.text
    assert 'fetchJson("/place-assignments")' in js.text
    assert 'fetchJson("/attention-summary")' in js.text
    assert 'fetchJson("/spatial-metrics")' in js.text
    assert 'endpoint: "/attention-events"' in js.text
    assert "function validatedApiBase()" in js.text
    assert "function validatedViewToken()" in js.text
    assert "function apiPath(relative)" in js.text
    assert '"/workbench-input"' in js.text
    assert "function validateWorkbenchInput" in js.text
    assert "function renderWorkbenchAssumptions" in js.text
    assert 'fetchJson("/workbench-input")' in js.text
    assert "const url = apiPath(relative);" in js.text
    assert '{ "X-AdLife-View-Token": viewToken }' in js.text
    assert 'fetch(url, { cache: "no-store", headers' in js.text
    assert 'evidencePageUrl("attention", next, offsets.attention)' in js.text
    assert "abortController.signal" in js.text
    assert "function schedulePlaybackStep(generation)" in js.text
    assert "setInterval(" not in js.text
    assert 'id="previous-day-button"' in html.text
    assert 'id="playback-speed"' in html.text
    assert 'id="zoom-reset-button"' in html.text
    assert 'id="map-text-summary"' in html.text
    assert "limit=${EVIDENCE_PAGE_SIZE}" in js.text
    assert "drawAttentionMarker" in js.text
    assert 'byId("model-label").textContent' in js.text
    assert "drawPlaceMarker" in js.text
    assert "state.placeAssignments" in js.text
    assert "list.replaceChildren()" not in js.text
    assert "http://" not in html.text and "https://" not in html.text
    assert "textContent" in js.text
    assert "innerHTML" not in js.text
    assert "insertAdjacentHTML" not in js.text
    assert "outerHTML" not in js.text
    policy = html.headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert html.headers["X-Frame-Options"] == "DENY"
    assert html.headers["Referrer-Policy"] == "no-referrer"
    assert html.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


@pytest.mark.asyncio
async def test_saved_run_identity_is_visible_but_ephemeral_viewer_is_not_mislabeled() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=2, days=1)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_city_app(simulation, run_id="saved-study")),
            base_url="http://127.0.0.1",
        ) as saved,
        client() as ephemeral,
    ):
        saved_meta = (await saved.get("/api/meta")).json()
        temporary_meta = (await ephemeral.get("/api/meta")).json()
        html = (await saved.get("/")).text
        script = (await saved.get("/static/app.js")).text
    assert saved_meta["saved"] is True
    assert saved_meta["run_id"] == "saved-study"
    assert saved_meta["run_schema_version"] == 1
    assert "saved" not in temporary_meta and "run_id" not in temporary_meta
    assert 'id="saved-run-label" hidden' in html
    # The right-hand header is hidden below 900px; saved identity must stay visible.
    identity_header = html.split('<div class="masthead-right">', 1)[0]
    assert 'id="saved-run-label"' in identity_header
    assert 'byId("saved-run-label").textContent =' in script
    assert "SAVED RUN / ${meta.run_id} · V${meta.run_schema_version}" in script
    assert "innerHTML" not in script


@pytest.mark.asyncio
async def test_saved_v2_run_reports_its_actual_manifest_schema() -> None:
    simulation = CityMobility(load_pack_v2(pack_v2_data()), seed=42, agent_count=2, days=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation, run_id="saved-v2-study")),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
    assert metadata["saved"] is True
    assert metadata["run_id"] == "saved-v2-study"
    assert metadata["run_schema_version"] == 2


@pytest.mark.asyncio
async def test_saved_v3_run_reports_manifest_schema_and_place_evidence() -> None:
    places = mobility_place_set()
    simulation = CityMobility(
        load_pack_v2(pack_v2_data()), seed=42, agent_count=2, days=7, places=places
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_city_app(simulation, run_id="saved-v3-study", run_schema_version=3)
        ),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
    assert metadata["saved"] is True
    assert metadata["run_id"] == "saved-v3-study"
    assert metadata["run_schema_version"] == 3
    assert metadata["place_set_sha256"] == places.fingerprint


@pytest.mark.asyncio
async def test_saved_v4_run_remains_available_as_a_read_only_mobility_view() -> None:
    pack = load_pack(pack_data())
    simulation = _mobility(pack, agent_count=2)
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2),
        ],
    )
    evaluation = _evaluate(simulation, scenario)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_city_app(
                simulation,
                run_id="saved-spatial-study",
                run_schema_version=4,
                spatial_scenario=scenario,
                opportunity_evaluation=evaluation,
            )
        ),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        summary = await web.get("/api/opportunity-summary")
        first = await web.get("/api/opportunities?minute=0&limit=1")
        second = await web.get("/api/opportunities?minute=0&offset=1&limit=1")
        selected = await web.get("/api/opportunities?minute=0&agent_id=person-002")
        invalid_minute = await web.get("/api/opportunities?minute=1440")
        write = await web.post("/api/opportunities?minute=0")
        attention_summary = await web.get("/api/attention-summary")
        attention_events = await web.get("/api/attention-events?minute=0")
        metrics = await web.get("/api/spatial-metrics")
    assert metadata["saved"] is True
    assert metadata["run_id"] == "saved-spatial-study"
    assert metadata["run_schema_version"] == 4
    assert metadata["spatial_opportunities"] is True
    assert metadata["claim_scope"] == "synthetic-opportunity-not-impression"
    assert summary.status_code == 200
    assert summary.json() == {
        "schema_version": 1,
        "scenario_id": "opportunity-golden",
        "scenario_name": "Fictional opportunity golden",
        "scenario_sha256": scenario.fingerprint,
        "opportunity_model_id": "spatial-opportunity-v1",
        "claim_scope": "synthetic-opportunity-not-impression",
        "counts": evaluation.counts.model_dump(mode="json"),
        "campaigns": [item.model_dump(mode="json") for item in scenario.campaigns],
        "placements": [item.model_dump(mode="json") for item in scenario.placements],
    }
    assert first.status_code == second.status_code == selected.status_code == 200
    assert first.json()["total"] == 2
    assert first.json()["next_offset"] == 1
    assert first.json()["items"][0]["agent_id"] == "person-001"
    assert first.json()["channel_counts"] == {
        "roadside-billboard": 0,
        "mobile-feed": 2,
    }
    assert first.json()["agent_counts"] == {"person-001": 1, "person-002": 1}
    assert second.json()["next_offset"] is None
    assert second.json()["items"][0]["agent_id"] == "person-002"
    assert selected.json()["total"] == 1
    assert selected.json()["items"][0]["agent_id"] == "person-002"
    assert invalid_minute.status_code == 422
    assert write.status_code == 405
    assert attention_summary.status_code == attention_events.status_code == 404
    assert metrics.status_code == 404
    assert (
        attention_summary.json()
        == attention_events.json()
        == {"detail": "spatial attention evidence not configured"}
    )


@pytest.mark.asyncio
async def test_saved_v5_run_exposes_exact_paged_attention_evidence_read_only() -> None:
    pack = load_pack(pack_data())
    simulation = _mobility(pack, agent_count=2)
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2),
        ],
    )
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    expected = tuple(event for event in attention.events if event.model_minute == 0)
    selected_expected = tuple(event for event in expected if event.agent_id == "person-002")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_city_app(
                simulation,
                run_id="saved-attention-study",
                run_schema_version=5,
                spatial_scenario=scenario,
                opportunity_evaluation=opportunities,
                attention_evaluation=attention,
            )
        ),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        summary = await web.get("/api/attention-summary")
        first = await web.get("/api/attention-events?minute=0&limit=1")
        second = await web.get("/api/attention-events?minute=0&offset=1&limit=100")
        selected = await web.get("/api/attention-events?minute=0&agent_id=person-002")
        invalid_minute = await web.get("/api/attention-events?minute=1440")
        unknown_agent = await web.get("/api/attention-events?minute=0&agent_id=person-999")
        invalid_page = await web.get("/api/attention-events?minute=0&limit=101")
        write = await web.post("/api/attention-events?minute=0")

    assert metadata["run_schema_version"] == 5
    assert metadata["spatial_attention"] is True
    assert metadata["attention_model_id"] == "spatial-attention-v1"
    assert metadata["attention_claim_scope"] == "synthetic-attention-not-observed-behavior"
    assert metadata["impression_count"] == attention.counts.impression_count
    assert metadata["noticed_count"] == attention.counts.noticed_count
    assert summary.status_code == 200
    assert summary.json() == {
        "schema_version": 1,
        "scenario_id": scenario.scenario_id,
        "scenario_name": scenario.name,
        "scenario_sha256": scenario.fingerprint,
        "attention_model_id": "spatial-attention-v1",
        "claim_scope": "synthetic-attention-not-observed-behavior",
        "notice_probability": 0.5,
        "counts": attention.counts.model_dump(mode="json"),
    }
    assert first.status_code == second.status_code == selected.status_code == 200
    assert first.json()["total"] == len(expected)
    assert first.json()["next_offset"] == 1
    assert first.json()["items"] == [expected[0].model_dump(mode="json")]
    assert second.json()["items"] == [event.model_dump(mode="json") for event in expected[1:]]
    assert selected.json()["total"] == len(selected_expected)
    assert selected.json()["items"] == [
        event.model_dump(mode="json") for event in selected_expected
    ]
    assert first.json()["event_type_counts"] == {
        "spatial.impression": sum(event.event_type == "spatial.impression" for event in expected),
        "spatial.noticed": sum(event.event_type == "spatial.noticed" for event in expected),
    }
    assert invalid_minute.status_code == invalid_page.status_code == 422
    assert unknown_agent.status_code == 400
    assert write.status_code == 405


def test_saved_v5_view_requires_matching_attention_evidence() -> None:
    pack = load_pack(pack_data())
    simulation = _mobility(pack)
    scenario = _scenario(pack, [_phone(windows=[{"start_minute": 0, "end_minute": 1}])])
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)

    with pytest.raises(ValueError, match="schema version 5 requires spatial attention"):
        create_city_app(
            simulation,
            run_id="incomplete-attention-study",
            run_schema_version=5,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
        )
    with pytest.raises(
        ValueError,
        match="only valid for a saved schema version 5, 6, 7, 8, 9 or 10 run",
    ):
        create_city_app(
            simulation,
            run_id="wrong-attention-schema",
            run_schema_version=4,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=attention,
        )
    with pytest.raises(ValueError, match="does not match its saved inputs"):
        create_city_app(
            simulation,
            run_id="mismatched-attention-study",
            run_schema_version=5,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=evaluate_spatial_attention(opportunities, seed=43),
        )


@pytest.mark.asyncio
async def test_saved_v5_view_exposes_exact_prevalidated_metrics_read_only() -> None:
    pack = load_pack(pack_data())
    simulation = _mobility(pack, agent_count=2)
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2),
        ],
    )
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=simulation.days,
    )
    application = create_city_app(
        simulation,
        run_id="saved-metrics-study",
        run_schema_version=5,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=metrics,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        response = await web.get("/api/spatial-metrics")
        write = await web.post("/api/spatial-metrics", json={})

    assert metadata["spatial_metrics"] is True
    assert response.status_code == 200
    assert response.json() == metrics.model_dump(mode="json")
    assert write.status_code == 405


@pytest.mark.asyncio
async def test_metrics_endpoint_is_absent_without_prevalidated_v5_metrics() -> None:
    async with client() as legacy:
        legacy_response = await legacy.get("/api/spatial-metrics")

    pack = load_pack(pack_data())
    simulation = _mobility(pack)
    scenario = _scenario(pack, [_phone(windows=[{"start_minute": 0, "end_minute": 1}])])
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_city_app(
                simulation,
                run_id="v5-without-metrics",
                run_schema_version=5,
                spatial_scenario=scenario,
                opportunity_evaluation=opportunities,
                attention_evaluation=attention,
            )
        ),
        base_url="http://127.0.0.1",
    ) as v5_without_metrics:
        omitted_response = await v5_without_metrics.get("/api/spatial-metrics")

    assert legacy_response.status_code == omitted_response.status_code == 404
    assert (
        legacy_response.json()
        == omitted_response.json()
        == {"detail": "spatial metrics not configured"}
    )


def test_saved_view_refuses_metrics_outside_v5_or_mismatched_with_evidence() -> None:
    pack = load_pack(pack_data())
    simulation = _mobility(pack)
    scenario = _scenario(pack, [_phone(windows=[{"start_minute": 0, "end_minute": 1}])])
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=("person-001",),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=1,
    )

    with pytest.raises(ValueError, match="only valid for a saved schema version 5"):
        create_city_app(simulation, spatial_metrics=metrics)
    with pytest.raises(ValueError, match="do not match their saved inputs"):
        create_city_app(
            simulation,
            run_id="mismatched-metrics",
            run_schema_version=5,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=attention,
            spatial_metrics=metrics.model_copy(update={"scenario_sha256": "f" * 64}),
        )


@pytest.mark.asyncio
async def test_non_spatial_view_has_no_opportunity_api_or_spatial_metadata() -> None:
    async with client() as web:
        metadata = (await web.get("/api/meta")).json()
        summary = await web.get("/api/opportunity-summary")
        opportunities = await web.get("/api/opportunities?minute=0")
        timeline = await web.get("/api/timeline?minute=0&agent_id=person-001")
    assert "spatial_opportunities" not in metadata
    assert "claim_scope" not in metadata
    assert summary.status_code == opportunities.status_code == timeline.status_code == 404
    assert (
        summary.json()
        == opportunities.json()
        == timeline.json()
        == {"detail": "spatial opportunity evidence not configured"}
    )


def test_spatial_view_requires_a_complete_schema_v4_saved_run() -> None:
    pack = load_pack(pack_data())
    simulation = _mobility(pack)
    scenario = _scenario(pack, [_phone(windows=[{"start_minute": 0, "end_minute": 1}])])
    evaluation = _evaluate(simulation, scenario)

    with pytest.raises(ValueError, match="schema version 4 requires spatial opportunity evidence"):
        create_city_app(
            simulation,
            run_id="incomplete-spatial-study",
            run_schema_version=4,
        )
    with pytest.raises(
        ValueError,
        match="only valid for a saved schema version 4, 5, 6, 7, 8, 9 or 10 run",
    ):
        create_city_app(
            simulation,
            run_id="wrong-schema-study",
            run_schema_version=3,
            spatial_scenario=scenario,
            opportunity_evaluation=evaluation,
        )


@pytest.mark.asyncio
async def test_saved_v6_view_exposes_exact_response_resources_without_mutating_sources() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=simulation.days,
        source_run_schema_version=6,
    )
    source_before = tuple(
        canonical_json(item)
        for item in (
            scenario,
            opportunities,
            attention,
            response_input,
            responses,
            metrics,
        )
    )
    frame_before = simulation.frame_document(0)
    minute_records = tuple(record for record in responses.records if record.model_minute == 0)
    assert [record.event_type for record in minute_records] == [
        "spatial.response",
        "spatial.state-updated",
    ]
    state_document = spatial_response_state_document(responses)
    summary_document = summarize_spatial_response_artifact(responses)
    application = create_city_app(
        simulation,
        run_id="saved-response-study",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=metrics,
        response_input=response_input,
        response_evaluation=responses,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        summary = await web.get("/api/response-summary")
        first = await web.get("/api/response-events?minute=0&limit=1")
        second = await web.get("/api/response-events?minute=0&offset=1&limit=100")
        selected = await web.get("/api/response-events?minute=0&agent_id=person-002")
        empty = await web.get("/api/response-events?minute=0&agent_id=person-001")
        ignored_path = await web.get("/api/response-events?minute=0&limit=1&path=../../run.json")
        first_state = await web.get("/api/response-state?limit=1")
        second_state = await web.get("/api/response-state?offset=1&limit=100")
        selected_state = await web.get("/api/response-state?agent_id=person-002")
        metrics_response = await web.get("/api/spatial-metrics")

    assert metadata["run_schema_version"] == 6
    assert metadata["spatial_response"] is True
    assert metadata["response_model_id"] == "spatial-response-v1"
    assert metadata["response_claim_scope"] == "synthetic-response-not-observed-behavior"
    assert metadata["response_count"] == 2
    assert metadata["state_update_count"] == 2
    assert metadata["response_campaign_count"] == 1
    assert metadata["final_state_count"] == 2
    assert summary.status_code == 200
    assert summary.json() == summary_document.model_dump(mode="json")
    assert set(summary.json()) == {
        "schema_version",
        "model_id",
        "claim_scope",
        "response_model_id",
        "response_input_sha256",
        "scenario_sha256",
        "city_sha256",
        "attention_seed",
        "stream_sha256",
        "stream_bytes",
        "state_document_sha256",
        "state_document_bytes",
        "counts",
    }
    assert first.status_code == second.status_code == selected.status_code == 200
    assert first.json() == {
        "schema_version": 1,
        "response_model_id": "spatial-response-v1",
        "claim_scope": "synthetic-response-not-observed-behavior",
        "minute": 0,
        "agent_id": None,
        "offset": 0,
        "limit": 1,
        "total": 2,
        "event_type_counts": {
            "spatial.response": 1,
            "spatial.state-updated": 1,
        },
        "channel_counts": {
            "roadside-billboard": 0,
            "mobile-feed": 1,
        },
        "agent_counts": {"person-002": 2},
        "next_offset": 1,
        "items": [minute_records[0].model_dump(mode="json")],
    }
    assert second.json() == {
        **first.json(),
        "offset": 1,
        "limit": 100,
        "next_offset": None,
        "items": [minute_records[1].model_dump(mode="json")],
    }
    assert selected.json() == {
        **first.json(),
        "agent_id": "person-002",
        "limit": 100,
        "next_offset": None,
        "items": [record.model_dump(mode="json") for record in minute_records],
    }
    assert empty.json() == {
        **first.json(),
        "agent_id": "person-001",
        "limit": 100,
        "total": 0,
        "event_type_counts": {
            "spatial.response": 0,
            "spatial.state-updated": 0,
        },
        "channel_counts": {
            "roadside-billboard": 0,
            "mobile-feed": 0,
        },
        "agent_counts": {},
        "next_offset": None,
        "items": [],
    }
    assert ignored_path.json() == first.json()

    state_header = {
        "schema_version": 1,
        "model_id": "spatial-response-state-v1",
        "claim_scope": "synthetic-response-not-observed-behavior",
        "response_input_sha256": state_document.response_input_sha256,
        "scenario_sha256": state_document.scenario_sha256,
        "city_sha256": state_document.city_sha256,
        "state_scope": "final-end-of-run-not-scrubbed-minute",
    }
    assert first_state.status_code == second_state.status_code == selected_state.status_code == 200
    assert first_state.json() == {
        **state_header,
        "agent_id": None,
        "offset": 0,
        "limit": 1,
        "total": 2,
        "next_offset": 1,
        "items": [state_document.states[0].model_dump(mode="json")],
    }
    assert second_state.json() == {
        **state_header,
        "agent_id": None,
        "offset": 1,
        "limit": 100,
        "total": 2,
        "next_offset": None,
        "items": [state_document.states[1].model_dump(mode="json")],
    }
    assert selected_state.json() == {
        **state_header,
        "agent_id": "person-002",
        "offset": 0,
        "limit": 100,
        "total": 1,
        "next_offset": None,
        "items": [state_document.states[1].model_dump(mode="json")],
    }
    assert metrics_response.status_code == 200
    assert metrics_response.json() == metrics.model_dump(mode="json")
    assert (
        tuple(
            canonical_json(item)
            for item in (
                scenario,
                opportunities,
                attention,
                response_input,
                responses,
                metrics,
            )
        )
        == source_before
    )
    assert simulation.frame_document(0) == frame_before


@pytest.mark.asyncio
async def test_saved_v6_view_exposes_exact_prevalidated_response_metrics_get_only() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    attention_metrics, response_metrics = _response_view_metrics(
        simulation,
        scenario,
        opportunities,
        attention,
        response_input,
        responses,
    )
    source_before = tuple(
        canonical_json(item)
        for item in (
            scenario,
            opportunities,
            attention,
            response_input,
            responses,
            attention_metrics,
            response_metrics,
        )
    )
    application = create_city_app(
        simulation,
        run_id="saved-response-study",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=attention_metrics,
        response_input=response_input,
        response_evaluation=responses,
        spatial_response_metrics=response_metrics,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        response = await web.get("/api/spatial-response-metrics")
        ignored_path = await web.get("/api/spatial-response-metrics?path=../../run.json")
        write = await web.post("/api/spatial-response-metrics", json={})

    assert response.status_code == ignored_path.status_code == 200
    assert response.json() == ignored_path.json() == response_metrics.model_dump(mode="json")
    assert write.status_code == 405
    assert (
        tuple(
            canonical_json(item)
            for item in (
                scenario,
                opportunities,
                attention,
                response_input,
                responses,
                attention_metrics,
                response_metrics,
            )
        )
        == source_before
    )


@pytest.mark.asyncio
async def test_saved_v7_view_preserves_exact_metric_source_schema() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    attention_metrics, response_metrics = _response_view_metrics(
        simulation,
        scenario,
        opportunities,
        attention,
        response_input,
        responses,
        source_run_schema_version=7,
    )
    application = create_city_app(
        simulation,
        run_id="saved-v7-response-study",
        run_schema_version=7,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=attention_metrics,
        response_input=response_input,
        response_evaluation=responses,
        spatial_response_metrics=response_metrics,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        projected_attention = (await web.get("/api/spatial-metrics")).json()
        projected_response = (await web.get("/api/spatial-response-metrics")).json()
        javascript = (await web.get("/static/app.js")).text

    assert metadata["run_schema_version"] == 7
    assert projected_attention["source_run_schema_version"] == 7
    assert projected_response["source_run_schema_version"] == 7
    assert "![6, 7, 8, 9].includes(document.source_run_schema_version)" in javascript
    assert "document.source_run_schema_version !== state.meta.run_schema_version" in javascript


@pytest.mark.asyncio
async def test_response_metrics_endpoint_is_absent_for_legacy_or_omitted_v6_metrics() -> None:
    async with client() as legacy:
        legacy_response = await legacy.get("/api/spatial-response-metrics")

    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    application = create_city_app(
        simulation,
        run_id="v6-without-response-metrics",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as without_metrics:
        omitted_response = await without_metrics.get("/api/spatial-response-metrics")

    assert legacy_response.status_code == omitted_response.status_code == 404


def test_saved_view_rederives_response_metrics_and_rejects_wrong_schema() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    attention_metrics, response_metrics = _response_view_metrics(
        simulation,
        scenario,
        opportunities,
        attention,
        response_input,
        responses,
    )
    common = {
        "run_id": "saved-response-study",
        "run_schema_version": 6,
        "spatial_scenario": scenario,
        "opportunity_evaluation": opportunities,
        "attention_evaluation": attention,
        "spatial_metrics": attention_metrics,
        "response_input": response_input,
        "response_evaluation": responses,
    }

    with pytest.raises(ValueError, match="response metrics do not match their saved inputs"):
        create_city_app(
            simulation,
            **common,
            spatial_response_metrics=response_metrics.model_copy(
                update={"scenario_sha256": "f" * 64}
            ),
        )
    with pytest.raises(
        ValueError,
        match="only valid for a saved schema version 6, 7, 8, 9 or 10 run",
    ):
        create_city_app(
            simulation,
            run_id="wrong-response-metrics-schema",
            run_schema_version=5,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=attention,
            spatial_response_metrics=response_metrics,
        )


@pytest.mark.asyncio
async def test_saved_v6_response_routes_enforce_filters_bounds_and_get_only_semantics() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    application = create_city_app(
        simulation,
        run_id="saved-response-study",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        missing_minute = await web.get("/api/response-events")
        negative_minute = await web.get("/api/response-events?minute=-1")
        outside_minute = await web.get("/api/response-events?minute=1440")
        malformed_agent = await web.get("/api/response-events?minute=0&agent_id=person-1")
        unknown_agent = await web.get("/api/response-events?minute=0&agent_id=person-999")
        zero_limit = await web.get("/api/response-events?minute=0&limit=0")
        large_limit = await web.get("/api/response-events?minute=0&limit=101")
        large_offset = await web.get(
            f"/api/response-events?minute=0&offset={MAX_SPATIAL_RESPONSE_RECORDS + 1}"
        )
        empty_page = await web.get("/api/response-events?minute=0&offset=100")
        malformed_state_agent = await web.get("/api/response-state?agent_id=person-1")
        unknown_state_agent = await web.get("/api/response-state?agent_id=person-999")
        large_state_limit = await web.get("/api/response-state?limit=101")
        large_state_offset = await web.get("/api/response-state?offset=601")
        writes = [
            await web.post("/api/response-summary", json={}),
            await web.post("/api/response-events?minute=0", json={}),
            await web.post("/api/response-state", json={}),
        ]

    assert missing_minute.status_code == negative_minute.status_code == 422
    assert outside_minute.status_code == 422
    assert outside_minute.json() == {"detail": "minute is outside the saved run"}
    assert malformed_agent.status_code == malformed_state_agent.status_code == 422
    assert unknown_agent.status_code == unknown_state_agent.status_code == 400
    assert unknown_agent.json() == unknown_state_agent.json() == {"detail": "unknown city agent"}
    assert zero_limit.status_code == large_limit.status_code == large_offset.status_code == 422
    assert large_state_limit.status_code == large_state_offset.status_code == 422
    assert empty_page.status_code == 200
    assert empty_page.json()["total"] == 2
    assert empty_page.json()["next_offset"] is None
    assert empty_page.json()["items"] == []
    assert [response.status_code for response in writes] == [405, 405, 405]


@pytest.mark.asyncio
async def test_timeline_projection_matches_the_five_existing_read_only_resources() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    application = create_city_app(
        simulation,
        run_id="timeline-projection",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
    )
    params = {
        "minute": 0,
        "agent_id": "person-002",
        "opportunity_offset": 0,
        "attention_offset": 0,
        "response_offset": 0,
        "response_state_offset": 0,
        "limit": 1,
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        combined = await web.get("/api/timeline", params=params)
        frame = await web.get("/api/frame?minute=0&agent_id=person-002")
        opportunity_page = await web.get("/api/opportunities?minute=0&offset=0&limit=1")
        attention_page = await web.get("/api/attention-events?minute=0&offset=0&limit=1")
        response_page = await web.get("/api/response-events?minute=0&offset=0&limit=1")
        response_state_page = await web.get(
            "/api/response-state?agent_id=person-002&offset=0&limit=1"
        )

    assert combined.status_code == 200
    assert combined.json() == {
        "schema_version": 1,
        "minute": 0,
        "agent_id": "person-002",
        "frame": frame.json(),
        "opportunity_page": opportunity_page.json(),
        "attention_page": attention_page.json(),
        "response_page": response_page.json(),
        "response_state_page": response_state_page.json(),
    }


@pytest.mark.asyncio
async def test_timeline_projection_enforces_every_existing_bound_and_is_get_only() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    application = create_city_app(
        simulation,
        run_id="bounded-timeline-projection",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
    )
    base = "/api/timeline?minute=0&agent_id=person-001"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        missing_agent = await web.get("/api/timeline?minute=0")
        malformed_agent = await web.get("/api/timeline?minute=0&agent_id=person-1")
        unknown_agent = await web.get("/api/timeline?minute=0&agent_id=person-999")
        outside_minute = await web.get("/api/timeline?minute=1440&agent_id=person-001")
        bad_offsets = [
            await web.get(f"{base}&opportunity_offset={MAX_SPATIAL_RESPONSE_RECORDS + 1}"),
            await web.get(f"{base}&attention_offset={MAX_SPATIAL_RESPONSE_RECORDS + 1}"),
            await web.get(f"{base}&response_offset={MAX_SPATIAL_RESPONSE_RECORDS + 1}"),
            await web.get(f"{base}&response_state_offset=601"),
        ]
        zero_limit = await web.get(f"{base}&limit=0")
        large_limit = await web.get(f"{base}&limit=101")
        write = await web.post(base, json={})

    assert missing_agent.status_code == malformed_agent.status_code == 422
    assert unknown_agent.status_code == 400
    assert unknown_agent.json() == {"detail": "unknown city agent"}
    assert outside_minute.status_code == 422
    assert outside_minute.json() == {"detail": "minute is outside the saved run"}
    assert all(response.status_code == 422 for response in bad_offsets)
    assert zero_limit.status_code == large_limit.status_code == 422
    assert write.status_code == 405


@pytest.mark.asyncio
async def test_saved_v6_zero_response_view_still_exposes_every_final_campaign_state() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = _response_view_case(
        seed=27
    )
    assert responses.counts.response_count == 0
    assert responses.counts.state_update_count == 0
    assert responses.counts.final_state_count == 2
    application = create_city_app(
        simulation,
        run_id="zero-response-study",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        summary = await web.get("/api/response-summary")
        events = await web.get("/api/response-events?minute=0")
        states = await web.get("/api/response-state")

    assert summary.status_code == events.status_code == states.status_code == 200
    assert summary.json()["counts"] == {
        "schema_version": 1,
        "response_count": 0,
        "state_update_count": 0,
        "roadside_response_count": 0,
        "phone_response_count": 0,
        "campaign_count": 1,
        "final_state_count": 2,
    }
    assert events.json()["total"] == 0
    assert events.json()["items"] == []
    assert states.json()["state_scope"] == "final-end-of-run-not-scrubbed-minute"
    assert states.json()["total"] == 2
    assert states.json()["items"] == [
        state.model_dump(mode="json") for state in responses.final_states
    ]
    assert all(item["response_count"] == 0 for item in states.json()["items"])
    assert all(item["last_response_minute"] is None for item in states.json()["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("run_schema_version", [1, 2, 3, 4, 5])
async def test_schema_v1_through_v5_views_have_no_response_resources(
    run_schema_version: int,
) -> None:
    simulation, scenario, opportunities, attention, _, _ = _response_view_case()
    kwargs: dict[str, object] = {
        "run_id": f"legacy-v{run_schema_version}",
        "run_schema_version": run_schema_version,
    }
    if run_schema_version >= 4:
        kwargs.update(
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
        )
    if run_schema_version >= 5:
        kwargs["attention_evaluation"] = attention
    application = create_city_app(simulation, **kwargs)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        results = [
            await web.get("/api/response-summary"),
            await web.get("/api/response-events?minute=0"),
            await web.get("/api/response-state"),
            await web.get("/api/spatial-response-metrics"),
        ]

    assert "spatial_response" not in metadata
    assert not any(key.startswith("response_") for key in metadata)
    assert "state_update_count" not in metadata
    assert "final_state_count" not in metadata
    assert [response.status_code for response in results] == [404, 404, 404, 404]
    assert [response.json() for response in results[:3]] == [
        {"detail": "spatial response evidence not configured"},
        {"detail": "spatial response evidence not configured"},
        {"detail": "spatial response evidence not configured"},
    ]


def test_saved_v6_view_requires_complete_matching_revalidated_response_evidence() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    common: dict[str, object] = {
        "run_id": "saved-response-study",
        "run_schema_version": 6,
        "spatial_scenario": scenario,
        "opportunity_evaluation": opportunities,
        "attention_evaluation": attention,
    }

    with pytest.raises(ValueError, match="schema version 6 requires spatial response evidence"):
        create_city_app(simulation, **common)
    for partial in (
        {"response_input": response_input},
        {"response_evaluation": responses},
    ):
        with pytest.raises(ValueError, match="must be supplied together"):
            create_city_app(simulation, **common, **partial)
    with pytest.raises(
        ValueError,
        match="only valid for a saved schema version 6, 7, 8, 9 or 10 run",
    ):
        create_city_app(
            simulation,
            **(common | {"run_schema_version": 5}),
            response_input=response_input,
            response_evaluation=responses,
        )

    changed_input = _response_input(
        scenario,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
        sentiment=-0.5,
    )
    with pytest.raises(ValueError, match="does not match its saved inputs"):
        create_city_app(
            simulation,
            **common,
            response_input=changed_input,
            response_evaluation=responses,
        )
    poisoned = responses.model_copy()
    object.__setattr__(poisoned, "claim_scope", "observed-behavior")
    with pytest.raises(ValueError):
        create_city_app(
            simulation,
            **common,
            response_input=response_input,
            response_evaluation=poisoned,
        )
    for invalid_version in (True, 6.0):
        with pytest.raises(ValueError, match="unsupported city run schema version"):
            create_city_app(
                simulation,
                **(common | {"run_schema_version": invalid_version}),
                response_input=response_input,
                response_evaluation=responses,
            )


def test_saved_v9_view_accepts_complete_response_and_workbench_evidence() -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )

    context = create_city_view_context(
        simulation,
        run_id="saved-cognition-study",
        run_schema_version=9,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
        workbench_input_sha256="a" * 64,
    )

    assert context.run_schema_version == 9
    assert context.response_evaluation == responses
    assert context.workbench_input_sha256 == "a" * 64


def _v10_view_arguments(stored) -> dict[str, object]:
    """Complete exact V10 evidence, deriving metrics directly from the core models."""
    from adlife.city.workbench_cognition_views import (
        build_workbench_cognition_records_view,
        build_workbench_cognition_summary_view,
        build_workbench_run_input_view_v4,
    )
    from adlife.core.experiments.spatial_response_metrics_v2 import (
        derive_spatial_response_metrics_v2,
    )

    agent_ids = tuple(agent.agent_id for agent in stored.mobility.agents)
    attention_metrics = derive_spatial_metrics(
        stored.opportunity_evaluation,
        stored.attention_evaluation,
        agent_ids=agent_ids,
        agents_sha256=stored.manifest.agents_sha256,
        trace_sha256=stored.manifest.trace_sha256,
        days=stored.manifest.days,
        source_run_schema_version=10,
    )
    return {
        "run_id": stored.manifest.run_id,
        "run_schema_version": 10,
        "spatial_scenario": stored.spatial_scenario,
        "opportunity_evaluation": stored.opportunity_evaluation,
        "attention_evaluation": stored.attention_evaluation,
        "spatial_metrics": attention_metrics,
        "response_input": stored.response_input,
        "response_evaluation": stored.response_evaluation,
        "spatial_response_metrics": derive_spatial_response_metrics_v2(
            stored.response_input,
            stored.response_evaluation,
            scenario=stored.spatial_scenario,
            opportunities=stored.opportunity_evaluation,
            attention=stored.attention_evaluation,
            agent_ids=agent_ids,
            attention_metrics=attention_metrics,
            cognition_input=stored.cognition_input,
            cognition=stored.cognition_evaluation,
        ),
        "cognition_input": stored.cognition_input,
        "cognition_evaluation": stored.cognition_evaluation,
        "workbench_input_view": build_workbench_run_input_view_v4(stored),
        "cognition_summary_view": build_workbench_cognition_summary_view(stored),
        "cognition_records_view": build_workbench_cognition_records_view(stored),
    }


@pytest.fixture(scope="module")
def saved_v10(tmp_path_factory: pytest.TempPathFactory):
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10
    from tests.unit.city.test_workbench_cognition_views import dense_phone

    root = tmp_path_factory.mktemp("city-web-v10")
    _saved_v10(root, mode="mock", run_id="dense-v10", agents=4, mutation=dense_phone)
    _saved_v10(root, mode="rules", run_id="rules-v10")
    _saved_v10(root, mode="rules", run_id="empty-v10", empty=True)
    store = CityRunStore(root)
    return {name: store.load(f"{name}-v10") for name in ("dense", "rules", "empty")}


def _v10_client(stored, **changes: object) -> httpx.AsyncClient:
    arguments = _v10_view_arguments(stored) | changes
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(stored.mobility, **arguments)),
        base_url="http://127.0.0.1",
    )


@pytest.mark.asyncio
async def test_saved_v10_response_pages_keep_canonical_dual_state_stream_order(saved_v10) -> None:
    stored = saved_v10["dense"]
    evaluation = stored.response_evaluation
    records = evaluation.records
    minutes = sorted(
        {
            record.baseline.model_minute
            if record.event_type == "spatial.applied-response"
            else record.model_minute
            for record in records
        }
    )
    async with _v10_client(stored) as web:
        pages = {
            minute: (await web.get(f"/api/response-events?minute={minute}&limit=100")).json()
            for minute in minutes
        }
        busiest = max(minutes, key=lambda minute: pages[minute]["total"])
        split = [
            (await web.get(f"/api/response-events?minute={busiest}&offset={offset}&limit=1")).json()
            for offset in range(pages[busiest]["total"])
        ]
        agent = pages[busiest]["items"][-1]["agent_id"]
        filtered = (await web.get(f"/api/response-events?minute={busiest}&agent_id={agent}")).json()

    streamed = [item for minute in minutes for item in pages[minute]["items"]]
    assert streamed == [record.model_dump(mode="json") for record in records]
    for minute, page in pages.items():
        assert page["schema_version"] == 2
        assert page["response_model_id"] == "spatial-response-v2"
        assert page["claim_scope"] == "synthetic-bounded-cognition-not-observed-behavior"
        items = page["items"]
        applied = [item for item in items if item["event_type"] == "spatial.applied-response"]
        assert {item["event_type"] for item in items} <= {
            "spatial.applied-response",
            "spatial.dual-state-updated",
        }
        assert page["event_type_counts"] == {
            "spatial.applied-response": len(applied),
            "spatial.dual-state-updated": len(items) - len(applied),
        }
        assert page["channel_counts"] == {
            "roadside-billboard": sum(
                item["baseline"]["channel"] == "roadside-billboard" for item in applied
            ),
            "mobile-feed": sum(item["baseline"]["channel"] == "mobile-feed" for item in applied),
        }
        # Every applied record embeds its deterministic baseline; there is no
        # standalone baseline line, and each group ends with exactly one dual update.
        assert all(item["baseline"]["event_type"] == "spatial.response" for item in applied)
        assert all(item["baseline"]["model_minute"] == minute for item in applied)
        pending: list[str] = []
        for item in items:
            if item["event_type"] == "spatial.applied-response":
                pending.append(item["event_id"])
            else:
                assert item["caused_by_event_ids"] == pending
                assert item["model_minute"] == minute
                pending = []
        assert pending == []
    # A page may end with an applied response whose dual update starts the next page.
    assert [page["items"][0] for page in split] == pages[busiest]["items"]
    assert [page["next_offset"] for page in split] == [
        *range(1, pages[busiest]["total"]),
        None,
    ]
    assert split[0]["items"][0]["event_type"] == "spatial.applied-response"
    assert filtered["items"] == [
        item
        for item in pages[busiest]["items"]
        if item.get("agent_id", item.get("baseline", {}).get("agent_id")) == agent
    ]
    assert filtered["agent_counts"] == {agent: filtered["total"]}


@pytest.mark.asyncio
async def test_saved_v10_state_pages_expose_separate_rule_and_cognition_tracks(saved_v10) -> None:
    from adlife.core.simulation.spatial_response_v2 import spatial_response_state_document_v2

    stored = saved_v10["dense"]
    document = spatial_response_state_document_v2(stored.response_evaluation)
    async with _v10_client(stored) as web:
        everything = (await web.get("/api/response-state")).json()
        first = (await web.get("/api/response-state?offset=0&limit=1")).json()
        agent = (await web.get("/api/response-state?agent_id=person-002")).json()

    assert {key: value for key, value in everything.items() if key != "items"} == {
        "schema_version": 2,
        "model_id": "spatial-response-state-v2",
        "claim_scope": "synthetic-bounded-cognition-not-observed-behavior",
        "response_input_sha256": document.response_input_sha256,
        "scenario_sha256": document.scenario_sha256,
        "city_sha256": document.city_sha256,
        "cognition_input_sha256": document.cognition_input_sha256,
        "rule_state_model_id": "spatial-rule-state-v2",
        "cognition_state_model_id": "spatial-cognition-state-v2",
        "state_scope": "final-end-of-run-not-scrubbed-minute",
        "agent_id": None,
        "offset": 0,
        "limit": 100,
        "total": len(document.rule.states),
        "next_offset": None,
    }
    assert everything["items"] == [
        {
            "rule_state": rule.model_dump(mode="json"),
            "cognition_state": cognition.model_dump(mode="json"),
        }
        for rule, cognition in zip(document.rule.states, document.cognition.states, strict=True)
    ]
    for item in everything["items"]:
        assert "purchase_intention" in item["rule_state"]
        assert "purchase" not in json.dumps(item["cognition_state"])
        assert item["rule_state"]["response_count"] == item["cognition_state"]["response_count"]
    assert first["items"] == everything["items"][:1]
    assert first["next_offset"] == 1
    assert {item["rule_state"]["agent_id"] for item in agent["items"]} == {"person-002"}


@pytest.mark.asyncio
async def test_saved_v10_meta_documents_and_timeline_share_one_exact_projection(
    saved_v10,
) -> None:
    from adlife.core.simulation.spatial_response_v2 import summarize_spatial_response_artifact_v2

    stored = saved_v10["dense"]
    arguments = _v10_view_arguments(stored)
    evaluation = stored.response_evaluation
    minute = next(
        record.model_minute
        for record in evaluation.records
        if record.event_type == "spatial.dual-state-updated"
    )
    params = {"minute": minute, "agent_id": "person-001", "response_offset": 1, "limit": 2}
    async with _v10_client(stored) as web:
        meta = (await web.get("/api/meta")).json()
        summary = (await web.get("/api/response-summary")).json()
        metrics = (await web.get("/api/spatial-response-metrics")).json()
        attention_metrics = (await web.get("/api/spatial-metrics")).json()
        timeline = (await web.get("/api/timeline", params=params)).json()
        page = (await web.get(f"/api/response-events?minute={minute}&offset=1&limit=2")).json()
        state = (await web.get("/api/response-state?agent_id=person-001&limit=2")).json()
        saved_input = (await web.get("/api/workbench-input")).json()
        cognition_summary = (await web.get("/api/cognition-summary")).json()
        cognition_records = (await web.get("/api/cognition-records")).json()

    counts = evaluation.counts
    assert {
        key: meta[key]
        for key in (
            "saved",
            "run_id",
            "run_schema_version",
            "workbench_input_available",
            "workbench_input_sha256",
            "spatial_response",
            "response_model_id",
            "response_claim_scope",
            "response_count",
            "state_update_count",
            "response_campaign_count",
            "final_state_count",
            "bounded_cognition",
            "cognition_effect_scope",
            "spatial_metrics",
            "spatial_response_metrics",
        )
    } == {
        "saved": True,
        "run_id": "dense-v10",
        "run_schema_version": 10,
        "workbench_input_available": True,
        "workbench_input_sha256": stored.manifest.workbench_input_sha256,
        "spatial_response": True,
        "response_model_id": "spatial-response-v2",
        "response_claim_scope": "synthetic-bounded-cognition-not-observed-behavior",
        "response_count": counts.baseline_response_count,
        "state_update_count": counts.dual_state_update_count,
        "response_campaign_count": counts.campaign_count,
        "final_state_count": counts.final_rule_state_count,
        "bounded_cognition": True,
        "cognition_effect_scope": "bounded-signals-v1",
        "spatial_metrics": True,
        "spatial_response_metrics": True,
    }
    assert summary == summarize_spatial_response_artifact_v2(evaluation).model_dump(mode="json")
    assert metrics == arguments["spatial_response_metrics"].model_dump(mode="json")
    assert metrics["schema_version"] == 2 and metrics["source_run_schema_version"] == 10
    assert attention_metrics["source_run_schema_version"] == 10
    assert timeline["schema_version"] == 1
    assert timeline["response_page"] == page
    assert timeline["response_state_page"] == state
    assert saved_input == arguments["workbench_input_view"].model_dump(mode="json")
    assert cognition_summary == arguments["cognition_summary_view"].model_dump(mode="json")
    assert cognition_records == arguments["cognition_records_view"].model_dump(mode="json")


@pytest.mark.asyncio
async def test_standalone_saved_run_routes_are_v10_only_get_only_and_value_free(
    saved_v10,
) -> None:
    simulation, scenario, opportunities, attention, response_input, responses = (
        _response_view_case()
    )
    legacy = create_city_app(
        simulation,
        run_id="saved-cognition-study",
        run_schema_version=9,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=response_input,
        response_evaluation=responses,
    )
    marker = "sk-never-echo-standalone-1234567890"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=legacy), base_url="http://127.0.0.1"
    ) as web:
        legacy_meta = (await web.get("/api/meta")).json()
        legacy_routes = [
            await web.get(f"/api/{suffix}")
            for suffix in ("workbench-input", "cognition-summary", "cognition-records")
        ]
    async with _v10_client(saved_v10["rules"]) as web:
        query = await web.get(f"/api/cognition-summary?private={marker}")
        write = await web.post("/api/cognition-records", json={})
        timeline = await web.get("/api/timeline?minute=0&agent_id=person-001")

    assert "bounded_cognition" not in legacy_meta
    assert "workbench_input_available" not in legacy_meta
    assert all(route.status_code == 404 for route in legacy_routes)
    assert all(route.json() == {"detail": "Not Found"} for route in legacy_routes)
    assert query.status_code == 422
    assert query.json() == {"detail": "request validation failed"}
    assert marker not in query.text
    assert write.status_code == 405
    assert timeline.status_code == 200
    assert timeline.json()["response_page"]["schema_version"] == 2


def test_saved_v10_view_refuses_wrong_version_or_cross_bound_evidence(saved_v10) -> None:
    stored = saved_v10["dense"]
    other = saved_v10["rules"]
    arguments = _v10_view_arguments(stored)
    context = create_city_view_context(
        stored.mobility,
        workbench_input_sha256=stored.manifest.workbench_input_sha256,
        **arguments,
    )
    assert context.run_schema_version == 10
    assert context.response_minutes == tuple(
        record.baseline.model_minute
        if record.event_type == "spatial.applied-response"
        else record.model_minute
        for record in stored.response_evaluation.records
    )
    baseline = evaluate_spatial_responses(
        stored.response_input,
        stored.spatial_scenario,
        stored.opportunity_evaluation,
        stored.attention_evaluation,
        agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
    )
    other_views = _v10_view_arguments(other)
    hostile = {
        "v1-response": {"response_evaluation": baseline, "spatial_response_metrics": None},
        "v1-metrics": {"spatial_response_metrics": _response_view_metrics_v1(stored, baseline)},
        "no-cognition": {"cognition_input": None, "cognition_evaluation": None},
        "half-cognition": {"cognition_evaluation": None},
        "foreign-cognition": {
            "cognition_input": other.cognition_input,
            "cognition_evaluation": other.cognition_evaluation,
        },
        "no-workbench-hash": {
            "workbench_input_view": None,
            "cognition_summary_view": None,
            "cognition_records_view": None,
        },
        "partial-views": {"cognition_records_view": None},
        "foreign-input-view": {"workbench_input_view": other_views["workbench_input_view"]},
        "foreign-summary": {"cognition_summary_view": other_views["cognition_summary_view"]},
        "foreign-records": {"cognition_records_view": other_views["cognition_records_view"]},
        "v9": {"run_schema_version": 9},
        "boolean": {"run_schema_version": True},
        "float": {"run_schema_version": 10.0},
        "future": {"run_schema_version": 11},
    }
    for name, changes in hostile.items():
        candidate = arguments | changes
        sha = candidate["workbench_input_view"]
        with pytest.raises(ValueError):
            create_city_view_context(
                stored.mobility,
                workbench_input_sha256=(
                    None if sha is None else stored.manifest.workbench_input_sha256
                ),
                **candidate,
            )
            pytest.fail(f"{name} was accepted")
    # V9 contexts keep V1 evidence only: a V2 response or bounded cognition is refused.
    legacy = _response_view_case()
    simulation, scenario, opportunities, attention, response_input, responses = legacy
    with pytest.raises(ValueError, match="schema version 10 run"):
        create_city_view_context(
            simulation,
            run_id="legacy",
            run_schema_version=9,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=attention,
            response_input=response_input,
            response_evaluation=responses,
            cognition_input=stored.cognition_input,
            cognition_evaluation=stored.cognition_evaluation,
        )


def _response_view_metrics_v1(stored, baseline):
    attention_metrics = derive_spatial_metrics(
        stored.opportunity_evaluation,
        stored.attention_evaluation,
        agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
        agents_sha256=stored.manifest.agents_sha256,
        trace_sha256=stored.manifest.trace_sha256,
        days=stored.manifest.days,
        source_run_schema_version=9,
    )
    return derive_spatial_response_metrics(
        stored.response_input,
        baseline,
        scenario=stored.spatial_scenario,
        opportunities=stored.opportunity_evaluation,
        attention=stored.attention_evaluation,
        attention_metrics=attention_metrics,
        agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
    )


@pytest.mark.asyncio
async def test_saved_v10_zero_response_view_keeps_complete_states_and_empty_pages(
    saved_v10,
) -> None:
    stored = saved_v10["empty"]
    assert stored.response_evaluation.records == ()
    async with _v10_client(stored) as web:
        meta = (await web.get("/api/meta")).json()
        events = (await web.get("/api/response-events?minute=480")).json()
        state = (await web.get("/api/response-state")).json()
        summary = (await web.get("/api/cognition-summary")).json()
        records = (await web.get("/api/cognition-records")).json()
        timeline = (await web.get("/api/timeline?minute=480&agent_id=person-001")).json()

    assert (meta["response_count"], meta["state_update_count"]) == (0, 0)
    assert events["schema_version"] == 2
    assert (events["total"], events["items"], events["next_offset"]) == (0, [], None)
    assert events["event_type_counts"] == {
        "spatial.applied-response": 0,
        "spatial.dual-state-updated": 0,
    }
    assert events["channel_counts"] == {"roadside-billboard": 0, "mobile-feed": 0}
    assert events["agent_counts"] == {}
    assert state["total"] == len(stored.mobility.agents) == len(state["items"])
    assert all(item["cognition_state"]["response_count"] == 0 for item in state["items"])
    assert summary["summary"]["coverage"]["eligible_response_count"] == 0
    assert summary["summary"]["terminal_record_count"] == 0
    assert records["records"] == []
    assert timeline["response_page"] == events


@pytest.mark.asyncio
async def test_saved_v10_pages_enforce_the_existing_exact_bounds(saved_v10) -> None:
    stored = saved_v10["dense"]
    minute = next(
        record.model_minute
        for record in stored.response_evaluation.records
        if record.event_type == "spatial.dual-state-updated"
    )
    base = f"/api/response-events?minute={minute}"
    async with _v10_client(stored) as web:
        total = (await web.get(base)).json()["total"]
        at_end = (await web.get(f"{base}&offset={total}")).json()
        last = (await web.get(f"{base}&offset={total - 1}&limit=1")).json()
        refusals = [
            await web.get(f"{base}&limit=0"),
            await web.get(f"{base}&limit=101"),
            await web.get(f"{base}&offset={MAX_SPATIAL_RESPONSE_RECORDS + 1}"),
            await web.get("/api/response-events?minute=2880"),
            await web.get(f"{base}&agent_id=person-999"),
            await web.get("/api/response-state?offset=601"),
            await web.get("/api/response-state?agent_id=person-999"),
        ]
        write = await web.post(base, json={})

    assert (at_end["items"], at_end["next_offset"], at_end["total"]) == ([], None, total)
    assert last["items"][0]["event_type"] == "spatial.dual-state-updated"
    assert last["next_offset"] is None
    assert [response.status_code for response in refusals] == [422, 422, 422, 422, 400, 422, 400]
    assert write.status_code == 405
