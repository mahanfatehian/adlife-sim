from __future__ import annotations

import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256

import pytest

from adlife.adapters.cognition.city_credentials import (
    LOCAL_PROVIDER_CREDENTIAL_SOURCE,
    CityCredentialStoreError,
    OSKeyringCityCredentialStore,
    SecretBuffer,
)
from adlife.city.provider_profiles import ProviderProfileDraftV1, ProviderProfileRevisionV1
from adlife.city.provider_runtime import ProviderRuntimePolicy, build_provider_runtime_policy
from adlife.core.domain.city_cognition import (
    canonical_city_cognition_provider_profile_payload,
    city_cognition_provider_profile_revision_sha256,
)

FAKE_REMOTE_SECRET = "sk-test-000000000000000000000000"


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls.append((service, username))
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.set_calls.append((service, username))
        self.values[(service, username)] = password


class FakeBackendSubclass(FakeBackend):
    pass


class FailingBackend(FakeBackend):
    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError(f"backend echoed {FAKE_REMOTE_SECRET}")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError(f"backend echoed {password}")


class TrackingEnvironment(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("credential resolution must not enumerate the environment")

    def __len__(self) -> int:
        raise AssertionError("credential resolution must not inspect ambient variables")

    def get(self, key: str, default: str | None = None) -> str | None:
        self.reads.append(key)
        return self._values.get(key, default)


def _policy(*, bind_environment: bool = True) -> ProviderRuntimePolicy:
    return build_provider_runtime_policy(
        provider_origins=("remote=https://api.example.invalid/v1",),
        provider_key_envs=("remote=REMOTE_API_KEY",) if bind_environment else (),
    )


def _profile(**updates: object) -> ProviderProfileRevisionV1:
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "remote-profile",
        "display_label": "Remote profile",
        "mode": "remote",
        "base_url": "https://api.example.invalid/v1",
        "model_id": "model-v1",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "omit",
        "seed": None,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "remote",
    }
    document.update(updates)
    draft = ProviderProfileDraftV1.model_validate(document)
    credential_slot_id = sha256(
        canonical_city_cognition_provider_profile_payload(draft)
    ).hexdigest()
    facts = {**draft.model_dump(mode="json"), "credential_slot_id": credential_slot_id}
    return ProviderProfileRevisionV1.model_validate(
        {
            **facts,
            "revision_sha256": city_cognition_provider_profile_revision_sha256(facts),
        }
    )


@pytest.mark.parametrize("size", [1, 4_096])
def test_secret_buffer_accepts_visible_ascii_boundaries_and_clears(size: int) -> None:
    secret = "x" * size
    buffer = SecretBuffer(secret)
    copied = buffer.copy()

    assert len(buffer) == size
    assert buffer.reveal_text() == secret
    assert copied.reveal_text() == secret
    assert secret not in repr(buffer)
    assert secret not in str(buffer)
    buffer.clear()
    assert buffer.cleared
    assert buffer.reveal_text() == ""
    assert copied.reveal_text() == secret
    copied.clear()


@pytest.mark.parametrize(
    "value",
    ["", "x" * 4_097, "contains space", "line\nbreak", "tab\tvalue", "é", b"\xff"],
)
def test_secret_buffer_refuses_non_header_safe_values_without_echo(
    value: str | bytes | bytearray,
) -> None:
    with pytest.raises(CityCredentialStoreError) as caught:
        SecretBuffer(value)

    assert str(caught.value) == "provider credential is invalid"
    assert repr(value) not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_approved_keyring_precedes_bound_environment_without_reading_it() -> None:
    backend = FakeBackend()
    environment = TrackingEnvironment({"REMOTE_API_KEY": "environment-value"})
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=environment,
    )
    profile = _profile()
    username = store.keyring_username(profile)
    backend.values[(store.service_name, username)] = FAKE_REMOTE_SECRET

    resolved = store.resolve(profile)
    try:
        assert resolved.source == "keyring"
        assert resolved.secret.reveal_text() == FAKE_REMOTE_SECRET
        assert environment.reads == []
        assert FAKE_REMOTE_SECRET not in repr(resolved)
    finally:
        resolved.clear()
    assert resolved.secret.cleared


