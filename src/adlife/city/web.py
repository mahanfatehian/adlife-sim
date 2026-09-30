"""Read-only loopback web adapter for the deterministic city mobility pilot."""

from __future__ import annotations

from dataclasses import asdict
from importlib.resources import files

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from adlife.core.simulation.city_mobility import CityMobility

_ASSETS = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
)


def create_city_app(
    simulation: CityMobility,
    *,
    run_id: str | None = None,
    run_schema_version: int | None = None,
) -> FastAPI:
    """Serve only this loaded immutable city and its derived, deterministic frames."""
    if run_id is None and run_schema_version is not None:
        raise ValueError("run schema version requires a saved run identifier")
    if run_schema_version is not None and (
        type(run_schema_version) is not int or run_schema_version not in {1, 2, 3}
    ):
        raise ValueError("unsupported city run schema version")
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
            headers={"Content-Security-Policy": _CSP, "X-Content-Type-Options": "nosniff"},
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
