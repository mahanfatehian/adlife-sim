"""The in-memory V10 pipeline preserves rule authority and binds all V2 evidence."""

from __future__ import annotations

import asyncio
import importlib
import warnings
from dataclasses import replace
from hashlib import sha256

import pytest

from adlife.adapters.cognition.city_service import CityCognitionCancellation
from adlife.city import runs
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionRequestV2,
    CityProviderAttemptV2,
    CityProviderRepairContextV2,
)
from adlife.core.domain.city_run import CityRunManifestV10, build_city_cognition_manifest_receipt_v2
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition_v2 import (
    build_city_budget_skipped_result_v2,
    build_city_cognition_terminal_record_v2,
    build_city_mock_result_v2,
    build_city_rules_result_v2,
    city_cognition_terminal_lines_v2,
    validate_city_cognition_evaluation_v2,
)
from adlife.core.simulation.spatial_response_v2 import (
    spatial_cognition_state_document_v2,
    spatial_response_state_document_v2,
    spatial_response_v2_lines,
    spatial_rule_state_document_v2,
)
from tests.unit.city.test_city_run_preparation import (
    _bypass_v2,
    _provider_revision,
    _v2_arguments,
    _v2_prepare_api,
    _validated_v4,
)


def _prepare(validated, *, profile=None):
    return _v2_prepare_api()(validated.pack, provider_profile=profile, **_v2_arguments(validated))


def _finalize(prepared, evaluation, *, cognition_input=None):
    finalize = getattr(runs, "finalize_city_run_v2", None)
    assert finalize is not None, "finalize_city_run_v2 is not implemented"
    return finalize(
        prepared,
        cognition_input=prepared.cognition_input if cognition_input is None else cognition_input,
        cognition_evaluation=evaluation,
    )


def _terminal_evaluation(prepared):
    """Real core evidence for finalizer tests independent of the async service."""
    records = []
    frozen = prepared.cognition_input
    for index, request in enumerate(prepared.cognition_requests):
        reservation = frozen.reservations[index]
        source = frozen.mode
        reason = None
        attempts = 0
        if not reservation.funded:
            source, reason = "budget-skipped", "budget-skipped"
            result = build_city_budget_skipped_result_v2(request)
        elif source == "mock":
            result = build_city_mock_result_v2(request, policy=frozen.causal_contract.mock_policy)
        elif source == "rules":
            result = build_city_rules_result_v2(request)
        else:
            source, attempts = "provider", 1
            result = build_city_rules_result_v2(request).model_copy(
                update={
                    "affect": "positive",
                    "relevance": "high",
                    "credibility": "high",
                    "salience": "high",
                }
            )
        records.append(
            build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=index,
                source=source,
                result=result,
                attempt_count=attempts,
                reason=reason,
            )
        )
    return validate_city_cognition_evaluation_v2(
        frozen,
        prepared.cognition_requests,
        tuple(records),
        baseline_evaluation=prepared.baseline_evaluation,
    )


async def _resolve(prepared, *, provider=None):
    module = importlib.import_module("adlife.adapters.cognition.city_service_v2")
    return await module.CityCognitionServiceV2().resolve_many(
        prepared.cognition_input,
        prepared.cognition_requests,
        baseline_evaluation=prepared.baseline_evaluation,
        provider=provider,
        cancellation=CityCognitionCancellation(),
    )


