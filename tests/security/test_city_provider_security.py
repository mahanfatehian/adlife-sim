from __future__ import annotations

import json
import logging
import traceback

import pytest

from adlife.adapters.cognition.city_openai_compatible import (
    CityProviderAuthenticationError,
    CityProviderProtocolError,
    CityProviderTimeoutError,
)
from tests.security.test_redaction_corpus import LEAK_CORPUS, LeakShape
from tests.unit.cognition.test_city_openai_compatible import (
    FAKE_CREDENTIAL,
    _case,
    _completion,
    _FailingFactory,
    _http_response,
    _provider,
    _provider_with_factory,
    _result,
)


def _traceback_local_rendering(error: BaseException) -> str:
    retained_locals: list[str] = []
    current = error.__traceback__
    while current is not None:
        module_name = current.tb_frame.f_globals.get("__name__")
        if isinstance(module_name, str) and module_name.startswith("adlife."):
            retained_locals.extend(repr(value) for value in current.tb_frame.f_locals.values())
        current = current.tb_next
    return "\n".join(retained_locals)


@pytest.mark.parametrize(
    "shape",
    LEAK_CORPUS,
    ids=[f"{shape.finding}-{shape.label}" for shape in LEAK_CORPUS],
)
async def test_city_provider_leak_corpus_never_crosses_the_adapter_boundary(
    shape: LeakShape,
) -> None:
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(_completion(shape.body))
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "unsafe-content"
    published = attempt.model_dump_json() + repr(attempt) + repr(provider)
    assert shape.secret not in published
    assert shape.body not in published
    await provider.aclose()


async def test_city_provider_error_body_and_transport_credential_are_absent_from_traceback() -> (
    None
):
    response_secret = "0000abcdef1234567890"
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(
            f'{{"authorization":"Bearer {response_secret}"}}'.encode(),
            status=401,
            reason="Unauthorized",
        )
    )

    with pytest.raises(CityProviderAuthenticationError) as caught:
        await provider.call(request)

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert response_secret not in rendered
    assert FAKE_CREDENTIAL not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


async def test_public_provider_error_traceback_retains_no_transport_secrets() -> None:
    response_secret = "sk-provider-response-header-secret"
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(
            b"{}",
            status=401,
            reason="Unauthorized",
            headers=(("X-Provider-Diagnostic", response_secret),),
        )
    )

    with pytest.raises(CityProviderAuthenticationError) as caught:
        await provider.call(request)

    retained = _traceback_local_rendering(caught.value)
    assert FAKE_CREDENTIAL not in retained
    assert response_secret not in retained
    await provider.aclose()


async def test_parse_protocol_error_traceback_retains_no_raw_provider_values() -> None:
    response_marker = "opaque-provider-diagnostic-sentinel"
    request, _input_document, _policy, _endpoint, _resolver = _case()
    body = _completion(
        json.dumps(_result(request.request_id), separators=(",", ":")),
        unsupported_provider_field=response_marker,
    )
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)

    retained = _traceback_local_rendering(caught.value)
    assert FAKE_CREDENTIAL not in retained
    assert response_marker not in retained
    await provider.aclose()


@pytest.mark.parametrize(
    "content",
    [
        "[" * 2_000 + "0" + "]" * 2_000,
        '{"schema_version":1,"request_id":"cog-'
        + "0" * 64
        + '","interpretation":1e100000,"decision_factors":[],'
        '"safety_flags":[],"disclosure":"synthetic-narrative-not-observed-behavior"}',
    ],
)
async def test_deep_or_nonfinite_provider_json_fails_closed_without_raw_content(
    content: str,
) -> None:
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(_completion(content))
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert content not in attempt.model_dump_json()
    await provider.aclose()


async def test_nonfinite_numeric_extension_in_the_outer_envelope_is_rejected() -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    result = json.dumps(_result(request.request_id), separators=(",", ":"))
    body = _completion(result)
    body = body[:-1] + b',"provider_score":1e100000}'
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "invalid-json"
    await provider.aclose()


async def test_authorization_value_is_never_copied_into_prompt_or_json_payload() -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(
        _http_response(_completion("{}"))
    )

    await provider.call(request)

    wire = b"".join(factory.backends[0].stream.writes)
    _headers, body = wire.split(b"\r\n\r\n", 1)
    assert FAKE_CREDENTIAL.encode() not in body
    assert b"authorization" not in body.lower()
    await provider.aclose()


async def test_city_transport_suppresses_provider_values_from_httpcore_debug_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="httpcore")
    logger = logging.getLogger("httpcore.http11")
    logger.debug("safe-before-city-provider-call")
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(
            _completion("{}"),
            headers=(("X-Provider-Diagnostic", FAKE_CREDENTIAL),),
        )
    )

    attempt = await provider.call(request)
    await provider.aclose()
    logger.debug("safe-after-city-provider-call")

    assert attempt.status == "invalid-result"
    assert "safe-before-city-provider-call" in caplog.text
    assert "safe-after-city-provider-call" in caplog.text
    assert FAKE_CREDENTIAL not in caplog.text


async def test_city_transport_suppresses_transport_exception_values_from_debug_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="httpcore")
    factory = _FailingFactory()
    request, _input_document, provider, _credential, _resolver, _factory = _provider_with_factory(
        factory
    )

    with pytest.raises(CityProviderTimeoutError) as caught:
        await provider.call(request)
    await provider.aclose()

    assert FAKE_CREDENTIAL not in caplog.text
    assert "private/provider.sock" not in caplog.text
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
