from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import re
from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

TOKEN = "fixed-test-csrf-token"
HOST = "127.0.0.1:8765"
ORIGIN = f"http://{HOST}"


def _module():
    spec = importlib.util.find_spec("adlife.city.workbench_security")
    assert spec is not None, "workbench HTTP security boundary is not implemented"
    return importlib.import_module("adlife.city.workbench_security")


def _test_app() -> tuple[FastAPI, dict[str, int]]:
    module = _module()
    calls = {"read": 0, "write": 0}
    app = FastAPI()
    app.add_middleware(module.WorkbenchSecurityMiddleware, csrf_token=TOKEN)

    @app.get("/api/read")
    async def read() -> JSONResponse:
        calls["read"] += 1
        return JSONResponse({"ok": True})

    @app.head("/api/read")
    async def read_head() -> JSONResponse:
        calls["read"] += 1
        return JSONResponse({"ok": True})

    @app.post("/api/write")
    async def write(request: Request) -> JSONResponse:
        calls["write"] += 1
        payload = module.strict_json_object(await request.body())
        return JSONResponse({"keys": sorted(payload)})

    return app, calls


def _write_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-AdLife-CSRF": TOKEN,
    }
    headers.update(overrides)
    return headers


def test_new_csrf_token_is_unique_urlsafe_operational_entropy() -> None:
    first = _module().new_csrf_token()
    second = _module().new_csrf_token()

    assert first != second
    assert len(first) >= 40
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_get_and_head_do_not_require_csrf_and_all_responses_are_no_store() -> None:
    app, calls = _test_app()
    with TestClient(app, base_url=ORIGIN) as client:
        get_response = client.get("/api/read")
        head_response = client.head("/api/read")

    assert get_response.status_code == 200
    assert head_response.status_code == 200
    assert calls["read"] == 2
    for response in (get_response, head_response):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers


def test_post_accepts_only_exact_loopback_headers_and_replays_body_once() -> None:
    app, calls = _test_app()
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/write",
            headers=_write_headers(),
            content=b'{"beta":2,"alpha":1}',
        )

    assert response.status_code == 200
    assert response.json() == {"keys": ["alpha", "beta"]}
    assert calls["write"] == 1
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_post_accepts_each_exact_loopback_host_form(host: str) -> None:
    app, calls = _test_app()
    origin = f"http://{host}"
    with TestClient(app, base_url=origin) as client:
        response = client.post(
            "/api/write",
            headers=_write_headers(Origin=origin),
            content=b"{}",
        )

    assert response.status_code == 200
    assert calls["write"] == 1


@pytest.mark.parametrize(
    ("header", "value", "status"),
    [
        ("Origin", "", 403),
        ("Origin", "null", 403),
        ("Origin", "http://127.0.0.1:8766", 403),
        ("Origin", "https://127.0.0.1:8765", 403),
        ("Origin", "http://example.test", 403),
        ("X-AdLife-CSRF", "", 403),
        ("X-AdLife-CSRF", "wrong-token", 403),
        ("Content-Type", "application/json; charset=utf-8", 400),
        ("Content-Type", "Application/JSON", 400),
        ("Content-Type", "text/plain", 400),
    ],
)
def test_post_rejects_missing_or_inexact_security_headers_before_endpoint(
    header: str, value: str, status: int
) -> None:
    app, calls = _test_app()
    headers = _write_headers()
    if value:
        headers[header] = value
    else:
        headers.pop(header)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/write",
            headers=headers,
            content=b'{"do_not_echo":"payload-marker"}',
        )

    assert response.status_code == status
    assert calls["write"] == 0
    assert response.json()["schema_version"] == 1
    assert "payload-marker" not in response.text


def test_post_rejects_hostile_host_before_endpoint() -> None:
    app, calls = _test_app()
    headers = _write_headers()
    headers["Host"] = "example.test"
    headers["Origin"] = "http://example.test"
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/api/write", headers=headers, content=b"{}")

    assert response.status_code == 400
    assert calls["write"] == 0
    assert response.json()["error"]["code"] == "invalid-host"


RawApp = Callable[[Scope, Receive, Send], Awaitable[None]]


async def _raw_request(
    app: RawApp,
    *,
    body_messages: list[Message],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
    method: str = "POST",
) -> tuple[int, dict[str, str], bytes]:
    headers = [
        (b"host", HOST.encode("ascii")),
        (b"content-type", b"application/json"),
        (b"origin", ORIGIN.encode("ascii")),
        (b"x-adlife-csrf", TOKEN.encode("ascii")),
    ]
    headers.extend(extra_headers or [])
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/api/write",
        "raw_path": b"/api/write",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 40_000),
        "server": ("127.0.0.1", 8_765),
    }
    pending = list(body_messages)
    sent: list[Message] = []

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1") for key, value in start.get("headers", [])
    }
    return int(start["status"]), response_headers, body


def _counting_raw_app(calls: list[bytes]) -> RawApp:
    async def app(_scope: Scope, receive: Receive, send: Send) -> None:
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            assert message["type"] == "http.request"
            chunks.append(message.get("body", b""))
            more = bool(message.get("more_body", False))
        calls.append(b"".join(chunks))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    return app