class _SignalProvider:
    """Typed deterministic provider, optionally reversing each concurrent batch."""

    def __init__(self, requests: tuple[CityCognitionRequestV2, ...], *, reverse: bool = False):
        self.requests = requests
        self.reverse = reverse
        self.completed: list[str] = []
        self.closed = False
        self.entered = [asyncio.Event() for _ in requests]
        self.release = [asyncio.Event() for _ in requests]
        self.finished = [asyncio.Event() for _ in requests]

    async def call(self, request: CityCognitionRequestV2) -> CityProviderAttemptV2:
        index = self.requests.index(request)
        if self.reverse:
            self.entered[index].set()
            await self.release[index].wait()
        self.completed.append(request.request_id)
        self.finished[index].set()
        return CityProviderAttemptV2(
            schema_version=2,
            status="valid",
            repair_context=None,
            usage=None,
            result=build_city_rules_result_v2(request).model_copy(
                update={
                    "affect": "positive",
                    "relevance": "high",
                    "credibility": "high",
                    "salience": "high",
                }
            ),
        )

    async def repair_call(
        self,
        request: CityCognitionRequestV2,
        *,
        context: CityProviderRepairContextV2,
    ) -> CityProviderAttemptV2:
        raise AssertionError("valid typed provider output must not require repair")

    async def aclose(self) -> None:
        self.closed = True


def _assert_rule_identity(prepared, result):
    baseline = prepared.baseline_evaluation
    applied = result.response_evaluation
    expected = tuple(r for r in baseline.records if r.event_type == "spatial.response")
    observed = tuple(
        r.baseline for r in applied.records if r.event_type == "spatial.applied-response"
    )
    assert tuple(map(canonical_json, observed)) == tuple(map(canonical_json, expected))
    assert tuple(map(canonical_json, applied.final_rule_states)) == tuple(
        map(canonical_json, baseline.final_states)
    )
    updates = tuple(r for r in applied.records if r.event_type == "spatial.dual-state-updated")
    old_updates = tuple(r for r in baseline.records if r.event_type == "spatial.state-updated")
    for current, previous in zip(updates, old_updates, strict=True):
        assert canonical_json(current.previous_rule_state) == canonical_json(
            previous.previous_state
        )
        assert canonical_json(current.rule_state) == canonical_json(previous.state)
        assert current.rule_state.purchase_intention == previous.state.purchase_intention
        assert current.cognition_state.response_count == current.rule_state.response_count
        assert (
            current.cognition_state.last_response_minute == current.rule_state.last_response_minute
        )
    for state in applied.final_cognition_states:
        assert -1 <= state.synthetic_sentiment <= 1 and 0 <= state.synthetic_recall <= 1
        assert not hasattr(state, "purchase_intention")
    assert result.mobility is prepared.mobility
    assert result.opportunity_evaluation == prepared.opportunity_evaluation
    assert result.attention_evaluation == prepared.attention_evaluation
    assert result.response_input == prepared.response_input


def _document_hash(document):
    return sha256((canonical_json(document) + "\n").encode()).hexdigest()


