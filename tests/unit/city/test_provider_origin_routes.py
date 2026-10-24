"""The guided pages' routes for connecting any OpenAI-compatible service.

``POST /api/providers/origins`` approves one typed address for this server process, and
the credential route takes an optional ``remember`` flag: without it the key is held in
memory only. Both sit behind the same loopback, Origin and CSRF checks as every POST.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adlife.adapters.cognition.city_credentials import OSKeyringCityCredentialStore
from adlife.city.provider_runtime import ProviderOriginRegistry
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.unit.city.test_provider_profiles import _draft

TOKEN = "provider-origin-route-token"
ORIGIN = "http://127.0.0.1:8765"
TYPED_KEY = "sk-or-route-test-000000000000000000"


def _post(client: TestClient, path: str, payload: object, *, csrf: bool = True):
    headers = {"Content-Type": "application/json", "Origin": ORIGIN}
    if csrf:
        headers["X-AdLife-CSRF"] = TOKEN
    return client.post(path, headers=headers, content=json.dumps(payload).encode())


def _app(tmp_path: Path, **kwargs: Any):
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    return create_city_workbench_app(workspace, csrf_token=TOKEN, **kwargs)


def test_a_typed_address_is_approved_for_this_process_and_listed(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path), base_url=ORIGIN) as client:
        approved = _post(client, "/api/providers/origins", {"base_url": "openrouter.ai/api/v1"})
        again = _post(
            client, "/api/providers/origins", {"base_url": "https://openrouter.ai/api/v1/"}
        )
        listed = client.get("/api/providers").json()["origins"]

    assert approved.status_code == again.status_code == 200
    view = approved.json()
    assert set(view) == {"approval_id", "base_url", "trust_class", "environment_variable"}
    assert view["base_url"] == "https://openrouter.ai/api/v1"
    assert view["trust_class"] == "remote"
    assert view["environment_variable"] is None
    assert again.json() == view
    assert view in listed
    assert [origin["approval_id"] for origin in listed].count(view["approval_id"]) == 1


def test_unsafe_addresses_extra_members_and_this_servers_own_port_are_refused(
    tmp_path: Path,
) -> None:
    secret = "sk-or-never-echo-000000000000000"
    with TestClient(_app(tmp_path, server_port=8765), base_url=ORIGIN) as client:
        before = client.get("/api/providers").json()["origins"]
        responses = [
            _post(client, "/api/providers/origins", {"base_url": "http://api.example.com/v1"}),
            _post(client, "/api/providers/origins", {"base_url": "10.0.0.5/v1"}),
            _post(client, "/api/providers/origins", {"base_url": "127.0.0.1:8765/v1"}),
            _post(client, "/api/providers/origins", {"base_url": 7}),
            _post(client, "/api/providers/origins", {}),
            _post(
                client,
                "/api/providers/origins",
                {"base_url": "openrouter.ai/api/v1", "api_key": secret},
            ),
        ]
        after = client.get("/api/providers").json()["origins"]

    for response in responses:
        assert 400 <= response.status_code < 500, response.text
        assert secret not in response.text
        assert "api.example.com" not in response.text
    assert [response.json()["error"]["code"] for response in responses[:4]] == [
        "invalid-provider-origin"
    ] * 4
    assert after == before


def test_approving_an_address_still_needs_the_csrf_token(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path), base_url=ORIGIN) as client:
        refused = _post(
            client, "/api/providers/origins", {"base_url": "openrouter.ai/api/v1"}, csrf=False
        )
        listed = client.get("/api/providers").json()["origins"]
    assert refused.status_code == 403
    assert all("openrouter" not in origin["base_url"] for origin in listed)


def test_a_key_sent_without_remember_is_held_in_memory_and_never_echoed(tmp_path: Path) -> None:
    registry = ProviderOriginRegistry()

    class _NoKeyring:
        def get_password(self, service: str, username: str) -> None:
            raise AssertionError("an in-memory key never reads the keyring")

        def set_password(self, service: str, username: str, password: str) -> None:
            raise AssertionError("an in-memory key never writes the keyring")

    store = OSKeyringCityCredentialStore(
        registry, backend=_NoKeyring(), approved_backend_types=(_NoKeyring,), environ={}
    )
    with TestClient(
        _app(tmp_path, provider_runtime=registry, credential_store=store), base_url=ORIGIN
    ) as client:
        origin = _post(
            client, "/api/providers/origins", {"base_url": "openrouter.ai/api/v1"}
        ).json()
        draft = _draft(
            profile_id=f"guide-{origin['approval_id']}",
            display_label="Guided",
            mode="remote",
            base_url=origin["base_url"],
            origin_approval_id=origin["approval_id"],
            model_id="qwen/qwen3-8b",
            max_output_tokens=256,
        ).model_dump(mode="json")
        profile = _post(client, "/api/providers", draft).json()
        path = f"/api/providers/{profile['profile_id']}/revisions/{profile['revision_sha256']}"
        stored = _post(client, f"{path}/credential", {"credential": TYPED_KEY, "remember": False})
        listed = client.get("/api/providers")
        assert store.holds_session_key(origin["approval_id"])

    assert stored.status_code == 200
    assert stored.json()["credential_available"] is True
    assert TYPED_KEY not in stored.text + listed.text
    # The server's shutdown clears every key held in memory.
    assert not store.holds_session_key(origin["approval_id"])


def test_a_malformed_remember_flag_is_refused_without_echo(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path), base_url=ORIGIN) as client:
        refused = _post(
            client,
            f"/api/providers/local-ollama/revisions/{'a' * 64}/credential",
            {"credential": TYPED_KEY, "remember": "yes"},
        )
    assert refused.status_code == 400
    assert TYPED_KEY not in refused.text