def test_bound_environment_is_read_exactly_when_keyring_has_no_value() -> None:
    backend = FakeBackend()
    environment = TrackingEnvironment(
        {"REMOTE_API_KEY": FAKE_REMOTE_SECRET, "AMBIENT_PROVIDER_KEY": "must-not-read"}
    )
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=environment,
    )

    resolved = store.resolve(_profile())
    try:
        assert resolved.source == "environment"
        assert resolved.secret.reveal_text() == FAKE_REMOTE_SECRET
        assert environment.reads == ["REMOTE_API_KEY"]
    finally:
        resolved.clear()


@pytest.mark.parametrize("source", ["keyring", "environment"])
def test_invalid_resolved_secret_is_refused_without_retaining_its_exception_chain(
    source: str,
) -> None:
    invalid_secret = f"{FAKE_REMOTE_SECRET}-é"
    backend = FakeBackend()
    environment_values: dict[str, str] = {}
    profile = _profile()
    if source == "keyring":
        bootstrap_store = OSKeyringCityCredentialStore(
            _policy(),
            backend=backend,
            approved_backend_types=(FakeBackend,),
            environ=TrackingEnvironment({}),
        )
        backend.values[
            (bootstrap_store.service_name, bootstrap_store.keyring_username(profile))
        ] = invalid_secret
    else:
        environment_values["REMOTE_API_KEY"] = invalid_secret
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=TrackingEnvironment(environment_values),
    )

    with pytest.raises(CityCredentialStoreError) as caught:
        store.resolve(profile)

    assert invalid_secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_no_environment_binding_means_no_ambient_environment_read() -> None:
    backend = FakeBackend()
    environment = TrackingEnvironment({"OPENAI_API_KEY": FAKE_REMOTE_SECRET})
    store = OSKeyringCityCredentialStore(
        _policy(bind_environment=False),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=environment,
    )

    with pytest.raises(CityCredentialStoreError, match="unavailable"):
        store.resolve(_profile())

    assert environment.reads == []


def test_local_profile_uses_fixed_dummy_without_keyring_or_environment() -> None:
    backend = FailingBackend()
    environment = TrackingEnvironment({"REMOTE_API_KEY": FAKE_REMOTE_SECRET})
    store = OSKeyringCityCredentialStore(
        build_provider_runtime_policy(),
        backend=backend,
        approved_backend_types=(FailingBackend,),
        environ=environment,
    )
    local = _profile(
        profile_id="local-ollama",
        display_label="Local Ollama",
        mode="local",
        base_url="http://127.0.0.1:11434/v1",
        origin_approval_id="local-ollama",
    )

    resolved = store.resolve(local)
    try:
        assert resolved.source == LOCAL_PROVIDER_CREDENTIAL_SOURCE
        assert resolved.secret.reveal_text() != FAKE_REMOTE_SECRET
        assert environment.reads == []
        assert backend.get_calls == []
    finally:
        resolved.clear()


def test_profile_origin_mismatch_refuses_before_secret_sources_are_touched() -> None:
    backend = FakeBackend()
    environment = TrackingEnvironment({"REMOTE_API_KEY": FAKE_REMOTE_SECRET})
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=environment,
    )

    with pytest.raises(CityCredentialStoreError) as caught:
        store.resolve(_profile(base_url="https://other.example.invalid/v1"))

    assert str(caught.value) == "provider credential binding is invalid"
    assert backend.get_calls == []
    assert environment.reads == []


def test_constructed_profile_cannot_borrow_a_different_credential_slot() -> None:
    backend = FakeBackend()
    environment = TrackingEnvironment({"REMOTE_API_KEY": FAKE_REMOTE_SECRET})
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=environment,
    )
    valid = _profile()
    document = valid.model_dump(mode="python")
    document["credential_slot_id"] = "0" * 64
    tampered = ProviderProfileRevisionV1.model_construct(**document)

    with pytest.raises(CityCredentialStoreError) as caught:
        store.resolve(tampered)

    assert str(caught.value) == "provider credential binding is invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert backend.get_calls == []
    assert environment.reads == []


def test_store_and_availability_use_exact_revision_username_without_exposing_value() -> None:
    backend = FakeBackend()
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=TrackingEnvironment({}),
    )
    first = _profile()
    second = _profile(model_id="model-v2")
    credential = SecretBuffer(FAKE_REMOTE_SECRET)

    store.store(first, credential)

    assert not credential.cleared
    assert store.available(first)
    assert not store.available(second)
    assert store.keyring_username(first) != store.keyring_username(second)
    assert all(FAKE_REMOTE_SECRET not in repr(call) for call in backend.set_calls)
    credential.clear()


