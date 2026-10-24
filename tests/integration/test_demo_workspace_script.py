"""The live-demo backup script, driven against a real loopback city workbench.

``scripts/demo_workspace.py`` may reach the workbench only the way the browser page does:
the per-process security token read from the guide page, then same-origin JSON writes.
These tests start a real workbench on an ephemeral loopback port with a temporary
workspace, run the script, and check the saved runs through the routes the guide uses.
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import uvicorn

from adlife.city.run_store import CityRunStore
from adlife.city.workbench_input import WorkbenchRunInputV4
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition_v2 import CityCognitionResultV2, CityProviderAttemptV2

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "demo_workspace.py"
TOKEN = "demo-workspace-script-token"
SOURCES = ("rules", "mock", "provider", "cache", "fallback", "budget-skipped")


@pytest.fixture(scope="module")
def script() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("demo_workspace", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@contextmanager
def _serve(application: object) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("the test workbench did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        if thread.is_alive():
            raise RuntimeError("the test workbench did not stop")


@contextmanager
def _fake_ollama(
    installed: frozenset[str] = frozenset({"qwen2.5:7b-instruct"}),
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """A loopback stand-in for Ollama's native API that only answers the keep-alive load."""
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            calls.append({"path": self.path, "body": body})
            if self.path != "/api/generate" or body.get("model") not in installed:
                self.send_error(404)
                return
            payload = json.dumps(
                {"model": body["model"], "response": "", "done": True, "done_reason": "load"}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _SignalProvider:
    """A fake bounded-signal provider: one valid closed-category answer per request."""

    def __init__(self, **_kwargs: object) -> None:
        self.calls = 0

    async def call(self, request: Any) -> CityProviderAttemptV2:
        self.calls += 1
        result = CityCognitionResultV2(
            schema_version=2,
            request_id=request.request_id,
            affect="curious",
            relevance="high",
            credibility="medium",
            salience="high",
            safety_flags=(),
            disclosure="synthetic-bounded-cognition-not-observed-behavior",
        )
        return CityProviderAttemptV2(
            schema_version=2, status="valid", result=result, repair_context=None, usage=None
        )

    async def repair_call(self, *_args: object, **_kwargs: object) -> CityProviderAttemptV2:
        raise AssertionError("a valid signal never needs repair")

    async def aclose(self) -> None:
        return None


def _counts(line: str) -> dict[str, int]:
    pairs = (field.split("=", maxsplit=1) for field in line.split() if "=" in field)
    return {name: int(value) for name, value in pairs}


def test_seeds_ready_to_open_offline_runs_through_the_real_http_api(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    app = create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=TOKEN)
    expected = {"seeded-v10-mock-001": "mock", "seeded-v9-rules-001": "rules"}
    # A V10 run opens as its guided story; the V9 run has none and is listed on /guide.
    views = {
        "seeded-v10-mock-001": "/guide/runs/seeded-v10-mock-001",
        "seeded-v9-rules-001": "/guide",
    }
    with _serve(app) as base_url:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--base-url", base_url, "--run-prefix", "seeded"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            cwd=ROOT,
        )
        assert completed.returncode == 0, completed.stderr
        # The page's per-process token authorizes writes; it is never echoed or stored.
        assert TOKEN not in completed.stdout + completed.stderr

        lines = completed.stdout.splitlines()
        assert [line.split()[0] for line in lines] == list(expected)
        for line, (run_id, source) in zip(lines, expected.items(), strict=True):
            fields = line.split()
            assert fields[1] == "completed"
            assert fields[-1] == f"{base_url}{views[run_id]}"
            counts = _counts(line)
            assert tuple(counts) == SOURCES
            assert counts[source] > 0
            assert all(count == 0 for name, count in counts.items() if name != source)

        with httpx.Client(base_url=base_url, trust_env=False) as client:
            listed = client.get("/api/runs", params={"limit": 100}).json()
            assert {run["run_id"] for run in listed["runs"]} == set(expected)
            story = client.get("/api/runs/seeded-v10-mock-001/story")
            assert story.status_code == 200
            assert story.json()["run"]["run_id"] == "seeded-v10-mock-001"
            for run_id in expected:
                assert client.get(f"/api/runs/{run_id}/meta").status_code == 200

    store = CityRunStore(root)
    assert store.load("seeded-v10-mock-001").manifest.schema_version == 10
    assert store.load("seeded-v9-rules-001").manifest.schema_version == 9


