"""Secure provider controls and shared workbench operation admission.

Compatibility checks use the production transport and a fixed synthetic request. They
never publish cognition evidence, retry, repair, or retain provider prose. The shared
operation state must also be used by the job manager: admission precedes preflight and
run-ID reservation, and the lease lasts until the worker has drained and cleared secrets.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from hashlib import sha256
from threading import RLock
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, ValidationError, model_validator

from adlife.adapters.cognition.city_credentials import (
    CityCredentialStore,
    CityCredentialStoreError,
    OSKeyringCityCredentialStore,
    ProviderCredentialUnavailable,
    ResolvedCredential,
    SecretBuffer,
)
from adlife.adapters.cognition.city_endpoint_policy import (
    BoundProviderEndpoint,
    CityEndpointBindingError,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_openai_compatible import (
    CityOpenAICompatibleProvider,
    CityProviderCallError,
    CityProviderTransportError,
)
from adlife.adapters.cognition.city_service import CityCognitionCancellation
from adlife.city.provider_profiles import (
    InvalidProviderProfile,
    ProviderProfileDraftV1,
    ProviderProfileRegistry,
    ProviderProfileRevisionV1,
)
from adlife.city.provider_runtime import ProviderRuntimePolicy
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
    CITY_COGNITION_DEFAULT_SELECTION_POLICY,
    CITY_COGNITION_FALLBACK_POLICY_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_TEXT,
    CITY_COGNITION_RESULT_SCHEMA_DOCUMENT,
    CITY_COGNITION_RESULT_SCHEMA_SHA256,
    CITY_COGNITION_SELECTION_POLICY_SHA256,
    CityCognitionAgentContextV1,
    CityCognitionCampaignContextV1,
    CityCognitionCommittedStateV1,
    CityCognitionCreativeSnapshotV1,
    CityCognitionEffectiveSettingsV1,
    CityCognitionInputV1,
    CityCognitionProviderProfileSnapshotV1,
    CityCognitionRequestV1,
    CityCognitionRuleFactorsV1,
    CityCognitionRuntimeFallbackReason,
    CityProviderAttemptV1,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition import allocate_city_cognition_reservations


class WorkbenchProviderBusy(RuntimeError):
    def __init__(self) -> None:
        super().__init__("workbench provider operation conflicts with active work")


class WorkbenchProviderClosed(RuntimeError):
    def __init__(self) -> None:
        super().__init__("workbench provider operations are closed")


class WorkbenchProviderRateLimited(RuntimeError):
    def __init__(self) -> None:
        super().__init__("workbench provider check start limit was reached")


class WorkbenchProviderEndpointRefused(RuntimeError):
    def __init__(self) -> None:
        super().__init__("provider profile is excluded by the current endpoint policy")


@dataclass(frozen=True, slots=True, eq=False)
class WorkbenchOperationLease:
    """Identity token owned by exactly one admitted operation; contains no credential."""

    kind: Literal["job", "check", "credential"]
    cancellation: CityCognitionCancellation | None = None


class WorkbenchOperationState:
    """One locked, process-local arbiter shared by jobs, checks, and secret writes.

    ``lock`` may be the job manager's own state lock, making its reservation and
    admission atomic. ``finish`` is identity checked so stale workers cannot release a
    newer operation. Shutdown cancels a check but retains its lease through cleanup.
    """

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.lock = RLock()
        self._monotonic = monotonic
        self._starts: deque[float] = deque()
        self._active: WorkbenchOperationLease | None = None
        self._closed = False

    def _begin(self, kind: Literal["job", "check", "credential"]) -> WorkbenchOperationLease:
        with self.lock:
            if self._closed:
                raise WorkbenchProviderClosed()
            if self._active is not None:
                raise WorkbenchProviderBusy()
            if kind == "check":
                now = self._monotonic()
                while self._starts and self._starts[0] <= now - 60.0:
                    self._starts.popleft()
                if len(self._starts) >= 5:
                    raise WorkbenchProviderRateLimited()
                self._starts.append(now)
            lease = WorkbenchOperationLease(
                kind, CityCognitionCancellation() if kind == "check" else None
            )
            self._active = lease
            return lease

    def begin_job(self) -> WorkbenchOperationLease:
        return self._begin("job")

    def begin_check(self) -> WorkbenchOperationLease:
        return self._begin("check")

    def begin_credential(self) -> WorkbenchOperationLease:
        return self._begin("credential")

    def finish(self, lease: WorkbenchOperationLease) -> None:
        with self.lock:
            if self._active is lease:
                self._active = None

    def cancel_check(self) -> None:
        with self.lock:
            if self._active is not None and self._active.cancellation is not None:
                self._active.cancellation.cancel()

    def close(self) -> None:
        with self.lock:
            self._closed = True
            self.cancel_check()


class WorkbenchProviderCheckResult(DomainModel):
    """Only bounded machine status crosses the compatibility-check boundary."""

    model_config = ConfigDict(hide_input_in_errors=True)
    status: Literal["compatible", "incompatible", "unavailable"]
    reason_code: (
        CityCognitionRuntimeFallbackReason
        | Literal["credential-unavailable", "endpoint-policy-refused"]
        | None
    ) = None

    @model_validator(mode="after")
    def coherent_status(self) -> WorkbenchProviderCheckResult:
        unavailable = {"credential-unavailable", "endpoint-policy-refused"}
        if self.status == "compatible":
            valid = self.reason_code is None
        elif self.status == "unavailable":
            valid = self.reason_code in unavailable
        else:
            valid = self.reason_code is not None and self.reason_code not in unavailable
        if not valid:
            raise ValueError("provider check status and reason are inconsistent")
        return self


class WorkbenchProviderView(ProviderProfileDraftV1):
    revision_sha256: str
    credential_slot_id: str
    credential_available: bool
    endpoint_policy_accepted: bool
    last_check: WorkbenchProviderCheckResult | None = None


class WorkbenchProviderOriginView(DomainModel):
    approval_id: str
    base_url: str
    trust_class: Literal["local", "remote"]
    environment_variable: str | None = Field(
        default=None,
        pattern=r"^[A-Z_][A-Z0-9_]{0,127}$",
    )


class WorkbenchProviderList(DomainModel):
    schema_version: Literal[1] = 1
    profiles: tuple[WorkbenchProviderView, ...]
    origins: tuple[WorkbenchProviderOriginView, ...]
    provider_configuration: Literal[True] = True
    oauth: Literal[False] = False
    cognition_modes: tuple[Literal["rules", "mock", "local", "remote"], ...] = (
        "rules",
        "mock",
        "local",
        "remote",
    )


class _CheckProvider(Protocol):
    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1: ...

    async def aclose(self) -> None: ...


class WorkbenchCheckProviderFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint_policy: CityEndpointPolicy,
        endpoint: BoundProviderEndpoint,
        cognition_input: CityCognitionInputV1,
        credential: ResolvedCredential,
    ) -> _CheckProvider: ...


def _compatibility_request() -> CityCognitionRequestV1:
    """Build fixed, fictional evidence without reading a catalog or running a city."""
    creative = CityCognitionCreativeSnapshotV1(
        template_id="compatibility-check",
        product_name="Fictional item",
        product_category="Fictional category",
        message="A fictional offer.",
        call_to_action="Consider the fictional offer.",
    )
    facts: dict[str, object] = {
        "schema_version": 1,
        **{
            name: "0" * 64
            for name in (
                "run_input_sha256",
                "response_input_sha256",
                "scenario_sha256",
                "city_sha256",
                "response_event_id",
                "notice_event_id",
                "opportunity_id",
                "state_update_event_id",
                "state_before_sha256",
            )
        },
        "agent_id": "person-001",
        "campaign_id": "compatibility-check",
        "placement_id": "compatibility-check",
        "model_minute": 0,
        "millisecond_within_minute": 0,
        "activity": "home",
        "channel": "mobile-feed",
        "campaign": {
            "schema_version": 1,
            "campaign_id": "compatibility-check",
            "name": "Fictional compatibility check",
            "creative_sha256": creative.fingerprint,
            "creative": creative,
            "target_interests": ("fictional items",),
            "relative_price": 1.0,
        },
        "persona": {
            "schema_version": 1,
            "agent_id": "person-001",
            "persona_id": "persona-001",
            "fictional": True,
            "display_name": "Fictional check persona",
            "interests": ("fictional items",),
            "traits": {
                name: 0.5
                for name in (
                    "price_sensitivity",
                    "novelty_seeking",
                    "advertising_skepticism",
                    "mobile_recall_encoding",
                    "roadside_recall_encoding",
                    "impulsivity",
                )
            },
        },
        "rule_factors": {
            "schema_version": 1,
            "prior_notices_today": 0,
            "frequency_cap_per_agent_per_day": 1,
            **{
                name: 0.5
                for name in (
                    "interest_match",
                    "price_sensitivity",
                    "affordability",
                    "novelty_seeking",
                    "advertising_skepticism",
                    "channel_recall_encoding",
                    "impulsivity",
                    "frequency_fatigue",
                    "value_match",
                )
            },
            "relative_price": 1.0,
            "sentiment_delta": 0.0,
            "recall_delta": 0.0,
        },
        "state_update_cause_count": 1,
        "committed_state": {
            "schema_version": 1,
            "agent_id": "person-001",
            "campaign_id": "compatibility-check",
            "brand_sentiment": 0.0,
            "recall_strength": 0.5,
            "purchase_intention": 0.5,
            "response_count": 1,
            "last_response_minute": 0,
        },
        "disclosure": "synthetic-narrative-not-observed-behavior",
    }
    for name, model in (
        ("campaign", CityCognitionCampaignContextV1),
        ("persona", CityCognitionAgentContextV1),
        ("rule_factors", CityCognitionRuleFactorsV1),
        ("committed_state", CityCognitionCommittedStateV1),
    ):
        facts[name] = model.model_validate(facts[name])
    # Hash JSON-mode facts, including model defaults, exactly as the request does.
    provisional = CityCognitionRequestV1.model_construct(_fields_set=None, request_id="", **facts)
    request_id = (
        "cog-"
        + sha256(
            canonical_json(provisional.model_dump(mode="json", exclude={"request_id"})).encode()
        ).hexdigest()
    )
    return CityCognitionRequestV1.model_validate({**facts, "request_id": request_id})


def _compatibility_input(
    profile: ProviderProfileRevisionV1, request: CityCognitionRequestV1
) -> CityCognitionInputV1:
    # Checks have their own effective settings. This ephemeral snapshot retains the
    # selected revision's exact origin/slot; it is never a registry revision or run.
    facts = profile.model_dump(mode="python", exclude={"revision_sha256"})
    facts.update(
        max_output_tokens=384, attempt_timeout_seconds=10, transient_retries=0, repair_attempts=0
    )
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        {
            **facts,
            "revision_sha256": city_cognition_provider_profile_revision_sha256(facts),
        }
    )
    settings = CityCognitionEffectiveSettingsV1.model_validate(
        {name: getattr(snapshot, name) for name in CityCognitionEffectiveSettingsV1.model_fields}
    )
    return CityCognitionInputV1(
        schema_version=1,
        mode=profile.mode,
        run_input_sha256=request.run_input_sha256,
        explainer_id="city-provider-explainer-v1",
        provider_profile=snapshot,
        effective_settings=settings,
        prompt_contract_version=1,
        prompt_contract_text=CITY_COGNITION_PROMPT_CONTRACT_TEXT,
        prompt_contract_sha256=CITY_COGNITION_PROMPT_CONTRACT_SHA256,
        result_schema_version=1,
        result_schema_document=CITY_COGNITION_RESULT_SCHEMA_DOCUMENT,
        result_schema_contract_sha256=CITY_COGNITION_RESULT_SCHEMA_SHA256,
        selection_policy_version=1,
        selection_policy=CITY_COGNITION_DEFAULT_SELECTION_POLICY,
        selection_policy_sha256=CITY_COGNITION_SELECTION_POLICY_SHA256,
        fallback_policy_version=1,
        fallback_policy=CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
        fallback_policy_sha256=CITY_COGNITION_FALLBACK_POLICY_SHA256,
        selected_requests_per_agent=4,
        selected_requests_per_run=60,
        concurrency_limit=4,
        max_request_bytes=8192,
        max_wire_response_bytes=1048576,
        max_output_tokens_per_attempt=384,
        reservation_tokens_per_request=1536,
        initial_attempts=1,
        transient_retries_ceiling=2,
        repair_attempts_ceiling=1,
        attempt_timeout_seconds_ceiling=30,
        aggregate_deadline_seconds=180,
        aggregate_output_token_ceiling=1536,
        reservations=allocate_city_cognition_reservations(
            (request,),
            aggregate_output_token_ceiling=1536,
        ),
    )


class WorkbenchProviderController:
    """Profile/configuration UI boundary with operation-local credentials only."""

    def __init__(
        self,
        registry: ProviderProfileRegistry,
        runtime_policy: ProviderRuntimePolicy,
        *,
        credentials: CityCredentialStore | None = None,
        operations: WorkbenchOperationState | None = None,
        provider_factory: WorkbenchCheckProviderFactory | None = None,
    ) -> None:
        self.registry = registry
        self.operations = operations if operations is not None else WorkbenchOperationState()
        self._policy = runtime_policy
        self._endpoint_policy = CityEndpointPolicy(runtime_policy)
        self._credentials = (
            credentials if credentials is not None else OSKeyringCityCredentialStore(runtime_policy)
        )
        self._factory = provider_factory or CityOpenAICompatibleProvider
        self._last_checks: dict[tuple[str, str], WorkbenchProviderCheckResult] = {}

    def _view(self, profile: ProviderProfileRevisionV1) -> WorkbenchProviderView:
        accepted = True
        try:
            self._endpoint_policy.bind(profile)
        except CityEndpointBindingError:
            accepted = False
        available = False
        if accepted:
            with suppress(CityCredentialStoreError):
                available = self._credentials.available(profile)
        with self.operations.lock:
            last_check = self._last_checks.get((profile.profile_id, profile.revision_sha256))
        return WorkbenchProviderView(
            **profile.model_dump(mode="python"),
            credential_available=available,
            endpoint_policy_accepted=accepted,
            last_check=last_check,
        )

    def list(self) -> WorkbenchProviderList:
        environment_variables = {
            binding.origin_approval_id: binding.environment_variable
            for binding in self._policy.key_environments
        }
        return WorkbenchProviderList(
            profiles=tuple(self._view(profile) for profile in self.registry.list()),
            origins=tuple(
                WorkbenchProviderOriginView(
                    approval_id=origin.approval_id,
                    base_url=origin.base_url,
                    trust_class=origin.trust_class,
                    environment_variable=environment_variables.get(origin.approval_id),
                )
                for origin in self._policy.origins
            ),
        )

    def create(self, draft: ProviderProfileDraftV1 | Mapping[str, object]) -> WorkbenchProviderView:
        checked = None
        try:
            checked = ProviderProfileDraftV1.model_validate(
                draft.model_dump(mode="python")
                if isinstance(draft, ProviderProfileDraftV1)
                else draft
            )
            approval = self._policy.approval(checked.origin_approval_id)
            if approval.base_url != checked.base_url or approval.trust_class != checked.mode:
                raise InvalidProviderProfile()
        except (TypeError, ValueError, ValidationError):
            checked = None
        if checked is None:
            raise InvalidProviderProfile() from None
        return self._view(self.registry.create(checked))

    def store_credential(
        self, profile_id: str, revision_id: str, credential: SecretBuffer
    ) -> WorkbenchProviderView:
        """Take ownership of and clear the supplied secret even when admission fails."""
        lease = None
        failed = False
        try:
            lease = self.operations.begin_credential()
            profile = self.registry.load(profile_id, revision_id)
            try:
                self._endpoint_policy.bind(profile)
            except CityEndpointBindingError:
                raise WorkbenchProviderEndpointRefused() from None
            with self.operations.lock:
                self._last_checks.pop((profile_id, revision_id), None)
            try:
                self._credentials.store(profile, credential)
            except Exception:
                failed = True
            if not failed:
                return self._view(profile)
        finally:
            credential.clear()
            if lease is not None:
                self.operations.finish(lease)
        raise CityCredentialStoreError("provider credential storage was refused") from None

    async def check(self, profile_id: str, revision_id: str) -> WorkbenchProviderCheckResult:
        """Perform one explicit ten-second check; cancellation is never a success."""
        lease = self.operations.begin_check()
        cancellation = lease.cancellation
        assert cancellation is not None
        cancellation.bind_running_loop(asyncio.get_running_loop())
        credential = None
        provider = None
        result = None
        defect = False
        try:
            profile = self.registry.load(profile_id, revision_id)
            try:
                endpoint = self._endpoint_policy.bind(profile)
                credential = self._credentials.resolve(profile)
                if (
                    credential.origin_approval_id != profile.origin_approval_id
                    or credential.credential_slot_id != profile.credential_slot_id
                    or credential.secret.cleared
                    or (profile.mode == "local" and credential.source != "local-dummy")
                    or (
                        profile.mode == "remote"
                        and credential.source not in {"environment", "keyring"}
                    )
                ):
                    raise ProviderCredentialUnavailable("provider credential is unavailable")
            except CityEndpointBindingError:
                result = WorkbenchProviderCheckResult(
                    status="unavailable", reason_code="endpoint-policy-refused"
                )
            except (ProviderCredentialUnavailable, CityCredentialStoreError):
                result = WorkbenchProviderCheckResult(
                    status="unavailable", reason_code="credential-unavailable"
                )
            if result is None:
                assert credential is not None
                cancellation.raise_if_cancelled()
                request = _compatibility_request()
                cognition_input = _compatibility_input(profile, request)
                snapshot = cognition_input.provider_profile
                assert snapshot is not None
                try:
                    provider = self._factory(
                        endpoint_policy=self._endpoint_policy,
                        endpoint=replace(
                            endpoint, profile_revision_sha256=snapshot.revision_sha256
                        ),
                        cognition_input=cognition_input,
                        credential=credential,
                    )
                    result = await self._attempt(provider, request, cancellation)
                except CityProviderCallError as error:
                    result = WorkbenchProviderCheckResult(
                        status="incompatible", reason_code=error.reason_code
                    )
                except TimeoutError:
                    result = WorkbenchProviderCheckResult(
                        status="incompatible", reason_code="timeout-exhausted"
                    )
                except Exception:
                    defect = True
            cancellation.raise_if_cancelled()
        except asyncio.CancelledError:
            cancellation.cancel()
            raise
        finally:
            try:
                if provider is not None:
                    try:
                        await provider.aclose()
                    except asyncio.CancelledError:
                        cancellation.cancel()
                        raise
                    except Exception:
                        defect = True
            finally:
                if credential is not None:
                    credential.clear()
                with self.operations.lock:
                    try:
                        cancellation.raise_if_cancelled()
                        if result is not None and not defect:
                            self._last_checks[(profile_id, revision_id)] = result
                    finally:
                        self.operations.finish(lease)
        if defect:
            raise CityProviderTransportError() from None
        assert result is not None
        return result

    @staticmethod
    async def _attempt(
        provider: _CheckProvider,
        request: CityCognitionRequestV1,
        cancellation: CityCognitionCancellation,
    ) -> WorkbenchProviderCheckResult:
        task = cancellation._run_if_active(lambda: asyncio.create_task(provider.call(request)))
        cancelled = asyncio.create_task(cancellation.wait())
        try:
            async with asyncio.timeout(10):
                done, _ = await asyncio.wait((task, cancelled), return_when=asyncio.FIRST_COMPLETED)
                if cancelled in done:
                    raise asyncio.CancelledError("provider check was cancelled")
                cancellation.raise_if_cancelled()
                attempt = CityProviderAttemptV1.model_validate(
                    task.result().model_dump(mode="python", warnings="none")
                )
                if attempt.status == "valid" and attempt.result is not None:
                    if attempt.result.request_id != request.request_id:
                        return WorkbenchProviderCheckResult(
                            status="incompatible", reason_code="invalid-result"
                        )
                    return WorkbenchProviderCheckResult(status="compatible")
                return WorkbenchProviderCheckResult(
                    status="incompatible", reason_code="invalid-result"
                )
        finally:
            task.cancel()
            cancelled.cancel()
            await asyncio.gather(task, cancelled, return_exceptions=True)
