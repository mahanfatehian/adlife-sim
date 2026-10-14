"""Fail-closed endpoint binding and per-attempt DNS policy for city cognition.

This module deliberately stops at the socket boundary.  It turns one immutable,
startup-approved provider profile into the exact request authority and, for each remote
attempt, one freshly resolved numeric address.  The HTTP adapter remains responsible for
connecting to that numeric address while retaining :attr:`host_header` and
:attr:`tls_server_hostname` for HTTP and TLS authentication.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from adlife.city.provider_profiles import ProviderProfileRevisionV1
from adlife.city.provider_runtime import (
    DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
    ProviderEnvironmentBinding,
    ProviderOriginApproval,
    ProviderRuntimePolicy,
    build_provider_runtime_policy,
)
from adlife.core.domain.identifiers import WINDOWS_RESERVED_RUN_IDS
from adlife.core.domain.person import contains_provider_secret_text

MAX_CITY_ENDPOINT_ADDRESSES = 64
_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
_LOOPBACK_LITERALS = frozenset({"127.0.0.1", "::1"})
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_REVISION_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOCATED_GLOBAL_UNICAST_IPV6 = ipaddress.IPv6Network("2000::/3")

# ``ipaddress.is_global`` is intentionally the first gate, but its classification has
# changed as Python has incorporated newer IANA registries.  These explicit networks
# keep the deny side stable on every supported Python and also reject globally reachable
# special-purpose allocations: this feature accepts ordinary public unicast, not merely
# an address for which IANA happens to set the "globally reachable" column.
_SPECIAL_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.31.196.0/24",
        "192.52.193.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "192.175.48.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)
_SPECIAL_IPV6_NETWORKS = tuple(
    ipaddress.IPv6Network(value)
    for value in (
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "100:0:0:1::/64",
        "2001::/23",
        "2001:db8::/32",
        "2002::/16",
        "2620:4f:8000::/48",
        "3fff::/20",
        "5f00::/16",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)


class CityEndpointBindingError(ValueError):
    """The immutable profile did not match a startup-approved exact origin."""

    def __init__(self) -> None:
        super().__init__("provider endpoint binding was refused")


class CityEndpointResolutionError(ConnectionError):
    """A remote hostname did not produce one wholly public bounded answer set."""

    def __init__(self) -> None:
        super().__init__("provider endpoint resolution was refused")


class CityEndpointPeerMismatch(ConnectionError):
    """The connected peer was not the deterministic address selected for this attempt."""

    def __init__(self) -> None:
        super().__init__("provider endpoint peer verification was refused")


class CityAddressResolver(Protocol):
    """One injected, fresh DNS lookup boundary."""

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class SystemCityAddressResolver:
    """Resolve stream addresses using the running event loop for every call."""

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        answers = await loop.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        return tuple(str(answer[4][0]) for answer in answers)


@dataclass(frozen=True, slots=True)
class BoundProviderEndpoint:
    """One exact approved non-secret endpoint, frozen before any provider attempt."""

    profile_id: str
    profile_revision_sha256: str
    origin_approval_id: str
    trust_class: Literal["local", "remote"]
    scheme: Literal["http", "https"]
    hostname: str
    port: int
    base_path: str
    base_url: str
    host_header: str
    tls_server_hostname: str | None

    @property
    def chat_completions_target(self) -> str:
        """The only request target supported by the city provider protocol."""
        return self.base_path + _CHAT_COMPLETIONS_SUFFIX


@dataclass(frozen=True, slots=True)
class ResolvedProviderEndpoint:
    """A bounded DNS snapshot and its deterministically selected connection address."""

    endpoint: BoundProviderEndpoint
    addresses: tuple[str, ...]
    selected_address: str

    def verify_peer_address(self, peer_address: str) -> None:
        """Require the socket peer to be exactly the address selected for this attempt."""
        peer: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        try:
            if type(peer_address) is str and "%" not in peer_address:
                peer = ipaddress.ip_address(peer_address)
        except ValueError:
            peer = None
        if peer is None or str(peer) != self.selected_address:
            raise CityEndpointPeerMismatch() from None


def _checked_runtime_policy(policy: object) -> ProviderRuntimePolicy:
    """Rebuild the public policy so manually constructed dataclasses fail closed."""
    checked: ProviderRuntimePolicy | None = None
    try:
        if (
            type(policy) is ProviderRuntimePolicy
            and type(policy.origins) is tuple
            and type(policy.key_environments) is tuple
            and all(
                type(approval) is ProviderOriginApproval
                and type(approval.approval_id) is str
                and type(approval.base_url) is str
                and type(approval.trust_class) is str
                for approval in policy.origins
            )
            and all(
                type(binding) is ProviderEnvironmentBinding
                and type(binding.origin_approval_id) is str
                and type(binding.environment_variable) is str
                for binding in policy.key_environments
            )
        ):
            remote_and_extra_local = tuple(
                f"{approval.approval_id}={approval.base_url}"
                for approval in policy.origins
                if approval.approval_id != DEFAULT_LOCAL_PROVIDER_APPROVAL_ID
            )
            environments = tuple(
                f"{binding.origin_approval_id}={binding.environment_variable}"
                for binding in policy.key_environments
            )
            rebuilt = build_provider_runtime_policy(
                provider_origins=remote_and_extra_local,
                provider_key_envs=environments,
            )
            if rebuilt == policy:
                checked = rebuilt
    except (AttributeError, TypeError, ValueError):
        checked = None
    if checked is None:
        raise CityEndpointBindingError() from None
    return checked


def _validated_profile(profile: object) -> ProviderProfileRevisionV1:
    checked: ProviderProfileRevisionV1 | None = None
    try:
        if type(profile) is ProviderProfileRevisionV1:
            checked = ProviderProfileRevisionV1.model_validate(
                profile.model_dump(mode="json", warnings="none")
            )
    except (AttributeError, TypeError, ValueError, ValidationError):
        checked = None
    if checked is None:
        raise CityEndpointBindingError() from None
    return checked


def _is_public_unicast(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return not any(address in network for network in _SPECIAL_IPV4_NETWORKS)
    if address not in _ALLOCATED_GLOBAL_UNICAST_IPV6:
        return False
    if address.ipv4_mapped is not None:
        return False
    return not any(address in network for network in _SPECIAL_IPV6_NETWORKS)


def _canonical_public_answers(raw: object) -> tuple[str, ...] | None:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return None
    try:
        count = len(raw)
    except (OverflowError, TypeError, ValueError):
        return None
    if not 1 <= count <= MAX_CITY_ENDPOINT_ADDRESSES:
        return None

    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        for value in raw:
            if type(value) is not str or not value or "%" in value:
                return None
            address = ipaddress.ip_address(value)
            if not _is_public_unicast(address):
                return None
            parsed.append(address)
    except (TypeError, ValueError):
        return None
    unique = sorted(set(parsed), key=lambda address: (address.version, int(address)))
    return tuple(str(address) for address in unique)


class CityEndpointPolicy:
    """Bind approved profiles and produce one fail-closed DNS snapshot per attempt."""

    __slots__ = ("_resolver", "_runtime_policy")

    _resolver: CityAddressResolver
    _runtime_policy: ProviderRuntimePolicy

    def __init__(
        self,
        runtime_policy: ProviderRuntimePolicy,
        *,
        resolver: CityAddressResolver | None = None,
    ) -> None:
        self._runtime_policy = _checked_runtime_policy(runtime_policy)
        candidate: object = SystemCityAddressResolver() if resolver is None else resolver
        if not callable(getattr(candidate, "resolve", None)):
            raise CityEndpointBindingError() from None
        self._resolver = cast("CityAddressResolver", candidate)

    def bind(self, profile: ProviderProfileRevisionV1) -> BoundProviderEndpoint:
        """Revalidate and bind one profile to its exact startup-approved origin."""
        checked = _validated_profile(profile)
        approval = None
        try:
            approval = self._runtime_policy.validate_profile(checked)
            parsed = urlsplit(approval.base_url)
            hostname = parsed.hostname
            port = parsed.port
            if hostname is None:
                approval = None
            else:
                scheme = cast("Literal['http', 'https']", parsed.scheme)
                port = (80 if scheme == "http" else 443) if port is None else port
                is_local = approval.trust_class == "local"
                if (
                    scheme not in {"http", "https"}
                    or (is_local and hostname not in _LOOPBACK_LITERALS)
                    or (not is_local and scheme != "https")
                ):
                    approval = None
        except (AttributeError, TypeError, ValueError):
            approval = None
        if approval is None or hostname is None or port is None:
            raise CityEndpointBindingError() from None

        rendered_host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 80 if scheme == "http" else 443
        host_header = rendered_host if port == default_port else f"{rendered_host}:{port}"
        return BoundProviderEndpoint(
            profile_id=checked.profile_id,
            profile_revision_sha256=checked.revision_sha256,
            origin_approval_id=approval.approval_id,
            trust_class=approval.trust_class,
            scheme=scheme,
            hostname=hostname,
            port=port,
            base_path=parsed.path,
            base_url=approval.base_url,
            host_header=host_header,
            tls_server_hostname=hostname if scheme == "https" else None,
        )

    async def resolve_attempt(
        self,
        endpoint: BoundProviderEndpoint,
    ) -> ResolvedProviderEndpoint:
        """Resolve remote DNS afresh and select one canonical public address."""
        checked = self._checked_bound(endpoint)
        if checked.trust_class == "local":
            return ResolvedProviderEndpoint(
                endpoint=checked,
                addresses=(checked.hostname,),
                selected_address=checked.hostname,
            )

        raw: object | None = None
        failed = False
        try:
            raw = await self._resolver.resolve(checked.hostname, checked.port)
        except OSError:  # resolver diagnostics can contain local paths or DNS payloads
            failed = True
        if failed:
            raise CityEndpointResolutionError() from None
        answers = _canonical_public_answers(raw)
        if answers is None:
            raise CityEndpointResolutionError() from None
        return ResolvedProviderEndpoint(
            endpoint=checked,
            addresses=answers,
            selected_address=answers[0],
        )

    def _checked_bound(self, endpoint: object) -> BoundProviderEndpoint:
        valid = False
        try:
            if (
                type(endpoint) is BoundProviderEndpoint
                and type(endpoint.profile_id) is str
                and type(endpoint.profile_revision_sha256) is str
                and type(endpoint.origin_approval_id) is str
                and type(endpoint.trust_class) is str
                and type(endpoint.scheme) is str
                and type(endpoint.hostname) is str
                and type(endpoint.port) is int
                and type(endpoint.base_path) is str
                and type(endpoint.base_url) is str
                and type(endpoint.host_header) is str
                and (
                    endpoint.tls_server_hostname is None
                    or type(endpoint.tls_server_hostname) is str
                )
            ):
                approval = self._runtime_policy.approval(endpoint.origin_approval_id)
                parsed = urlsplit(approval.base_url)
                expected_port = parsed.port
                if expected_port is None:
                    expected_port = 80 if parsed.scheme == "http" else 443
                rendered_host = (
                    f"[{parsed.hostname}]"
                    if parsed.hostname and ":" in parsed.hostname
                    else parsed.hostname
                )
                default_port = 80 if parsed.scheme == "http" else 443
                expected_host_header = (
                    rendered_host
                    if expected_port == default_port
                    else f"{rendered_host}:{expected_port}"
                )
                valid = (
                    _PROFILE_ID.fullmatch(endpoint.profile_id) is not None
                    and endpoint.profile_id not in WINDOWS_RESERVED_RUN_IDS
                    and not contains_provider_secret_text(endpoint.profile_id)
                    and _REVISION_SHA256.fullmatch(endpoint.profile_revision_sha256) is not None
                    and endpoint.base_url == approval.base_url
                    and endpoint.trust_class == approval.trust_class
                    and endpoint.scheme == parsed.scheme
                    and endpoint.hostname == parsed.hostname
                    and endpoint.port == expected_port
                    and endpoint.base_path == parsed.path
                    and endpoint.host_header == expected_host_header
                    and endpoint.tls_server_hostname
                    == (parsed.hostname if parsed.scheme == "https" else None)
                    and endpoint.chat_completions_target == parsed.path + _CHAT_COMPLETIONS_SUFFIX
                )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise CityEndpointBindingError() from None
        return cast("BoundProviderEndpoint", endpoint)


__all__ = [
    "MAX_CITY_ENDPOINT_ADDRESSES",
    "BoundProviderEndpoint",
    "CityAddressResolver",
    "CityEndpointBindingError",
    "CityEndpointPeerMismatch",
    "CityEndpointPolicy",
    "CityEndpointResolutionError",
    "ResolvedProviderEndpoint",
    "SystemCityAddressResolver",
]