@pytest.mark.asyncio
async def test_streaming_limit_accepts_exact_limit_and_rejects_limit_plus_one() -> None:
    module = _module()
    calls: list[bytes] = []
    middleware = module.WorkbenchSecurityMiddleware(_counting_raw_app(calls), csrf_token=TOKEN)
    limit = module.MAX_WORKBENCH_BODY_BYTES
    exact = b"a" * limit
    status, headers, _ = await _raw_request(
        middleware,
        body_messages=[
            {"type": "http.request", "body": exact[:70_000], "more_body": True},
            {"type": "http.request", "body": exact[70_000:], "more_body": False},
        ],
    )
    assert status == 200
    assert calls == [exact]
    assert headers["cache-control"] == "no-store"

    calls.clear()
    oversized = b"b" * (limit + 1)
    status, _, body = await _raw_request(
        middleware,
        body_messages=[
            {"type": "http.request", "body": oversized[:60_000], "more_body": True},
            {"type": "http.request", "body": oversized[60_000:], "more_body": False},
        ],
    )
    assert status == 413
    assert calls == []
    assert json.loads(body)["error"]["code"] == "request-too-large"


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [b"-1", b"1.5", b"abc", b" 2", b""])
async def test_malformed_content_length_is_refused_without_calling_endpoint(
    content_length: bytes,
) -> None:
    calls: list[bytes] = []
    middleware = _module().WorkbenchSecurityMiddleware(_counting_raw_app(calls), csrf_token=TOKEN)
    status, _, _ = await _raw_request(
        middleware,
        body_messages=[{"type": "http.request", "body": b"{}", "more_body": False}],
        extra_headers=[(b"content-length", content_length)],
    )

    assert status == 400
    assert calls == []


@pytest.mark.asyncio
async def test_declared_oversize_and_duplicate_sensitive_headers_are_refused() -> None:
    module = _module()
    calls: list[bytes] = []
    middleware = module.WorkbenchSecurityMiddleware(_counting_raw_app(calls), csrf_token=TOKEN)
    status, _, _ = await _raw_request(
        middleware,
        body_messages=[{"type": "http.request", "body": b"", "more_body": False}],
        extra_headers=[
            (b"content-length", str(module.MAX_WORKBENCH_BODY_BYTES + 1).encode("ascii"))
        ],
    )
    assert status == 413
    assert calls == []

    status, _, _ = await _raw_request(
        middleware,
        body_messages=[{"type": "http.request", "body": b"{}", "more_body": False}],
        extra_headers=[(b"origin", ORIGIN.encode("ascii"))],
    )
    assert status == 403
    assert calls == []


@pytest.mark.asyncio
async def test_body_length_mismatch_and_oversize_secret_are_value_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    calls: list[bytes] = []
    middleware = module.WorkbenchSecurityMiddleware(_counting_raw_app(calls), csrf_token=TOKEN)
    status, _, _ = await _raw_request(
        middleware,
        body_messages=[{"type": "http.request", "body": b"{}", "more_body": False}],
        extra_headers=[(b"content-length", b"3")],
    )
    assert status == 400
    assert calls == []

    secret = "Authorization: Bearer sk-never-echo-this-1234567890"
    payload = secret.encode("utf-8") + b"x" * module.MAX_WORKBENCH_BODY_BYTES
    with caplog.at_level(logging.DEBUG):
        status, _, response_body = await _raw_request(
            middleware,
            body_messages=[
                {"type": "http.request", "body": payload[:70_000], "more_body": True},
                {"type": "http.request", "body": payload[70_000:], "more_body": False},
            ],
        )
    assert status == 413
    assert calls == []
    assert secret not in response_body.decode("utf-8")
    assert secret not in caplog.text


@pytest.mark.parametrize(
    "document",
    [
        b"\xff",
        b'{"item":1,"item":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"[]",
        b'"scalar"',
        b"{not-json}",
        ('{"value":' + "[" * 40 + "0" + "]" * 40 + "}").encode("utf-8"),
        b'{"value":"\\ud800"}',
    ],
)
def test_strict_json_object_refuses_malformed_ambiguous_or_deep_documents(
    document: bytes,
) -> None:
    with pytest.raises(ValueError) as captured:
        _module().strict_json_object(document)

    assert "item" not in str(captured.value)
    assert "value" not in str(captured.value)
    assert "not-json" not in str(captured.value)


def test_strict_json_object_preserves_one_finite_object() -> None:
    assert _module().strict_json_object(b'{"text":"fictional","items":[true,null,1,1.5]}') == {
        "text": "fictional",
        "items": [True, None, 1, 1.5],
    }


def test_error_envelope_is_exact_bounded_and_never_leaks_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    response = module.workbench_error(
        422,
        "invalid-scenario",
        "The scenario is invalid.",
        {"scenario.roadside.road_id": "Unknown road identifier."},
    )

    assert json.loads(response.body) == {
        "schema_version": 1,
        "error": {
            "code": "invalid-scenario",
            "message": "The scenario is invalid.",
            "fields": {"scenario.roadside.road_id": "Unknown road identifier."},
        },
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in response.headers
    assert "server" not in response.headers

    secret = "Authorization: Bearer sk-secret-value-1234567890"
    absolute_path = r"C:\private\workspace\run.json"
    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as captured:
        module.workbench_error(400, "invalid-request", secret, {"field": absolute_path})
    combined = str(captured.value) + "\n" + caplog.text
    assert secret not in combined
    assert absolute_path not in combined

    with pytest.raises(ValueError) as path_failure:
        module.workbench_error(
            400,
            "invalid-request",
            "The request is invalid.",
            {"field": absolute_path},
        )
    assert absolute_path not in str(path_failure.value)
