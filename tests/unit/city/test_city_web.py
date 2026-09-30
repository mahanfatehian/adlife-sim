import httpx
import pytest

from adlife.city.web import create_city_app
from adlife.core.simulation.city_mobility import CityMobility
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data


def client() -> httpx.AsyncClient:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=3, days=2)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_city_app(simulation)),
        base_url="http://city.test",
    )


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
        base_url="http://city.test",
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
        base_url="http://city.test",
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
        base_url="http://city.test",
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
    assert "Synthetic mobility pilot" in html.text
    assert "not a prediction" in html.text
    assert "1-MINUTE MOBILITY FRAMES" in html.text
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
    assert 'fetchJson("/api/places")' in js.text
    assert 'fetchJson("/api/place-assignments")' in js.text
    assert 'byId("model-label").textContent' in js.text
    assert "drawPlaceMarker" in js.text
    assert "state.placeAssignments" in js.text
    assert "list.replaceChildren()" not in js.text
    assert "http://" not in html.text and "https://" not in html.text
    assert "textContent" in js.text
    assert "innerHTML" not in js.text
    assert "Content-Security-Policy" in html.headers


@pytest.mark.asyncio
async def test_saved_run_identity_is_visible_but_ephemeral_viewer_is_not_mislabeled() -> None:
    simulation = CityMobility(load_pack(pack_data()), seed=42, agent_count=2, days=1)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_city_app(simulation, run_id="saved-study")),
            base_url="http://city.test",
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
        base_url="http://city.test",
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
        base_url="http://city.test",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
    assert metadata["saved"] is True
    assert metadata["run_id"] == "saved-v3-study"
    assert metadata["run_schema_version"] == 3
    assert metadata["place_set_sha256"] == places.fingerprint
