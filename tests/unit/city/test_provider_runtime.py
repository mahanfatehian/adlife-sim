from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from adlife.city.provider_profiles import ProviderProfileDraftV1, ProviderProfileRevisionV1
from adlife.city.provider_runtime import (
    DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
    DEFAULT_LOCAL_PROVIDER_BASE_URL,
    MAX_PROVIDER_ORIGINS,
    ProviderOriginLimitReached,
    ProviderOriginRegistry,
    ProviderRuntimeConfigurationError,
    ProviderRuntimePolicy,
    build_provider_runtime_policy,
    current_provider_policy,
)
from adlife.core.domain.city_cognition import (
    canonical_city_cognition_provider_profile_payload,
    city_cognition_provider_profile_revision_sha256,
)


def _profile(**updates: object) -> ProviderProfileRevisionV1:
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "local-ollama",
        "display_label": "Local Ollama",
        "mode": "local",
        "base_url": DEFAULT_LOCAL_PROVIDER_BASE_URL,
        "model_id": "qwen2.5:7b",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
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


def test_default_policy_approves_only_the_fixed_loopback_origin() -> None:
    policy = build_provider_runtime_policy()

    assert tuple(
        (item.approval_id, item.base_url, item.trust_class) for item in policy.origins
    ) == (
        (
            DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
            DEFAULT_LOCAL_PROVIDER_BASE_URL,
            "local",
        ),
    )
    assert policy.key_environments == ()
    assert policy.approval(DEFAULT_LOCAL_PROVIDER_APPROVAL_ID).base_url == (
        DEFAULT_LOCAL_PROVIDER_BASE_URL
    )
    with pytest.raises(FrozenInstanceError):
        policy.__setattr__("origins", ())


def test_policy_normalizes_repeatable_local_and_remote_origins() -> None:
    policy = build_provider_runtime_policy(
        provider_origins=(
            "local-ipv6=http://[::1]:11434/v1/",
            "remote-acme=HTTPS://API.EXAMPLE.INVALID:443/openai/v1/",
        ),
        provider_key_envs=("remote-acme=ACME_API_KEY",),
    )

    assert tuple(
        (item.approval_id, item.base_url, item.trust_class) for item in policy.origins
    ) == (
        ("local-ipv6", "http://[::1]:11434/v1", "local"),
        ("local-ollama", "http://127.0.0.1:11434/v1", "local"),
        ("remote-acme", "https://api.example.invalid/openai/v1", "remote"),
    )
    assert tuple(
        (item.origin_approval_id, item.environment_variable) for item in policy.key_environments
    ) == (("remote-acme", "ACME_API_KEY"),)


@pytest.mark.parametrize(
    "binding",
    [
        "missing-separator",
        "=https://api.example.invalid/v1",
        "UPPER=https://api.example.invalid/v1",
        "sk-test-abcdefghijklmnopqrst=https://api.example.invalid/v1",
        "remote=http://api.example.invalid/v1",
        "remote=https://localhost/v1",
        "remote=https://127.0.0.2/v1",
        "remote=https://10.0.0.1/v1",
        "remote=https://user:password@example.invalid/v1",
        "remote=https://example.invalid/v1?x=1",
        "remote=https://example.invalid/v1#fragment",
        "remote=https://example.invalid/v1//chat",
        "remote=https://example.invalid/v1/../admin",
        "remote=https://example.invalid/sk-live-AAAABBBBCCCCDDDD",
        "remote=https://example.invalid:0/v1",
        "remote=https://example.invalid:/v1",
        "remote=https://example.invalid:not-a-port/v1",
        "remote=https://bad_host.example.invalid/v1",
        "remote=https://[unterminated/v1",
        "local=http://localhost:11434/v1",
        "local=http://127.0.0.1:/v1",
        "local=http://127.0.0.1:11434/v1?",
        "local=file:///tmp/provider",
    ],
)
def test_policy_refuses_ambiguous_or_unsafe_origin_bindings_without_echo(
    binding: str,
) -> None:
    with pytest.raises(ProviderRuntimeConfigurationError) as caught:
        build_provider_runtime_policy(provider_origins=(binding,))

    assert str(caught.value) == "provider origin configuration is invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "password" not in str(caught.value)
    assert "AAAABBBB" not in str(caught.value)


