from __future__ import annotations

import importlib
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


async def test_shared_transport_unexpected_diagnostic_does_not_escape_in_traceback() -> None:
    from tests.unit.cognition.test_city_openai_compatible import _UnexpectedWriteFactory
    from tests.unit.cognition.test_city_provider_transport import _transport

    shared = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    transport, credential, _resolver, _factory = _transport(factory=_UnexpectedWriteFactory())
    with pytest.raises(shared.CityProviderTransportError) as caught:
        await transport.post_json({})
    assert str(caught.value) == "city cognition provider transport failed unexpectedly"
    assert FAKE_CREDENTIAL not in _traceback_local_rendering(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await transport.aclose()
    credential.clear()


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


@pytest.mark.parametrize("carrier", ["inner", "outer", "usage", "escaped"])
@pytest.mark.parametrize(
    "secret",
    [
        "sk-test-city-provider-secret",
        "Bearer fake-authorization-opaque",
        "api_key=opaquevalue1234567890",
        "https://user:opaquevalue1234567890@example.invalid/",
    ],
)
async def test_v2_screens_complete_raw_body_before_field_selection(carrier, secret, caplog) -> None:
    from tests.unit.cognition.test_city_openai_compatible_v2 import _module, _provider, _result
    from tests.unit.cognition.test_city_prompts_v2 import _provider_case

    _module()
    caplog.set_level(logging.DEBUG, logger="httpcore")
    request, _ = _provider_case()
    result = _result(request.request_id)
    envelope = json.loads(_completion(json.dumps(result)))
    if carrier == "inner":
        result["extra"] = {"secret": secret}
        envelope["choices"][0]["message"]["content"] = json.dumps(result)
    elif carrier == "outer":
        envelope["ignored"] = {"secret": secret}
    elif carrier == "usage":
        envelope["usage"]["ignored"] = secret
    else:
        envelope["ignored"] = json.dumps(json.dumps(secret))
    request, _, provider, *_ = _provider(json.dumps(envelope).encode())
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.repair_context.reason_code == "unsafe-content"
    published = attempt.model_dump_json() + repr(attempt) + repr(provider) + caplog.text
    assert secret not in published
    await provider.aclose()


async def test_v2_protocol_exception_discards_raw_body_and_parser_frames() -> None:
    from tests.unit.cognition.test_city_openai_compatible_v2 import _module, _provider

    _module()
    marker = "opaque-provider-diagnostic-sentinel"
    # A wrong envelope type is a protocol error; members nobody reads are ignored instead.
    request, _, provider, *_ = _provider(_completion("{}", object=marker))
    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert marker not in _traceback_local_rendering(caught.value)
    assert FAKE_CREDENTIAL not in _traceback_local_rendering(caught.value)
    await provider.aclose()


async def test_v2_transport_exception_logs_and_traceback_contain_no_secrets(caplog) -> None:
    from tests.unit.cognition.test_city_openai_compatible import _UnexpectedWriteFactory
    from tests.unit.cognition.test_city_openai_compatible_v2 import _module, _provider

    module = _module()
    caplog.set_level(logging.DEBUG, logger="httpcore")
    request, _, provider, *_ = _provider(factory=_UnexpectedWriteFactory())
    with pytest.raises(module.CityProviderTransportError) as caught:
        await provider.call(request)
    assert FAKE_CREDENTIAL not in caplog.text + _traceback_local_rendering(caught.value)
    assert caught.value.__cause__ is caught.value.__context__ is None
    await provider.aclose()


@pytest.mark.parametrize("carrier", ["request", "repair"])
async def test_v2_forged_call_values_do_not_survive_in_public_traceback(carrier) -> None:
    from adlife.core.domain.city_cognition_v2 import CityProviderRepairContextV2
    from tests.unit.cognition.test_city_openai_compatible_v2 import _provider

    request, _, provider, *_ = _provider()
    secret = "api_key=opaquevalue1234567890"
    if carrier == "request":
        request = type(request).model_construct(
            **(request.model_dump(mode="python") | {"request_id": {"secret": secret}})
        )
        operation = provider.call(request)
    else:
        context = CityProviderRepairContextV2.model_construct(
            schema_version=2, reason_code="invalid-schema", safe_excerpt=secret
        )
        operation = provider.repair_call(request, context=context)
    with pytest.raises(ValueError) as caught:
        await operation
    assert secret not in _traceback_local_rendering(caught.value)
    assert caught.value.__cause__ is caught.value.__context__ is None
    await provider.aclose()


@pytest.mark.parametrize("carrier", ["credential-source", "credential-slot", "endpoint-url"])
def test_v2_constructor_refusal_discards_untrusted_binding_metadata(carrier) -> None:
    from dataclasses import replace

    from adlife.adapters.cognition.city_endpoint_policy import CityEndpointBindingError
    from tests.unit.cognition.test_city_openai_compatible import _remote_credential
    from tests.unit.cognition.test_city_openai_compatible_v2 import _module
    from tests.unit.cognition.test_city_prompts_v2 import _provider_case

    module = _module()
    _, frozen = _provider_case()
    _, _, policy, endpoint, _ = _case()
    credential = _remote_credential(frozen)
    secret = "api_key=opaquevalue1234567890"
    if carrier == "endpoint-url":
        endpoint = replace(endpoint, base_url="https://user:" + secret + "@example.invalid/")
    else:
        credential = replace(
            credential,
            **{"source" if carrier == "credential-source" else "credential_slot_id": secret},
        )
    with pytest.raises(CityEndpointBindingError) as caught:
        module.CityOpenAICompatibleProviderV2(
            endpoint_policy=policy, endpoint=endpoint, cognition_input=frozen, credential=credential
        )
    current = caught.value.__traceback__
    retained = []
    while current is not None:
        if str(current.tb_frame.f_globals.get("__name__", "")).startswith("adlife."):
            retained.extend(repr(v) for k, v in current.tb_frame.f_locals.items() if k != "self")
        current = current.tb_next
    assert secret not in "\n".join(retained)
    assert caught.value.__cause__ is caught.value.__context__ is None
    credential.clear()