def _assert_manifest(prepared, evaluation, result):
    from adlife.core.simulation.city_trace import summarize_city_trace
    from adlife.core.simulation.spatial_attention import (
        spatial_attention_lines,
        summarize_spatial_attention_artifact,
    )
    from adlife.core.simulation.spatial_opportunity import (
        spatial_opportunity_lines,
        summarize_spatial_opportunity_artifact,
    )

    manifest = result.manifest
    response = result.response_evaluation
    counts = response.counts
    coverage = evaluation.summary.coverage
    assert type(manifest) is CityRunManifestV10
    assert manifest.schema_version == 10 and manifest.workbench_input_schema_version == 4
    assert manifest.spatial_response_schema_version == 2
    assert manifest.spatial_response_model_id == "spatial-response-v2"
    assert manifest.workbench_input_sha256 == prepared.workbench_input.fingerprint
    assert manifest.city_sha256 == prepared.pack.fingerprint
    assert manifest.scenario_sha256 == prepared.spatial_scenario.fingerprint
    trace = summarize_city_trace(prepared.mobility)
    assert (
        manifest.agents_sha256,
        manifest.trace_sha256,
        manifest.frame_count,
        manifest.position_count,
    ) == (
        trace.agents_sha256,
        trace.trace_sha256,
        trace.frame_count,
        trace.position_count,
    )
    for prefix, evaluation_object, lines, summarize in (
        (
            "opportunity",
            prepared.opportunity_evaluation,
            spatial_opportunity_lines,
            summarize_spatial_opportunity_artifact,
        ),
        (
            "attention",
            prepared.attention_evaluation,
            spatial_attention_lines,
            summarize_spatial_attention_artifact,
        ),
    ):
        stream = b"".join(lines(evaluation_object))
        assert getattr(manifest, prefix + "_stream_sha256") == sha256(stream).hexdigest()
        assert getattr(manifest, prefix + "_stream_bytes") == len(stream)
        assert getattr(manifest, prefix + "_summary_sha256") == _document_hash(
            summarize(evaluation_object)
        )
    assert manifest.opportunity_count == prepared.opportunity_evaluation.counts.opportunity_count
    assert manifest.impression_count == prepared.attention_evaluation.counts.impression_count
    assert manifest.noticed_count == prepared.attention_evaluation.counts.noticed_count
    assert manifest.response_input_sha256 == prepared.response_input.fingerprint
    assert manifest.influence_policy_id == "city-cognition-influence-v1"
    assert (
        manifest.influence_policy_sha256
        == prepared.cognition_input.causal_contract.influence_policy_sha256
    )
    assert (
        manifest.response_count
        == manifest.baseline_response_count
        == manifest.applied_response_count
    )
    assert (
        manifest.response_count == counts.baseline_response_count == counts.applied_response_count
    )
    assert manifest.response_count == coverage.eligible_response_count
    assert (
        manifest.state_update_count
        == manifest.dual_state_update_count
        == counts.dual_state_update_count
    )
    assert manifest.final_state_count == counts.final_rule_state_count
    assert (
        manifest.final_cognition_state_count
        == counts.final_cognition_state_count
        == manifest.final_state_count
    )
    assert manifest.response_campaign_count == counts.campaign_count
    assert (
        coverage.eligible_response_count
        == coverage.selected_request_count + coverage.unselected_response_count
    )
    assert (
        coverage.selected_request_count
        == coverage.funded_request_count + coverage.budget_skipped_count
    )
    assert (
        coverage.selected_request_count
        == len(evaluation.records)
        == len(prepared.cognition_input.reservations)
    )
    stream = b"".join(spatial_response_v2_lines(response))
    assert manifest.response_stream_bytes == len(stream)
    assert manifest.response_stream_sha256 == sha256(stream).hexdigest()
    assert manifest.response_state_sha256 == _document_hash(
        spatial_response_state_document_v2(response)
    )
    assert manifest.rule_state_document_sha256 == _document_hash(
        spatial_rule_state_document_v2(response)
    )
    assert manifest.cognition_state_document_sha256 == _document_hash(
        spatial_cognition_state_document_v2(response)
    )
    from adlife.core.simulation.spatial_response_v2 import summarize_spatial_response_artifact_v2

    assert manifest.response_summary_sha256 == _document_hash(
        summarize_spatial_response_artifact_v2(response)
    )
    assert manifest.cognition == build_city_cognition_manifest_receipt_v2(
        prepared.cognition_input, evaluation
    )
    terminal_stream = b"".join(city_cognition_terminal_lines_v2(evaluation))
    assert manifest.cognition.terminal_stream_sha256 == sha256(terminal_stream).hexdigest()
    assert manifest.cognition.terminal_stream_bytes == len(terminal_stream)
    assert manifest.cognition.cognition_input_sha256 == prepared.cognition_input.fingerprint
    assert manifest.cognition.cognition_input_bytes == len(
        (canonical_json(prepared.cognition_input) + "\n").encode()
    )
    assert manifest.cognition.summary_sha256 == _document_hash(evaluation.summary)
    assert manifest.cognition.terminal_record_count == len(evaluation.records)
    frozen = prepared.cognition_input
    contract = frozen.causal_contract
    assert manifest.cognition.causal_contract_sha256 == frozen.causal_contract_sha256
    assert manifest.cognition.funded_response_limit == frozen.funded_response_limit
    assert (
        manifest.cognition.budget_allocation_sha256
        == sha256(
            canonical_json(
                {
                    "aggregate_output_token_ceiling": frozen.aggregate_output_token_ceiling,
                    "reservations": [r.model_dump(mode="json") for r in frozen.reservations],
                }
            ).encode()
        ).hexdigest()
    )
    assert manifest.cognition.provider_profile_snapshot_sha256 == (
        None if frozen.provider_profile is None else frozen.provider_profile.fingerprint
    )
    assert manifest.cognition.provider_revision_sha256 == (
        None if frozen.provider_profile is None else frozen.provider_profile.revision_sha256
    )
    assert manifest.cognition.effective_settings_sha256 == (
        sha256(canonical_json({"effective_settings": None}).encode()).hexdigest()
        if frozen.effective_settings is None
        else frozen.effective_settings.fingerprint
    )
    for name in (
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "mock_policy_sha256",
        "influence_policy_sha256",
    ):
        assert getattr(manifest.cognition, name) == getattr(contract, name)
    for name in (
        "coverage",
        "outcomes",
        "reasons",
        "neutralization",
        "attempt_count",
        "prompt_tokens",
        "completion_tokens",
    ):
        assert getattr(manifest.cognition, name) == getattr(evaluation.summary, name)
    assert (
        result.cognition_evaluation == evaluation
        and result.cognition_input == prepared.cognition_input
    )
    for field in ("directory", "path", "root"):
        assert not hasattr(result, field)