def test_backend_type_policy_is_exact_and_plaintext_or_subclasses_are_refused() -> None:
    store = OSKeyringCityCredentialStore(
        _policy(bind_environment=False),
        backend=FakeBackendSubclass(),
        approved_backend_types=(FakeBackend,),
        environ=TrackingEnvironment({}),
    )

    with pytest.raises(CityCredentialStoreError) as resolve_error:
        store.resolve(_profile())
    with pytest.raises(CityCredentialStoreError) as store_error:
        store.store(_profile(), SecretBuffer(FAKE_REMOTE_SECRET))

    assert str(resolve_error.value) == "provider credential is unavailable"
    assert str(store_error.value) == "approved OS keyring backend is unavailable"


@pytest.mark.parametrize("operation", ["resolve", "store"])
def test_backend_failures_are_value_free_and_never_chained(operation: str) -> None:
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=FailingBackend(),
        approved_backend_types=(FailingBackend,),
        environ=TrackingEnvironment({"REMOTE_API_KEY": FAKE_REMOTE_SECRET}),
    )
    credential = SecretBuffer(FAKE_REMOTE_SECRET)

    with pytest.raises(CityCredentialStoreError) as caught:
        if operation == "resolve":
            store.resolve(_profile())
        else:
            store.store(_profile(), credential)

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert FAKE_REMOTE_SECRET not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    credential.clear()


def test_lazy_backend_loader_is_never_called_for_local_and_is_value_free_on_failure() -> None:
    calls = 0

    def failing_loader() -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"loader echoed {FAKE_REMOTE_SECRET}")

    store = OSKeyringCityCredentialStore(
        build_provider_runtime_policy(),
        backend_loader=failing_loader,
        environ=TrackingEnvironment({}),
    )
    local = _profile(
        profile_id="local-ollama",
        display_label="Local Ollama",
        mode="local",
        base_url="http://127.0.0.1:11434/v1",
        origin_approval_id="local-ollama",
    )

    resolved = store.resolve(local)
    resolved.clear()
    assert calls == 0

    remote_store = OSKeyringCityCredentialStore(
        _policy(bind_environment=False),
        backend_loader=failing_loader,
        environ=TrackingEnvironment({}),
    )
    with pytest.raises(CityCredentialStoreError) as caught:
        remote_store.store(_profile(), SecretBuffer(FAKE_REMOTE_SECRET))
    assert str(caught.value) == "approved OS keyring backend is unavailable"
    assert caught.value.__cause__ is None
    assert calls == 1


def test_store_clears_its_internal_copy_even_when_keyring_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared_ids: list[int] = []
    original_clear = SecretBuffer.clear

    def recording_clear(buffer: SecretBuffer) -> None:
        cleared_ids.append(id(buffer))
        original_clear(buffer)

    monkeypatch.setattr(SecretBuffer, "clear", recording_clear)
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=FailingBackend(),
        approved_backend_types=(FailingBackend,),
        environ=TrackingEnvironment({}),
    )
    credential = SecretBuffer(FAKE_REMOTE_SECRET)

    with pytest.raises(CityCredentialStoreError):
        store.store(_profile(), credential)

    assert id(credential) not in cleared_ids
    assert len(cleared_ids) == 1
    credential.clear()


@contextmanager
def _resolved_secret(store: OSKeyringCityCredentialStore) -> Iterator[SecretBuffer]:
    resolved = store.resolve(_profile())
    try:
        yield resolved.secret.copy()
    finally:
        resolved.clear()


def test_operation_local_copy_can_always_be_cleared_in_finally() -> None:
    backend = FakeBackend()
    store = OSKeyringCityCredentialStore(
        _policy(),
        backend=backend,
        approved_backend_types=(FakeBackend,),
        environ=TrackingEnvironment({"REMOTE_API_KEY": FAKE_REMOTE_SECRET}),
    )

    with _resolved_secret(store) as copied:
        try:
            assert copied.reveal_text() == FAKE_REMOTE_SECRET
        finally:
            copied.clear()

    assert copied.cleared
