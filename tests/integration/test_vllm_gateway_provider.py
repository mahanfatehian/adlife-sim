"""The bounded-signal path end to end against a loopback stand-in for a vLLM gateway.

GapGPT serves ``gapgpt-qwen-3.6`` (Qwen/Qwen3.6-35B-A3B-FP8 on vLLM) behind an
OpenAI-compatible gateway. The stand-in reproduces what the live gateway did with the
real key: it refuses a strict schema that uses ``uniqueItems`` with HTTP 400, adds vLLM
metadata members to every answer, spends the whole output budget thinking unless the
request carries ``chat_template_kwargs`` ``{"enable_thinking": false}``, and can run away
in whitespace until ``max_tokens``. It listens on a local-trust loopback origin, so the
real hardened transport, compatibility check, job worker, saved-run verification, and
offline replay all run unchanged, and no request leaves the machine.
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import uvicorn

from adlife.city.provider_runtime import build_provider_runtime_policy
from adlife.city.run_store import CityRunStore
from adlife.city.runs import replay_city_run
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "demo_workspace.py"
TOKEN = "vllm-gateway-provider-token"
MODEL = "gapgpt-qwen-3.6"
BEGIN = "BEGIN_UNTRUSTED_CITY_COGNITION_DATA"
END = "END_UNTRUSTED_CITY_COGNITION_DATA"
DISCLOSURE = "synthetic-bounded-cognition-not-observed-behavior"
SCHEMA_ERROR = (
    'Grammar error: Unimplemented keys: ["uniqueItems"] in the JSON schema used for '
    "structured outputs"
)


@pytest.fixture(scope="module")
def script() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("demo_workspace_for_vllm", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


class _Gateway:
    """Request log and switches shared with the handler thread."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.runaways_pending = 0

    def take_runaway(self) -> bool:
        with self.lock:
            if self.runaways_pending:
                self.runaways_pending -= 1
                return True
            return False


def _request_id(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and BEGIN in content:
            data = content.split(BEGIN + "\n", 1)[1].split("\n" + END, 1)[0]
            return str(json.loads(data)["request_id"])
    raise AssertionError("the request carried no delimited causal data")


def _answer(body: dict[str, Any], gateway: _Gateway) -> tuple[int, dict[str, Any]]:
    if "uniqueItems" in json.dumps(body.get("response_format")):
        return 400, {"error": {"message": SCHEMA_ERROR, "type": "BadRequestError", "code": 400}}
    messages = body["messages"]
    request_id = _request_id(messages)
    budget = int(body["max_tokens"])
    thinking = body.get("chat_template_kwargs") != {"enable_thinking": False}
    result = {
        "schema_version": 2,
        "request_id": request_id,
        "affect": "curious",
        "relevance": "high",
        "credibility": "medium",
        "salience": "high",
        "safety_flags": [],
        "disclosure": DISCLOSURE,
    }
    reasoning = None
    if thinking:
        # Qwen's chat template thinks first; the answer never starts within the cap.
        content, finish, completion, reasoned = "", "length", budget, budget
        reasoning = "Okay, I need to weigh the agent's interests against the campaign first."
    elif len(messages) == 2 and gateway.take_runaway():
        # Grammar decoding that never closes the object and fills max_tokens.
        content = json.dumps(result, indent=1)[:-2] + "\n  " + "\t" * 400
        finish, completion, reasoned = "length", budget, 0
    else:
        content, finish, completion, reasoned = json.dumps(result, indent=1), "stop", 137, 0
    prompt_text = "".join(
        f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n" for message in messages
    )
    return 200, {
        "id": f"chatcmpl-{len(gateway.requests)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": None,
                    "annotations": None,
                    "audio": None,
                    "function_call": None,
                    "tool_calls": [],
                    "reasoning": reasoning,
                    "reasoning_content": None,
                },
                "logprobs": None,
                "finish_reason": finish,
                "stop_reason": None,
                "token_ids": None,
                "routed_experts": None,
            }
        ],
        "service_tier": None,
        "system_fingerprint": None,
        "usage": {
            "prompt_tokens": 992,
            "completion_tokens": completion,
            "total_tokens": 992 + completion,
            "prompt_tokens_details": None,
            "completion_tokens_details": {"reasoning_tokens": reasoned},
        },
        "prompt_logprobs": None,
        "prompt_token_ids": [151644, 8948, 198, 151645],
        "prompt_text": prompt_text + "<|im_start|>assistant\n",
        "kv_transfer_params": None,
        "ec_transfer_params": None,
        "metrics": {"arrival_time": time.time(), "first_token_latency": 0.41},
    }


@contextmanager
def _fake_vllm_gateway() -> Iterator[tuple[str, _Gateway]]:
    gateway = _Gateway()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            with gateway.lock:
                gateway.requests.append({"path": self.path, "body": body})
            status, document = _answer(body, gateway)
            payload = json.dumps(document).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", gateway
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _serve(application: object) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application, host="127.0.0.1", port=port, log_level="critical", access_log=False
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


