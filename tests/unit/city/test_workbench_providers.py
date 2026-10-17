from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.cognition.city_credentials import (
    CityCredentialStoreError,
    OSKeyringCityCredentialStore,
    ResolvedCredential,
    SecretBuffer,
)
from adlife.adapters.cognition.city_openai_compatible import (
    CityOpenAICompatibleProvider,
    CityProviderAuthenticationError,
    CityProviderCallError,
    CityProviderRateLimitError,
    CityProviderResponseTooLargeError,
    CityProviderServerError,
    CityProviderTransportError,
)
from adlife.city.provider_profiles import (
    InvalidProviderProfile,
    ProviderProfileConflict,
    ProviderProfileRegistry,
    ProviderProfileRevisionV1,
)
from adlife.city.provider_runtime import build_provider_runtime_policy
from adlife.city.workbench_providers import (
    WorkbenchOperationState,
    WorkbenchProviderBusy,
    WorkbenchProviderCheckResult,
    WorkbenchProviderClosed,
    WorkbenchProviderController,
    WorkbenchProviderEndpointRefused,
    WorkbenchProviderRateLimited,
)
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import (
    CityCognitionRequestV1,
    CityProviderAttemptV1,
    CityProviderRepairContextV1,
)
from adlife.core.simulation.city_cognition import build_city_mock_result
from tests.unit.city.test_provider_profiles import _draft
from tests.unit.cognition.test_city_openai_compatible import (
    _BackendFactory,
    _completion,
    _http_response,
    _result,
)

SECRET = "sk-test-workbench-provider-secret"


class _Keyring:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        del service
        return self.values.get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        del service
        self.values[username] = password


class _Provider:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls = 0
        self.repairs = 0
        self.closed = False
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.failure: BaseException | None = None
        self.invalid = False

    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        self.calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure
        if self.invalid:
            return CityProviderAttemptV1(
                schema_version=1,
                status="invalid-result",
                repair_context=CityProviderRepairContextV1(
                    schema_version=1,
                    reason_code="invalid-schema",
                    safe_excerpt="Invalid schema.",
                ),
            )
        return CityProviderAttemptV1(
            schema_version=1, status="valid", result=build_city_mock_result(request)
        )

    async def aclose(self) -> None:
        self.closed = True


def _controller(
    tmp_path: Path,
    *,
    monotonic: Callable[[], float] = lambda: 0.0,
    provider_factory: Callable[..., _Provider] | None = None,
) -> tuple[WorkbenchProviderController, _Keyring, list[_Provider]]:
    policy = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/v1",),
    )
    keyring = _Keyring()
    credentials = OSKeyringCityCredentialStore(
        policy, backend=keyring, approved_backend_types=(_Keyring,), environ={}
    )
    providers: list[_Provider] = []

    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs) if provider_factory is None else provider_factory(**kwargs)
        providers.append(provider)
        return provider

    controller = WorkbenchProviderController(
        ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "workspace")),
        policy,
        credentials=credentials,
        operations=WorkbenchOperationState(monotonic=monotonic),
        provider_factory=factory,
    )
    return controller, keyring, providers


def _remote(controller: WorkbenchProviderController) -> ProviderProfileRevisionV1:
    view = controller.create(
        _draft(
            profile_id="remote-acme",
            display_label="Remote Acme",
            mode="remote",
            base_url="https://api.example.invalid/v1",
            origin_approval_id="remote-acme",
            max_output_tokens=64,
        )
    )
    return controller.registry.load(view.profile_id, view.revision_sha256)


def test_create_and_listing_distinguish_configuration_credentials_and_explicit_check(
    tmp_path: Path,
) -> None:
    controller, _, providers = _controller(tmp_path)
    local = controller.create(_draft())
    remote = _remote(controller)
    listing = controller.list()
    assert len(listing.profiles) == 2
    views = {item.profile_id: item for item in listing.profiles}
    assert local.credential_available is True
    assert views[remote.profile_id].credential_available is False
    assert local.endpoint_policy_accepted is True
    assert all(item.last_check is None for item in listing.profiles)
    assert listing.oauth is False
    assert listing.provider_configuration is True
    assert listing.cognition_modes == ("rules", "mock", "local", "remote")
    assert providers == []
    assert str(tmp_path) not in listing.model_dump_json()
    assert SECRET not in listing.model_dump_json()
    with pytest.raises(ProviderProfileConflict):
        controller.create(_draft())


