"""Read-only loopback web adapter for the deterministic city mobility pilot."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict
from importlib.resources import files

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    validate_spatial_scenario_against_city,
)
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_attention import (
    MAX_SPATIAL_ATTENTION_EVENTS,
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
)
from adlife.core.simulation.spatial_opportunity import (
    MAX_SPATIAL_OPPORTUNITIES,
    SpatialOpportunityEvaluation,
)

_ASSETS = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
_DASHBOARD_HEADERS = {
    "Content-Security-Policy": _CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def create_city_app(
    simulation: CityMobility,
    *,
    run_id: str | None = None,
    run_schema_version: int | None = None,
    spatial_scenario: SpatialCampaignScenario | None = None,
    opportunity_evaluation: SpatialOpportunityEvaluation | None = None,
    attention_evaluation: SpatialAttentionEvaluation | None = None,
    spatial_metrics: SpatialMetrics | None = None,
) -> FastAPI:
    """Serve only this loaded immutable city and its derived, deterministic frames."""
    if run_id is None and run_schema_version is not None:
        raise ValueError("run schema version requires a saved run identifier")
    if run_schema_version is not None and (
        type(run_schema_version) is not int or run_schema_version not in {1, 2, 3, 4, 5}
    ):
        raise ValueError("unsupported city run schema version")
    if (spatial_scenario is None) != (opportunity_evaluation is None):
        raise ValueError("spatial scenario and opportunity evaluation must be supplied together")
    has_spatial_evidence = spatial_scenario is not None
    if run_schema_version == 4 and not has_spatial_evidence:
        raise ValueError("schema version 4 requires spatial opportunity evidence")
    if run_schema_version == 5 and (not has_spatial_evidence or attention_evaluation is None):
        raise ValueError("schema version 5 requires spatial attention evidence")
    if has_spatial_evidence and (run_id is None or run_schema_version not in {4, 5}):
        raise ValueError(
            "spatial opportunity evidence is only valid for a saved schema version 4 or 5 run"
        )
    if attention_evaluation is not None and (run_id is None or run_schema_version != 5):
        raise ValueError(
            "spatial attention evidence is only valid for a saved schema version 5 run"
        )
    if spatial_metrics is not None and (run_id is None or run_schema_version != 5):
        raise ValueError("spatial metrics are only valid for a saved schema version 5 run")
    if spatial_scenario is not None and opportunity_evaluation is not None:
        spatial_scenario = SpatialCampaignScenario.model_validate(
            spatial_scenario.model_dump(mode="python")
        )
        opportunity_evaluation = SpatialOpportunityEvaluation.model_validate(
            opportunity_evaluation.model_dump(mode="python")
        )
        validate_spatial_scenario_against_city(spatial_scenario, simulation.pack)
        if spatial_scenario.days != simulation.days:
            raise ValueError("spatial scenario duration does not match city mobility")
        if (
            opportunity_evaluation.scenario_sha256 != spatial_scenario.fingerprint
            or opportunity_evaluation.city_sha256 != simulation.pack.fingerprint
        ):
            raise ValueError("spatial opportunity evidence does not match its saved inputs")
    if attention_evaluation is not None and opportunity_evaluation is not None:
        attention_evaluation = SpatialAttentionEvaluation.model_validate(
            attention_evaluation.model_dump(mode="python")
        )
        if attention_evaluation != evaluate_spatial_attention(
            opportunity_evaluation,
            seed=simulation.seed,
        ):
            raise ValueError("spatial attention evidence does not match its saved inputs")
    if spatial_metrics is not None:
        if opportunity_evaluation is None or attention_evaluation is None:
            raise ValueError("spatial metrics require opportunity and attention evidence")
        spatial_metrics = SpatialMetrics.model_validate(spatial_metrics.model_dump(mode="python"))
        expected_metrics = derive_spatial_metrics(
            opportunity_evaluation,
            attention_evaluation,
            agent_ids=tuple(agent.agent_id for agent in simulation.agents),
            agents_sha256=spatial_metrics.agents_sha256,
            trace_sha256=spatial_metrics.trace_sha256,
            days=simulation.days,
        )
        if spatial_metrics != expected_metrics:
            raise ValueError("spatial metrics do not match their saved inputs")
    opportunity_minutes = (
        tuple(item.model_minute for item in opportunity_evaluation.opportunities)
        if opportunity_evaluation is not None
        else ()
    )
    attention_minutes = (
        tuple(item.model_minute for item in attention_evaluation.events)
        if attention_evaluation is not None
        else ()
    )
    app = FastAPI(
        title="AdLife city mobility pilot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", include_in_schema=False)
    def dashboard() -> HTMLResponse:
        html = files("adlife.city").joinpath("static/index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            headers=_DASHBOARD_HEADERS,
        )

    @app.get("/static/{asset_name}", include_in_schema=False)
    def static_asset(asset_name: str) -> Response:
        if asset_name not in _ASSETS:
            raise HTTPException(status_code=404, detail="asset not found")
        asset = files("adlife.city").joinpath("static", asset_name).read_bytes()
        return Response(
            asset,
            media_type=_ASSETS[asset_name],
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/meta")
    def metadata() -> dict[str, object]:
        document = simulation.metadata()
        if run_id is not None:
            document["saved"] = True
            document["run_id"] = run_id
            document["run_schema_version"] = (
                simulation.pack.schema_version if run_schema_version is None else run_schema_version
            )
        if opportunity_evaluation is not None:
            document["spatial_opportunities"] = True
            document["claim_scope"] = "synthetic-opportunity-not-impression"
            document["opportunity_count"] = opportunity_evaluation.counts.opportunity_count
        if attention_evaluation is not None:
            document["spatial_attention"] = True
            document["attention_model_id"] = attention_evaluation.model_id
            document["attention_claim_scope"] = attention_evaluation.claim_scope
            document["attention_notice_probability"] = attention_evaluation.notice_probability
            document["impression_count"] = attention_evaluation.counts.impression_count
            document["noticed_count"] = attention_evaluation.counts.noticed_count
        if spatial_metrics is not None:
            document["spatial_metrics"] = True
        return document

    @app.get("/api/city")
    def city() -> dict[str, object]:
        return simulation.pack.model_dump(mode="json")

    @app.get("/api/agents")
    def agents() -> list[dict[str, object]]:
        return [asdict(agent) for agent in simulation.agents]

    @app.get("/api/places")
    def places() -> dict[str, object]:
        if simulation.places is None:
            raise HTTPException(status_code=404, detail="synthetic places not configured")
        return simulation.places.model_dump(mode="json")

    @app.get("/api/place-assignments")
    def place_assignments() -> dict[str, object]:
        if simulation.places is None:
            raise HTTPException(status_code=404, detail="synthetic places not configured")
        return simulation.place_assignment_document()

    @app.get("/api/opportunity-summary")
    def opportunity_summary() -> dict[str, object]:
        if spatial_scenario is None or opportunity_evaluation is None:
            raise HTTPException(
                status_code=404,
                detail="spatial opportunity evidence not configured",
            )
        return {
            "schema_version": 1,
            "scenario_id": spatial_scenario.scenario_id,
            "scenario_name": spatial_scenario.name,
            "scenario_sha256": spatial_scenario.fingerprint,
            "opportunity_model_id": opportunity_evaluation.model_id,
            "claim_scope": "synthetic-opportunity-not-impression",
            "counts": opportunity_evaluation.counts.model_dump(mode="json"),
            "campaigns": [item.model_dump(mode="json") for item in spatial_scenario.campaigns],
            "placements": [item.model_dump(mode="json") for item in spatial_scenario.placements],
        }

    @app.get("/api/opportunities")
    def opportunities(
        minute: int = Query(ge=0),
        agent_id: str | None = Query(default=None, pattern=r"^person-[0-9]{3}$"),
        offset: int = Query(default=0, ge=0, le=MAX_SPATIAL_OPPORTUNITIES),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, object]:
        if opportunity_evaluation is None:
            raise HTTPException(
                status_code=404,
                detail="spatial opportunity evidence not configured",
            )
        if minute >= simulation.days * 1_440:
            raise HTTPException(status_code=422, detail="minute is outside the saved run")
        if agent_id is not None and all(agent.agent_id != agent_id for agent in simulation.agents):
            raise HTTPException(status_code=400, detail="unknown city agent")
        start = bisect_left(opportunity_minutes, minute)
        stop = bisect_right(opportunity_minutes, minute)
        matching = opportunity_evaluation.opportunities[start:stop]
        if agent_id is not None:
            matching = tuple(item for item in matching if item.agent_id == agent_id)
        total = len(matching)
        channel_counts = {
            "roadside-billboard": sum(item.channel == "roadside-billboard" for item in matching),
            "mobile-feed": sum(item.channel == "mobile-feed" for item in matching),
        }
        agent_counts = {
            known_agent.agent_id: sum(item.agent_id == known_agent.agent_id for item in matching)
            for known_agent in simulation.agents
            if any(item.agent_id == known_agent.agent_id for item in matching)
        }
        page = matching[offset : offset + limit]
        consumed = offset + len(page)
        return {
            "schema_version": 1,
            "claim_scope": "synthetic-opportunity-not-impression",
            "minute": minute,
            "agent_id": agent_id,
            "offset": offset,
            "limit": limit,
            "total": total,
            "channel_counts": channel_counts,
            "agent_counts": agent_counts,
            "next_offset": consumed if consumed < total else None,
            "items": [item.model_dump(mode="json") for item in page],
        }

    @app.get("/api/attention-summary")
    def attention_summary() -> dict[str, object]:
        if (
            spatial_scenario is None
            or opportunity_evaluation is None
            or attention_evaluation is None
        ):
            raise HTTPException(
                status_code=404,
                detail="spatial attention evidence not configured",
            )
        return {
            "schema_version": 1,
            "scenario_id": spatial_scenario.scenario_id,
            "scenario_name": spatial_scenario.name,
            "scenario_sha256": spatial_scenario.fingerprint,
            "attention_model_id": attention_evaluation.model_id,
            "claim_scope": attention_evaluation.claim_scope,
            "notice_probability": attention_evaluation.notice_probability,
            "counts": attention_evaluation.counts.model_dump(mode="json"),
        }

    @app.get("/api/spatial-metrics")
    def metrics() -> dict[str, object]:
        if spatial_metrics is None:
            raise HTTPException(status_code=404, detail="spatial metrics not configured")
        return spatial_metrics.model_dump(mode="json")

    @app.get("/api/attention-events")
    def attention_events(
        minute: int = Query(ge=0),
        agent_id: str | None = Query(default=None, pattern=r"^person-[0-9]{3}$"),
        offset: int = Query(default=0, ge=0, le=MAX_SPATIAL_ATTENTION_EVENTS),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, object]:
        if attention_evaluation is None:
            raise HTTPException(
                status_code=404,
                detail="spatial attention evidence not configured",
            )
        if minute >= simulation.days * 1_440:
            raise HTTPException(status_code=422, detail="minute is outside the saved run")
        if agent_id is not None and all(agent.agent_id != agent_id for agent in simulation.agents):
            raise HTTPException(status_code=400, detail="unknown city agent")
        start = bisect_left(attention_minutes, minute)
        stop = bisect_right(attention_minutes, minute)
        matching = attention_evaluation.events[start:stop]
        if agent_id is not None:
            matching = tuple(item for item in matching if item.agent_id == agent_id)
        total = len(matching)
        event_type_counts = {
            "spatial.impression": sum(item.event_type == "spatial.impression" for item in matching),
            "spatial.noticed": sum(item.event_type == "spatial.noticed" for item in matching),
        }
        channel_counts = {
            "roadside-billboard": sum(item.channel == "roadside-billboard" for item in matching),
            "mobile-feed": sum(item.channel == "mobile-feed" for item in matching),
        }
        agent_counts = {
            known_agent.agent_id: sum(item.agent_id == known_agent.agent_id for item in matching)
            for known_agent in simulation.agents
            if any(item.agent_id == known_agent.agent_id for item in matching)
        }
        page = matching[offset : offset + limit]
        consumed = offset + len(page)
        return {
            "schema_version": 1,
            "attention_model_id": attention_evaluation.model_id,
            "claim_scope": attention_evaluation.claim_scope,
            "minute": minute,
            "agent_id": agent_id,
            "offset": offset,
            "limit": limit,
            "total": total,
            "event_type_counts": event_type_counts,
            "channel_counts": channel_counts,
            "agent_counts": agent_counts,
            "next_offset": consumed if consumed < total else None,
            "items": [item.model_dump(mode="json") for item in page],
        }

    @app.get("/api/frame")
    def frame(
        minute: int = Query(ge=0), agent_id: str | None = Query(default=None)
    ) -> dict[str, object]:
        try:
            return simulation.frame_document(minute, selected_agent_id=agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid minute or city agent") from None

    return app


__all__ = ["create_city_app"]