def test_with_llm_reuses_the_local_profile_warms_ollama_and_funds_every_selected_request(
    tmp_path: Path, script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers: list[_SignalProvider] = []

    def factory(**kwargs: object) -> _SignalProvider:
        provider = _SignalProvider(**kwargs)
        providers.append(provider)
        return provider

    root = tmp_path / "workspace"
    # The built-in local-ollama approval stays the real one; the fake provider answers the
    # check and the run, and only the script's keep-alive call goes to a fake Ollama root.
    app = create_city_workbench_app(
        prepare_workbench_workspace(root), csrf_token=TOKEN, provider_factory_v2=factory
    )
    derived_from: list[str] = []
    with _fake_ollama() as (ollama_url, ollama_calls), _serve(app) as base_url:

        def fake_ollama_root(local_base_url: str) -> str:
            derived_from.append(local_base_url)
            return ollama_url

        monkeypatch.setattr(script, "ollama_root", fake_ollama_root)
        out, err = io.StringIO(), io.StringIO()
        options = script.Options(
            base_url=base_url, with_llm=True, run_prefix="llm", offline_runs=False
        )
        (outcome,) = script.seed_workspace(options, out=out, err=err)

        assert derived_from == ["http://127.0.0.1:11434/v1"]
        assert ollama_calls == [
            {
                "path": "/api/generate",
                "body": {"model": "qwen2.5:7b-instruct", "keep_alive": "30m", "stream": False},
            }
        ]
        assert outcome.run_id == "llm-v10-local-001"
        assert outcome.phase == "completed"
        assert outcome.view_url == f"{base_url}/guide/runs/llm-v10-local-001"
        counts = dict(outcome.counts)
        selected = sum(counts.values())
        assert selected > 0
        # Selection policy V3 selects at most the AI limit and funds every selected request.
        assert counts["provider"] == selected <= 60
        assert counts["budget_skipped"] == 0
        assert out.getvalue().splitlines() == [outcome.line()]
        # One check probe plus every funded request reached the provider.
        assert sum(provider.calls for provider in providers) == 1 + counts["provider"]
        assert TOKEN not in out.getvalue() + err.getvalue()

        # The script saved the browser's prefilled default revision, and reuses it.
        workbench = script.Workbench(base_url)
        try:
            workbench.connect()
            profile_id, revision = script.ensure_local_profile(
                workbench, "qwen2.5:7b-instruct", "http://127.0.0.1:11434/v1", err=io.StringIO()
            )
        finally:
            workbench.close()
        profiles = httpx.get(f"{base_url}/api/providers", trust_env=False).json()["profiles"]
        assert [(item["profile_id"], item["revision_sha256"]) for item in profiles] == [
            (profile_id, revision)
        ]
        assert profile_id == "local-ollama"
        assert profiles[0]["display_label"] == "Local Ollama"
        assert profiles[0]["model_id"] == "qwen2.5:7b-instruct"

    stored = CityRunStore(root).load("llm-v10-local-001")
    assert stored.manifest.schema_version == 10
    assert isinstance(stored.workbench_input, WorkbenchRunInputV4)
    cognition = stored.workbench_input.draft.cognition
    # The smallest published V3 AI limit.
    assert (cognition.mode, cognition.funded_response_limit) == ("local", 60)
    assert stored.cognition_input.causal_contract.selection_policy_version == 3
    assert (cognition.provider_profile_id, cognition.provider_revision_sha256) == (
        profile_id,
        revision,
    )


def test_a_missing_model_fails_before_any_profile_revision_is_saved(
    tmp_path: Path, script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"), csrf_token=TOKEN
    )
    with _fake_ollama() as (ollama_url, ollama_calls), _serve(app) as base_url:
        monkeypatch.setattr(script, "ollama_root", lambda _local_base_url: ollama_url)
        options = script.Options(
            base_url=base_url,
            with_llm=True,
            model="qwen9:not-installed",
            run_prefix="missing",
            offline_runs=False,
        )
        out = io.StringIO()
        with pytest.raises(script.DemoError) as refused:
            script.seed_workspace(options, out=out, err=io.StringIO())
        assert str(refused.value) == (
            "Ollama has no model qwen9:not-installed; run `ollama pull qwen9:not-installed` first"
        )
        assert [call["body"]["model"] for call in ollama_calls] == ["qwen9:not-installed"]
        assert out.getvalue() == ""
        listed = httpx.get(f"{base_url}/api/providers", trust_env=False).json()
        assert listed["profiles"] == []


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.com:50050",
        "https://127.0.0.1:50050",
        "http://127.0.0.2:50050",
        "http://0.0.0.0:50050",
        "http://[::1]:50050",
        "http://127.1:50050",
        "http://127.0.0.1.nip.io:50050",
        "http://localhost.:50050",
        "http://LOCALHOST:50050",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:050050",
        "http://127.0.0.1:65536",
        "http://operator:pw-not-real@127.0.0.1:50050",
        "http://127.0.0.1:50050/api",
        "http://127.0.0.1:50050?next=1",
        "http://127.0.0.1:50050#run",
        " http://127.0.0.1:50050",
        "127.0.0.1:50050",
        "",
    ],
)
def test_the_loopback_guard_refuses_every_other_base_url(script: ModuleType, raw: str) -> None:
    with pytest.raises(script.DemoError) as refused:
        script.parse_base_url(raw)
    assert str(refused.value) == (
        "--base-url must be http://127.0.0.1:<port> or http://localhost:<port>"
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("http://127.0.0.1:50050", "http://127.0.0.1:50050"),
        ("http://127.0.0.1:50050/", "http://127.0.0.1:50050"),
        ("http://localhost:8765", "http://localhost:8765"),
    ],
)
def test_the_loopback_guard_accepts_exact_loopback_http(
    script: ModuleType, raw: str, normalized: str
) -> None:
    assert script.parse_base_url(raw) == normalized


