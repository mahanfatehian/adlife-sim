"""Local-only FastAPI control plane for bounded city workbench runs."""

from __future__ import annotations

import copy
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any, Literal, get_args, get_origin

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from adlife.adapters.cognition.city_credentials import (
    CityCredentialStore,
    CityCredentialStoreError,
    OSKeyringCityCredentialStore,
)
from adlife.adapters.cognition.city_endpoint_policy import (
    CityEndpointBindingError,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_openai_compatible import CityProviderTransportError
from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.catalog import (
    CityCatalogError,
    UnknownCatalogCity,
)
from adlife.city.guide_web import (
    GuideRouteContext,
    GuideRunMissing,
    GuideRunUnverifiable,
    install_guide_routes,
)
from adlife.city.provider_compatibility import (
    ProviderEffectScope,
    WorkbenchProviderCompatibilityRegistry,
    WorkbenchProviderCompatibilityRequired,
    build_workbench_provider_compatibility_key,
)
from adlife.city.provider_profiles import (
    InvalidProviderProfile,
    ProviderProfileConflict,
    ProviderProfileNotFound,
    ProviderProfilePublicationError,
    ProviderProfileRegistry,
    ProviderProfileRegistryUnavailable,
)
from adlife.city.provider_runtime import (
    ProviderOriginRegistry,
    ProviderRuntimePolicy,
    build_provider_runtime_policy,
)
from adlife.city.run_store import StoredCityRun
from adlife.city.web import (
    CityViewContext,
    city_validation_error_response,
    create_city_view_context,
    install_city_view_routes,
)
from adlife.city.workbench_catalog import (
    PackagedCityCatalogUnavailable,
    UnknownWorkbenchCity,
    WorkbenchCityRegistryUnavailable,
    WorkbenchCityResolver,
)
from adlife.city.workbench_city_registry import (
    MAX_CITY_PACK_UPLOAD_BYTES,
    CityPackPublicationError,
    CityPackRegistrationConflict,
    CityPackUploadTooLarge,
    InvalidCityPackUpload,
    MalformedCityPackUpload,
)
from adlife.city.workbench_cognition_views import (
    build_saved_workbench_run_input_view,
    build_workbench_cognition_records_view,
    build_workbench_cognition_summary_view,
)
from adlife.city.workbench_creatives import load_creative_template_catalog
from adlife.city.workbench_input import (
    WorkbenchCognitionDraftV4,
    WorkbenchRunDraftDocument,
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
    WorkbenchRunDraftV4,
)
from adlife.city.workbench_jobs import (
    CityJobManager,
    WorkbenchJobBusy,
    WorkbenchJobCancellationConflict,
    WorkbenchJobManagerClosed,
    WorkbenchJobNotFound,
    WorkbenchJobSubmissionError,
)
from adlife.city.workbench_personas import (
    PersonaTemplateCatalogError,
    load_persona_template_catalog,
)
from adlife.city.workbench_providers import (
    InvalidProviderOrigin,
    WorkbenchCheckProviderFactory,
    WorkbenchCheckProviderFactoryV2,
    WorkbenchOperationState,
    WorkbenchProviderBusy,
    WorkbenchProviderCheckResultV2,
    WorkbenchProviderClosed,
    WorkbenchProviderController,
    WorkbenchProviderEndpointRefused,
    WorkbenchProviderOriginLimit,
    WorkbenchProviderRateLimited,
)
from adlife.city.workbench_runs import (
    DEFAULT_RUN_PAGE_LIMIT,
    MAX_RUN_PAGE_LIMIT,
    MAX_RUN_PAGE_OFFSET,
    WorkbenchRunRepository,
)
from adlife.city.workbench_security import (
    MAX_WORKBENCH_BODY_BYTES,
    WorkbenchJsonContentError,
    WorkbenchJsonError,
    WorkbenchSecurityMiddleware,
    new_csrf_token,
    strict_json_object,
    workbench_error,
)
from adlife.city.workbench_transport import (
    WorkbenchDraftTransportError,
    build_workbench_city_catalog_row,
    build_workbench_city_detail,
    parse_workbench_credential_submission,
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
from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_SELECTION_LIMITS,
    CITY_COGNITION_THOUGHT_LANGUAGES,
    CITY_COGNITION_THOUGHT_MAX_CHARACTERS,
    CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT,
    CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    city_cognition_thought_languages,
)
from adlife.core.domain.city_run import (
    CityRunManifestV7,
    CityRunManifestV8,
    CityRunManifestV9,
    CityRunManifestV10,
)
from adlife.core.ports.run_store import (
    DuplicateRun,
    RunNotFound,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_ASSET_LIMIT_BYTES = 262_144
_RUN_ID_IN_USE_MESSAGE = "A saved run already uses this ID. Choose a new run ID."
_CANONICAL_UNSIGNED_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")
_JOB_ID = re.compile(r"^job-[0-9a-f]{32}$")
_CANONICAL_PERSONA_FIELD = re.compile(r"population\.personas\.(0|[1-9][0-9]?)(\.[a-z_]+)?")
# The removed saved-run inspector page sent this snapshot header; nothing issues it now.
_VIEW_TOKEN_HEADER = b"x-adlife-view-token"
_WORKBENCH_FIELD_COMPONENTS = frozenset(
    {
        "active_windows",
        "advertising_skepticism",
        "agent_count",
        "age",
        "aggregate_output_token_ceiling",
        "background",
        "brand_sentiment",
        "campaign",
        "campaign_id",
        "city_id",
        "cognition",
        "cohort",
        "count",
        "creative_template_id",
        "day",
        "days",
        "display_name",
        "effect_scope",
        "eligible_activities",
        "end_minute",
        "frequency_cap_per_agent_per_day",
        "fictional",
        "funded_response_limit",
        "impulsivity",
        "initial_state",
        "interests",
        "max_view_distance_meters",
        "mbti",
        "mobile_recall_encoding",
        "mode",
        "name",
        "novelty_seeking",
        "occupation",
        "opportunity_probability_per_minute",
        "orientation_degrees",
        "phone",
        "persona_id",
        "personas",
        "population",
        "price_sensitivity",
        "provider_profile_id",
        "provider_revision_sha256",
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
        "thought_languages",
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


class WorkbenchCognitionUnavailable(ValueError):
    """A verified legacy run has no frozen city cognition evidence."""


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


def _request_persona_field(
    error: WorkbenchValidationError,
    draft: WorkbenchRunDraftDocument,
    payload: Mapping[str, object],
) -> WorkbenchValidationError:
    """Name a persona refusal by its position in the request, as field diagnostics do.

    A parsed draft holds its personas in canonical ID order, which the request need not.
    """
    match = _CANONICAL_PERSONA_FIELD.fullmatch(error.field)
    population = payload.get("population")
    if (
        match is None
        or not isinstance(draft, (WorkbenchRunDraftV2, WorkbenchRunDraftV3, WorkbenchRunDraftV4))
        or not isinstance(population, Mapping)
    ):
        return error
    persona_id = draft.population.personas[int(match.group(1))].persona_id
    submitted = population.get("personas")
    for index, item in enumerate(submitted if isinstance(submitted, list) else ()):
        if isinstance(item, Mapping) and item.get("persona_id") == persona_id:
            return WorkbenchValidationError(
                error.code, f"population.personas.{index}{match.group(2) or ''}", error.safe_message
            )
    return error


def _construct_validated_run(
    payload: dict[str, object],
    resolver: WorkbenchCityResolver,
) -> ValidatedWorkbenchRun:
    draft = parse_workbench_http_draft(payload)
    try:
        selected = resolver.resolve_city(draft.city_id)
    except UnknownWorkbenchCity:
        raise WorkbenchValidationError(
            "unknown-city",
            "city_id",
            "The selected city is unavailable.",
        ) from None
    # Every new run freezes the current causal contract and its selection policy.
    try:
        return construct_workbench_run(
            draft,
            selected_pack=selected.pack,
            causal_contract=CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
        )
    except WorkbenchValidationError as error:
        raise _request_persona_field(error, draft, payload) from None


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


def _literal_values(model: type[BaseModel], field: str) -> tuple[Any, ...]:
    annotation = model.model_fields[field].annotation
    if get_origin(annotation) is not Literal:
        raise RuntimeError("workbench capability source is not a closed literal")
    return get_args(annotation)


def _single_literal(model: type[BaseModel], field: str) -> Any:
    values = _literal_values(model, field)
    if len(values) != 1:
        raise RuntimeError("workbench capability source is not one exact literal")
    return values[0]


def _bounded_cognition_capabilities() -> dict[str, object]:
    """Derive browser-visible schema-4 options from the current selection-policy row.

    Only selectable options and disclosure ceilings are exposed; policy documents,
    hashes, and transport ceilings stay server-derived and are never submitted.
    """
    draft = WorkbenchCognitionDraftV4
    current = CITY_COGNITION_SELECTION_LIMITS[CURRENT_CITY_COGNITION_SELECTION_POLICY_VERSION]
    thoughts = CITY_COGNITION_SELECTION_LIMITS[
        CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT.selection_policy_version
    ]
    effect_scope = _single_literal(draft, "effect_scope")
    modes = _literal_values(draft, "mode")
    provider_modes = _literal_values(CityCognitionProviderProfileSnapshotV1, "mode")

    def materialized_limit(mode: str) -> int:
        # The draft model owns the server-side default for an omitted funded limit.
        provider = mode in provider_modes
        selection = draft.model_validate(
            {
                "effect_scope": effect_scope,
                "mode": mode,
                "provider_profile_id": "capability-probe" if provider else None,
                "provider_revision_sha256": "0" * 64 if provider else None,
            }
        )
        return selection.funded_response_limit

    offline_limits = {materialized_limit(mode) for mode in modes if mode not in provider_modes}
    default_limits = {mode: materialized_limit(mode) for mode in provider_modes}
    allowed = set(current.funded_response_limits)
    if (
        offline_limits != {current.offline_funded_response_limit}
        or not set(default_limits.values()) <= allowed
        or not allowed <= set(_literal_values(draft, "funded_response_limit"))
    ):
        raise RuntimeError("bounded cognition default funding is outside the current policy")
    return {
        "effect_scope": effect_scope,
        "workbench_schema_version": _single_literal(WorkbenchRunDraftV4, "schema_version"),
        "run_schema_version": _single_literal(CityRunManifestV10, "schema_version"),
        "modes": list(modes),
        "selection_policy_version": current.selection_policy_version,
        "selection_strategy": current.strategy,
        "funded_response_limits": list(current.funded_response_limits),
        # The largest option means "every noticed ad", up to the per-run cap.
        "all_noticed_funded_response_limit": current.selected_requests_per_run,
        "default_provider_funded_response_limit": default_limits["remote"],
        "default_local_funded_response_limit": default_limits["local"],
        "offline_funded_response_limit": offline_limits.pop(),
        "selected_requests_per_run": current.selected_requests_per_run,
        "selected_requests_per_agent": current.selected_requests_per_agent,
        "concurrency_limits": {
            "remote": current.concurrency_limit("remote"),
            "local": current.concurrency_limit("local"),
        },
        "aggregate_deadline_seconds": current.aggregate_deadline_seconds,
        "reserved_output_tokens_per_request": current.reservation_tokens_per_request,
        "max_output_tokens_per_attempt": current.output_tokens_per_attempt,
        "max_http_attempts_per_request": current.max_attempts_per_request,
        "compatibility_check_required_modes": [mode for mode in modes if mode in provider_modes],
        # Inner thoughts: any choice of these languages (none means off), for the AI modes,
        # each answer then reserving the thoughts budget instead of the one above.
        "thought_languages": list(CITY_COGNITION_THOUGHT_LANGUAGES),
        "thought_modes": [mode for mode in modes if mode in provider_modes],
        "thought_max_output_tokens_per_attempt": thoughts.output_tokens_per_attempt,
        "thought_reserved_output_tokens_per_request": thoughts.reservation_tokens_per_request,
        "thought_max_characters": CITY_COGNITION_THOUGHT_MAX_CHARACTERS,
    }


def create_city_workbench_app(
    workspace: WorkbenchWorkspace,
    *,
    csrf_token: str | None = None,
    provider_runtime: ProviderRuntimePolicy | ProviderOriginRegistry | None = None,
    credential_store: CityCredentialStore | None = None,
    provider_factory: WorkbenchCheckProviderFactory | None = None,
    provider_factory_v2: WorkbenchCheckProviderFactoryV2 | None = None,
    server_port: int | None = None,
) -> FastAPI:
    """Build one process-local control app after rechecking its pinned workspace.

    The startup provider policy becomes a live origin registry, so the guided pages can
    connect another OpenAI-compatible address for this process (never this server's own
    ``server_port``)."""
    verify_workbench_workspace(workspace)
    token = new_csrf_token() if csrf_token is None else csrf_token
    if isinstance(provider_runtime, ProviderOriginRegistry):
        runtime = provider_runtime
    else:
        runtime = ProviderOriginRegistry.from_policy(
            build_provider_runtime_policy() if provider_runtime is None else provider_runtime,
            refused_local_ports=() if server_port is None else (server_port,),
        )
    provider_registry = ProviderProfileRegistry(workspace)
    credentials = (
        OSKeyringCityCredentialStore(runtime) if credential_store is None else credential_store
    )
    endpoint_policy = CityEndpointPolicy(runtime)
    operations = WorkbenchOperationState()
    compatibility_registry = WorkbenchProviderCompatibilityRegistry()
    providers = WorkbenchProviderController(
        provider_registry,
        runtime,
        credentials=credentials,
        operations=operations,
        provider_factory=provider_factory,
        provider_factory_v2=provider_factory_v2,
        compatibility_registry=compatibility_registry,
    )
    repository = WorkbenchRunRepository(
        workspace,
        provider_runtime=runtime,
        provider_registry=provider_registry,
        credential_store=credentials,
        endpoint_policy=endpoint_policy,
        compatibility_registry=compatibility_registry,
        provider_factory_v2=provider_factory_v2,
    )
    jobs = CityJobManager(repository, operations=operations)
    city_resolver = WorkbenchCityResolver(workspace)
    effect_scopes = list(get_args(ProviderEffectScope))
    bounded_cognition = _bounded_cognition_capabilities()

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

    def city_view_from_stored(stored: StoredCityRun) -> CityViewContext:
        manifest = stored.manifest
        schema_version = manifest.schema_version
        try:
            # The context dispatches V1/V2 evidence by exact version; only V10 binds
            # its frozen bounded-cognition input and terminal evaluation.
            cognition_input: CityCognitionInputV2 | None = None
            cognition_evaluation: CityCognitionEvaluationV2 | None = None
            if isinstance(manifest, CityRunManifestV10):
                if not isinstance(stored.cognition_input, CityCognitionInputV2) or not isinstance(
                    stored.cognition_evaluation, CityCognitionEvaluationV2
                ):
                    raise ValueError("V10 city view requires bounded cognition evidence")
                cognition_input = stored.cognition_input
                cognition_evaluation = stored.cognition_evaluation
            spatial_metrics = (
                metrics_for_stored_city_run(stored)
                if schema_version in {5, 6, 7, 8, 9, 10}
                else None
            )
            response_metrics = (
                response_metrics_for_stored_city_run(stored)
                if schema_version in {6, 7, 8, 9, 10}
                else None
            )
            return create_city_view_context(
                stored.mobility,
                run_id=manifest.run_id,
                run_schema_version=schema_version,
                spatial_scenario=stored.spatial_scenario,
                opportunity_evaluation=stored.opportunity_evaluation,
                attention_evaluation=stored.attention_evaluation,
                spatial_metrics=spatial_metrics,
                response_input=stored.response_input,
                response_evaluation=stored.response_evaluation,
                spatial_response_metrics=response_metrics,
                workbench_input_sha256=(
                    manifest.workbench_input_sha256
                    if isinstance(
                        manifest,
                        (
                            CityRunManifestV7,
                            CityRunManifestV8,
                            CityRunManifestV9,
                            CityRunManifestV10,
                        ),
                    )
                    else None
                ),
                cognition_input=cognition_input,
                cognition_evaluation=cognition_evaluation,
            )
        except (StorageError, TypeError, ValueError):
            raise WorkbenchRunUnavailable from None

    def verified_city_view(run_id: object) -> CityViewContext:
        return city_view_from_stored(verified_stored_run(run_id))

    def refuse_view_token(request: Request, run_id: object) -> None:
        """Screen the run ID, then refuse the retired inspector's snapshot header.

        Only the removed saved-run inspector page issued view tokens, so a request that
        still carries one names no snapshot: it is refused, never silently re-verified.
        """
        try:
            validate_run_id(run_id)
        except UnsafeRunLocation:
            raise WorkbenchRunNotFound from None
        if any(
            name.lower() == _VIEW_TOKEN_HEADER for name, _value in request.scope.get("headers", ())
        ):
            raise WorkbenchRunUnavailable

    def resolve_city_view(request: Request) -> CityViewContext:
        run_id = request.path_params.get("run_id")
        refuse_view_token(request, run_id)
        return verified_city_view(run_id)

    def resolve_stored_run(request: Request, run_id: object) -> StoredCityRun:
        refuse_view_token(request, run_id)
        return verified_stored_run(run_id)

    def resolve_cognition_run(request: Request, run_id: str) -> StoredCityRun:
        stored = resolve_stored_run(request, run_id)
        version = stored.manifest.schema_version
        if type(version) is not int or version not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}:
            raise WorkbenchRunUnavailable
        if version not in {9, 10}:
            raise WorkbenchCognitionUnavailable
        if (
            not isinstance(
                stored.manifest, CityRunManifestV10 if version == 10 else CityRunManifestV9
            )
            or stored.cognition_input is None
            or stored.cognition_evaluation is None
        ):
            raise WorkbenchRunUnavailable
        return stored

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            jobs.close()
            # Keys typed into the guided pages never outlive the server process.
            forget = getattr(credentials, "forget_session", None)
            if callable(forget):
                forget()

    app = FastAPI(
        title="AdLife local city workbench",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.provider_runtime = runtime.policy
    app.state.provider_origins = runtime
    app.state.provider_controller = providers
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(_LOOPBACK_HOSTS),
        www_redirect=False,
    )
    app.add_middleware(
        WorkbenchSecurityMiddleware,
        csrf_token=token,
        route_body_limits={"/api/catalog/cities/upload": MAX_CITY_PACK_UPLOAD_BYTES},
    )

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

    @app.exception_handler(WorkbenchJsonContentError)
    async def forbidden_json_content_handler(
        request: Request,
        _error: WorkbenchJsonContentError,
    ) -> JSONResponse:
        # The strict JSON boundary already checked bytes, syntax, depth, and finiteness.
        # Recover existing safe field diagnostics without constructing or submitting a run.
        if request.url.path in {"/api/scenarios/validate", "/api/jobs"}:
            try:
                parse_workbench_http_draft(await request.json())
            except ValidationError as error:
                return await validation_error_response(error)
            except WorkbenchDraftTransportError as error:
                return await draft_transport_error_handler(request, error)
        return workbench_error(
            422,
            "invalid-fields",
            "One or more request fields are invalid.",
            {"request": "This field is invalid."},
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
    @app.exception_handler(UnknownWorkbenchCity)
    async def unknown_catalog_city_handler(
        _request: Request,
        _error: UnknownCatalogCity | UnknownWorkbenchCity,
    ) -> JSONResponse:
        return workbench_error(404, "not-found", "The requested resource was not found.")

    @app.exception_handler(CityCatalogError)
    @app.exception_handler(PackagedCityCatalogUnavailable)
    async def city_catalog_error_handler(
        _request: Request,
        _error: CityCatalogError | PackagedCityCatalogUnavailable,
    ) -> JSONResponse:
        return workbench_error(
            500,
            "catalog-unavailable",
            "The verified city catalog is unavailable.",
        )

    @app.exception_handler(WorkbenchCityRegistryUnavailable)
    async def city_registry_error_handler(
        _request: Request,
        _error: WorkbenchCityRegistryUnavailable,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "city-registry-unavailable",
            "The workspace city registry is unavailable.",
        )

    @app.exception_handler(MalformedCityPackUpload)
    async def malformed_city_pack_handler(
        _request: Request,
        _error: MalformedCityPackUpload,
    ) -> JSONResponse:
        return workbench_error(
            400,
            "malformed-city-pack",
            "The city pack document is not valid JSON.",
        )

    @app.exception_handler(InvalidCityPackUpload)
    async def invalid_city_pack_handler(
        _request: Request,
        _error: InvalidCityPackUpload,
    ) -> JSONResponse:
        return workbench_error(
            422,
            "invalid-city-pack",
            "The city pack is not a valid CityPack V2 document.",
        )

    @app.exception_handler(CityPackUploadTooLarge)
    async def city_pack_too_large_handler(
        _request: Request,
        _error: CityPackUploadTooLarge,
    ) -> JSONResponse:
        return workbench_error(
            413,
            "city-pack-too-large",
            "The city pack exceeds the upload size limit.",
        )

    @app.exception_handler(CityPackRegistrationConflict)
    async def city_pack_conflict_handler(
        _request: Request,
        _error: CityPackRegistrationConflict,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "city-pack-conflict",
            "The city pack conflicts with the workspace catalog.",
        )

    @app.exception_handler(CityPackPublicationError)
    async def city_pack_publication_handler(
        _request: Request,
        error: CityPackPublicationError,
    ) -> JSONResponse:
        if error.potentially_committed:
            return workbench_error(
                500,
                "city-pack-publication-uncertain",
                "The city pack may already be registered. Reload the city catalog before retrying.",
            )
        return workbench_error(
            500,
            "city-pack-publication-failed",
            "The city pack could not be published.",
        )

    @app.exception_handler(PersonaTemplateCatalogError)
    async def persona_catalog_error_handler(
        _request: Request,
        _error: PersonaTemplateCatalogError,
    ) -> JSONResponse:
        return workbench_error(
            500,
            "persona-catalog-unavailable",
            "The fictional persona catalog is unavailable.",
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
        # The conflict names its field, so the browser marks the run ID input itself.
        return workbench_error(
            409,
            "run-conflict",
            "The run identifier is already in use.",
            {"settings.run_id": _RUN_ID_IN_USE_MESSAGE},
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

    @app.exception_handler(WorkbenchProviderCompatibilityRequired)
    async def provider_compatibility_required_handler(
        _request: Request,
        _error: WorkbenchProviderCompatibilityRequired,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-compatibility-required",
            "A successful bounded-cognition provider compatibility check is required.",
        )

    @app.exception_handler(CityEndpointBindingError)
    async def endpoint_binding_error_handler(
        _request: Request,
        _error: CityEndpointBindingError,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-endpoint-refused",
            "The provider profile is not allowed by the current startup policy.",
        )

    @app.exception_handler(InvalidProviderOrigin)
    async def invalid_provider_origin_handler(
        _request: Request,
        _error: InvalidProviderOrigin,
    ) -> JSONResponse:
        return workbench_error(
            422,
            "invalid-provider-origin",
            "The provider address is not allowed.",
        )

    @app.exception_handler(WorkbenchProviderOriginLimit)
    async def provider_origin_limit_handler(
        _request: Request,
        _error: WorkbenchProviderOriginLimit,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-origin-limit",
            "This server already holds the most provider addresses it accepts.",
        )

    @app.exception_handler(InvalidProviderProfile)
    async def invalid_provider_profile_handler(
        _request: Request,
        _error: InvalidProviderProfile,
    ) -> JSONResponse:
        return workbench_error(
            422,
            "invalid-provider-profile",
            "The provider profile is invalid.",
        )

    @app.exception_handler(ProviderProfileNotFound)
    async def provider_profile_not_found_handler(
        _request: Request,
        _error: ProviderProfileNotFound,
    ) -> JSONResponse:
        return workbench_error(
            404,
            "provider-profile-not-found",
            "The provider profile revision was not found.",
        )

    @app.exception_handler(ProviderProfileConflict)
    async def provider_profile_conflict_handler(
        _request: Request,
        _error: ProviderProfileConflict,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-profile-conflict",
            "The provider profile conflicts with workspace state.",
        )

    @app.exception_handler(ProviderProfileRegistryUnavailable)
    async def provider_registry_unavailable_handler(
        _request: Request,
        _error: ProviderProfileRegistryUnavailable,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-registry-unavailable",
            "The provider profile registry is unavailable.",
        )

    @app.exception_handler(ProviderProfilePublicationError)
    async def provider_publication_error_handler(
        _request: Request,
        error: ProviderProfilePublicationError,
    ) -> JSONResponse:
        return workbench_error(
            500,
            (
                "provider-publication-uncertain"
                if error.potentially_committed
                else "provider-publication-failed"
            ),
            (
                "The provider profile may already be stored. Reload the provider list."
                if error.potentially_committed
                else "The provider profile could not be stored."
            ),
        )

    @app.exception_handler(CityCredentialStoreError)
    async def credential_store_error_handler(
        _request: Request,
        _error: CityCredentialStoreError,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "credential-store-unavailable",
            "The credential could not be stored or resolved by an approved source.",
        )

    @app.exception_handler(WorkbenchProviderBusy)
    async def provider_busy_handler(
        _request: Request,
        _error: WorkbenchProviderBusy,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-operation-busy",
            "Another provider or simulation operation is active.",
        )

    @app.exception_handler(WorkbenchProviderEndpointRefused)
    async def provider_endpoint_refused_handler(
        _request: Request,
        _error: WorkbenchProviderEndpointRefused,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-endpoint-refused",
            "The provider profile is not allowed by the current startup policy.",
        )

    @app.exception_handler(WorkbenchProviderRateLimited)
    async def provider_rate_handler(
        _request: Request,
        _error: WorkbenchProviderRateLimited,
    ) -> JSONResponse:
        return workbench_error(
            429,
            "provider-check-rate-limited",
            "The provider compatibility check limit was reached.",
        )

    @app.exception_handler(WorkbenchProviderClosed)
    async def provider_closed_handler(
        _request: Request,
        _error: WorkbenchProviderClosed,
    ) -> JSONResponse:
        return workbench_error(
            409,
            "provider-operations-closed",
            "Provider operations are no longer accepting requests.",
        )

    @app.exception_handler(CityProviderTransportError)
    async def provider_transport_error_handler(
        _request: Request,
        _error: CityProviderTransportError,
    ) -> JSONResponse:
        return workbench_error(
            502,
            "provider-check-failed",
            "The provider compatibility check could not be completed.",
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

    @app.exception_handler(WorkbenchCognitionUnavailable)
    async def cognition_unavailable_handler(
        _request: Request,
        _error: WorkbenchCognitionUnavailable,
    ) -> JSONResponse:
        return workbench_error(
            404,
            "cognition-unavailable",
            "This saved run has no frozen cognition evidence.",
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
    def root() -> RedirectResponse:
        """The server serves only the guided pages, so its root address opens the guide."""
        return RedirectResponse("/guide", status_code=307, headers=_SHELL_HEADERS)

    @app.get("/assets/workbench-icon.svg", include_in_schema=False)
    def icon() -> Response:
        return Response(
            _read_packaged_text("workbench-icon.svg"),
            media_type="image/svg+xml",
            headers={"X-Content-Type-Options": "nosniff"},
        )

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
                "provider_configuration": True,
                "oauth": False,
                "cognition_modes": ["rules", "mock", "local", "remote"],
                "response_modes": ["deterministic-rules"],
                "cognition_effect_scopes": list(effect_scopes),
                "bounded_cognition": copy.deepcopy(bounded_cognition),
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
                "city": (
                    "City-pack geometry; no live map or traffic feed. "
                    "Uploaded sources are operator-declared and unreviewed."
                ),
                "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
            },
        }

    @app.get("/api/catalog/cities")
    def cities(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        resolved = city_resolver.list_cities()
        return {
            "schema_version": 1,
            "cities": [
                build_workbench_city_catalog_row(city).model_dump(mode="json") for city in resolved
            ],
        }

    @app.post("/api/catalog/cities/upload")
    async def upload_city(request: Request) -> JSONResponse:
        if request.query_params.multi_items():
            return workbench_error(
                400,
                "invalid-upload-request",
                "The city pack upload request is invalid.",
            )
        resolved = city_resolver.register_city(await request.body())
        return JSONResponse(
            status_code=201,
            content=build_workbench_city_catalog_row(resolved).model_dump(mode="json"),
        )

    @app.get("/api/catalog/cities/{city_id}")
    def city_detail(city_id: str, request: Request) -> dict[str, object]:
        _require_empty_query(request)
        resolved = city_resolver.resolve_city(city_id)
        return build_workbench_city_detail(resolved).model_dump(mode="json")

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

    @app.get("/api/providers")
    def provider_profiles(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        return providers.list().model_dump(mode="json")

    @app.post("/api/providers")
    async def create_provider_profile(request: Request) -> JSONResponse:
        _require_empty_query(request)
        profile = providers.create(strict_json_object(await request.body()))
        return JSONResponse(status_code=201, content=profile.model_dump(mode="json"))

    @app.post("/api/providers/origins")
    async def approve_provider_origin(request: Request) -> JSONResponse:
        """Approve one typed OpenAI-compatible address for this process (never a key)."""
        _require_empty_query(request)
        payload = strict_json_object(await request.body())
        if set(payload) != {"base_url"}:
            raise InvalidProviderOrigin
        origin = providers.approve_origin(payload["base_url"])
        return JSONResponse(content=origin.model_dump(mode="json"))

    @app.post("/api/providers/{profile_id}/revisions/{revision_id}/credential")
    async def store_provider_credential(
        profile_id: str,
        revision_id: str,
        request: Request,
    ) -> JSONResponse:
        _require_empty_query(request)
        credential, remember = parse_workbench_credential_submission(await request.body())
        profile = providers.store_credential(profile_id, revision_id, credential, remember=remember)
        return JSONResponse(content=profile.model_dump(mode="json"))

    @app.post("/api/providers/{profile_id}/revisions/{revision_id}/check")
    async def check_provider_compatibility(
        profile_id: str,
        revision_id: str,
        request: Request,
    ) -> JSONResponse:
        _require_empty_query(request)
        payload = strict_json_object(await request.body())
        scope = payload.get("effect_scope", "explanation-only")
        try:
            # A run that writes inner thoughts checks the request shape it will send.
            languages = city_cognition_thought_languages(payload.get("thought_languages", []))
        except ValueError:
            languages = None
        if (
            set(payload) - {"effect_scope", "thought_languages"}
            or scope not in ("explanation-only", "bounded-signals-v1")
            or languages is None
            or (languages and scope != "bounded-signals-v1")
        ):
            return workbench_error(
                422,
                "invalid-fields",
                "One or more request fields are invalid.",
                {"request": "Unknown field."},
            )
        effect_scope: ProviderEffectScope = scope
        result = await providers.check(
            profile_id, revision_id, effect_scope=effect_scope, thought_languages=languages
        )
        return JSONResponse(content=result.model_dump(mode="json"))

    @app.get("/api/providers/{profile_id}/revisions/{revision_id}/bounded-check")
    def recorded_bounded_check(
        profile_id: str,
        revision_id: str,
        request: Request,
    ) -> JSONResponse:
        """Read, never run, this process's bounded-signal check for one exact revision.

        A page reopened after inspecting a run can show the result the server already
        enforces at job submission; nothing is re-checked or contacted here. The one
        accepted query, ``thoughts=true``, reads the check of the inner-thoughts contract.
        """
        query = request.query_params.multi_items()
        if query not in ([], [("thoughts", "true")]):
            raise WorkbenchQueryError("this route accepts only thoughts=true")
        profile = provider_registry.load(profile_id, revision_id)
        key = build_workbench_provider_compatibility_key(
            profile,
            effect_scope="bounded-signals-v1",
            causal_contract=CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT if query else None,
        )
        record = compatibility_registry.lookup(key)
        if record is None:
            return workbench_error(
                404,
                "provider-check-not-recorded",
                "No bounded-signal check has run for this profile revision since the workbench "
                "started.",
            )
        result = WorkbenchProviderCheckResultV2.model_validate(
            {
                "status": record.status,
                "reason_code": record.reason_code,
                "compatibility_key_sha256": key.fingerprint,
                **{
                    name: getattr(key, name)
                    for name in (
                        "effect_scope",
                        "profile_id",
                        "revision_sha256",
                        "causal_contract_sha256",
                        "prompt_contract_sha256",
                        "result_schema_contract_sha256",
                        "selection_policy_sha256",
                        "fallback_policy_sha256",
                        "mock_policy_sha256",
                        "influence_policy_sha256",
                    )
                },
            }
        )
        return JSONResponse(
            content=result.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/scenarios/validate")
    async def validate_scenario(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        payload = strict_json_object(await request.body(), user_draft=True)
        result = _construct_validated_run(payload, city_resolver)
        return _validation_summary(result)

    @app.get("/api/persona-templates")
    def persona_templates(request: Request) -> dict[str, object]:
        _require_empty_query(request)
        return load_persona_template_catalog().model_dump(mode="json")

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
        payload = strict_json_object(await request.body(), user_draft=True)
        validated = _construct_validated_run(payload, city_resolver)
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
        stored = resolve_stored_run(request, run_id)
        if not isinstance(
            stored.manifest,
            (CityRunManifestV7, CityRunManifestV8, CityRunManifestV9, CityRunManifestV10),
        ):
            raise WorkbenchInputUnavailable
        try:
            view = build_saved_workbench_run_input_view(stored)
        except (TypeError, ValueError):
            raise WorkbenchRunUnavailable from None
        return JSONResponse(
            content=view.model_dump(mode="json"),
            headers=_SHELL_HEADERS,
        )

    @app.get("/api/runs/{run_id}/cognition-summary")
    def cognition_summary(run_id: str, request: Request) -> JSONResponse:
        _require_empty_query(request)
        stored = resolve_cognition_run(request, run_id)
        try:
            view = build_workbench_cognition_summary_view(stored)
        except (TypeError, ValueError):
            raise WorkbenchRunUnavailable from None
        return JSONResponse(content=view.model_dump(mode="json"), headers=_SHELL_HEADERS)

    @app.get("/api/runs/{run_id}/cognition-records")
    def cognition_records(run_id: str, request: Request) -> JSONResponse:
        _require_empty_query(request)
        stored = resolve_cognition_run(request, run_id)
        try:
            view = build_workbench_cognition_records_view(stored)
        except (TypeError, ValueError):
            raise WorkbenchRunUnavailable from None
        return JSONResponse(content=view.model_dump(mode="json"), headers=_SHELL_HEADERS)

    def guide_load_verified_run(run_id: str) -> StoredCityRun:
        """Freshly verify one run for the guide, refusing with the guide's own errors."""
        try:
            return verified_stored_run(run_id)
        except WorkbenchRunNotFound:
            raise GuideRunMissing from None
        except WorkbenchRunUnavailable:
            raise GuideRunUnverifiable from None

    # The guided presenter pages and their story API; they only read verified runs.
    install_guide_routes(
        app,
        GuideRouteContext(
            csrf_token=token,
            shell_headers=_SHELL_HEADERS,
            load_verified_run=guide_load_verified_run,
            content_fingerprint=repository.content_fingerprint,
        ),
    )
    install_city_view_routes(
        app,
        prefix="/api/runs/{run_id}",
        resolver=resolve_city_view,
    )
    return app


__all__ = ["create_city_workbench_app"]