def _profile_draft(base_url: str, *, disable_thinking: bool) -> dict[str, object]:
    draft: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "gapgpt-stand-in",
        "display_label": "GapGPT stand-in",
        "mode": "local",
        "base_url": base_url,
        "model_id": MODEL,
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "gapgpt-stand-in",
    }
    if disable_thinking:
        draft["disable_thinking"] = True
    return draft


def _check(workbench: Any, profile: Any) -> Any:
    path = f"/api/providers/{profile['profile_id']}/revisions/{profile['revision_sha256']}/check"
    return workbench.request_json(
        "POST", path, step="check", body={"effect_scope": "bounded-signals-v1"}, timeout=150
    )[1]


def test_the_stand_in_refuses_the_schema_keyword_the_live_gateway_refused() -> None:
    """The double is faithful to the failure this change fixes, not only to the fix."""
    from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_RESULT_SCHEMA_V2_DOCUMENT

    earlier = json.loads(CITY_COGNITION_RESULT_SCHEMA_V2_DOCUMENT)
    earlier["properties"]["safety_flags"]["uniqueItems"] = True
    status, document = _answer(
        {"response_format": {"type": "json_schema", "json_schema": {"schema": earlier}}},
        _Gateway(),
    )
    assert status == 400
    assert document["error"]["message"].startswith(
        'Grammar error: Unimplemented keys: ["uniqueItems"]'
    )


def test_a_vllm_gateway_runs_a_v10_study_once_model_thinking_is_turned_off(
    tmp_path: Path, script: ModuleType
) -> None:
    root = tmp_path / "workspace"
    with _fake_vllm_gateway() as (gateway_url, gateway):
        runtime = build_provider_runtime_policy(
            provider_origins=(f"gapgpt-stand-in={gateway_url}",)
        )
        app = create_city_workbench_app(
            prepare_workbench_workspace(root), csrf_token=TOKEN, provider_runtime=runtime
        )
        with _serve(app) as base_url:
            workbench = script.Workbench(base_url)
            try:
                workbench.connect()
                _, thinking = workbench.request_json(
                    "POST",
                    "/api/providers",
                    step="save the thinking profile",
                    body=_profile_draft(gateway_url, disable_thinking=False),
                    expect=frozenset({201}),
                )
                assert "disable_thinking" not in thinking
                # Thinking eats the whole cap: the check reports an invalid result, and
                # the strict schema itself is never what the gateway refuses.
                refused = _check(workbench, thinking)
                assert (refused["status"], refused["reason_code"]) == (
                    "incompatible",
                    "invalid-result",
                )

                _, profile = workbench.request_json(
                    "POST",
                    "/api/providers",
                    step="save the no-thinking profile",
                    body=_profile_draft(gateway_url, disable_thinking=True),
                    expect=frozenset({201}),
                )
                assert profile["disable_thinking"] is True
                assert profile["revision_sha256"] != thinking["revision_sha256"]
                checked = _check(workbench, profile)
                assert (checked["status"], checked["reason_code"]) == ("compatible", None)

                # One funded request runs away in whitespace; its single repair recovers it.
                gateway.runaways_pending = 1
                plan = script.local_plan(profile["profile_id"], profile["revision_sha256"])
                outcome = script.create_run(
                    workbench,
                    plan,
                    options=script.Options(base_url=base_url, run_prefix="gapgpt", seed="42"),
                    number=1,
                    audience=script.read_audience(workbench),
                    err=io.StringIO(),
                )
            finally:
                workbench.close()

        assert outcome.phase == "completed", outcome.error_code
        counts = dict(outcome.counts)
        # Selection policy V3 funds every selected request, at most the plan's AI limit.
        selected = sum(counts.values())
        assert 0 < selected <= 60
        assert counts["provider"] == selected
        assert counts["fallback"] == counts["cache"] == counts["budget_skipped"] == 0
        bodies = [entry["body"] for entry in gateway.requests]
        # Two checks, one first attempt per selected request, and one repair of the runaway.
        assert len(bodies) == 2 + selected + 1
        assert all(entry["path"] == "/v1/chat/completions" for entry in gateway.requests)
        assert all("uniqueItems" not in json.dumps(body["response_format"]) for body in bodies)
        assert "chat_template_kwargs" not in bodies[0]
        assert all(
            body["chat_template_kwargs"] == {"enable_thinking": False} for body in bodies[1:]
        )
        assert all(body["model"] == MODEL for body in bodies)

    # The gateway is gone: the saved run verifies and replays with no provider at all.
    stored = CityRunStore(root).load(outcome.run_id)
    assert stored.manifest.schema_version == 10
    frozen = stored.cognition_input
    assert frozen.provider_profile.disable_thinking is True
    assert frozen.effective_settings.disable_thinking is True
    records = stored.cognition_evaluation.records
    provider_records = [record for record in records if record.source == "provider"]
    assert len(provider_records) == selected
    assert sorted(record.attempt_count for record in provider_records) == [1] * (selected - 1) + [2]
    assert {record.provider_effective_settings_sha256 for record in provider_records} == {
        frozen.effective_settings.fingerprint
    }
    replayed = replay_city_run(stored)
    assert replayed.cognition_effective_settings_sha256 == frozen.effective_settings.fingerprint
