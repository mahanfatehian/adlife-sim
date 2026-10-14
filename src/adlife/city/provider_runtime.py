"""Non-secret startup policy for optional city cognition providers.

The workbench does not discover provider endpoints or ambient credentials.  An operator
approves exact origins and, independently, may bind one remote approval to one explicitly
named environment variable at process startup.  This module stores names and normalized
origins only; credential values belong to the credential adapter.
"""

from __future__ import annotations

import ipaddress
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from adlife.core.domain.person import contains_provider_secret_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from adlife.city.provider_profiles import ProviderProfileRevisionV1

DEFAULT_LOCAL_PROVIDER_APPROVAL_ID = "local-ollama"
DEFAULT_LOCAL_PROVIDER_BASE_URL = "http://127.0.0.1:11434/v1"

_PORTABLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_HOST_NAME = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_LOOPBACK_LITERALS = frozenset({"127.0.0.1", "::1"})


class ProviderRuntimeConfigurationError(ValueError):
    """A value-free refusal of unsafe or ambiguous startup policy."""


class _Profile(Protocol):
    mode: str
    base_url: str
    origin_approval_id: str


@dataclass(frozen=True, slots=True)
class ProviderOriginApproval:
    """One exact normalized endpoint approval, safe to expose to the browser later."""

    approval_id: str
    base_url: str
    trust_class: Literal["local", "remote"]


@dataclass(frozen=True, slots=True)
class ProviderEnvironmentBinding:
    """The name, never the value, of a remote origin's allowed environment source."""

    origin_approval_id: str
    environment_variable: str


@dataclass(frozen=True, slots=True)
class ProviderRuntimePolicy:
    """Complete process-start provider allowlist and explicit environment bindings."""

    origins: tuple[ProviderOriginApproval, ...]
    key_environments: tuple[ProviderEnvironmentBinding, ...]

    def approval(self, approval_id: str) -> ProviderOriginApproval:
        for approval in self.origins:
            if approval.approval_id == approval_id:
                return approval
        raise ProviderRuntimeConfigurationError(
            "provider profile does not match its approved origin"
        ) from None

    def validate_profile(
        self, profile: ProviderProfileRevisionV1 | _Profile
    ) -> ProviderOriginApproval:
        """Require the profile's trust class and normalized base path to match exactly."""
        approval: ProviderOriginApproval | None = None
        with suppress(AttributeError, ProviderRuntimeConfigurationError):
            approval = self.approval(profile.origin_approval_id)
        if approval is None:
            raise ProviderRuntimeConfigurationError(
                "provider profile does not match its approved origin"
            ) from None
        if profile.mode != approval.trust_class or profile.base_url != approval.base_url:
            raise ProviderRuntimeConfigurationError(
                "provider profile does not match its approved origin"
            ) from None
        return approval

    def environment_variable_for(self, profile: ProviderProfileRevisionV1 | _Profile) -> str | None:
        approval = self.validate_profile(profile)
        if approval.trust_class != "remote":
            return None
        for binding in self.key_environments:
            if binding.origin_approval_id == approval.approval_id:
                return binding.environment_variable
        return None


def _split_binding(raw: str, *, kind: Literal["origin", "environment"]) -> tuple[str, str]:
    message = f"provider {kind} configuration is invalid"
    if not isinstance(raw, str) or raw.count("=") != 1:
        raise ProviderRuntimeConfigurationError(message) from None
    identifier, value = raw.split("=", maxsplit=1)
    if (
        _PORTABLE_ID.fullmatch(identifier) is None
        or contains_provider_secret_text(identifier)
        or not value
    ):
        raise ProviderRuntimeConfigurationError(message) from None
    return identifier, value


def _validated_host(parsed: SplitResult) -> str:
    host: str | None = None
    port_is_valid = True
    try:
        host = parsed.hostname
        _port = parsed.port
    except ValueError:
        port_is_valid = False
    if not port_is_valid:
        raise ProviderRuntimeConfigurationError(
            "provider origin configuration is invalid"
        ) from None
    if host is None:
        raise ProviderRuntimeConfigurationError(
            "provider origin configuration is invalid"
        ) from None
    if not host.isascii() or _HOST_NAME.fullmatch(host) is None:
        is_ip_literal = True
        try:
            ipaddress.ip_address(host)
        except ValueError:
            is_ip_literal = False
        if not is_ip_literal:
            raise ProviderRuntimeConfigurationError(
                "provider origin configuration is invalid"
            ) from None
    return host.lower()


