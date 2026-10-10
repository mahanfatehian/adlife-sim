"""Strict same-origin HTTP boundary for the local city workbench."""

from __future__ import annotations

import hmac
import json
import re
import secrets
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from adlife.core.domain.json_values import freeze_json_mapping, thaw_json_mapping
from adlife.core.domain.person import contains_provider_secret_text
from adlife.core.ports.cognition import redact_provider_body

MAX_WORKBENCH_BODY_BYTES = 131_072

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FIELD_PATH = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,159}$")
_LOOPBACK_HOST = re.compile(r"^(?:127\.0\.0\.1|localhost|\[::1\])(?::(?:[1-9][0-9]{0,4}))?$")
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SECURITY_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
)


class WorkbenchJsonError(ValueError):
    """A value-free strict JSON refusal."""


def new_csrf_token() -> str:
    """Create process-local operational entropy for same-origin writes."""
    return secrets.token_urlsafe(32)


def _safe_error_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("workbench error text is unsafe")
    if any(
        ord(character) < 32 or 127 <= ord(character) < 160 or "\ud800" <= character <= "\udfff"
        for character in value
    ):
        raise ValueError("workbench error text is unsafe")
    if contains_provider_secret_text(value) or redact_provider_body(value) != value:
        raise ValueError("workbench error text is unsafe")
    return value


