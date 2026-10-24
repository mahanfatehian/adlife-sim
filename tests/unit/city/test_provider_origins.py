"""Provider addresses the operator connects from the guided pages, and keys held in memory.

The guided pages let anyone point AdLife at an OpenAI-compatible service by typing its base
URL: an online service (https) or a server on this computer (a loopback literal on any
port). The server approves such an address for its own process only, through the same
rules as ``--provider-origin``, and holds a typed key in memory unless the operator asks to
remember it in the OS keyring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adlife.adapters.cognition.city_credentials import OSKeyringCityCredentialStore, SecretBuffer
from adlife.city.provider_profiles import ProviderProfileRegistry
from adlife.city.provider_runtime import (
    MAX_PROVIDER_ORIGINS,
    ProviderOriginRegistry,
    build_provider_runtime_policy,
)
from adlife.city.workbench_providers import (
    InvalidProviderOrigin,
    WorkbenchOperationState,
    WorkbenchProviderController,
    WorkbenchProviderOriginLimit,
)
from adlife.city.workbench_transport import (
    WorkbenchCredentialRequestError,
    parse_workbench_credential_submission,
)
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.unit.city.test_provider_profiles import _draft

TYPED_KEY = "sk-or-test-0000000000000000000000"


class _Keyring:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        del service
        return self.values.get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        del service
        self.values[username] = password


def _controller(
    tmp_path: Path, *, live: bool = True
) -> tuple[WorkbenchProviderController, _Keyring, ProviderOriginRegistry | None]:
    registry = ProviderOriginRegistry(refused_local_ports=(50050,)) if live else None
    policy = registry if registry is not None else build_provider_runtime_policy()
    keyring = _Keyring()
    credentials = OSKeyringCityCredentialStore(
        policy, backend=keyring, approved_backend_types=(_Keyring,), environ={}
    )
    controller = WorkbenchProviderController(
        ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "workspace")),
        policy,
        credentials=credentials,
        operations=WorkbenchOperationState(),
    )
    return controller, keyring, registry


def _connect(
    controller: WorkbenchProviderController, address: str, *, model: str = "qwen/qwen3-8b"
):
    origin = controller.approve_origin(address)
    view = controller.create(
        _draft(
            profile_id=f"guide-{origin.approval_id}"[:64],
            display_label="Guided demo",
            mode=origin.trust_class,
            base_url=origin.base_url,
            origin_approval_id=origin.approval_id,
            model_id=model,
            max_output_tokens=256,
        )
    )
    return origin, view


def test_an_operator_typed_address_is_approved_listed_and_usable_for_a_profile(
    tmp_path: Path,
) -> None:
    controller, _, _ = _controller(tmp_path)

    origin, view = _connect(controller, "openrouter.ai/api/v1")

    assert origin.base_url == "https://openrouter.ai/api/v1"
    assert origin.trust_class == "remote"
    assert origin.environment_variable is None
    listed = controller.list()
    assert origin in listed.origins
    assert view.endpoint_policy_accepted is True
    assert view.credential_available is False
    # Approving the same address again is a no-op that returns the same approval.
    assert controller.approve_origin("https://openrouter.ai/api/v1/") == origin
    assert len(controller.list().origins) == len(listed.origins)


def test_a_server_on_this_computer_may_use_any_port_but_this_servers_own(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)

    origin, view = _connect(controller, "localhost:1234/v1", model="qwen2.5-7b-instruct")

    assert (origin.base_url, origin.trust_class) == ("http://127.0.0.1:1234/v1", "local")
    # A local server needs no key: the fixed loopback dummy is available at once.
    assert view.credential_available is True
    with pytest.raises(InvalidProviderOrigin):
        controller.approve_origin("127.0.0.1:50050/v1")


@pytest.mark.parametrize(
    "address",
    ["http://api.example.com/v1", "10.1.2.3/v1", "api.example.com/v1?x=1", "", 7, None],
)
def test_unsafe_or_malformed_addresses_are_refused_without_echo(
    tmp_path: Path, address: object
) -> None:
    controller, _, _ = _controller(tmp_path)
    before = controller.list().origins

    with pytest.raises(InvalidProviderOrigin) as caught:
        controller.approve_origin(address)

    assert str(caught.value) == "provider address is not allowed"
    assert controller.list().origins == before


def test_a_fixed_startup_policy_cannot_grow(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path, live=False)
    with pytest.raises(InvalidProviderOrigin):
        controller.approve_origin("openrouter.ai/api/v1")


def test_the_origin_limit_is_a_distinct_refusal(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    for index in range(MAX_PROVIDER_ORIGINS - 1):
        controller.approve_origin(f"service-{index}.example.com/v1")
    with pytest.raises(WorkbenchProviderOriginLimit):
        controller.approve_origin("one-more.example.com/v1")


def test_a_key_that_is_not_remembered_stays_in_memory_for_the_whole_origin(
    tmp_path: Path,
) -> None:
    controller, keyring, _ = _controller(tmp_path)
    origin, first = _connect(controller, "openrouter.ai/api/v1", model="qwen/qwen3-8b")
    _, second = _connect(controller, "openrouter.ai/api/v1", model="meta-llama/llama-3.1-8b")
    typed = SecretBuffer(TYPED_KEY)

    stored = controller.store_credential(
        first.profile_id, first.revision_sha256, typed, remember=False
    )

    assert typed.cleared
    assert stored.credential_available is True
    assert keyring.values == {}
    views = {view.model_id: view for view in controller.list().profiles}
    assert views["meta-llama/llama-3.1-8b"].credential_available is True
    assert second.origin_approval_id == origin.approval_id
    assert TYPED_KEY not in json.dumps(controller.list().model_dump(mode="json"))


def test_a_remembered_key_goes_to_the_approved_keyring_only(tmp_path: Path) -> None:
    controller, keyring, _ = _controller(tmp_path)
    _, view = _connect(controller, "openrouter.ai/api/v1")

    controller.store_credential(
        view.profile_id, view.revision_sha256, SecretBuffer(TYPED_KEY), remember=True
    )

    assert list(keyring.values.values()) == [TYPED_KEY]


@pytest.mark.parametrize(
    ("body", "remember"),
    [
        (b'{"credential":"sk-test-0000000000"}', False),
        (b'{"credential":"sk-test-0000000000","remember":false}', False),
        (b'{"credential":"sk-test-0000000000","remember":true}', True),
        (b'{"remember":true,"credential":"sk-test-0000000000"}', True),
    ],
)
def test_the_credential_submission_takes_an_optional_exact_remember_flag(
    body: bytes, remember: bool
) -> None:
    secret, flag = parse_workbench_credential_submission(body)
    assert flag is remember
    assert secret.reveal_text() == "sk-test-0000000000"
    secret.clear()


@pytest.mark.parametrize(
    "body",
    [
        b'{"credential":"sk-test-0000000000","remember":"yes"}',
        b'{"credential":"sk-test-0000000000","remember":1}',
        b'{"credential":"sk-test-0000000000","remember":null}',
        b'{"credential":"sk-test-0000000000","extra":true}',
        b'{"remember":true}',
        b'{"credential":"sk test with spaces","remember":true}',
        b'{"credential":"sk-test-0000000000","credential":"sk-test-1111111111"}',
        b'[{"credential":"sk-test-0000000000"}]',
    ],
)
def test_the_credential_submission_refuses_anything_else_without_echo(body: bytes) -> None:
    with pytest.raises(WorkbenchCredentialRequestError) as caught:
        parse_workbench_credential_submission(body)
    assert b"sk-test" not in str(caught.value).encode()
    assert caught.value.__context__ is None