def normalize_provider_origin(raw: str) -> tuple[str, Literal["local", "remote"]]:
    """Return one canonical provider endpoint and its structural trust class."""
    message = "provider origin configuration is invalid"
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw.encode("utf-8")) > 2_048
        or not raw.isascii()
        or any(ord(character) < 33 or ord(character) == 127 for character in raw)
        or "\\" in raw
        or "?" in raw
        or "#" in raw
        or contains_provider_secret_text(raw)
    ):
        raise ProviderRuntimeConfigurationError(message) from None
    parsed: SplitResult | None = None
    with suppress(ValueError):
        parsed = urlsplit(raw)
    if parsed is None:
        raise ProviderRuntimeConfigurationError(message) from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.netloc.rsplit("@", maxsplit=1)[-1].endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderRuntimeConfigurationError(message) from None

    host = _validated_host(parsed)
    if host == "localhost" or host.endswith(".localhost"):
        raise ProviderRuntimeConfigurationError(message) from None
    if parsed.port == 0:
        raise ProviderRuntimeConfigurationError(message) from None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    is_local = host in _LOOPBACK_LITERALS
    if address is not None and not is_local:
        raise ProviderRuntimeConfigurationError(message) from None
    scheme = parsed.scheme.lower()
    if not is_local and scheme != "https":
        raise ProviderRuntimeConfigurationError(message) from None

    path = parsed.path or ""
    segments = path.split("/")
    if "%" in path or "//" in path or any(segment in {".", ".."} for segment in segments):
        raise ProviderRuntimeConfigurationError(message) from None
    path = path.rstrip("/")

    port = parsed.port
    default_port = 80 if scheme == "http" else 443
    explicit_port = None if port in {None, default_port} else port
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if explicit_port is None else f"{rendered_host}:{explicit_port}"
    normalized = urlunsplit((scheme, netloc, path, "", ""))
    trust_class: Literal["local", "remote"] = "local" if is_local else "remote"
    return normalized, trust_class


def build_provider_runtime_policy(
    *,
    provider_origins: Sequence[str] = (),
    provider_key_envs: Sequence[str] = (),
) -> ProviderRuntimePolicy:
    """Parse all startup bindings atomically and return their deterministic policy."""
    approvals: dict[str, ProviderOriginApproval] = {
        DEFAULT_LOCAL_PROVIDER_APPROVAL_ID: ProviderOriginApproval(
            approval_id=DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
            base_url=DEFAULT_LOCAL_PROVIDER_BASE_URL,
            trust_class="local",
        )
    }
    for raw in provider_origins:
        approval_id, raw_url = _split_binding(raw, kind="origin")
        if approval_id in approvals:
            raise ProviderRuntimeConfigurationError(
                "provider origin configuration is invalid"
            ) from None
        base_url, trust_class = normalize_provider_origin(raw_url)
        approvals[approval_id] = ProviderOriginApproval(
            approval_id=approval_id,
            base_url=base_url,
            trust_class=trust_class,
        )

    environment_bindings: dict[str, ProviderEnvironmentBinding] = {}
    for raw in provider_key_envs:
        approval_id, environment_variable = _split_binding(raw, kind="environment")
        approval = approvals.get(approval_id)
        if (
            approval is None
            or approval.trust_class != "remote"
            or _ENVIRONMENT_NAME.fullmatch(environment_variable) is None
            or approval_id in environment_bindings
            or any(
                item.environment_variable == environment_variable
                for item in environment_bindings.values()
            )
        ):
            raise ProviderRuntimeConfigurationError(
                "provider environment configuration is invalid"
            ) from None
        environment_bindings[approval_id] = ProviderEnvironmentBinding(
            origin_approval_id=approval_id,
            environment_variable=environment_variable,
        )

    return ProviderRuntimePolicy(
        origins=tuple(approvals[key] for key in sorted(approvals)),
        key_environments=tuple(environment_bindings[key] for key in sorted(environment_bindings)),
    )


__all__ = [
    "DEFAULT_LOCAL_PROVIDER_APPROVAL_ID",
    "DEFAULT_LOCAL_PROVIDER_BASE_URL",
    "ProviderEnvironmentBinding",
    "ProviderOriginApproval",
    "ProviderRuntimeConfigurationError",
    "ProviderRuntimePolicy",
    "build_provider_runtime_policy",
    "normalize_provider_origin",
]