def test_unapproved_create_refuses_before_publishing(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    with pytest.raises(InvalidProviderProfile):
        controller.create(_draft(base_url="http://127.0.0.1:9999/v1"))
    assert controller.list().profiles == ()


def test_origin_capabilities_expose_only_the_configured_environment_variable_name(
    tmp_path: Path,
) -> None:
    policy = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/v1",),
        provider_key_envs=("remote-acme=ADLIFE_ACME_API_KEY",),
    )
    controller = WorkbenchProviderController(
        ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "workspace")),
        policy,
    )

    origins = {origin.approval_id: origin for origin in controller.list().origins}

    assert origins["local-ollama"].environment_variable is None
    assert origins["remote-acme"].environment_variable == "ADLIFE_ACME_API_KEY"
    assert SECRET not in controller.list().model_dump_json()


def test_credential_write_and_replacement_clear_input_and_remain_revision_bound(
    tmp_path: Path,
) -> None:
    controller, keyring, _ = _controller(tmp_path)
    remote = _remote(controller)
    original = SecretBuffer(SECRET)
    view = controller.store_credential(remote.profile_id, remote.revision_sha256, original)
    assert original.cleared
    assert view.credential_available
    assert SECRET not in view.model_dump_json()
    replacement = SecretBuffer("replacement-value")
    controller.store_credential(remote.profile_id, remote.revision_sha256, replacement)
    assert replacement.cleared
    assert tuple(keyring.values.values()) == ("replacement-value",)
    changed = controller.create(
        _draft(
            profile_id="remote-acme",
            display_label="Remote Acme",
            mode="remote",
            base_url="https://api.example.invalid/v1",
            origin_approval_id="remote-acme",
            model_id="another-model",
        )
    )
    assert not changed.credential_available


def test_credential_is_cleared_when_lookup_or_local_write_fails(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    local = controller.create(_draft())
    for profile_id, revision_id in (
        ("missing", "0" * 64),
        (local.profile_id, local.revision_sha256),
    ):
        secret = SecretBuffer(SECRET)
        with pytest.raises((LookupError, CityCredentialStoreError)):
            controller.store_credential(profile_id, revision_id, secret)
        assert secret.cleared


async def test_ambiguous_credential_write_clears_stale_compatibility_outcome(
    tmp_path: Path,
) -> None:
    class WriteThenFailKeyring(_Keyring):
        fail_after_write = False

        def set_password(self, service: str, username: str, password: str) -> None:
            super().set_password(service, username, password)
            if self.fail_after_write:
                raise RuntimeError("ambiguous keyring write")

    policy = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/v1",)
    )
    keyring = WriteThenFailKeyring()
    credentials = OSKeyringCityCredentialStore(
        policy,
        backend=keyring,
        approved_backend_types=(WriteThenFailKeyring,),
        environ={},
    )
    controller = WorkbenchProviderController(
        ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "workspace")),
        policy,
        credentials=credentials,
        provider_factory=lambda **kwargs: _Provider(**kwargs),
    )
    remote = _remote(controller)
    controller.store_credential(remote.profile_id, remote.revision_sha256, SecretBuffer(SECRET))
    assert (
        await controller.check(remote.profile_id, remote.revision_sha256)
    ).status == "compatible"
    assert controller.list().profiles[0].last_check is not None

    keyring.fail_after_write = True
    replacement = SecretBuffer("replacement-after-check")
    with pytest.raises(CityCredentialStoreError):
        controller.store_credential(remote.profile_id, remote.revision_sha256, replacement)

    assert replacement.cleared
    assert tuple(keyring.values.values()) == ("replacement-after-check",)
    assert controller.list().profiles[0].last_check is None


def test_credential_write_refuses_profile_excluded_by_current_startup_policy(
    tmp_path: Path,
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    approved = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/v1",)
    )
    registry = ProviderProfileRegistry(workspace)
    original = WorkbenchProviderController(registry, approved)
    remote = _remote(original)
    restarted = WorkbenchProviderController(registry, build_provider_runtime_policy())
    secret = SecretBuffer(SECRET)

    with pytest.raises(WorkbenchProviderEndpointRefused):
        restarted.store_credential(remote.profile_id, remote.revision_sha256, secret)

    assert secret.cleared
    view = restarted.list().profiles[0]
    assert view.endpoint_policy_accepted is False
    assert view.last_check is None