async def test_v10_provider_pipeline_changes_only_cognition_state(tmp_path):
    _v2_prepare_api()
    profile = _provider_revision(tmp_path)
    prepared = _prepare(_validated_v4(mode="local", profile=profile), profile=profile)
    before = canonical_json(prepared.baseline_evaluation)
    frozen_before = canonical_json(prepared.cognition_input)
    provider = _SignalProvider(prepared.cognition_requests)
    evaluation = await _resolve(prepared, provider=provider)
    result = _finalize(prepared, evaluation)
    _assert_rule_identity(prepared, result)
    _assert_manifest(prepared, evaluation, result)
    assert canonical_json(prepared.baseline_evaluation) == before
    assert canonical_json(prepared.cognition_input) == frozen_before
    assert provider.closed
    assert evaluation.summary.outcomes.provider > 0
    assert any(
        cognitive.synthetic_sentiment != rule.brand_sentiment
        or cognitive.synthetic_recall != rule.recall_strength
        for cognitive, rule in zip(
            result.response_evaluation.final_cognition_states,
            result.response_evaluation.final_rule_states,
            strict=True,
        )
    )


@pytest.mark.parametrize("mode", ["rules", "mock"])
async def test_v10_offline_pipeline_resolves_locally_and_preserves_rule_authority(
    mode, monkeypatch
):
    _v2_prepare_api()
    prepared = _prepare(_validated_v4(mode=mode))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline pipeline consulted a network")

    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    evaluation = await _resolve(prepared)
    result = _finalize(prepared, evaluation)
    _assert_rule_identity(prepared, result)
    _assert_manifest(prepared, evaluation, result)
    assert {record.source for record in evaluation.records} == {mode}
    for request, record in zip(prepared.cognition_requests, evaluation.records, strict=True):
        expected = (
            build_city_rules_result_v2(request)
            if mode == "rules"
            else build_city_mock_result_v2(request)
        )
        assert record.result == expected
    if mode == "rules":
        assert all(
            (c.synthetic_sentiment, c.synthetic_recall) == (r.brand_sentiment, r.recall_strength)
            for c, r in zip(
                result.response_evaluation.final_cognition_states,
                result.response_evaluation.final_rule_states,
                strict=True,
            )
        )