@pytest.mark.parametrize(
    "binding",
    [
        "missing-separator",
        "remote=",
        "remote=lowercase_key",
        "remote=1INVALID",
        "remote=API KEY",
        "unknown=UNKNOWN_API_KEY",
        "local-ollama=LOCAL_KEY_MUST_NOT_BE_READ",
    ],
)
def test_policy_refuses_invalid_or_non_remote_environment_bindings(binding: str) -> None:
    origins = ("remote=https://api.example.invalid/v1",)

    with pytest.raises(ProviderRuntimeConfigurationError, match="environment") as caught:
        build_provider_runtime_policy(
            provider_origins=origins,
            provider_key_envs=(binding,),
        )

    assert caught.value.__cause__ is None


def test_policy_refuses_duplicate_approval_and_environment_ids() -> None:
    with pytest.raises(ProviderRuntimeConfigurationError, match="origin"):
        build_provider_runtime_policy(
            provider_origins=(
                "remote=https://one.example.invalid/v1",
                "remote=https://two.example.invalid/v1",
            )
        )
    with pytest.raises(ProviderRuntimeConfigurationError, match="environment"):
        build_provider_runtime_policy(
            provider_origins=("remote=https://api.example.invalid/v1",),
            provider_key_envs=("remote=ONE_API_KEY", "remote=TWO_API_KEY"),
        )


def test_one_environment_variable_cannot_be_reused_for_two_remote_origins() -> None:
    with pytest.raises(ProviderRuntimeConfigurationError, match="environment"):
        build_provider_runtime_policy(
            provider_origins=(
                "one=https://one.example.invalid/v1",
                "two=https://two.example.invalid/v1",
            ),
            provider_key_envs=("one=SHARED_API_KEY", "two=SHARED_API_KEY"),
        )


def test_profile_must_match_exact_approved_trust_class_origin_and_base_path() -> None:
    policy = build_provider_runtime_policy(
        provider_origins=("remote=https://api.example.invalid/openai/v1",),
        provider_key_envs=("remote=REMOTE_API_KEY",),
    )
    matching = _profile(
        profile_id="remote",
        display_label="Remote",
        mode="remote",
        base_url="https://api.example.invalid/openai/v1",
        origin_approval_id="remote",
    )

    assert policy.validate_profile(matching).approval_id == "remote"
    assert policy.environment_variable_for(matching) == "REMOTE_API_KEY"

    def bypassed_profile(**updates: object) -> ProviderProfileRevisionV1:
        document = matching.model_dump(mode="python")
        document.update(updates)
        return ProviderProfileRevisionV1.model_construct(**document)

    for mismatch in (
        bypassed_profile(
            base_url="https://api.example.invalid/v1",
        ),
        bypassed_profile(
            mode="local",
        ),
        bypassed_profile(
            origin_approval_id="local-ollama",
        ),
    ):
        with pytest.raises(ProviderRuntimeConfigurationError) as caught:
            policy.validate_profile(mismatch)
        assert str(caught.value) == "provider profile does not match its approved origin"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_policy_representation_contains_no_environment_value() -> None:
    policy = build_provider_runtime_policy(
        provider_origins=("remote=https://api.example.invalid/v1",),
        provider_key_envs=("remote=REMOTE_API_KEY",),
    )

    rendered = repr(policy)
    assert "REMOTE_API_KEY" in rendered
    assert "credential" not in rendered.lower()
    assert "secret-value" not in rendered


# --- origins the operator adds while the server runs --------------------------------------


