"""Local-only FastAPI control plane for bounded city workbench runs."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.catalog import (
    CityCatalogError,
    UnknownCatalogCity,
    load_city_catalog,
)
from adlife.city.run_store import StoredCityRun
from adlife.city.web import (
    CityViewContext,
    city_dashboard_response,
    city_static_asset_response,
    city_validation_error_response,
    create_city_view_context,
    install_city_view_routes,
)
from adlife.city.workbench_creatives import load_creative_template_catalog
from adlife.city.workbench_jobs import (
    CityJobManager,
    WorkbenchJobBusy,
    WorkbenchJobCancellationConflict,
    WorkbenchJobManagerClosed,
    WorkbenchJobNotFound,
    WorkbenchJobSubmissionError,
)
from adlife.city.workbench_runs import (
    DEFAULT_RUN_PAGE_LIMIT,
    MAX_RUN_PAGE_LIMIT,
    MAX_RUN_PAGE_OFFSET,
    WorkbenchRunRepository,
)
from adlife.city.workbench_security import (
    MAX_WORKBENCH_BODY_BYTES,
    WorkbenchJsonError,
    WorkbenchSecurityMiddleware,
    new_csrf_token,
    strict_json_object,
    workbench_error,
)
from adlife.city.workbench_transport import (
    WorkbenchDraftTransportError,
    build_workbench_city_detail,
    build_workbench_run_input_view,
    parse_workbench_http_draft,
)
from adlife.city.workbench_validation import (
    ValidatedWorkbenchRun,
    WorkbenchValidationError,
    construct_workbench_run,
)
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    WorkbenchWorkspace,
    verify_workbench_workspace,
)
from adlife.core.domain.city_run import CityRunManifestV7
from adlife.core.ports.run_store import (
    DuplicateRun,
    RunNotFound,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_ASSET_LIMIT_BYTES = 262_144
_CANONICAL_UNSIGNED_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")
_JOB_ID = re.compile(r"^job-[0-9a-f]{32}$")
_WORKBENCH_FIELD_COMPONENTS = frozenset(
    {
        "active_windows",
        "advertising_skepticism",
        "agent_count",
        "brand_sentiment",
        "campaign",
        "campaign_id",
        "city_id",
        "cohort",
        "creative_template_id",
        "day",
        "days",
        "eligible_activities",
        "end_minute",
        "frequency_cap_per_agent_per_day",
        "impulsivity",
        "initial_state",
        "interests",
        "max_view_distance_meters",
        "mobile_recall_encoding",
        "name",
        "novelty_seeking",
        "opportunity_probability_per_minute",
        "orientation_degrees",
        "phone",
        "price_sensitivity",
        "purchase_intention",
        "recall_strength",
        "relative_price",
        "response_mode",
        "road_fraction",
        "road_id",
        "roadside",
        "roadside_recall_encoding",
        "run_id",
        "scenario",
        "scenario_id",
        "schema_version",
        "seed",
        "settings",
        "side",
        "start_minute",
        "target_interests",
        "traits",
        "travel_direction",
    }
)
_CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_SHELL_HEADERS = {
    "Content-Security-Policy": _CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class WorkbenchQueryError(ValueError):
    """A value-free refusal for a noncanonical or unbounded run-list query."""


class WorkbenchRunNotFound(ValueError):
    """A value-free refusal for an invalid or unknown completed run."""


class WorkbenchRunUnavailable(RuntimeError):
    """A value-free refusal for an artifact that cannot be freshly verified."""


class WorkbenchInputUnavailable(ValueError):
    """A verified legacy run has no frozen browser workbench input."""


def _read_packaged_text(relative: str) -> str:
    resource = files("adlife.city").joinpath("static", relative)
    try:
        with resource.open("rb") as source:
            data = source.read(_ASSET_LIMIT_BYTES + 1)
    except OSError:
        raise RuntimeError("workbench interface resource is unavailable") from None
    if len(data) > _ASSET_LIMIT_BYTES:
        raise RuntimeError("workbench interface resource exceeds its size limit")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeError("workbench interface resource is invalid") from None


def _validation_fields(errors: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for error in errors:
        raw_location = error.get("loc", ())
        location = raw_location if isinstance(raw_location, (list, tuple)) else ()
        components: list[str] = []
        safe = True
        for component in location:
            if (
                isinstance(component, str)
                and component in {"body", "query", "path"}
                and not components
            ):
                continue
            if isinstance(component, int) and not isinstance(component, bool) and component >= 0:
                components.append(str(component))
            elif isinstance(component, str) and component in _WORKBENCH_FIELD_COMPONENTS:
                components.append(component)
            else:
                safe = False
                break
        field = ".".join(components) if safe and components else "request"
        error_type = error.get("type")
        if error_type == "missing":
            message = "This field is required."
        elif error_type == "extra_forbidden":
            message = "Unknown field."
        else:
            message = "This field is invalid."
        fields.setdefault(field, message)
        if len(fields) == 32:
            break
    return fields


def _validation_summary(result: ValidatedWorkbenchRun) -> dict[str, object]:
    draft = result.workbench_input.draft
    scenario = result.scenario
    response_input = result.response_input
    template = result.workbench_input.creative_template
    placements = [
        {
            "placement_id": placement.placement_id,
            "channel": placement.channel,
        }
        for placement in scenario.placements
    ]
    channels = sorted({placement.channel for placement in scenario.placements})
    return {
        "schema_version": 1,
        "valid": True,
        "run": {
            "run_id": draft.settings.run_id,
            "agent_count": draft.settings.agent_count,
            "days": draft.settings.days,
            "seed": str(draft.settings.seed),
            "response_mode": draft.settings.response_mode,
        },
        "city": {
            "city_id": result.pack.city_id,
            "city_sha256": result.pack.fingerprint,
        },
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "campaign_id": scenario.campaigns[0].campaign_id,
            "placement_count": len(scenario.placements),
            "channels": channels,
            "placements": placements,
        },
        "creative": {
            "template_id": template.template_id,
            "creative_sha256": template.fingerprint,
        },
        "response": {
            "profile_count": len(response_input.profiles),
            "campaign_count": len(response_input.campaigns),
            "initial_state_count": len(response_input.initial_states),
        },
        "hashes": {
            "input_sha256": result.workbench_input.fingerprint,
            "city_sha256": result.pack.fingerprint,
            "scenario_sha256": scenario.fingerprint,
            "creative_sha256": template.fingerprint,
            "response_sha256": response_input.fingerprint,
        },
        "disclosure": {
            "population": "Synthetic cohort assumptions; not real residents.",
            "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
        },
    }


def _construct_validated_run(payload: dict[str, object]) -> ValidatedWorkbenchRun:
    return construct_workbench_run(parse_workbench_http_draft(payload))


def _pagination(request: Request) -> tuple[int, int]:
    items = request.query_params.multi_items()
    if len(items) > 2 or any(name not in {"offset", "limit"} for name, _value in items):
        raise WorkbenchQueryError("run pagination is invalid")
    values: dict[str, str] = {}
    for name, value in items:
        if name in values:
            raise WorkbenchQueryError("run pagination is invalid")
        values[name] = value

    def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
        value = values.get(name)
        if value is None:
            return default
        if len(value) > len(str(maximum)) or _CANONICAL_UNSIGNED_INTEGER.fullmatch(value) is None:
            raise WorkbenchQueryError("run pagination is invalid")
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise WorkbenchQueryError("run pagination is invalid")
        return parsed

    return (
        bounded("offset", 0, 0, MAX_RUN_PAGE_OFFSET),
        bounded("limit", DEFAULT_RUN_PAGE_LIMIT, 1, MAX_RUN_PAGE_LIMIT),
    )


def _require_empty_query(request: Request) -> None:
    if request.query_params.multi_items():
        raise WorkbenchQueryError("this route does not accept query fields")


def _require_job_id(job_id: str) -> str:
    if _JOB_ID.fullmatch(job_id) is None:
        raise WorkbenchJobNotFound("The workbench job does not exist.")
    return job_id


def create_city_workbench_app(
    workspace: WorkbenchWorkspace,
    *,
    csrf_token: str | None = None,
) -> FastAPI:
    """Build one process-local control app after rechecking its pinned workspace."""
    verify_workbench_workspace(workspace)
    token = new_csrf_token() if csrf_token is None else csrf_token
    repository = WorkbenchRunRepository(workspace)
    jobs = CityJobManager(repository)

    def verified_stored_run(run_id: object) -> StoredCityRun:
        try:
            portable_run_id = validate_run_id(run_id)
        except UnsafeRunLocation:
            raise WorkbenchRunNotFound from None
        try:
            stored = repository.load(portable_run_id)
        except RunNotFound:
            raise WorkbenchRunNotFound from None
        except UnsafeRunLocation:
            raise WorkbenchRunUnavailable from None
        except StorageError:
            raise WorkbenchRunUnavailable from None
        return stored

    def verified_city_view(run_id: object) -> CityViewContext:
        stored = verified_stored_run(run_id)
        schema_version = stored.manifest.schema_version
        try:
            spatial_metrics = metrics_for_stored_city_run(stored) if schema_version >= 5 else None
            response_metrics = (
                response_metrics_for_stored_city_run(stored) if schema_version >= 6 else None
            )
            return create_city_view_context(
                stored.mobility,
                run_id=stored.manifest.run_id,
                run_schema_version=schema_version,
                spatial_scenario=stored.spatial_scenario,
                opportunity_evaluation=stored.opportunity_evaluation,
                attention_evaluation=stored.attention_evaluation,
                spatial_metrics=spatial_metrics,
                response_input=stored.response_input,
                response_evaluation=stored.response_evaluation,
                spatial_response_metrics=response_metrics,
                workbench_input_sha256=(
                    stored.manifest.workbench_input_sha256
                    if isinstance(stored.manifest, CityRunManifestV7)
                    else None
                ),
            )
        except (StorageError, ValueError):
            raise WorkbenchRunUnavailable from None

    def resolve_city_view(request: Request) -> CityViewContext:
        return verified_city_view(request.path_params.get("run_id"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            jobs.close()

    app = FastAPI(
        title="AdLife local city workbench",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(_LOOPBACK_HOSTS),
        www_redirect=False,
    )
    app.add_middleware(WorkbenchSecurityMiddleware, csrf_token=token)

    @app.exception_handler(WorkbenchJsonError)
    async def malformed_json_handler(
        _request: Request,
        _error: WorkbenchJsonError,
    ) -> JSONResponse:
        return workbench_error(
            400,
            "malformed-json",
            "The request body is not valid JSON.",
        )

    async def validation_error_response(error: ValidationError) -> JSONResponse:
        return workbench_error(
            422,
            "invalid-fields",
            "One or more request fields are invalid.",
            _validation_fields(error.errors(include_url=False, include_context=False)),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        if request.url.path.startswith("/api/runs/"):
            return city_validation_error_response()
        return workbench_error(
            422,
            "invalid-fields",
            "One or more request fields are invalid.",
            _validation_fields(error.errors()),
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(
        _request: Request,
        error: ValidationError,
    ) -> JSONResponse:
        return await validation_error_response(error)

    @app.exception_handler(WorkbenchDraftTransportError)
    async def draft_transport_error_handler(
        _request: Request,
        error: WorkbenchDraftTransportError,
    ) -> JSONResponse:
        return workbench_error(
            422,
            "invalid-fields",
            "One or more request fields are invalid.",
            {error.field: error.message},
        )

    @app.exception_handler(WorkbenchValidationError)
    async def workbench_validation_handler(
        _request: Request,
        error: WorkbenchValidationError,
    ) -> JSONResponse:
        if error.code in {"unknown-city", "unknown-creative-template"}:
            status_code = 404
        elif error.code in {"city-catalog-unavailable", "creative-catalog-unavailable"}:
            status_code = 500
        else:
            status_code = 400
        return workbench_error(
            status_code,
            error.code,
            "The scenario could not be validated.",
            {error.field: error.safe_message},
        )

    @app.exception_handler(UnknownCatalogCity)
    async def unknown_catalog_city_handler(
        _request: Request,
        _error: UnknownCatalogCity,
    ) -> JSONResponse:
        return workbench_error(404, "not-found", "The requested resource was not found.")

    @app.exception_handler(CityCatalogError)
    async def city_catalog_error_handler(
        _request: Request,
        _error: CityCatalogError,
    ) -> JSONResponse:
        return workbench_error(
            500,
            "catalog-unavailable",
            "The verified city catalog is unavailable.",
        )

    @app.exception_handler(WorkbenchQueryError)
    async def query_error_handler(
        _request: Request,
        _error: WorkbenchQueryError,
    ) -> JSONResponse:
        return workbench_error(
            422,
            "invalid-query",
            "One or more query fields are invalid.",
            {"request": "The query is invalid."},
        )

    @app.exception_handler(WorkbenchJobNotFound)
    async def job_not_found_handler(
        _request: Request,
        _error: WorkbenchJobNotFound,
    ) -> JSONResponse:
        return workbench_error(404, "job-not-found", "The workbench job was not found.")

    @app.exception_handler(DuplicateRun)
    async def duplicate_run_handler(
        _request: Request,
        _error: DuplicateRun,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "run-conflict",
            "The run identifier is already in use.",
        )

    @app.exception_handler(WorkbenchJobBusy)
    async def job_busy_handler(
        _request: Request,
        _error: WorkbenchJobBusy,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "job-busy",
            "Another workbench run is already active.",
        )

    @app.exception_handler(WorkbenchJobCancellationConflict)
    async def cancellation_conflict_handler(
        _request: Request,
        _error: WorkbenchJobCancellationConflict,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "job-state-conflict",
            "The workbench job can no longer be cancelled.",
        )

    @app.exception_handler(WorkbenchJobManagerClosed)
    @app.exception_handler(WorkbenchJobSubmissionError)
    async def worker_unavailable_handler(
        _request: Request,
        _error: WorkbenchJobManagerClosed | WorkbenchJobSubmissionError,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "worker-unavailable",
            "The workbench worker is not accepting jobs.",
        )

    @app.exception_handler(UnsafeWorkbenchWorkspace)
    async def workspace_unavailable_handler(
        _request: Request,
        _error: UnsafeWorkbenchWorkspace,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "workspace-unavailable",
            "The workbench workspace is unavailable.",
        )

    @app.exception_handler(WorkbenchRunNotFound)
    async def run_not_found_handler(
        _request: Request,
        _error: WorkbenchRunNotFound,
    ) -> JSONResponse:
        return workbench_error(404, "not-found", "The requested resource was not found.")

    @app.exception_handler(WorkbenchRunUnavailable)
    async def run_unavailable_handler(
        _request: Request,
        _error: WorkbenchRunUnavailable,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "run-unavailable",
            "The saved run could not be verified.",
        )

    @app.exception_handler(WorkbenchInputUnavailable)
    async def workbench_input_unavailable_handler(
        _request: Request,
        _error: WorkbenchInputUnavailable,
    ) -> JSONResponse:
        return workbench_error(
            404,
            "workbench-input-unavailable",
            "This saved run has no browser workbench input.",
        )

    @app.exception_handler(StorageError)
    async def storage_conflict_handler(
        _request: Request,
        _error: StorageError,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "artifact-conflict",
            "The workbench artifact state conflicts with this request.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        status_code = error.status_code if 400 <= error.status_code <= 599 else 500
        if request.url.path.startswith("/api/runs/"):
            return JSONResponse(
                status_code=status_code,
                content={"detail": error.detail},
            )
        if status_code == 404:
            return workbench_error(404, "not-found", "The requested resource was not found.")
        return workbench_error(
            status_code,
            "request-failed",
            "The request could not be completed.",
        )

    @app.get("/", include_in_schema=False)
    def shell() -> HTMLResponse:
        template = _read_packaged_text("workbench.html")
        marker = "__ADLIFE_CSRF_TOKEN__"
        if template.count(marker) != 1:
            raise RuntimeError("workbench interface token marker is invalid")
        rendered = template.replace(marker, html.escape(token, quote=True))
        return HTMLResponse(rendered, headers=_SHELL_HEADERS)

    @app.get("/assets/workbench.css", include_in_schema=False)
    def stylesheet() -> Response:
        return Response(
            _read_packaged_text("workbench.css"),
            media_type="text/css",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/assets/workbench.js", include_in_schema=False)
    def client_script() -> Response:
        return Response(
            _read_packaged_text("workbench.js"),
            media_type="text/javascript",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/assets/workbench-icon.svg", include_in_schema=False)
    def icon() -> Response:
        return Response(
            _read_packaged_text("workbench-icon.svg"),
            media_type="image/svg+xml",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/static/{asset_name}", include_in_schema=False)
    def city_static_asset(asset_name: str) -> Response:
        return city_static_asset_response(asset_name)

    @app.get("/runs/{run_id}", include_in_schema=False)
    def inspect_run(run_id: str, request: Request) -> HTMLResponse:
        _require_empty_query(request)
        context = verified_city_view(run_id)
        assert context.run_id is not None
        return city_dashboard_response(api_base=f"/api/runs/{context.run_id}")

    @app.get("/api/workbench")
    def capabilities(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        active = jobs.active_job()
        return {
            "schema_version": 1,
            "phase": "bounded-jobs",
            "capabilities": {
                "scenario_validation": True,
                "jobs": True,
                "run_execution": True,
                "run_inspection": True,
                "provider_configuration": False,
                "oauth": False,
                "response_modes": ["deterministic-rules"],
            },
            "active_job": None if active is None else active.model_dump(mode="json"),
            "limits": {
                "request_body_bytes": MAX_WORKBENCH_BODY_BYTES,
                "agent_count": {"minimum": 1, "maximum": 30},
                "days": {"minimum": 1, "maximum": 7},
                "seed": {
                    "minimum": "0",
                    "maximum": "9223372036854775807",
                },
                "placements": ["mobile-feed", "roadside-billboard"],
            },
            "disclosure": {
                "population": "Synthetic cohort assumptions; not real residents.",
                "city": "Bundled fictional city geometry; not a live city or traffic feed.",
                "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
            },
        }

    @app.get("/api/catalog/cities")
    def cities(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        catalog = load_city_catalog()
        entries: list[dict[str, object]] = []
        for entry in catalog.entries:
            document = entry.model_dump(mode="json")
            document.pop("resource_name", None)
            entries.append(document)
        return {"schema_version": 1, "cities": entries}

    @app.get("/api/catalog/cities/{city_id}")
    def city_detail(city_id: str, request: Request) -> dict[str, object]:
        _require_empty_query(request)
        return build_workbench_city_detail(city_id).model_dump(mode="json")

    @app.get("/api/creative-templates")
    def creative_templates(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        catalog = load_creative_template_catalog()
        templates = [
            {
                **template.model_dump(mode="json"),
                "creative_sha256": template.fingerprint,
            }
            for template in catalog.templates
        ]
        return {"schema_version": 1, "templates": templates}

    @app.post("/api/scenarios/validate")
    async def validate_scenario(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        payload = strict_json_object(await request.body())
        result = _construct_validated_run(payload)
        return _validation_summary(result)

    @app.get("/api/runs")
    def list_runs(request: Request) -> dict[str, object]:
        offset, limit = _pagination(request)
        return repository.list_runs(offset=offset, limit=limit).model_dump(mode="json")

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, request: Request) -> dict[str, object]:
        _require_empty_query(request)
        return jobs.get(_require_job_id(job_id)).model_dump(mode="json")

    @app.post("/api/jobs")
    async def submit_job(request: Request) -> JSONResponse:
        _require_empty_query(request)
        payload = strict_json_object(await request.body())
        validated = _construct_validated_run(payload)
        job = jobs.submit(validated)
        return JSONResponse(
            status_code=202,
            content={
                "schema_version": 1,
                "job": job.model_dump(mode="json"),
                "status_url": f"/api/jobs/{job.job_id}",
            },
        )

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request) -> JSONResponse:
        _require_empty_query(request)
        payload = strict_json_object(await request.body())
        if payload:
            return workbench_error(
                422,
                "invalid-fields",
                "One or more request fields are invalid.",
                {"request": "Unknown field."},
            )
        return JSONResponse(content=jobs.cancel(_require_job_id(job_id)).model_dump(mode="json"))

    @app.get("/api/runs/{run_id}/workbench-input")
    def workbench_run_input(run_id: str, request: Request) -> JSONResponse:
        _require_empty_query(request)
        stored = verified_stored_run(run_id)
        if not isinstance(stored.manifest, CityRunManifestV7):
            raise WorkbenchInputUnavailable
        try:
            view = build_workbench_run_input_view(stored)
        except ValueError:
            raise WorkbenchRunUnavailable from None
        return JSONResponse(
            content=view.model_dump(mode="json"),
            headers=_SHELL_HEADERS,
        )

    install_city_view_routes(
        app,
        prefix="/api/runs/{run_id}",
        resolver=resolve_city_view,
    )
    return app


__all__ = ["create_city_workbench_app"]
