"""Local-only FastAPI composition for the city workbench validation foundation."""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from adlife.city.catalog import load_city_catalog
from adlife.city.workbench_creatives import load_creative_template_catalog
from adlife.city.workbench_input import WorkbenchRunDraft
from adlife.city.workbench_security import (
    MAX_WORKBENCH_BODY_BYTES,
    WorkbenchJsonError,
    WorkbenchSecurityMiddleware,
    new_csrf_token,
    strict_json_object,
    workbench_error,
)
from adlife.city.workbench_validation import (
    ValidatedWorkbenchRun,
    WorkbenchValidationError,
    construct_workbench_run,
)
from adlife.city.workbench_workspace import (
    WorkbenchWorkspace,
    verify_workbench_workspace,
)

_LOGGER = logging.getLogger(__name__)
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_ASSET_LIMIT_BYTES = 262_144
_SAFE_FIELD_COMPONENT = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
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
            elif (
                isinstance(component, str)
                and _SAFE_FIELD_COMPONENT.fullmatch(component) is not None
            ):
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
            "seed": draft.settings.seed,
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


def create_city_workbench_app(
    workspace: WorkbenchWorkspace,
    *,
    csrf_token: str | None = None,
) -> FastAPI:
    """Build one process-local validation app after rechecking its pinned workspace."""
    verify_workbench_workspace(workspace)
    token = new_csrf_token() if csrf_token is None else csrf_token
    app = FastAPI(
        title="AdLife local city workbench",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
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

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        status_code = error.status_code if 400 <= error.status_code <= 599 else 500
        if status_code == 404:
            return workbench_error(404, "not-found", "The requested resource was not found.")
        return workbench_error(
            status_code,
            "request-failed",
            "The request could not be completed.",
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        error: Exception,
    ) -> JSONResponse:
        _LOGGER.error("workbench request failed with %s", type(error).__name__)
        return workbench_error(
            500,
            "internal-error",
            "The workbench could not complete the request.",
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

    @app.get("/api/workbench")
    def capabilities() -> dict[str, object]:
        return {
            "schema_version": 1,
            "phase": "validation-foundation",
            "capabilities": {
                "scenario_validation": True,
                "jobs": False,
                "run_execution": False,
                "run_inspection": False,
                "provider_configuration": False,
                "oauth": False,
                "response_modes": ["deterministic-rules"],
            },
            "active_job": None,
            "limits": {
                "request_body_bytes": MAX_WORKBENCH_BODY_BYTES,
                "agent_count": {"minimum": 1, "maximum": 30},
                "days": {"minimum": 1, "maximum": 7},
                "placements": ["mobile-feed", "roadside-billboard"],
            },
            "disclosure": {
                "population": "Synthetic cohort assumptions; not real residents.",
                "city": "Bundled fictional city geometry; not a live city or traffic feed.",
                "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
            },
        }

    @app.get("/api/catalog/cities")
    def cities() -> dict[str, object]:
        catalog = load_city_catalog()
        entries: list[dict[str, object]] = []
        for entry in catalog.entries:
            document = entry.model_dump(mode="json")
            document.pop("resource_name", None)
            entries.append(document)
        return {"schema_version": 1, "cities": entries}

    @app.get("/api/creative-templates")
    def creative_templates() -> dict[str, object]:
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
        payload = strict_json_object(await request.body())
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        draft = WorkbenchRunDraft.model_validate_json(encoded)
        result = construct_workbench_run(draft)
        return _validation_summary(result)

    return app


__all__ = ["create_city_workbench_app"]