@pytest.mark.parametrize(
    ("typed", "base_url", "trust_class", "prefix"),
    [
        ("openrouter.ai/api/v1", "https://openrouter.ai/api/v1", "remote", "openrouter-ai-"),
        (
            "https://openrouter.ai/api/v1/",
            "https://openrouter.ai/api/v1",
            "remote",
            "openrouter-ai-",
        ),
        ("  api.example.com/v1  ", "https://api.example.com/v1", "remote", "example-com-"),
        ("HTTPS://API.Example.COM:443/v1", "https://api.example.com/v1", "remote", "example-com-"),
        ("127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1", "local", "local-1234-"),
        ("localhost:1234/v1", "http://127.0.0.1:1234/v1", "local", "local-1234-"),
        ("http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "local", "local-1234-"),
        ("[::1]:8080/v1", "http://[::1]:8080/v1", "local", "local-8080-"),
    ],
)
def test_registry_approves_an_operator_typed_address_with_the_startup_rules(
    typed: str, base_url: str, trust_class: str, prefix: str
) -> None:
    registry = ProviderOriginRegistry(refused_local_ports=(50050,))
    before = registry.policy

    approval = registry.approve(typed)

    assert (approval.base_url, approval.trust_class) == (base_url, trust_class)
    assert approval.approval_id.startswith(prefix)
    assert len(approval.approval_id) <= 64
    # A new frozen policy replaced the old one, which is left exactly as it was.
    assert registry.policy is not before
    assert type(registry.policy) is ProviderRuntimePolicy
    assert registry.policy.approval(approval.approval_id) == approval
    assert approval not in before.origins
    # The same address is the same approval, also after a restart (a new registry).
    assert registry.approve(typed) == approval
    assert ProviderOriginRegistry().approve(base_url) == approval


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "http://api.example.com/v1",
        "10.0.0.1/v1",
        "https://192.168.1.20/v1",
        "127.0.0.2:1234/v1",
        "user:pass@api.example.com/v1",
        "api.example.com/v1?key=1",
        "api.example.com/v1#frag",
        "api.example.com/v1/../admin",
        "ftp://api.example.com/v1",
        "api.example.com/v1/sk-0123456789abcdefghijklmnopqrstuv",
        "127.0.0.1:50050/v1",
        "localhost:50050",
        "ex\u00e4mple.com/v1",
    ],
)
def test_registry_refuses_unsafe_addresses_without_echoing_them(typed: str) -> None:
    registry = ProviderOriginRegistry(refused_local_ports=(50050,))
    before = registry.policy

    with pytest.raises(ProviderRuntimeConfigurationError) as caught:
        registry.approve(typed)

    assert typed.strip() == "" or typed.strip() not in str(caught.value)
    assert registry.policy is before


def test_registry_keeps_startup_approvals_and_key_bindings() -> None:
    startup = build_provider_runtime_policy(
        provider_origins=("gateway=https://gateway.example.com/v1",),
        provider_key_envs=("gateway=GATEWAY_API_KEY",),
    )
    registry = ProviderOriginRegistry.from_policy(startup)
    assert registry.policy == startup

    added = registry.approve("openrouter.ai/api/v1")

    assert registry.policy.approval("gateway") == startup.approval("gateway")
    assert registry.policy.key_environments == startup.key_environments
    assert registry.policy.approval(DEFAULT_LOCAL_PROVIDER_APPROVAL_ID).base_url == (
        DEFAULT_LOCAL_PROVIDER_BASE_URL
    )
    assert {origin.approval_id for origin in registry.policy.origins} == {
        "gateway",
        DEFAULT_LOCAL_PROVIDER_APPROVAL_ID,
        added.approval_id,
    }
    # The startup address typed again is its startup approval, not a second one.
    assert registry.approve("gateway.example.com/v1").approval_id == "gateway"


def test_registry_stops_growing_at_its_origin_limit() -> None:
    registry = ProviderOriginRegistry()
    for index in range(MAX_PROVIDER_ORIGINS - 1):
        registry.approve(f"service-{index}.example.com/v1")
    assert len(registry.policy.origins) == MAX_PROVIDER_ORIGINS
    full = registry.policy

    with pytest.raises(ProviderOriginLimitReached):
        registry.approve("one-more.example.com/v1")

    assert registry.policy is full
    # An address it already holds is still answered.
    assert registry.approve("service-0.example.com/v1").base_url == (
        "https://service-0.example.com/v1"
    )


def test_current_provider_policy_reads_a_fixed_policy_or_a_live_registry() -> None:
    fixed = build_provider_runtime_policy()
    registry = ProviderOriginRegistry()
    assert current_provider_policy(fixed) is fixed
    assert current_provider_policy(registry) is registry.policy
    registry.approve("openrouter.ai/api/v1")
    assert current_provider_policy(registry) is registry.policy
    with pytest.raises(ProviderRuntimeConfigurationError):
        ProviderOriginRegistry.from_policy(object())  # type: ignore[arg-type]