def test_a_refused_base_url_exits_before_any_request_and_is_not_echoed(
    script: ModuleType, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_seeding(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a refused base URL must not reach any request")

    # Seeding owns every HTTP client the script creates.
    monkeypatch.setattr(script, "seed_workspace", no_seeding)
    with pytest.raises(SystemExit) as exited:
        script.main(["--base-url", "http://operator:pw-not-real@example.com:50050"])
    assert exited.value.code == 2
    captured = capsys.readouterr()
    assert "--base-url must be http://127.0.0.1:<port> or http://localhost:<port>" in captured.err
    for echoed in ("pw-not-real", "operator", "example.com"):
        assert echoed not in captured.out + captured.err


@pytest.mark.parametrize(
    "raw",
    [
        "http://10.0.0.5:11434/v1",
        "https://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
        "http://127.0.0.1:11434",
        "http://127.0.0.1:11434/v1/",
        "http://127.0.0.1:11434/api",
    ],
)
def test_only_the_loopback_ollama_origin_is_ever_warmed(script: ModuleType, raw: str) -> None:
    assert script.ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    with pytest.raises(script.DemoError):
        script.ollama_root(raw)


def test_run_numbers_continue_after_the_highest_used_with_the_prefix(script: ModuleType) -> None:
    assert script.next_run_number([], "backup") == 1
    existing = [
        "backup-v10-mock-001",
        "backup-v9-rules-003",
        "demo-v10-001",
        "backups-v10-mock-009",
        "backup-v10-local-007-copy",
        "other-backup-v10-mock-010",
    ]
    assert script.next_run_number(existing, "backup") == 4
    assert script.next_run_number(existing, "demo") == 1


def test_an_unreachable_workbench_fails_with_one_concise_message(
    script: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    assert script.main(["--base-url", f"http://127.0.0.1:{port}"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"cannot reach the workbench at http://127.0.0.1:{port}" in captured.err
    assert "Traceback" not in captured.err
