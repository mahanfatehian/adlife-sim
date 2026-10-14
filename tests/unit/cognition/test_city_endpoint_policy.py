from __future__ import annotations

import asyncio
import warnings
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from types import SimpleNamespace
from typing import cast

import pytest

from adlife.adapters.cognition.city_endpoint_policy import (
    MAX_CITY_ENDPOINT_ADDRESSES,
    BoundProviderEndpoint,
    CityEndpointBindingError,
    CityEndpointPeerMismatch,
    CityEndpointPolicy,
    CityEndpointResolutionError,
    ResolvedProviderEndpoint,
)
from adlife.city.provider_profiles import (
    ProviderProfileDraftV1,
    ProviderProfileRevisionV1,
)
from adlife.city.provider_runtime import build_provider_runtime_policy
from adlife.core.domain.city_cognition import (
    canonical_city_cognition_provider_profile_payload,
    city_cognition_provider_profile_revision_sha256,
)


def _profile(**updates: object) -> ProviderProfileRevisionV1:
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "remote-acme",
        "display_label": "Remote Acme",
        "mode": "remote",
        "base_url": "https://api.example.invalid/openai/v1",
        "model_id": "example-model",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "remote-acme",
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


def _policy(*, resolver: object | None = None) -> CityEndpointPolicy:
    runtime = build_provider_runtime_policy(
        provider_origins=(
            "remote-acme=https://api.example.invalid/openai/v1",
            "remote-alt=https://api.example.invalid:8443/alternate",
            "local-v6=http://[::1]:11434/v1",
        )
    )
    return CityEndpointPolicy(runtime, resolver=resolver)


class RecordingResolver:
    def __init__(self, *answers: tuple[str, ...]) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        return self._answers.pop(0)


def test_bind_freezes_the_exact_approved_origin_and_request_target() -> None:
    endpoint = _policy().bind(_profile())

    assert endpoint == BoundProviderEndpoint(
        profile_id="remote-acme",
        profile_revision_sha256=_profile().revision_sha256,
        origin_approval_id="remote-acme",
        trust_class="remote",
        scheme="https",
        hostname="api.example.invalid",
        port=443,
        base_path="/openai/v1",
        base_url="https://api.example.invalid/openai/v1",
        host_header="api.example.invalid",
        tls_server_hostname="api.example.invalid",
    )
    assert endpoint.chat_completions_target == "/openai/v1/chat/completions"
    with pytest.raises(FrozenInstanceError):
        endpoint.__setattr__("hostname", "attacker.invalid")


def test_policy_revalidation_does_not_format_forged_runtime_values() -> None:
    secret = "sk-live-do-not-format-runtime-policy"

    class NoisyText(str):
        def __format__(self, format_spec: str) -> str:
            warnings.warn(secret, UserWarning, stacklevel=2)
            return super().__format__(format_spec)

    runtime = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/openai/v1",)
    )
    forged_origins = tuple(
        replace(approval, base_url=NoisyText(approval.base_url))
        if approval.approval_id == "remote-acme"
        else approval
        for approval in runtime.origins
    )
    forged = replace(runtime, origins=forged_origins)

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(CityEndpointBindingError) as caught:
            CityEndpointPolicy(forged)

    assert caught_warnings == []
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


@pytest.mark.parametrize(
    ("profile", "expected_host", "expected_header", "expected_tls"),
    [
        (
            _profile(
                profile_id="local-ollama",
                display_label="Local Ollama",
                mode="local",
                base_url="http://127.0.0.1:11434/v1",
                origin_approval_id="local-ollama",
            ),
            "127.0.0.1",
            "127.0.0.1:11434",
            None,
        ),
        (
            _profile(
                profile_id="local-v6",
                display_label="Local IPv6",
                mode="local",
                base_url="http://[::1]:11434/v1",
                origin_approval_id="local-v6",
            ),
            "::1",
            "[::1]:11434",
            None,
        ),
        (
            _profile(
                profile_id="remote-alt",
                display_label="Remote Alternate",
                base_url="https://api.example.invalid:8443/alternate",
                origin_approval_id="remote-alt",
            ),
            "api.example.invalid",
            "api.example.invalid:8443",
            "api.example.invalid",
        ),
    ],
)
def test_bind_preserves_literal_loopback_and_the_original_tls_hostname(
    profile: ProviderProfileRevisionV1,
    expected_host: str,
    expected_header: str,
    expected_tls: str | None,
) -> None:
    endpoint = _policy().bind(profile)

    assert endpoint.hostname == expected_host
    assert endpoint.host_header == expected_header
    assert endpoint.tls_server_hostname == expected_tls


