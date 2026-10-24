"""Bounded V2 orchestration honors frozen admission before external effects."""

from __future__ import annotations

import asyncio
import importlib
from collections import Counter

import pytest

from adlife.adapters.cognition.city_service import CityCognitionCancellation
from adlife.core.domain.city_cognition import MAX_CITY_COGNITION_USAGE_INTEGER
from adlife.core.domain.city_cognition_v2 import (
    CityProviderAttemptV2,
    CityProviderRepairContextV2,
    CityProviderUsageV2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition_v2 import (
    build_city_mock_result_v2,
    build_city_rules_result_v2,
    validate_city_cognition_evaluation_v2,
)
from tests.unit.cognition.test_city_cache_v2 import (
    _bypass,
    _cache_case,
    _case,
    _record,
    _storage,
)
from tests.unit.cognition.test_city_cache_v2 import (
    _module as _cache_module,
)


def _module():
    return importlib.import_module("adlife.adapters.cognition.city_service_v2")


def _valid(request, *, usage=None, **updates):
    result = build_city_rules_result_v2(request).model_copy(update=updates)
    return CityProviderAttemptV2(
        schema_version=2, status="valid", result=result, repair_context=None, usage=usage
    )


def _invalid(reason="invalid-schema", *, usage=None):
    return CityProviderAttemptV2(
        schema_version=2,
        status="invalid-result",
        result=None,
        repair_context=CityProviderRepairContextV2(
            schema_version=2,
            reason_code=reason,
            safe_excerpt="The answer did not satisfy the frozen result contract.",
        ),
        usage=usage,
    )


async def _success(request):
    await asyncio.sleep(0)
    return _valid(request)


class _Provider:
    def __init__(self, call=_success, *, repair=None, close=None):
        self._call = call
        self._repair = repair
        self._close = close
        self.calls = []
        self.repairs = []
        self.close_calls = 0

    async def call(self, request):
        self.calls.append(request.request_id)
        return await self._call(request)

    async def repair_call(self, request, *, context):
        self.repairs.append((request.request_id, context.reason_code))
        if self._repair is None:
            raise AssertionError("unexpected repair")
        return await self._repair(request, context)

    async def aclose(self):
        self.close_calls += 1
        if self._close is not None:
            await self._close()


class _Sleeps:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)
        await asyncio.sleep(0)


async def _resolve(module, case, *, provider=None, cache=None, cancellation=None, sleep=None):
    _, baseline, frozen, requests = case
    return await module.CityCognitionServiceV2(cache=cache, sleep=sleep).resolve_many(
        frozen,
        requests,
        baseline_evaluation=baseline,
        provider=provider,
        cancellation=CityCognitionCancellation() if cancellation is None else cancellation,
    )


def _assert_neutral(record, reason, attempts):
    assert record.source == "fallback"
    assert record.reason == reason
    assert record.attempt_count == attempts
    assert (
        record.result.affect,
        record.result.relevance,
        record.result.credibility,
        record.result.salience,
    ) == ("neutral", "medium", "medium", "medium")
    assert record.result.safety_flags == ()


async def test_service_returns_canonical_order_after_reverse_completion():
    module = _module()
    case = _case(agent_count=4)
    requests = case[3]
    active = 0
    maximum = 0
    completed = []
    entered = [asyncio.Event() for _ in requests]
    released = [asyncio.Event() for _ in requests]
    finished = [asyncio.Event() for _ in requests]

    async def reverse(request):
        nonlocal active, maximum
        index = requests.index(request)
        active += 1
        maximum = max(maximum, active)
        entered[index].set()
        await released[index].wait()
        completed.append(request.request_id)
        active -= 1
        finished[index].set()
        return _valid(request)

    provider = _Provider(reverse)
    task = asyncio.create_task(_resolve(module, case, provider=provider))
    # Questions start interleaved over the run; the first four are the first started.
    first = list(module._interleaved_order(len(requests))[:4])
    await asyncio.wait_for(asyncio.gather(*(entered[index].wait() for index in first)), 2)
    assert maximum == 4
    for index in reversed(first):
        released[index].set()
        await asyncio.wait_for(finished[index].wait(), 2)
    assert completed == [requests[index].request_id for index in reversed(first)]
    for event in released:
        event.set()
    evaluation = await asyncio.wait_for(task, 3)
    assert maximum == 4
    assert completed[:4] == [requests[index].request_id for index in reversed(first)]
    assert tuple(r.request_id for r in evaluation.records) == tuple(r.request_id for r in requests)
    assert tuple(r.selection_index for r in evaluation.records) == tuple(range(len(requests)))
    assert evaluation == validate_city_cognition_evaluation_v2(
        case[2], requests, evaluation.records, baseline_evaluation=case[1]
    )
    assert provider.close_calls == 1