@pytest.mark.parametrize(
    "status, reason_code",
    [
        ("compatible", "credential-unavailable"),
        ("incompatible", None),
        ("unavailable", "invalid-result"),
    ],
)
def test_provider_check_result_enforces_status_reason_coherence(
    status: str,
    reason_code: str | None,
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkbenchProviderCheckResult.model_validate({"status": status, "reason_code": reason_code})


async def test_successful_check_uses_independent_limits_and_clears_operation_credential(
    tmp_path: Path,
) -> None:
    controller, _, providers = _controller(tmp_path)
    remote = _remote(controller)
    controller.store_credential(remote.profile_id, remote.revision_sha256, SecretBuffer(SECRET))
    result = await controller.check(remote.profile_id, remote.revision_sha256)
    assert result.model_dump(mode="json") == {"status": "compatible", "reason_code": None}
    provider = providers[0]
    frozen = provider.kwargs["cognition_input"]
    assert frozen.effective_settings.max_output_tokens == 384
    assert frozen.effective_settings.attempt_timeout_seconds == 10
    assert frozen.effective_settings.transient_retries == 0
    assert frozen.effective_settings.repair_attempts == 0
    assert frozen.max_wire_response_bytes == 1_048_576
    assert provider.calls == 1
    assert provider.closed
    assert provider.kwargs["credential"].secret.cleared
    assert controller.registry.load(remote.profile_id, remote.revision_sha256) == remote
    listed = controller.list().profiles[0]
    assert listed.last_check == result


@pytest.mark.parametrize(
    "failure, reason",
    [
        (CityProviderAuthenticationError(), "authentication-refused"),
        (CityProviderRateLimitError(), "rate-limit-exhausted"),
        (CityProviderServerError(), "server-error-exhausted"),
        (CityProviderResponseTooLargeError(), "response-too-large"),
    ],
)
async def test_expected_check_failures_are_status_only_and_never_retried(
    tmp_path: Path,
    failure: CityProviderCallError,
    reason: str,
) -> None:
    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.failure = failure
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    result = await controller.check(local.profile_id, local.revision_sha256)
    assert result.model_dump(mode="json") == {"status": "incompatible", "reason_code": reason}
    assert providers[0].calls == 1
    assert providers[0].closed
    assert providers[0].kwargs["credential"].secret.cleared


async def test_invalid_schema_is_not_repaired(tmp_path: Path) -> None:
    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.invalid = True
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    result = await controller.check(local.profile_id, local.revision_sha256)
    assert result.reason_code == "invalid-result"
    assert providers[0].calls == 1
    assert providers[0].repairs == 0


async def test_result_with_a_different_request_id_is_incompatible(tmp_path: Path) -> None:
    class MismatchedProvider(_Provider):
        async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1:
            attempt = await super().call(request)
            assert attempt.result is not None
            mismatched = attempt.result.model_copy(update={"request_id": "cog-" + "f" * 64})
            return attempt.model_copy(update={"result": mismatched})

    controller, _, providers = _controller(tmp_path, provider_factory=MismatchedProvider)
    local = controller.create(_draft())

    result = await controller.check(local.profile_id, local.revision_sha256)

    assert result.model_dump(mode="json") == {
        "status": "incompatible",
        "reason_code": "invalid-result",
    }
    assert providers[0].calls == 1
    assert providers[0].closed


async def test_rolling_minute_counts_five_starts_and_boundary_expires(tmp_path: Path) -> None:
    now = [100.0]
    controller, _, providers = _controller(tmp_path, monotonic=lambda: now[0])
    local = controller.create(_draft())
    for _ in range(5):
        await controller.check(local.profile_id, local.revision_sha256)
    now[0] = 159.999
    with pytest.raises(WorkbenchProviderRateLimited):
        await controller.check(local.profile_id, local.revision_sha256)
    assert len(providers) == 5
    now[0] = 160.0
    assert (await controller.check(local.profile_id, local.revision_sha256)).status == "compatible"


async def test_check_job_and_credential_operations_are_exclusive(tmp_path: Path) -> None:
    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.release = asyncio.Event()
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    task = asyncio.create_task(controller.check(local.profile_id, local.revision_sha256))
    while not providers:
        await asyncio.sleep(0)
    await providers[0].entered.wait()
    with pytest.raises(WorkbenchProviderBusy):
        await controller.check(local.profile_id, local.revision_sha256)
    with pytest.raises(WorkbenchProviderBusy):
        controller.operations.begin_job()
    secret = SecretBuffer(SECRET)
    with pytest.raises(WorkbenchProviderBusy):
        controller.store_credential(local.profile_id, local.revision_sha256, secret)
    assert secret.cleared
    controller.operations.cancel_check()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert providers[0].closed
    assert providers[0].kwargs["credential"].secret.cleared
    lease = controller.operations.begin_job()
    with pytest.raises(WorkbenchProviderBusy):
        await controller.check(local.profile_id, local.revision_sha256)
    controller.operations.finish(lease)


async def test_shutdown_cancels_check_and_permanently_refuses_new_work(tmp_path: Path) -> None:
    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.release = asyncio.Event()
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    task = asyncio.create_task(controller.check(local.profile_id, local.revision_sha256))
    while not providers:
        await asyncio.sleep(0)
    await providers[0].entered.wait()
    controller.operations.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert providers[0].closed
    with pytest.raises(WorkbenchProviderClosed):
        controller.operations.begin_job()
    with pytest.raises(WorkbenchProviderClosed):
        await controller.check(local.profile_id, local.revision_sha256)
    assert controller.list().profiles[0].last_check is None


async def test_unexpected_provider_defect_is_value_free_and_releases_state(tmp_path: Path) -> None:
    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.failure = RuntimeError(SECRET + str(tmp_path))
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    with pytest.raises(CityProviderTransportError) as caught:
        await controller.check(local.profile_id, local.revision_sha256)
    assert SECRET not in str(caught.value)
    assert caught.value.__context__ is None
    assert providers[0].closed
    lease = controller.operations.begin_job()
    controller.operations.finish(lease)


def test_atomic_job_admission_allows_only_one_thread() -> None:
    operations = WorkbenchOperationState()
    barrier = threading.Barrier(3)
    winners = []
    refusals = []

    def enter() -> None:
        barrier.wait()
        try:
            winners.append(operations.begin_job())
        except WorkbenchProviderBusy:
            refusals.append(True)

    threads = [threading.Thread(target=enter) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert len(winners) == 1
    assert len(refusals) == 1
    operations.finish(winners[0])


def test_simultaneous_check_and_job_admission_have_exactly_one_winner() -> None:
    operations = WorkbenchOperationState()
    barrier = threading.Barrier(3)
    winners = []
    refusals = []

    def enter(kind: str) -> None:
        barrier.wait()
        try:
            winners.append(operations.begin_check() if kind == "check" else operations.begin_job())
        except WorkbenchProviderBusy:
            refusals.append(kind)

    threads = [
        threading.Thread(target=enter, args=("check",)),
        threading.Thread(target=enter, args=("job",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    assert len(refusals) == 1
    operations.finish(winners[0])


async def test_missing_credentials_do_not_construct_provider_and_consume_a_start(
    tmp_path: Path,
) -> None:
    controller, _, providers = _controller(tmp_path)
    remote = _remote(controller)
    for _ in range(5):
        result = await controller.check(remote.profile_id, remote.revision_sha256)
        assert result.model_dump(mode="json") == {
            "status": "unavailable",
            "reason_code": "credential-unavailable",
        }
    with pytest.raises(WorkbenchProviderRateLimited):
        await controller.check(remote.profile_id, remote.revision_sha256)
    assert providers == []


async def test_check_refuses_and_clears_a_misbound_resolved_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, providers = _controller(tmp_path)
    remote = _remote(controller)
    credential = ResolvedCredential(
        source="environment",
        secret=SecretBuffer(SECRET),
        origin_approval_id="another-origin",
        credential_slot_id=remote.credential_slot_id,
    )
    monkeypatch.setattr(controller._credentials, "resolve", lambda _profile: credential)
    result = await controller.check(remote.profile_id, remote.revision_sha256)
    assert result.status == "unavailable"
    assert result.reason_code == "credential-unavailable"
    assert credential.secret.cleared
    assert providers == []


async def test_deadline_interrupts_hanging_attempt_and_closes_provider(tmp_path: Path) -> None:
    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.release = asyncio.Event()
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    result = await asyncio.wait_for(controller.check(local.profile_id, local.revision_sha256), 12)
    assert result.status == "incompatible"
    assert result.reason_code == "timeout-exhausted"
    assert providers[0].closed
    assert providers[0].calls == 1
    assert providers[0].kwargs["credential"].secret.cleared


async def test_cancellation_during_provider_close_does_not_save_success(tmp_path: Path) -> None:
    closing = asyncio.Event()

    class _SlowCloseProvider(_Provider):
        async def aclose(self) -> None:
            closing.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True

    controller, _, providers = _controller(tmp_path, provider_factory=_SlowCloseProvider)
    local = controller.create(_draft())
    task = asyncio.create_task(controller.check(local.profile_id, local.revision_sha256))
    await closing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert controller.list().profiles[0].last_check is None
    assert providers[0].kwargs["credential"].secret.cleared
    lease = controller.operations.begin_job()
    controller.operations.finish(lease)


def test_stale_lease_cannot_release_new_operation() -> None:
    state = WorkbenchOperationState()
    first = state.begin_job()
    state.finish(first)
    second = state.begin_job()
    state.finish(first)
    with pytest.raises(WorkbenchProviderBusy):
        state.begin_check()
    state.finish(second)


def test_prebound_cancellation_and_shutdown_retain_active_lease_until_cleanup() -> None:
    state = WorkbenchOperationState()
    lease = state.begin_check()
    state.cancel_check()
    assert lease.cancellation is not None
    with pytest.raises(asyncio.CancelledError):
        lease.cancellation.raise_if_cancelled()
    with pytest.raises(WorkbenchProviderBusy):
        state.begin_job()
    state.close()
    state.finish(lease)
    with pytest.raises(WorkbenchProviderClosed):
        state.begin_check()


async def test_cancellation_during_factory_prevents_attempt_dispatch(tmp_path: Path) -> None:
    state = WorkbenchOperationState()
    providers = []

    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        providers.append(provider)
        state.cancel_check()
        return provider

    controller, _, _ = _controller(tmp_path, provider_factory=factory)
    controller.operations = state
    local = controller.create(_draft())
    with pytest.raises(asyncio.CancelledError):
        await controller.check(local.profile_id, local.revision_sha256)
    assert providers[0].calls == 0
    assert providers[0].closed
    assert providers[0].kwargs["credential"].secret.cleared


async def test_real_transport_uses_full_schema_and_fixed_check_limits(tmp_path: Path) -> None:
    policy = build_provider_runtime_policy()
    providers = []
    backends = []

    class _LocalBackendFactory(_BackendFactory):
        def __call__(self):
            backend = super().__call__()
            backend.stream.peer = "127.0.0.1"
            return backend

    def factory(**kwargs: object) -> CityOpenAICompatibleProvider:
        frozen = kwargs["cognition_input"]
        request_id = frozen.reservations[0].request_id
        backend = _LocalBackendFactory(
            (_http_response(_completion(json.dumps(_result(request_id)))),)
        )
        backends.append(backend)
        provider = CityOpenAICompatibleProvider(**kwargs, network_backend_factory=backend)
        providers.append(provider)
        return provider

    controller = WorkbenchProviderController(
        ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "workspace")),
        policy,
        provider_factory=factory,
    )
    local = controller.create(_draft(max_output_tokens=1, attempt_timeout_seconds=1))
    result = await controller.check(local.profile_id, local.revision_sha256)
    assert result.status == "compatible"
    written = backends[0].backends[0].stream.writes
    wire = b"".join(written)
    payload = json.loads(wire.split(b"\r\n\r\n", 1)[1])
    assert payload["max_tokens"] == 384
    assert payload["stream"] is False
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert set(schema["schema"]["required"]) == {
        "schema_version",
        "request_id",
        "interpretation",
        "decision_factors",
        "safety_flags",
        "disclosure",
    }
    assert providers[0].is_closed