def test_bind_refuses_a_profile_that_is_not_the_exact_immutable_contract() -> None:
    values = _profile().model_dump(mode="python")
    disguised = SimpleNamespace(
        **values,
        proxy_url="http://127.0.0.1:8080",
        headers={"X-Forwarded-For": "127.0.0.1"},
        uds="/tmp/provider.sock",
        follow_redirects=True,
    )

    with pytest.raises(CityEndpointBindingError) as caught:
        _policy().bind(cast("ProviderProfileRevisionV1", disguised))

    assert str(caught.value) == "provider endpoint binding was refused"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "updates",
    [
        {"base_url": "https://api.example.invalid/other"},
        {"origin_approval_id": "remote-alt"},
        {"mode": "local"},
        {"revision_sha256": "0" * 64},
        {"credential_slot_id": "0" * 64},
    ],
)
def test_bind_revalidates_bypass_constructed_profiles_without_echo(
    updates: dict[str, object],
) -> None:
    facts = _profile().model_dump(mode="python")
    facts.update(updates)
    tampered = ProviderProfileRevisionV1.model_construct(**facts)

    with pytest.raises(CityEndpointBindingError) as caught:
        _policy().bind(tampered)

    assert str(caught.value) == "provider endpoint binding was refused"
    assert "api.example.invalid" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_bind_suppresses_serializer_warnings_for_bypass_constructed_values() -> None:
    secret = "sk-live-do-not-print-endpoint-secret"
    facts = _profile().model_dump(mode="python")
    facts["model_id"] = [secret]
    tampered = ProviderProfileRevisionV1.model_construct(**facts)

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(CityEndpointBindingError) as caught:
            _policy().bind(tampered)

    assert caught_warnings == []
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_local_literals_are_pinned_without_any_dns_lookup() -> None:
    resolver = RecordingResolver(("93.184.216.34",))
    policy = _policy(resolver=resolver)
    bound = policy.bind(
        _profile(
            profile_id="local-v6",
            display_label="Local IPv6",
            mode="local",
            base_url="http://[::1]:11434/v1",
            origin_approval_id="local-v6",
        )
    )

    resolved = await policy.resolve_attempt(bound)

    assert resolved.addresses == ("::1",)
    assert resolved.selected_address == "::1"
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", "sk-live-abcdefghijklmnopqrst"),
        ("profile_id", "../provider"),
        ("profile_id", "con"),
        ("profile_revision_sha256", "g" * 64),
        ("profile_revision_sha256", "A" * 64),
    ],
)
async def test_resolve_refuses_forged_profile_identity_metadata(
    field: str,
    value: str,
) -> None:
    resolver = RecordingResolver(("93.184.216.34",))
    policy = _policy(resolver=resolver)
    forged = replace(policy.bind(_profile()), **{field: value})

    with pytest.raises(CityEndpointBindingError) as caught:
        await policy.resolve_attempt(forged)

    assert str(caught.value) == "provider endpoint binding was refused"
    assert value not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert resolver.calls == []


async def test_resolve_refuses_value_object_forgery_before_local_connection() -> None:
    class DeceptiveText(str):
        def __eq__(self, other: object) -> bool:
            del other
            return True

        __hash__ = str.__hash__

    resolver = RecordingResolver(("93.184.216.34",))
    policy = _policy(resolver=resolver)
    local = policy.bind(
        _profile(
            profile_id="local-ollama",
            display_label="Local Ollama",
            mode="local",
            base_url="http://127.0.0.1:11434/v1",
            origin_approval_id="local-ollama",
        )
    )
    forged = replace(local, hostname=DeceptiveText("attacker.invalid"))

    with pytest.raises(CityEndpointBindingError) as caught:
        await policy.resolve_attempt(forged)

    assert str(caught.value) == "provider endpoint binding was refused"
    assert "attacker.invalid" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert resolver.calls == []


async def test_remote_dns_is_fresh_per_attempt_and_selection_is_order_invariant() -> None:
    resolver = RecordingResolver(
        ("2606:2800:220:1:248:1893:25c8:1946", "93.184.216.35", "93.184.216.34"),
        ("93.184.216.36",),
    )
    policy = _policy(resolver=resolver)
    bound = policy.bind(_profile())

    first = await policy.resolve_attempt(bound)
    second = await policy.resolve_attempt(bound)

    assert first.addresses == (
        "93.184.216.34",
        "93.184.216.35",
        "2606:2800:220:1:248:1893:25c8:1946",
    )
    assert first.selected_address == "93.184.216.34"
    assert second.addresses == ("93.184.216.36",)
    assert resolver.calls == [
        ("api.example.invalid", 443),
        ("api.example.invalid", 443),
    ]
    with pytest.raises(CityEndpointPeerMismatch):
        first.verify_peer_address(second.selected_address)