@pytest.mark.parametrize("mode", ["rules", "mock"])
async def test_offline_modes_resolve_every_selection_without_cache_or_network(
    tmp_path, mode, monkeypatch
):
    module = _module()
    cache_module = _cache_module()
    cache, _ = _cache_case(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline effect")

    monkeypatch.setattr(cache_module.CityCognitionCacheV2, "make_key", forbidden)
    monkeypatch.setattr(cache_module.CityCognitionCacheV2, "get", forbidden)
    monkeypatch.setattr(cache_module.CityCognitionCacheV2, "put", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    case = _case(mode=mode, agent_count=4)
    evaluation = await _resolve(module, case, cache=cache)
    assert {r.source for r in evaluation.records} == {mode}
    assert all(r.funded and r.reserved_output_tokens == 0 for r in case[2].reservations)
    for request, record in zip(case[3], evaluation.records, strict=True):
        expected = (
            build_city_mock_result_v2(request, policy=case[2].causal_contract.mock_policy)
            if mode == "mock"
            else build_city_rules_result_v2(request)
        )
        assert record.result == expected
        assert record.attempt_count == 0
        assert record.prompt_tokens is None and record.completion_tokens is None


@pytest.mark.parametrize(
    "mutation",
    [
        "permuted",
        "duplicate",
        "mixed-run",
        "forged-request",
        "missing-reservation",
        "wrong-reservation",
        "unfunded-reservation",
        "wrong-baseline",
        "forged-baseline",
        "wrong-policy",
        "list-requests",
    ],
)
async def test_invalid_admission_refuses_before_any_cache_or_provider_access(
    tmp_path, monkeypatch, mutation
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    context, baseline, frozen, requests = _case()
    if mutation == "permuted":
        requests = tuple(reversed(requests))
    elif mutation == "duplicate":
        requests = (requests[0], *requests)
    elif mutation == "mixed-run":
        requests = (_case(run_hash="e" * 64)[3][0], *requests[1:])
    elif mutation == "forged-request":
        requests = (_bypass(requests[0], request_record_sha256="e" * 64), *requests[1:])
    elif mutation == "missing-reservation":
        frozen = _bypass(frozen, reservations=frozen.reservations[:-1])
    elif mutation == "wrong-reservation":
        frozen = _bypass(
            frozen,
            reservations=(
                _bypass(frozen.reservations[0], request_record_sha256="e" * 64),
                *frozen.reservations[1:],
            ),
        )
    elif mutation == "unfunded-reservation":
        frozen = _bypass(
            frozen,
            reservations=(
                _bypass(frozen.reservations[0], funded=False, reserved_output_tokens=0),
                *frozen.reservations[1:],
            ),
        )
    elif mutation == "wrong-baseline":
        baseline = _case(agent_count=4)[1]
    elif mutation == "forged-baseline":
        baseline = _bypass(baseline, records=baseline.records[1:])
    elif mutation == "wrong-policy":
        frozen = _bypass(frozen, causal_contract_sha256="e" * 64)
    else:
        requests = list(requests)

    def forbidden(*args, **kwargs):
        raise AssertionError("effect before admission")

    monkeypatch.setattr(type(cache), "make_key", forbidden)
    monkeypatch.setattr(type(cache), "get", forbidden)
    monkeypatch.setattr(type(cache), "put", forbidden)
    provider = _Provider()
    with pytest.raises((TypeError, ValueError)):
        await _resolve(
            module, (context, baseline, frozen, requests), provider=provider, cache=cache
        )
    assert provider.calls == []
    assert provider.close_calls == 1


async def test_funded_prefix_precedes_key_lookup_and_cache_hits_never_refund_budget(
    tmp_path, monkeypatch
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    case = _case(agent_count=4, limit=10)
    frozen, requests = case[2:]
    keys = []
    looked_up = []
    original_make_key = type(cache).make_key
    original_get = type(cache).get
    for request in requests[:10]:
        record = _record(request, frozen)
        cache.put(record.key, record)

    def key(request, cognition_input):
        keys.append(request.request_id)
        assert request in requests[:10]
        return original_make_key(request, cognition_input)

    def get(self, key):
        looked_up.append(key)
        return original_get(self, key)

    monkeypatch.setattr(type(cache), "make_key", staticmethod(key))
    monkeypatch.setattr(type(cache), "get", get)
    provider = _Provider()
    evaluation = await _resolve(module, case, provider=provider, cache=cache)
    assert keys == [r.request_id for r in requests[:10]]
    assert len(looked_up) == 10
    assert provider.calls == []
    assert [r.source for r in evaluation.records] == ["cache"] * 10 + ["budget-skipped"] * (
        len(requests) - 10
    )
    assert all(
        r.reason == "budget-skipped" and r.attempt_count == 0 and r.prompt_tokens is None
        for r in evaluation.records[10:]
    )


async def test_causal_cache_reuse_builds_fresh_current_run_terminal_bindings(tmp_path):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    first_case = _case(run_hash="b" * 64, limit=10)
    first = await _resolve(module, first_case, provider=_Provider(), cache=cache)
    second_case = _case(run_hash="e" * 64, limit=20)
    provider = _Provider()
    second = await _resolve(module, second_case, provider=provider, cache=cache)
    assert provider.calls == []
    assert {r.source for r in second.records} == {"cache"}
    assert first.cognition_input_sha256 != second.cognition_input_sha256
    assert second.run_input_sha256 == "e" * 64
    for index, (request, record) in enumerate(zip(second_case[3], second.records, strict=True)):
        assert record.selection_index == index
        assert record.request_record_sha256 == request.request_record_sha256
        assert record.request_record_sha256 != first.records[index].request_record_sha256
        assert record.run_input_sha256 == "e" * 64
        assert record.baseline_sha256 == request.causal_identity.baseline_sha256
        assert record.record_hash != first.records[index].record_hash
    assert second == validate_city_cognition_evaluation_v2(
        second_case[2], second_case[3], second.records, baseline_evaluation=second_case[1]
    )


@pytest.mark.parametrize("misuse", ["offline-provider", "invalid-cancellation", "missing-provider"])
async def test_operation_misuse_is_explicit_and_still_closes_owned_provider(misuse):
    module = _module()
    provider = _Provider()
    case = _case(mode="rules" if misuse == "offline-provider" else "local")
    cancellation = object() if misuse == "invalid-cancellation" else CityCognitionCancellation()
    with pytest.raises((TypeError, ValueError)):
        await _resolve(
            module,
            case,
            provider=None if misuse == "missing-provider" else provider,
            cancellation=cancellation,
        )
    assert provider.calls == []
    assert provider.close_calls == (0 if misuse == "missing-provider" else 1)


async def test_self_consistent_cache_record_for_other_material_is_not_admitted(
    tmp_path, monkeypatch
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    case = _case()
    other = _record(case[3][1], case[2])

    def get(self, key):
        assert key != other.key
        return other

    monkeypatch.setattr(type(cache), "get", get)
    provider = _Provider()
    with pytest.raises(_storage().CityCognitionCacheCorruption):
        await _resolve(module, case, provider=provider, cache=cache)
    assert provider.calls == []
    assert provider.close_calls == 1


@pytest.mark.parametrize(
    "name,reason,attempts",
    [
        ("CityProviderAuthenticationError", "authentication-refused", 1),
        ("CityProviderTimeoutError", "timeout-exhausted", 3),
        ("CityProviderRateLimitError", "rate-limit-exhausted", 3),
        ("CityProviderServerError", "server-error-exhausted", 3),
        ("CityProviderUnavailableError", "endpoint-resolution-refused", 3),
        ("CityProviderResponseTooLargeError", "response-too-large", 1),
        ("CityProviderProtocolError", "protocol-incompatible", 1),
    ],
)
async def test_each_approved_provider_failure_yields_exact_neutral_fallback(name, reason, attempts):
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")

    async def fail(request):
        raise getattr(errors, name)()

    provider = _Provider(fail)
    sleeps = _Sleeps()
    case = _case()
    evaluation = await _resolve(module, case, provider=provider, sleep=sleeps)
    for record in evaluation.records:
        _assert_neutral(record, reason, attempts)
    assert Counter(provider.calls) == {r.request_id: attempts for r in case[3]}
    assert Counter(sleeps.delays) == Counter(
        module._retry_delay_seconds(request.request_id, retry, None)
        for request in case[3]
        for retry in range(attempts - 1)
    )
    assert provider.close_calls == 1


def test_backoff_is_exponential_bounded_and_deterministic_per_request():
    module = _module()
    request_id = "cog-" + "a" * 64
    delays = [module._retry_delay_seconds(request_id, retry, None) for retry in range(6)]
    assert delays == [module._retry_delay_seconds(request_id, retry, None) for retry in range(6)]
    for retry, delay in enumerate(delays):
        base = min(2.0**retry, 8.0)
        assert 0.8 * base <= delay <= 1.2 * base
    # Jitter keeps simultaneous retries of different questions apart.
    assert delays != [module._retry_delay_seconds("cog-" + "b" * 64, r, None) for r in range(6)]
    # A server's own Retry-After hint wins, bounded to 30 seconds.
    assert module._retry_delay_seconds(request_id, 0, 7) == 7.0
    assert module._retry_delay_seconds(request_id, 3, 0) == 0.0
    assert module._retry_delay_seconds(request_id, 0, 120) == 30.0


@pytest.mark.parametrize(("hint", "waited"), [(7, 7.0), (120, 30.0)])
async def test_a_retry_after_hint_sets_the_wait_before_the_next_attempt(hint, waited):
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    counts = Counter()

    async def call(request):
        counts[request.request_id] += 1
        if counts[request.request_id] == 1:
            raise errors.CityProviderRateLimitError(retry_after_seconds=hint)
        return _valid(request)

    sleeps = _Sleeps()
    evaluation = await _resolve(module, _case(), provider=_Provider(call), sleep=sleeps)
    assert {record.source for record in evaluation.records} == {"provider"}
    assert {record.attempt_count for record in evaluation.records} == {2}
    assert sleeps.delays == [waited] * len(evaluation.records)


async def test_a_transient_error_during_a_repair_is_retried_from_the_shared_budget():
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    repairs = Counter()

    async def invalid(request):
        return _invalid("invalid-json")

    async def repair(request, context):
        repairs[request.request_id] += 1
        assert context.reason_code == "invalid-json"
        if repairs[request.request_id] == 1:
            raise errors.CityProviderTimeoutError()
        return _valid(request)

    sleeps = _Sleeps()
    provider = _Provider(invalid, repair=repair)
    evaluation = await _resolve(module, _case(), provider=provider, sleep=sleeps)
    for record in evaluation.records:
        assert record.source == "provider"
        assert record.attempt_count == 3
    assert set(repairs.values()) == {2}
    assert len(sleeps.delays) == len(evaluation.records)


async def test_a_wait_that_would_pass_the_aggregate_deadline_falls_back_at_once(monkeypatch):
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    case = _case()
    clock = {"now": 1000.0}
    monkeypatch.setattr(module, "_loop_time", lambda: clock["now"])

    async def call(request):
        # The run's whole-batch deadline is now only five seconds away.
        clock["now"] = 1000.0 + case[2].aggregate_deadline_seconds - 5
        raise errors.CityProviderRateLimitError(retry_after_seconds=20)

    sleeps = _Sleeps()
    evaluation = await _resolve(module, case, provider=_Provider(call), sleep=sleeps)
    for record in evaluation.records:
        _assert_neutral(record, "rate-limit-exhausted", 1)
    assert sleeps.delays == []


def _v3_case(*, mode="local", limit=60, profile_updates=None):
    """A run under selection policy V3, whose ceilings allow 3 retries and 2 repairs."""
    from adlife.core.domain.city_cognition import (
        CityCognitionProviderProfileSnapshotV1,
        city_cognition_provider_profile_revision_sha256,
    )
    from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3
    from adlife.core.simulation.city_cognition_v2 import (
        build_city_cognition_input_v2,
        build_city_cognition_requests_v2,
    )
    from tests.unit.city.test_city_cognition_v2_contract import profile_data
    from tests.unit.cognition.test_city_cache_v2 import _evidence

    context, mobility, baseline = _evidence(1)
    contract = CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V3
    requests = build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=contract, funded_response_limit=limit
    )
    facts = profile_data(mode)
    facts.pop("revision_sha256")
    facts.update(profile_updates or {})
    profile = CityCognitionProviderProfileSnapshotV1.model_validate(
        facts | {"revision_sha256": city_cognition_provider_profile_revision_sha256(facts)}
    )
    frozen = build_city_cognition_input_v2(
        context,
        requests,
        mode=mode,
        funded_response_limit=limit,
        provider_profile=profile,
        causal_contract=contract,
    )
    return context, baseline, frozen, requests


async def test_a_v3_run_gives_an_invalid_answer_a_second_repair():
    module = _module()
    repairs = Counter()

    async def invalid(request):
        return _invalid("invalid-json")

    async def repair(request, context):
        repairs[request.request_id] += 1
        if repairs[request.request_id] == 1:
            return _invalid("invalid-schema")
        assert context.reason_code == "invalid-schema"
        return _valid(request)

    case = _v3_case(profile_updates={"repair_attempts": 2})
    evaluation = await _resolve(module, case, provider=_Provider(invalid, repair=repair))
    assert evaluation.records
    for record in evaluation.records:
        assert record.source == "provider"
        assert record.attempt_count == 3
    assert set(repairs.values()) == {2}


async def test_a_v3_run_retries_a_transient_failure_three_times():
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    counts = Counter()

    async def call(request):
        counts[request.request_id] += 1
        if counts[request.request_id] <= 3:
            raise errors.CityProviderServerError()
        return _valid(request)

    sleeps = _Sleeps()
    case = _v3_case(profile_updates={"transient_retries": 3})
    evaluation = await _resolve(module, case, provider=_Provider(call), sleep=sleeps)
    for record in evaluation.records:
        assert record.source == "provider"
        assert record.attempt_count == 4
    assert len(sleeps.delays) == 3 * len(evaluation.records)


def test_interleaved_order_samples_the_whole_run_early():
    module = _module()
    assert module._interleaved_order(0) == ()
    assert module._interleaved_order(1) == (0,)
    assert module._interleaved_order(4) == (0, 2, 1, 3)
    assert module._interleaved_order(8) == (0, 4, 2, 6, 1, 5, 3, 7)
    for size in range(1, 70):
        order = module._interleaved_order(size)
        assert sorted(order) == list(range(size))
        if size >= 4:
            # The first half already reaches into the second half of the run.
            assert max(order[: (size + 1) // 2]) >= size // 2


async def test_provider_calls_start_interleaved_while_records_stay_in_request_order():
    module = _module()
    provider = _Provider()
    case = _case(agent_count=4)
    evaluation = await _resolve(module, case, provider=provider)
    requests = case[3]
    order = module._interleaved_order(len(requests))
    assert provider.calls == [requests[index].request_id for index in order]
    assert [record.request_id for record in evaluation.records] == [
        request.request_id for request in requests
    ]


@pytest.mark.parametrize(
    "reason", ["invalid-json", "invalid-schema", "request-id-mismatch", "unsafe-content"]
)
@pytest.mark.parametrize("repair_succeeds", [False, True])
async def test_exactly_one_repair_for_approved_invalid_result_reasons(reason, repair_succeeds):
    module = _module()
    usage = CityProviderUsageV2(schema_version=2, prompt_tokens=7, completion_tokens=2)

    async def invalid(request):
        return _invalid(reason, usage=usage)

    async def repair(request, context):
        assert context.reason_code == reason
        return _valid(request, usage=usage) if repair_succeeds else _invalid(reason, usage=usage)

    provider = _Provider(invalid, repair=repair)
    evaluation = await _resolve(module, _case(), provider=provider)
    should_repair = reason != "unsafe-content"
    assert len(provider.repairs) == (len(evaluation.records) if should_repair else 0)
    for record in evaluation.records:
        if should_repair and repair_succeeds:
            assert record.source == "provider"
            assert record.attempt_count == 2
        else:
            _assert_neutral(record, "invalid-result", 2 if should_repair else 1)
        assert (record.prompt_tokens, record.completion_tokens) == (
            (14, 4) if should_repair else (7, 2)
        )


async def test_two_transient_retries_then_one_repair_never_exceeds_four_calls():
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    counts = Counter()

    async def call(request):
        counts[request.request_id] += 1
        if counts[request.request_id] < 3:
            raise errors.CityProviderServerError()
        return _invalid()

    async def repair(request, context):
        raise errors.CityProviderServerError()

    provider = _Provider(call, repair=repair)
    evaluation = await _resolve(module, _case(), provider=provider, sleep=_Sleeps())
    for record in evaluation.records:
        _assert_neutral(record, "server-error-exhausted", 4)
    assert set(counts.values()) == {3}
    assert len(provider.repairs) == len(evaluation.records)


@pytest.mark.parametrize("limited", ["transient", "repair"])
async def test_frozen_profile_can_further_restrict_retry_and_repair(limited):
    module = _module()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    updates = {"transient_retries": 1} if limited == "transient" else {"repair_attempts": 0}

    async def call(request):
        if limited == "transient":
            raise errors.CityProviderServerError()
        return _invalid()

    provider = _Provider(call)
    evaluation = await _resolve(
        module, _case(profile_updates=updates), provider=provider, sleep=_Sleeps()
    )
    for record in evaluation.records:
        _assert_neutral(
            record,
            "server-error-exhausted" if limited == "transient" else "invalid-result",
            2 if limited == "transient" else 1,
        )
    assert provider.repairs == []


@pytest.mark.parametrize("repair_wrong", [False, True])
async def test_valid_v2_wrong_request_id_uses_bounded_repair_and_invalid_result_fallback(
    repair_wrong,
):
    module = _module()

    async def wrong(request):
        return _valid(request, request_id="cog-" + "e" * 64)

    async def repair(request, context):
        assert context.reason_code == "request-id-mismatch"
        return await wrong(request) if repair_wrong else _valid(request)

    provider = _Provider(wrong, repair=repair)
    evaluation = await _resolve(module, _case(), provider=provider)
    for record in evaluation.records:
        if repair_wrong:
            _assert_neutral(record, "invalid-result", 2)
        else:
            assert record.source == "provider" and record.attempt_count == 2


@pytest.mark.parametrize(
    "kind",
    [
        "v1-attempt",
        "v1-result",
        "v1-usage",
        "v1-context",
        "forged-result",
        "forged-usage",
        "wrong-schema",
    ],
)
@pytest.mark.parametrize("at_repair", [False, True])
async def test_wrong_version_and_forged_attempt_models_are_defects_not_fallback(
    tmp_path, kind, at_repair
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    from tests.unit.cognition.test_city_service import _invalid_attempt, _valid_attempt

    def bad(request):
        old = _valid_attempt(request, prompt_tokens=1, completion_tokens=1)
        if kind == "v1-attempt":
            return old
        if kind == "v1-result":
            return _bypass(_valid(request), result=old.result)
        if kind == "v1-usage":
            return _bypass(_valid(request), usage=old.usage)
        if kind == "v1-context":
            return _bypass(_invalid(), repair_context=_invalid_attempt().repair_context)
        if kind == "forged-result":
            return _bypass(
                _valid(request), result=_bypass(_valid(request).result, affect="invented")
            )
        if kind == "forged-usage":
            return _bypass(
                _valid(request),
                usage=CityProviderUsageV2.model_construct(
                    schema_version=2, prompt_tokens=True, completion_tokens=1
                ),
            )
        return _bypass(_valid(request), schema_version=1)

    async def call(request):
        return _invalid() if at_repair else bad(request)

    async def repair(request, context):
        return bad(request)

    provider = _Provider(call, repair=repair)
    with pytest.raises((TypeError, ValueError)):
        await _resolve(module, _case(), provider=provider, cache=cache)
    assert provider.close_calls == 1
    assert not (cache.workspace.root / "city-cognition-cache-v2").exists()


async def test_usage_overflow_is_protocol_fallback_without_partial_usage():
    module = _module()
    usage = CityProviderUsageV2(
        schema_version=2, prompt_tokens=MAX_CITY_COGNITION_USAGE_INTEGER, completion_tokens=1
    )

    async def call(request):
        return _invalid(usage=usage)

    async def repair(request, context):
        return _valid(request, usage=usage)

    evaluation = await _resolve(module, _case(), provider=_Provider(call, repair=repair))
    for record in evaluation.records:
        _assert_neutral(record, "protocol-incompatible", 2)
        assert record.prompt_tokens is None and record.completion_tokens is None


@pytest.mark.parametrize("field", ["affect", "safety_flags"])
async def test_valid_uncertain_and_flagged_raw_vectors_remain_cacheable(tmp_path, field):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    changes = {field: "uncertain" if field == "affect" else ("unsafe-content",)}

    async def call(request):
        return _valid(request, **changes)

    case = _case()
    first = await _resolve(module, case, provider=_Provider(call), cache=cache)
    second = await _resolve(module, case, provider=_Provider(), cache=cache)
    assert {r.source for r in first.records} == {"provider"}
    assert {r.source for r in second.records} == {"cache"}
    assert all(getattr(r.result, field) == changes[field] for r in second.records)


@pytest.mark.parametrize("stage", ["pre-cancelled", "io", "backoff", "repair", "queued", "closing"])
@pytest.mark.parametrize("external", [False, True])
async def test_cancellation_raises_and_closes_provider_without_publication(
    tmp_path, stage, external
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    cancellation = CityCognitionCancellation()
    entered = asyncio.Event()
    close_release = asyncio.Event()
    errors = importlib.import_module("adlife.adapters.cognition.city_provider_transport")

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    async def call(request):
        if stage == "backoff":
            raise errors.CityProviderServerError()
        if stage == "repair":
            return _invalid()
        if stage == "closing":
            return _valid(request)
        await blocked()
        raise AssertionError("unreachable")

    async def repair(request, context):
        await blocked()
        raise AssertionError("unreachable")

    async def sleep(delay):
        await blocked()

    async def close():
        entered.set()
        await close_release.wait()

    provider = _Provider(call, repair=repair, close=close if stage == "closing" else None)
    case = _case(agent_count=4 if stage == "queued" else 1)
    if stage == "pre-cancelled":
        cancellation.cancel()
    task = asyncio.create_task(
        _resolve(
            module, case, provider=provider, cache=cache, cancellation=cancellation, sleep=sleep
        )
    )
    if stage != "pre-cancelled":
        await asyncio.wait_for(entered.wait(), 2)
        if external:
            task.cancel()
        else:
            cancellation.cancel()
        if stage == "closing":
            close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert provider.close_calls == 1
    assert not (cache.workspace.root / "city-cognition-cache-v2").exists()
    if stage == "pre-cancelled":
        assert provider.calls == []
    if stage == "queued":
        assert len(provider.calls) <= 4


@pytest.mark.parametrize(
    "kind", ["attempt", "attempt-late-success", "batch", "late-success", "cleanup-defect"]
)
async def test_frozen_deadlines_preserve_actual_attempts_and_refuse_late_results(
    tmp_path, monkeypatch, kind
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    case = _case(
        agent_count=4, profile_updates={"attempt_timeout_seconds": 1, "transient_retries": 0}
    )
    real_timeout = asyncio.timeout
    seen = []
    target = 1 if kind in {"attempt", "attempt-late-success"} else 180

    def short(delay):
        seen.append(delay)
        return real_timeout(0.02 if delay == target else delay)

    monkeypatch.setattr(module.asyncio, "timeout", short)

    async def blocked(request):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if kind in {"late-success", "attempt-late-success"}:
                return _valid(request)
            if kind == "cleanup-defect":
                raise TypeError("worker cleanup defect") from None
            raise
        raise AssertionError("unreachable")

    provider = _Provider(blocked)
    if kind == "cleanup-defect":
        with pytest.raises(TypeError, match="worker cleanup defect"):
            await _resolve(module, case, provider=provider, cache=cache)
    else:
        evaluation = await _resolve(module, case, provider=provider, cache=cache)
        assert len(evaluation.records) == len(case[3])
        for record in evaluation.records:
            _assert_neutral(record, "timeout-exhausted", record.attempt_count)
        if target == 180:
            assert sum(r.attempt_count for r in evaluation.records) <= 4
            assert any(r.attempt_count == 0 for r in evaluation.records)
    assert target in seen
    assert provider.close_calls == 1
    assert not (cache.workspace.root / "city-cognition-cache-v2").exists()


@pytest.mark.parametrize("failure", ["provider", "close", "corrupt-cache", "publication"])
async def test_defects_and_cache_failures_propagate_with_owned_provider_closed(
    tmp_path, monkeypatch, failure
):
    module = _module()
    cache, _ = _cache_case(tmp_path)

    async def broken(request):
        raise TypeError("provider defect")

    async def close():
        raise TypeError("close defect")

    provider = _Provider(
        broken if failure == "provider" else _success, close=close if failure == "close" else None
    )
    expected = TypeError
    if failure == "corrupt-cache":
        expected = _storage().CityCognitionCacheCorruption

        def get(self, key):
            raise expected()

        monkeypatch.setattr(type(cache), "get", get)
    elif failure == "publication":
        expected = _storage().CityCognitionCachePublicationError

        def put(self, key, record):
            assert provider.close_calls == 1
            raise expected(potentially_committed=True)

        monkeypatch.setattr(type(cache), "put", put)
    with pytest.raises(expected):
        await _resolve(module, _case(), provider=provider, cache=cache)
    assert provider.close_calls == 1
    if failure == "corrupt-cache":
        assert provider.calls == []


@pytest.mark.parametrize("reject", [False, True])
async def test_cache_publication_follows_complete_baseline_bound_evaluation_validation(
    tmp_path, monkeypatch, reject
):
    module = _module()
    cache, _ = _cache_case(tmp_path)
    case = _case()
    provider = _Provider()
    validated = False
    published = []
    real_validator = module.validate_city_cognition_evaluation_v2
    real_put = type(cache).put

    def validate(frozen, requests, records, *, baseline_evaluation):
        nonlocal validated
        assert baseline_evaluation == case[1]
        evaluation = real_validator(
            frozen, requests, records, baseline_evaluation=baseline_evaluation
        )
        if records and all(r.source == "provider" for r in records):
            assert provider.close_calls == 1
            assert len(records) == len(case[3])
            if reject:
                raise ValueError("final evaluation refused")
            validated = True
        return evaluation

    def put(self, key, record):
        assert validated
        assert provider.close_calls == 1
        published.append(record.request_id)
        real_put(self, key, record)

    monkeypatch.setattr(module, "validate_city_cognition_evaluation_v2", validate)
    monkeypatch.setattr(type(cache), "put", put)
    if reject:
        with pytest.raises(ValueError, match="final evaluation refused"):
            await _resolve(module, case, provider=provider, cache=cache)
        assert published == []
    else:
        await _resolve(module, case, provider=provider, cache=cache)
        assert published == [r.request_id for r in case[3]]


@pytest.mark.parametrize("stage", ["before-publication", "after-first-publication"])
async def test_cancellation_at_cache_publication_cannot_return_success(
    tmp_path, monkeypatch, stage
):
    module = _module()
    cache, _ = _cache_case(tmp_path)

    class PublicationCancellation(CityCognitionCancellation):
        def _run_if_active(self, operation):
            outcome = super()._run_if_active(operation)
            if stage == "after-first-publication":
                self.cancel()
            return outcome

    cancellation = PublicationCancellation()
    validator = module.validate_city_cognition_evaluation_v2

    def validate(frozen, requests, records, *, baseline_evaluation):
        result = validator(frozen, requests, records, baseline_evaluation=baseline_evaluation)
        if stage == "before-publication" and records and records[0].source == "provider":
            cancellation.cancel()
        return result

    monkeypatch.setattr(module, "validate_city_cognition_evaluation_v2", validate)
    provider = _Provider()
    with pytest.raises(asyncio.CancelledError):
        await _resolve(module, _case(), provider=provider, cache=cache, cancellation=cancellation)
    assert provider.close_calls == 1
    directory = cache.workspace.root / "city-cognition-cache-v2"
    assert len(tuple(directory.glob("*.json"))) == (1 if stage == "after-first-publication" else 0)


async def test_real_provider_close_clears_owned_credential_even_when_service_is_precancelled():
    module = _module()
    from tests.unit.cognition.test_city_openai_compatible_v2 import _provider

    _, frozen, provider, caller_secret, _, factory = _provider()
    # Preserve the provider fixture's frozen input and complete matching baseline selections.
    from adlife.core.simulation.city_cognition_v2 import build_city_cognition_requests_v2
    from tests.unit.cognition.test_city_prompts_v2 import _evidence as provider_evidence

    context, mobility, baseline = provider_evidence()
    requests = build_city_cognition_requests_v2(context, mobility, baseline)
    cancellation = CityCognitionCancellation()
    cancellation.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await module.CityCognitionServiceV2().resolve_many(
                frozen,
                requests,
                baseline_evaluation=baseline,
                provider=provider,
                cancellation=cancellation,
            )
        assert provider.is_closed
        assert provider._transport._credential.cleared
        assert factory.backends == []
        assert not caller_secret.cleared
        assert "opaque" not in canonical_json(frozen)
    finally:
        caller_secret.clear()