def workbench_error(
    status_code: int,
    code: str,
    message: str,
    fields: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the sole bounded, no-store workbench error representation."""
    if type(status_code) is not int or not 400 <= status_code <= 599:
        raise ValueError("workbench error status is invalid")
    if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
        raise ValueError("workbench error code is invalid")
    safe_message = _safe_error_text(message, maximum=240)
    if fields is None:
        safe_fields: dict[str, str] = {}
    else:
        if not isinstance(fields, Mapping) or len(fields) > 32:
            raise ValueError("workbench error fields are invalid")
        safe_fields = {}
        items = tuple(fields.items())
        if any(not isinstance(field, str) for field, _message in items):
            raise ValueError("workbench error field path is invalid")
        for field, field_message in sorted(items, key=lambda item: item[0]):
            if not isinstance(field, str) or _FIELD_PATH.fullmatch(field) is None:
                raise ValueError("workbench error field path is invalid")
            safe_fields[field] = _safe_error_text(field_message, maximum=160)
    return JSONResponse(
        status_code=status_code,
        content={
            "schema_version": 1,
            "error": {
                "code": code,
                "message": safe_message,
                "fields": safe_fields,
            },
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkbenchJsonError("JSON request is invalid")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise WorkbenchJsonError("JSON request is invalid")


def strict_json_object(data: bytes) -> dict[str, object]:
    """Decode one bounded, duplicate-free, finite JSON object with bounded depth."""
    if not isinstance(data, bytes):
        raise TypeError("JSON request body must be bytes")
    if len(data) > MAX_WORKBENCH_BODY_BYTES:
        raise WorkbenchJsonError("JSON request exceeds its size limit")
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(payload, dict):
            raise WorkbenchJsonError("JSON request must be an object")
        frozen = freeze_json_mapping(payload)
        thawed = thaw_json_mapping(frozen)
    except WorkbenchJsonError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise WorkbenchJsonError("JSON request is invalid") from None
    return thawed


def _header_values(scope: Scope, name: bytes) -> tuple[bytes, ...]:
    return tuple(value for key, value in scope.get("headers", []) if key.lower() == name)


def _one_ascii_header(scope: Scope, name: bytes) -> str | None:
    values = _header_values(scope, name)
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None


def _is_api_path(scope: Scope) -> bool:
    path = scope.get("path", "")
    return isinstance(path, str) and (path == "/api" or path.startswith("/api/"))


def _secured_send(send: Send) -> Send:
    async def secured(message: Message) -> None:
        if message["type"] == "http.response.start":
            removed = {b"cache-control", b"x-content-type-options", b"server"}
            headers = [
                (key, value)
                for key, value in message.get("headers", [])
                if key.lower() not in removed
            ]
            headers.extend(_SECURITY_HEADERS)
            message = {**message, "headers": headers}
        await send(message)

    return secured


class WorkbenchSecurityMiddleware:
    """Validate local write metadata and bound the request stream before parsing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        csrf_token: str,
        max_body_bytes: int = MAX_WORKBENCH_BODY_BYTES,
    ) -> None:
        if not isinstance(csrf_token, str) or not csrf_token:
            raise ValueError("CSRF token must be a nonempty string")
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= MAX_WORKBENCH_BODY_BYTES:
            raise ValueError("request body limit is invalid")
        self.app = app
        self.csrf_token = csrf_token
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        secured_send = _secured_send(send)
        method = str(scope.get("method", "")).upper()
        if not _is_api_path(scope) or method not in _STATE_CHANGING_METHODS:
            await self.app(scope, receive, secured_send)
            return
        if method != "POST":
            await workbench_error(
                405,
                "method-not-allowed",
                "This write method is not supported.",
            )(scope, receive, secured_send)
            return

        host = _one_ascii_header(scope, b"host")
        if host is None or _LOOPBACK_HOST.fullmatch(host) is None:
            await workbench_error(
                400,
                "invalid-host",
                "The request host is invalid.",
            )(scope, receive, secured_send)
            return
        content_type = _one_ascii_header(scope, b"content-type")
        if content_type != "application/json":
            await workbench_error(
                400,
                "invalid-content-type",
                "The request must use application/json.",
            )(scope, receive, secured_send)
            return
        origin = _one_ascii_header(scope, b"origin")
        if scope.get("scheme") != "http" or origin != f"http://{host}":
            await workbench_error(
                403,
                "invalid-origin",
                "The request origin is not allowed.",
            )(scope, receive, secured_send)
            return
        supplied_token = _one_ascii_header(scope, b"x-adlife-csrf")
        if supplied_token is None or not hmac.compare_digest(
            supplied_token,
            self.csrf_token,
        ):
            await workbench_error(
                403,
                "invalid-csrf",
                "The request security token is invalid.",
            )(scope, receive, secured_send)
            return

        length_values = _header_values(scope, b"content-length")
        declared_length: int | None = None
        if length_values:
            if len(length_values) != 1:
                await self._invalid_length(scope, receive, secured_send)
                return
            try:
                length_text = length_values[0].decode("ascii", errors="strict")
            except UnicodeDecodeError:
                await self._invalid_length(scope, receive, secured_send)
                return
            if not length_text or not length_text.isascii() or not length_text.isdecimal():
                await self._invalid_length(scope, receive, secured_send)
                return
            declared_length = int(length_text)
            if declared_length > self.max_body_bytes:
                await self._too_large(scope, receive, secured_send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await workbench_error(
                    400,
                    "invalid-request",
                    "The request body is invalid.",
                )(scope, receive, secured_send)
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await workbench_error(
                    400,
                    "invalid-request",
                    "The request body is invalid.",
                )(scope, receive, secured_send)
                return
            remaining = self.max_body_bytes + 1 - len(body)
            body.extend(chunk[: max(0, remaining)])
            if len(body) > self.max_body_bytes or len(chunk) > remaining:
                await self._too_large(scope, receive, secured_send)
                return
            if not message.get("more_body", False):
                break
        if declared_length is not None and declared_length != len(body):
            await self._invalid_length(scope, receive, secured_send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, secured_send)

    @staticmethod
    async def _invalid_length(scope: Scope, receive: Receive, send: Send) -> None:
        await workbench_error(
            400,
            "invalid-content-length",
            "The request content length is invalid.",
        )(scope, receive, send)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
        await workbench_error(
            413,
            "request-too-large",
            "The request body exceeds the size limit.",
        )(scope, receive, send)


__all__ = [
    "MAX_WORKBENCH_BODY_BYTES",
    "WorkbenchJsonError",
    "WorkbenchSecurityMiddleware",
    "new_csrf_token",
    "strict_json_object",
    "workbench_error",
]