async def test_v10_reverse_completion_and_source_input_permutations_are_identical(tmp_path):
    _v2_prepare_api()
    profile = _provider_revision(tmp_path)
    source = _validated_v4(mode="local", profile=profile)

    def permute(document):
        document["population"]["personas"].reverse()
        document["population"]["personas"][0]["interests"].reverse()
        document["scenario"]["phone"]["eligible_activities"].reverse()

    permuted = _validated_v4(mode="local", profile=profile, mutation=permute)
    first, second = _prepare(source, profile=profile), _prepare(permuted, profile=profile)
    assert first.workbench_input == second.workbench_input
    assert first.cognition_requests == second.cognition_requests
    forward_provider = _SignalProvider(first.cognition_requests)
    reverse_provider = _SignalProvider(second.cognition_requests, reverse=True)
    forward = await _resolve(first, provider=forward_provider)
    task = asyncio.create_task(_resolve(second, provider=reverse_provider))
    await asyncio.wait_for(
        asyncio.gather(*(event.wait() for event in reverse_provider.entered[:4])),
        5,
    )
    for index in (3, 2, 1, 0):
        reverse_provider.release[index].set()
        await asyncio.wait_for(reverse_provider.finished[index].wait(), 5)
    for event in reverse_provider.release:
        event.set()
    reverse = await asyncio.wait_for(task, 5)
    assert reverse_provider.completed[:4] == [
        r.request_id for r in reversed(second.cognition_requests[:4])
    ]
    assert canonical_json(forward) == canonical_json(reverse)
    a, b = _finalize(first, forward), _finalize(second, reverse)
    assert canonical_json(a.manifest) == canonical_json(b.manifest)
    assert tuple(spatial_response_v2_lines(a.response_evaluation)) == tuple(
        spatial_response_v2_lines(b.response_evaluation)
    )


def test_v10_finalization_maps_all_counts_hashes_and_receipt_without_a_path():
    _v2_prepare_api()
    prepared = _prepare(_validated_v4())
    evaluation = _terminal_evaluation(prepared)
    result = _finalize(prepared, evaluation)
    _assert_rule_identity(prepared, result)
    _assert_manifest(prepared, evaluation, result)


def test_v10_publication_persists_verified_frozen_evidence(tmp_path):
    prepared = _prepare(_validated_v4())
    result = _finalize(prepared, _terminal_evaluation(prepared))

    stored = runs.publish_city_run(result, root=tmp_path)
    loaded = runs.CityRunStore(tmp_path).load(result.manifest.run_id)
    assert stored.manifest == loaded.manifest == result.manifest
    assert stored.cognition_evaluation == loaded.cognition_evaluation == result.cognition_evaluation
    assert stored.response_evaluation == loaded.response_evaluation == result.response_evaluation


async def test_v10_budget_skipped_requests_remain_applied_and_unselected_responses_are_preserved(
    tmp_path,
):
    _v2_prepare_api()
    profile = _provider_revision(tmp_path)

    def dense(document):
        document["scenario"]["phone"].update(
            frequency_cap_per_agent_per_day=12,
            opportunity_probability_per_minute=1.0,
        )

    prepared = _prepare(
        _validated_v4(mode="local", limit=10, profile=profile, agents=8, mutation=dense),
        profile=profile,
    )
    provider = _SignalProvider(prepared.cognition_requests)
    evaluation = await _resolve(prepared, provider=provider)
    result = _finalize(prepared, evaluation)
    _assert_rule_identity(prepared, result)
    _assert_manifest(prepared, evaluation, result)
    coverage = evaluation.summary.coverage
    assert coverage.funded_request_count == evaluation.summary.outcomes.provider == 10
    assert coverage.budget_skipped_count > 0 and coverage.unselected_response_count > 0
    assert len(provider.completed) == 10
    assert evaluation.summary.outcomes.budget_skipped == coverage.budget_skipped_count
    applied = tuple(
        r for r in result.response_evaluation.records if r.event_type == "spatial.applied-response"
    )
    for record in applied:
        if record.source in {"budget-skipped", "unselected"}:
            assert record.applied_sentiment_delta == record.baseline.sentiment_delta
            assert record.applied_recall_delta == record.baseline.recall_delta


