"""Strict same-origin HTTP boundary for the local city workbench."""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from adlife.core.domain.json_values import freeze_json_mapping, thaw_json_mapping
from adlife.core.domain.person import contains_labelled_secret_member, contains_provider_secret_text
from adlife.core.ports.cognition import redact_provider_body

MAX_WORKBENCH_BODY_BYTES = 131_072
MAX_WORKBENCH_ROUTE_BODY_BYTES = 4_194_304
MAX_WORKBENCH_CREDENTIAL_BODY_BYTES = 8_192

_CREDENTIAL_ROUTE = re.compile(r"^/api/providers/[^/]+/revisions/[^/]+/credential$")

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FIELD_PATH = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,159}$")
_LOOPBACK_HOST = re.compile(r"^(?:127\.0\.0\.1|localhost|\[::1\])(?::(?:[1-9][0-9]{0,4}))?$")
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SECURITY_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
)
_LOGGER = logging.getLogger(__name__)


class WorkbenchJsonError(ValueError):
    """A value-free strict JSON refusal."""


class WorkbenchJsonContentError(WorkbenchJsonError):
    """Well-formed JSON carrying forbidden semantic content, not a syntax error."""


def new_csrf_token() -> str:
    """Create process-local operational entropy for same-origin writes."""
    return secrets.token_urlsafe(32)


def is_workbench_credential_path(path: str) -> bool:
    """Recognize the dedicated endpoint, including invalid route parameters.

    Its byte ceiling applies before identifier validation, matching HTTP routing.
    """
    return _CREDENTIAL_ROUTE.fullmatch(path) is not None


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


def strict_json_object(
    data: bytes,
    *,
    max_body_bytes: int = MAX_WORKBENCH_BODY_BYTES,
) -> dict[str, object]:
    """Decode one bounded, duplicate-free, finite JSON object with bounded depth."""
    if not isinstance(data, bytes):
        raise TypeError("JSON request body must be bytes")
    if type(max_body_bytes) is not int or max_body_bytes < 1:
        raise ValueError("JSON request size limit is invalid")
    if len(data) > max_body_bytes:
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
        if contains_labelled_secret_member(thawed):
            raise WorkbenchJsonContentError("JSON request contains a labelled secret member")
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
        route_body_limits: Mapping[str, int] | None = None,
    ) -> None:
        if not isinstance(csrf_token, str) or not csrf_token:
            raise ValueError("CSRF token must be a nonempty string")
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= MAX_WORKBENCH_BODY_BYTES:
            raise ValueError("request body limit is invalid")
        if route_body_limits is None:
            copied_route_limits: dict[str, int] = {}
        else:
            if not isinstance(route_body_limits, Mapping):
                raise ValueError("route body limits are invalid")
            copied_route_limits = {}
            for path, limit in route_body_limits.items():
                if (
                    type(path) is not str
                    or not path.startswith("/api/")
                    or "?" in path
                    or "#" in path
                    or type(limit) is not int
                    or not 1 <= limit <= MAX_WORKBENCH_ROUTE_BODY_BYTES
                ):
                    raise ValueError("route body limits are invalid")
                copied_route_limits[path] = limit
        self.app = app
        self.csrf_token = csrf_token
        self.max_body_bytes = max_body_bytes
        self.route_body_limits = copied_route_limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def observed_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._handle_http(scope, receive, observed_send)
        except Exception as error:
            _LOGGER.error("workbench request failed with %s", type(error).__name__)
            if response_started:
                return
            await workbench_error(
                500,
                "internal-error",
                "The workbench could not complete the request.",
            )(scope, receive, _secured_send(send))

    async def _handle_http(self, scope: Scope, receive: Receive, send: Send) -> None:

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

        path = scope.get("path")
        assert isinstance(path, str)
        body_limit = self.route_body_limits.get(path, self.max_body_bytes)
        if is_workbench_credential_path(path):
            body_limit = min(body_limit, MAX_WORKBENCH_CREDENTIAL_BODY_BYTES)

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
            normalized_length = length_text.lstrip("0") or "0"
            limit_text = str(body_limit)
            if len(normalized_length) > len(limit_text) or (
                len(normalized_length) == len(limit_text) and normalized_length > limit_text
            ):
                await self._too_large(scope, receive, secured_send)
                return
            declared_length = int(normalized_length)

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
            remaining = body_limit + 1 - len(body)
            body.extend(chunk[: max(0, remaining)])
            if len(body) > body_limit or len(chunk) > remaining:
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
    "MAX_WORKBENCH_CREDENTIAL_BODY_BYTES",
    "MAX_WORKBENCH_ROUTE_BODY_BYTES",
    "WorkbenchJsonContentError",
    "WorkbenchJsonError",
    "WorkbenchSecurityMiddleware",
    "is_workbench_credential_path",
    "new_csrf_token",
    "strict_json_object",
    "workbench_error",
]