async def test_duplicate_dns_answers_are_canonicalized_before_selection() -> None:
    resolver = RecordingResolver(
        ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34")
    )

    resolved = await _policy(resolver=resolver).resolve_attempt(_policy().bind(_profile()))

    assert resolved.addresses == (
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    )


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "0.1.2.3",
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.2",
        "169.254.169.254",
        "172.16.0.1",
        "192.0.0.1",
        "192.0.0.9",
        "192.0.2.1",
        "192.31.196.1",
        "192.52.193.1",
        "192.88.99.1",
        "192.168.0.1",
        "192.175.48.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "::ffff:93.184.216.34",
        "64:ff9b::5db8:d822",
        "64:ff9b:1::5db8:d822",
        "100::1",
        "100:0:0:1::1",
        "1000::1",
        "2001::1",
        "2001:2::1",
        "2001:10::1",
        "2001:20::1",
        "2001:db8::1",
        "2002::1",
        "2620:4f:8000::1",
        "3fff::1",
        "4000::1",
        "5f00::1",
        "8000::1",
        "e000::1",
        "fc00::1",
        "fe80::1",
        "fec0::1",
        "ff00::1",
    ],
)
async def test_remote_dns_refuses_every_private_or_special_address(address: str) -> None:
    policy = _policy(resolver=RecordingResolver((address,)))

    with pytest.raises(CityEndpointResolutionError) as caught:
        await policy.resolve_attempt(policy.bind(_profile()))

    assert str(caught.value) == "provider endpoint resolution was refused"
    assert address not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_one_private_answer_refuses_the_whole_mixed_dns_set() -> None:
    policy = _policy(resolver=RecordingResolver(("93.184.216.34", "10.0.0.1")))

    with pytest.raises(CityEndpointResolutionError):
        await policy.resolve_attempt(policy.bind(_profile()))


@pytest.mark.parametrize(
    "answers",
    [
        (),
        ("not-an-address",),
        ("93.184.216.34%scope",),
        ("93.184.216.34", 7),
        tuple("93.184.216.34" for _ in range(MAX_CITY_ENDPOINT_ADDRESSES + 1)),
    ],
)
async def test_malformed_or_unbounded_dns_answers_fail_closed_without_echo(
    answers: tuple[object, ...],
) -> None:
    class MalformedResolver:
        async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
            del hostname, port
            return cast("tuple[str, ...]", answers)

    resolver = MalformedResolver()
    policy = _policy(resolver=resolver)

    with pytest.raises(CityEndpointResolutionError) as caught:
        await policy.resolve_attempt(policy.bind(_profile()))

    assert str(caught.value) == "provider endpoint resolution was refused"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_resolver_exception_is_translated_without_its_secret_or_path() -> None:
    class FailingResolver:
        async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
            del hostname, port
            raise OSError("credential=do-not-print C:/private/provider.sock")

    policy = _policy(resolver=FailingResolver())

    with pytest.raises(CityEndpointResolutionError) as caught:
        await policy.resolve_attempt(policy.bind(_profile()))

    assert str(caught.value) == "provider endpoint resolution was refused"
    assert "do-not-print" not in repr(caught.value)
    assert "private" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_unexpected_resolver_defect_is_not_disguised_as_provider_failure() -> None:
    defect = RuntimeError("resolver programming defect")

    class DefectiveResolver:
        async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
            del hostname, port
            raise defect

    policy = _policy(resolver=DefectiveResolver())

    with pytest.raises(RuntimeError) as caught:
        await policy.resolve_attempt(policy.bind(_profile()))

    assert caught.value is defect


async def test_resolution_cancellation_propagates_and_stops_the_await() -> None:
    class BlockingResolver:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
            del hostname, port
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True
            return ("93.184.216.34",)

    resolver = BlockingResolver()
    policy = _policy(resolver=resolver)
    task = asyncio.create_task(policy.resolve_attempt(policy.bind(_profile())))
    await resolver.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert resolver.cancelled is True


async def test_peer_verification_requires_the_exact_selected_numeric_address() -> None:
    policy = _policy(resolver=RecordingResolver(("93.184.216.35", "93.184.216.34")))
    resolved = await policy.resolve_attempt(policy.bind(_profile()))

    resolved.verify_peer_address("93.184.216.34")
    for wrong in ("93.184.216.35", "10.0.0.1", "not-an-address"):
        with pytest.raises(CityEndpointPeerMismatch) as caught:
            resolved.verify_peer_address(wrong)
        assert str(caught.value) == "provider endpoint peer verification was refused"
        assert wrong not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


async def test_resolved_endpoint_is_immutable_and_bound_to_the_approved_base_path() -> None:
    policy = _policy(resolver=RecordingResolver(("93.184.216.34",)))
    bound = policy.bind(_profile())

    resolved = await policy.resolve_attempt(bound)

    assert resolved == ResolvedProviderEndpoint(
        endpoint=bound,
        addresses=("93.184.216.34",),
        selected_address="93.184.216.34",
    )
    assert resolved.endpoint.base_path == "/openai/v1"
    assert resolved.endpoint.chat_completions_target == "/openai/v1/chat/completions"
    with pytest.raises(FrozenInstanceError):
        resolved.__setattr__("selected_address", "10.0.0.1")