def test_v10_finalization_accepts_canonicalized_reverse_raw_arrivals():
    _v2_prepare_api()
    prepared = _prepare(_validated_v4())
    original = _terminal_evaluation(prepared)
    canonical = validate_city_cognition_evaluation_v2(
        prepared.cognition_input,
        prepared.cognition_requests,
        tuple(reversed(original.records)),
        baseline_evaluation=prepared.baseline_evaluation,
    )
    assert canonical == original
    assert _finalize(prepared, canonical).cognition_evaluation == original


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "orphan",
        "missing",
        "reordered",
        "run",
        "input",
        "agent",
        "campaign",
        "minute",
        "baseline",
        "group",
        "policy",
        "profile",
        "funding",
        "summary",
        "hash",
        "nested-result",
        "request",
        "request-order",
        "baseline-evaluation",
        "version",
    ],
)
def test_v10_finalization_refuses_corruption_before_a_prepared_run_exists(
    tmp_path, monkeypatch, case
):
    _v2_prepare_api()
    profile = _provider_revision(tmp_path)
    prepared = _prepare(
        _validated_v4(mode="local", limit=10, profile=profile, agents=8), profile=profile
    )
    frozen = prepared.cognition_input
    evaluation = _terminal_evaluation(prepared)
    records = evaluation.records
    first = records[0]
    if case == "duplicate":
        evaluation = _bypass_v2(evaluation, records=(first, first, *records[2:]))
    elif case == "orphan":
        changed = _bypass_v2(first, request_id="cog-" + "e" * 64)
        evaluation = _bypass_v2(evaluation, records=(changed, *records[1:]))
    elif case == "missing":
        evaluation = _bypass_v2(evaluation, records=records[:-1])
    elif case == "reordered":
        evaluation = _bypass_v2(evaluation, records=tuple(reversed(records)))
    elif case == "input":
        frozen = _bypass_v2(frozen, run_input_sha256="e" * 64)
    elif case == "funding":
        reservation = _bypass_v2(frozen.reservations[0], funded=False, reserved_output_tokens=0)
        frozen = _bypass_v2(frozen, reservations=(reservation, *frozen.reservations[1:]))
    elif case == "summary":
        summary = _bypass_v2(evaluation.summary, terminal_record_count=0)
        evaluation = _bypass_v2(evaluation, summary=summary)
    elif case == "hash":
        summary = _bypass_v2(evaluation.summary, terminal_stream_sha256="e" * 64)
        evaluation = _bypass_v2(evaluation, summary=summary)
    elif case == "request":
        request = _bypass_v2(prepared.cognition_requests[0], run_input_sha256="e" * 64)
        prepared = replace(prepared, cognition_requests=(request, *prepared.cognition_requests[1:]))
    elif case == "request-order":
        prepared = replace(
            prepared, cognition_requests=tuple(reversed(prepared.cognition_requests))
        )
    elif case == "baseline-evaluation":
        prepared = replace(
            prepared, baseline_evaluation=_bypass_v2(prepared.baseline_evaluation, records=())
        )
    elif case == "version":
        evaluation = _bypass_v2(evaluation, schema_version=1)
    else:
        changes = {
            "run": {"run_input_sha256": "e" * 64},
            "agent": {"agent_id": "person-030"},
            "campaign": {"campaign_id": "other-campaign"},
            "minute": {"model_minute": first.model_minute + 1},
            "baseline": {"baseline_sha256": "e" * 64},
            "group": {"baseline_state_update_event_id": "e" * 64},
            "policy": {"influence_policy_sha256": "e" * 64},
            "profile": {"provider_revision_sha256": "e" * 64},
            "nested-result": {"result": _bypass_v2(first.result, affect="not-an-affect")},
        }[case]
        facts = first.model_dump(mode="json", warnings="none") | {
            key: value.model_dump(mode="json", warnings="none")
            if hasattr(value, "model_dump")
            else value
            for key, value in changes.items()
        }
        # Rehash to prove binding admission rather than merely an outdated digest.
        facts["record_hash"] = sha256(
            canonical_json(
                {key: value for key, value in facts.items() if key != "record_hash"}
            ).encode()
        ).hexdigest()
        evaluation = _bypass_v2(
            evaluation,
            records=(_bypass_v2(first, **changes, record_hash=facts["record_hash"]), *records[1:]),
        )

        if case in {"agent", "campaign", "minute", "baseline", "group"}:
            stream = b"".join(
                (canonical_json(record) + "\n").encode() for record in evaluation.records
            )
            evaluation = _bypass_v2(
                evaluation,
                summary=_bypass_v2(
                    evaluation.summary,
                    terminal_stream_sha256=sha256(stream).hexdigest(),
                    terminal_stream_bytes=len(stream),
                ),
            )
            # These forgeries satisfy the standalone artifact model. Only replaying
            # the complete staged request/baseline binding can detect them.
            type(evaluation).model_validate_json(canonical_json(evaluation))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("corrupt evidence reached PreparedCityRun construction")

    monkeypatch.setattr(runs, "PreparedCityRun", forbidden)
    with pytest.raises((TypeError, ValueError)):
        _finalize(prepared, evaluation, cognition_input=frozen)


