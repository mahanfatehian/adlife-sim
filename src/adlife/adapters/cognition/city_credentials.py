"""Origin-bound credentials for optional city narrative providers.

Credential values are resolved only for one already validated immutable profile.  Local
loopback profiles receive a fixed non-secret dummy value.  Remote profiles first consult
an explicitly approved OS keyring backend and then, only when startup policy names one,
the exact bound environment variable.  Nothing in this module prompts or persists a
plaintext fallback.

``SecretBuffer.clear`` is best-effort memory hygiene, not a zeroization guarantee: Python,
the OS keyring implementation, and the HTTP stack may create immutable copies.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from threading import RLock
from typing import Literal, Protocol, cast

from pydantic import ValidationError

from adlife.city.provider_profiles import ProviderProfileRevisionV1
from adlife.city.provider_runtime import (
    ProviderOriginApproval,
    ProviderRuntimeConfigurationError,
    ProviderRuntimePolicy,
)

MAX_PROVIDER_CREDENTIAL_BYTES = 4_096
KEYRING_SERVICE_NAME = "adlife.city-provider.v1"
LOCAL_PROVIDER_CREDENTIAL_SOURCE: Literal["local-dummy"] = "local-dummy"
_LOCAL_DUMMY_CREDENTIAL = "adlife-local-provider"

_APPROVED_OS_BACKEND_IDENTITIES = frozenset(
    {
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.macOS", "Keyring"),
    }
)


class CityCredentialStoreError(RuntimeError):
    """A constant, credential-free failure at the secret-store boundary."""


class ProviderCredentialUnavailable(CityCredentialStoreError):
    """The exact profile has no approved credential source with a usable value."""


class ApprovedKeyringUnavailable(CityCredentialStoreError):
    """No exact approved OS-backed keyring can accept a credential write."""


class _KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


class SecretBuffer:
    """A bounded visible-ASCII credential held in a mutable byte array."""

    __slots__ = ("_data",)

    def __init__(self, value: str | bytes | bytearray) -> None:
        encoded: bytes | None
        if isinstance(value, str):
            try:
                encoded = value.encode("ascii")
            except UnicodeError:
                encoded = None
        elif isinstance(value, (bytes, bytearray)):
            encoded = bytes(value)
        else:
            encoded = None
        if encoded is None:
            raise CityCredentialStoreError("provider credential is invalid") from None
        if not 1 <= len(encoded) <= MAX_PROVIDER_CREDENTIAL_BYTES or any(
            byte < 0x21 or byte > 0x7E for byte in encoded
        ):
            raise CityCredentialStoreError("provider credential is invalid") from None
        self._data = bytearray(encoded)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted bytes={len(self._data)} cleared={self.cleared}>"

    def __str__(self) -> str:
        return "[redacted credential]"

    @property
    def cleared(self) -> bool:
        return not self._data

    def reveal_text(self) -> str:
        """Make the unavoidable short-lived text copy needed by keyring/HTTP APIs."""
        return self._data.decode("ascii")

    def copy(self) -> SecretBuffer:
        if self.cleared:
            raise CityCredentialStoreError("provider credential is unavailable") from None
        return SecretBuffer(self._data)

    def clear(self) -> None:
        for index in range(len(self._data)):
            self._data[index] = 0
        self._data.clear()


def _new_local_dummy_credential() -> SecretBuffer:
    """Return the module-owned non-secret credential used only for loopback calls."""
    return SecretBuffer(_LOCAL_DUMMY_CREDENTIAL)


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedCredential:
    """One operation-local secret plus non-secret source and binding metadata."""

    source: Literal["local-dummy", "keyring", "environment"]
    secret: SecretBuffer
    origin_approval_id: str
    credential_slot_id: str

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} source={self.source!r} "
            f"origin_approval_id={self.origin_approval_id!r} "
            f"credential_slot_id={self.credential_slot_id!r} secret=[redacted]>"
        )

    def clear(self) -> None:
        self.secret.clear()


class CityCredentialStore(Protocol):
    def available(self, profile: ProviderProfileRevisionV1) -> bool: ...

    def resolve(self, profile: ProviderProfileRevisionV1) -> ResolvedCredential: ...

    def store(self, profile: ProviderProfileRevisionV1, credential: SecretBuffer) -> None: ...


def _load_default_backend() -> object:
    import keyring

    return keyring.get_keyring()


class OSKeyringCityCredentialStore:
    """Lazy keyring/environment resolver constrained by one startup policy."""

    service_name = KEYRING_SERVICE_NAME

    def __init__(
        self,
        policy: ProviderRuntimePolicy,
        *,
        backend: object | None = None,
        backend_loader: Callable[[], object] | None = None,
        approved_backend_types: tuple[type[object], ...] = (),
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._policy = policy
        self._backend = backend
        self._backend_loaded = backend is not None
        self._backend_loader = backend_loader or _load_default_backend
        self._approved_backend_types = approved_backend_types
        self._environ = environ
        self._lock = RLock()

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} origins={len(self._policy.origins)} "
            f"environment_bindings={len(self._policy.key_environments)} "
            "credentials=[redacted]>"
        )

    def _validated_profile(
        self, profile: ProviderProfileRevisionV1
    ) -> tuple[ProviderProfileRevisionV1, ProviderOriginApproval]:
        checked: ProviderProfileRevisionV1 | None = None
        approval: ProviderOriginApproval | None = None
        try:
            if not isinstance(profile, ProviderProfileRevisionV1):
                raise TypeError
            checked = ProviderProfileRevisionV1.model_validate(
                profile.model_dump(mode="json", warnings="none")
            )
            approval = self._policy.validate_profile(checked)
        except (
            AttributeError,
            ProviderRuntimeConfigurationError,
            TypeError,
            ValidationError,
            ValueError,
        ):
            pass
        if checked is None or approval is None:
            raise CityCredentialStoreError("provider credential binding is invalid") from None
        return checked, approval

    def _load_backend(self) -> object | None:
        with self._lock:
            if self._backend_loaded:
                return self._backend
            try:
                self._backend = self._backend_loader()
            except Exception:
                self._backend = None
            self._backend_loaded = True
            return self._backend

    def _approved_backend(self) -> _KeyringBackend | None:
        backend = self._load_backend()
        if backend is None:
            return None
        backend_type = type(backend)
        identity = (backend_type.__module__, backend_type.__qualname__)
        if (
            backend_type not in self._approved_backend_types
            and identity not in _APPROVED_OS_BACKEND_IDENTITIES
        ):
            return None
        if not callable(getattr(backend, "get_password", None)) or not callable(
            getattr(backend, "set_password", None)
        ):
            return None
        return cast(_KeyringBackend, backend)

    def keyring_username(self, profile: ProviderProfileRevisionV1) -> str:
        """Bind one keyring username to the exact immutable revision and slot."""
        return f"v1:{profile.profile_id}:{profile.revision_sha256}:{profile.credential_slot_id}"

    def _from_keyring(self, profile: ProviderProfileRevisionV1) -> ResolvedCredential | None:
        backend = self._approved_backend()
        if backend is None:
            return None
        lookup_failed = False
        try:
            value = backend.get_password(
                self.service_name,
                self.keyring_username(profile),
            )
        except Exception:
            lookup_failed = True
            value = None
        if lookup_failed:
            raise CityCredentialStoreError("OS keyring credential lookup failed") from None
        if value is None:
            return None
        secret: SecretBuffer | None = None
        with suppress(CityCredentialStoreError):
            secret = SecretBuffer(value)
        if secret is None:
            raise CityCredentialStoreError("stored provider credential is invalid") from None
        return ResolvedCredential(
            source="keyring",
            secret=secret,
            origin_approval_id=profile.origin_approval_id,
            credential_slot_id=profile.credential_slot_id,
        )

    def _from_environment(self, profile: ProviderProfileRevisionV1) -> ResolvedCredential | None:
        variable = self._policy.environment_variable_for(profile)
        if variable is None:
            return None
        environment = os.environ if self._environ is None else self._environ
        value = environment.get(variable)
        if value is None or value == "":
            return None
        secret: SecretBuffer | None = None
        with suppress(CityCredentialStoreError):
            secret = SecretBuffer(value)
        if secret is None:
            raise CityCredentialStoreError("environment provider credential is invalid") from None
        return ResolvedCredential(
            source="environment",
            secret=secret,
            origin_approval_id=profile.origin_approval_id,
            credential_slot_id=profile.credential_slot_id,
        )

    def resolve(self, profile: ProviderProfileRevisionV1) -> ResolvedCredential:
        profile, approval = self._validated_profile(profile)
        if approval.trust_class == "local":
            return ResolvedCredential(
                source=LOCAL_PROVIDER_CREDENTIAL_SOURCE,
                secret=_new_local_dummy_credential(),
                origin_approval_id=profile.origin_approval_id,
                credential_slot_id=profile.credential_slot_id,
            )
        with self._lock:
            keyring_credential = self._from_keyring(profile)
            if keyring_credential is not None:
                return keyring_credential
            environment_credential = self._from_environment(profile)
            if environment_credential is not None:
                return environment_credential
        raise ProviderCredentialUnavailable("provider credential is unavailable") from None

    def available(self, profile: ProviderProfileRevisionV1) -> bool:
        try:
            resolved = self.resolve(profile)
        except ProviderCredentialUnavailable:
            return False
        try:
            return True
        finally:
            resolved.clear()

    def store(self, profile: ProviderProfileRevisionV1, credential: SecretBuffer) -> None:
        profile, approval = self._validated_profile(profile)
        if approval.trust_class != "remote":
            raise CityCredentialStoreError("local provider credentials cannot be stored") from None
        backend = self._approved_backend()
        if backend is None:
            raise ApprovedKeyringUnavailable("approved OS keyring backend is unavailable") from None
        copied = credential.copy()
        try:
            write_failed = False
            try:
                backend.set_password(
                    self.service_name,
                    self.keyring_username(profile),
                    copied.reveal_text(),
                )
            except Exception:
                write_failed = True
            if write_failed:
                raise CityCredentialStoreError("OS keyring credential write failed") from None
        finally:
            copied.clear()


__all__ = [
    "KEYRING_SERVICE_NAME",
    "LOCAL_PROVIDER_CREDENTIAL_SOURCE",
    "MAX_PROVIDER_CREDENTIAL_BYTES",
    "ApprovedKeyringUnavailable",
    "CityCredentialStore",
    "CityCredentialStoreError",
    "OSKeyringCityCredentialStore",
    "ProviderCredentialUnavailable",
    "ResolvedCredential",
    "SecretBuffer",
]