@pytest.mark.parametrize("argument", ["prepared", "cognition_input", "cognition_evaluation"])
def test_v10_finalization_requires_exact_v2_types(argument):
    _v2_prepare_api()
    prepared = _prepare(_validated_v4())
    evaluation = _terminal_evaluation(prepared)
    finalize = getattr(runs, "finalize_city_run_v2", None)
    assert finalize is not None, "finalize_city_run_v2 is not implemented"
    values = dict(
        prepared=prepared, cognition_input=prepared.cognition_input, cognition_evaluation=evaluation
    )
    values[argument] = object()
    with pytest.raises(TypeError):
        finalize(**values)


def test_v10_finalization_nested_bypass_is_value_free_and_preserves_defects(monkeypatch):
    _v2_prepare_api()
    prepared = _prepare(_validated_v4())
    evaluation = _terminal_evaluation(prepared)
    secret = "Authorization: Bearer opaquevalue1234567890"
    corrupt = _bypass_v2(evaluation, records=(secret,))
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as caught:
            _finalize(prepared, corrupt)
    assert captured == [] and secret not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None

    def defect(*_args, **_kwargs):
        raise TypeError("implementation defect")

    monkeypatch.setattr(runs, "revalidate_model", defect)
    with pytest.raises(TypeError, match="implementation defect"):
        _finalize(prepared, evaluation)


def test_v10_zero_selected_finalization_keeps_complete_empty_evidence():
    _v2_prepare_api()

    def no_opportunities(document):
        document["scenario"]["phone"]["opportunity_probability_per_minute"] = 0.0

    prepared = _prepare(_validated_v4(mutation=no_opportunities))
    assert prepared.cognition_requests == ()
    evaluation = _terminal_evaluation(prepared)
    result = _finalize(prepared, evaluation)
    _assert_rule_identity(prepared, result)
    _assert_manifest(prepared, evaluation, result)
    assert result.manifest.response_count == 0
    assert result.manifest.cognition.terminal_record_count == 0
    assert result.manifest.cognition.terminal_stream_sha256 == sha256(b"").hexdigest()
    assert result.manifest.cognition.terminal_stream_bytes == 0
    assert result.manifest.final_cognition_state_count == 3
