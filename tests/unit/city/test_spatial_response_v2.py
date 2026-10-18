"""Atomic dual response evidence from real deterministic city baselines."""

from __future__ import annotations

import importlib
from hashlib import sha256
from math import fsum, prod

import pytest

from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation import city_cognition_v2 as cognition
from tests.unit.city.test_city_cognition_v2 import _bypass, _dense_evidence, _same_minute_evidence
from tests.unit.city.test_city_cognition_v2_contract import profile_data


def _module():
    return importlib.import_module("adlife.core.simulation.spatial_response_v2")


def _evidence(*, mode="local", changes=None, context_hash=None, dense=False, evidence=None):
    context, mobility, baseline = evidence or (
        _dense_evidence(agent_count=2) if dense else _same_minute_evidence()
    )
    if context_hash is not None:
        context = context.model_copy(update={"run_input_sha256": context_hash})
    requests = cognition.build_city_cognition_requests_v2(context, mobility, baseline)
    profile = (
        None
        if mode in {"rules", "mock"}
        else CityCognitionProviderProfileSnapshotV1.model_validate(profile_data(mode))
    )
    frozen = cognition.build_city_cognition_input_v2(
        context, requests, mode=mode, funded_response_limit=60, provider_profile=profile
    )
    terminals = []
    for i, request in enumerate(requests):
        result = (
            cognition.build_city_mock_result_v2(request)
            if mode == "mock"
            else cognition.build_city_rules_result_v2(request)
        )
        if changes:
            result = result.model_copy(update=changes)
        terminals.append(
            cognition.build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=i,
                source=mode if mode in {"rules", "mock"} else "cache",
                result=result,
            )
        )
    evaluation = cognition.validate_city_cognition_evaluation_v2(
        frozen, requests, tuple(terminals), baseline_evaluation=baseline
    )
    return baseline, frozen, evaluation


def _hash(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def test_same_minute_responses_share_pre_states_and_commit_one_dual_update():
    module = _module()
    baseline, frozen, signals = _evidence(
        changes=dict(affect="positive", relevance="high", credibility="high", salience="high")
    )
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    applied, update = result.records[:-1], result.records[-1]
    original = baseline.records[-1]
    assert len(applied) > 4
    assert update.event_type == "spatial.dual-state-updated"
    assert all(item.event_type == "spatial.applied-response" for item in applied)
    assert {item.previous_rule_state_sha256 for item in applied} == {_hash(original.previous_state)}
    assert {item.previous_cognition_state_sha256 for item in applied} == {
        _hash(update.previous_cognition_state)
    }
    assert update.caused_by_event_ids == tuple(item.event_id for item in applied)
    assert canonical_json(update.rule_state) == canonical_json(original.state)
    assert (
        update.previous_cognition_state.synthetic_sentiment
        == original.previous_state.brand_sentiment
    )
    deltas = []
    recalls = []
    selected = {r.baseline_response_id for r in signals.records}
    by_baseline = {r.baseline_response_id: r for r in signals.records}
    for item in applied:
        b = item.baseline
        assert item.terminal_record_hash == (
            by_baseline[b.event_id].record_hash if b.event_id in selected else None
        )
        expected_s = (
            max(-0.2, min(0.2, fsum((b.sentiment_delta, 0.04)) * 1.15 * 1.1))
            if b.event_id in selected
            else b.sentiment_delta
        )
        expected_r = (
            max(0.0, min(0.3, b.recall_delta * 1.15 * 1.1 * 1.15))
            if b.event_id in selected
            else b.recall_delta
        )
        assert item.applied_sentiment_delta == expected_s
        assert item.applied_recall_delta == expected_r
        deltas.append(expected_s)
        recalls.append(expected_r)
    assert update.cognition_state.synthetic_sentiment == max(
        -1.0, min(1.0, fsum((original.previous_state.brand_sentiment, *deltas)))
    )
    assert update.cognition_state.synthetic_recall == max(
        0.0,
        min(
            1.0,
            1.0
            - (1.0 - original.previous_state.recall_strength)
            * prod(sorted(1.0 - r for r in recalls)),
        ),
    )
    assert update.cognition_state.response_count == original.state.response_count
    assert canonical_json(result.final_rule_states[0]) == canonical_json(baseline.final_states[0])


def test_unselected_applied_response_binds_complete_cognition_input():
    module = _module()
    baseline, first_input, first_signals = _evidence(mode="mock")
    second_baseline, second_input, second_signals = _evidence(mode="mock", context_hash="e" * 64)
    first = module.apply_spatial_cognition_v2(baseline, first_input, first_signals)
    second = module.apply_spatial_cognition_v2(second_baseline, second_input, second_signals)
    a = next(r for r in first.records if getattr(r, "source", None) == "unselected")
    b = next(r for r in second.records if getattr(r, "source", None) == "unselected")
    assert a.applied_sentiment_delta == b.applied_sentiment_delta
    assert a.applied_recall_delta == b.applied_recall_delta
    assert a.cognition_input_sha256 != b.cognition_input_sha256
    assert a.event_id != b.event_id
    assert a.unselected_marker == "unselected-neutral" and a.terminal_record_hash is None
    for evaluation, wrong in ((first, b), (second, a)):
        records = tuple(
            wrong if r.event_id == (a.event_id if evaluation is first else b.event_id) else r
            for r in evaluation.records
        )
        with pytest.raises(ValueError):
            type(evaluation).model_validate(
                evaluation.model_dump(mode="python") | {"records": records}
            )


def _rehash_event(record, **changes):
    facts = record.model_dump(mode="python", exclude={"event_id"}) | changes
    identity = dict(facts)
    if "baseline" in identity:
        identity["baseline"] = _hash(identity["baseline"])
    return type(record).model_validate(facts | {"event_id": _hash(identity)})


def test_v2_stream_embeds_each_baseline_then_places_one_update_last():
    import json

    module = _module()
    baseline, frozen, signals = _evidence(dense=True)
    evaluation = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    lines = tuple(module.spatial_response_v2_lines(evaluation))
    assert lines == tuple((canonical_json(r) + "\n").encode() for r in evaluation.records)
    documents = [json.loads(line) for line in lines]
    assert all(item["event_type"] != "spatial.response" for item in documents)
    expected_types = [
        "spatial.applied-response"
        if r.event_type == "spatial.response"
        else "spatial.dual-state-updated"
        for r in baseline.records
    ]
    assert [item["event_type"] for item in documents] == expected_types
    assert [item["baseline"]["event_id"] for item in documents if "baseline" in item] == [
        r.event_id for r in baseline.records if r.event_type == "spatial.response"
    ]
    state = module.spatial_response_state_document_v2(evaluation)
    rule = module.spatial_rule_state_document_v2(evaluation)
    cognitive = module.spatial_cognition_state_document_v2(evaluation)
    assert state.rule == rule and state.cognition == cognitive
    assert tuple((s.agent_id, s.campaign_id) for s in rule.states) == tuple(
        sorted((s.agent_id, s.campaign_id) for s in rule.states)
    )
    summary = module.summarize_spatial_response_artifact_v2(evaluation)
    assert summary.stream_sha256 == sha256(b"".join(lines)).hexdigest()
    assert summary.stream_bytes == sum(map(len, lines))
    assert (
        summary.state_document_sha256 == sha256((canonical_json(state) + "\n").encode()).hexdigest()
    )
    assert summary.state_document_bytes == len((canonical_json(state) + "\n").encode())
    assert (
        summary.rule_state_document_sha256
        == sha256((canonical_json(rule) + "\n").encode()).hexdigest()
    )
    assert (
        summary.cognition_state_document_sha256
        == sha256((canonical_json(cognitive) + "\n").encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "public",
    [
        "spatial_response_v2_lines",
        "spatial_rule_state_document_v2",
        "spatial_cognition_state_document_v2",
        "spatial_response_state_document_v2",
        "summarize_spatial_response_artifact_v2",
    ],
)
def test_every_public_projection_revalidates_nested_constructed_history(public):
    module = _module()
    function = getattr(module, public)
    baseline, frozen, signals = _evidence()
    evaluation = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    first = evaluation.records[0]
    poisoned = _bypass(first, baseline=_bypass(first.baseline, prior_notices_today=True))
    corrupted = _bypass(evaluation, records=(poisoned, *evaluation.records[1:]))
    with pytest.raises(ValueError):
        result = function(corrupted)
        if public.endswith("lines"):
            tuple(result)


def test_response_serialization_limits_are_exact_and_fail_without_truncation(monkeypatch):
    module = _module()
    baseline, frozen, signals = _evidence()
    evaluation = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    lines = tuple(module.spatial_response_v2_lines(evaluation))
    stream_size = sum(map(len, lines))
    state_size = len(
        (canonical_json(module.spatial_response_state_document_v2(evaluation)) + "\n").encode()
    )
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", stream_size)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STATE_BYTES_V2", state_size)
    module.summarize_spatial_response_artifact_v2(evaluation)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", stream_size - 1)
    with pytest.raises(ValueError):
        tuple(module.spatial_response_v2_lines(evaluation))
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", stream_size)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STATE_BYTES_V2", state_size - 1)
    with pytest.raises(ValueError):
        module.summarize_spatial_response_artifact_v2(evaluation)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STATE_BYTES_V2", state_size)
    summary = module.summarize_spatial_response_artifact_v2(evaluation)
    size = len((canonical_json(summary) + "\n").encode())
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2", size)
    module.summarize_spatial_response_artifact_v2(evaluation)
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2", size - 1)
    with pytest.raises(ValueError):
        module.summarize_spatial_response_artifact_v2(evaluation)


@pytest.mark.parametrize("boundary", ["applied", "dual", "evaluation", "builder"])
@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf")])
def test_nested_legacy_evidence_is_revalidated_before_hashing(boundary, invalid):
    module = _module()
    baseline, frozen, signals = _evidence()
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    applied, dual = result.records[0], result.records[-1]
    if boundary == "applied":
        bad = _bypass(applied.baseline, novelty_seeking=invalid)
        with pytest.raises(ValueError):
            type(applied).model_validate(applied.model_dump(mode="python") | {"baseline": bad})
    elif boundary == "dual":
        bad = _bypass(dual.previous_rule_state, purchase_intention=invalid)
        with pytest.raises(ValueError):
            type(dual).model_validate(dual.model_dump(mode="python") | {"previous_rule_state": bad})
    elif boundary == "evaluation":
        bad = _bypass(result.final_rule_states[0], recall_strength=invalid)
        with pytest.raises(ValueError):
            type(result).model_validate(
                result.model_dump(mode="python") | {"final_rule_states": (bad,)}
            )
    else:
        bad = _bypass(
            baseline,
            records=(_bypass(baseline.records[0], novelty_seeking=invalid), *baseline.records[1:]),
        )
        with pytest.raises(ValueError):
            module.apply_spatial_cognition_v2(bad, frozen, signals)


@pytest.mark.parametrize(
    "change", ["input", "run", "policy", "reservation", "terminal", "order", "baseline-hash"]
)
def test_apply_rejects_cross_bound_input_and_terminal_evidence(change):
    module = _module()
    baseline, frozen, signals = _evidence()
    if change == "input":
        signals = _bypass(signals, cognition_input_sha256="f" * 64)
    elif change == "run":
        frozen = frozen.model_copy(update={"run_input_sha256": "f" * 64})
    elif change == "policy":
        frozen = _bypass(
            frozen,
            causal_contract=_bypass(frozen.causal_contract, influence_policy_sha256="f" * 64),
        )
    elif change == "reservation":
        frozen = frozen.model_copy(update={"reservations": frozen.reservations[:-1]})
    else:
        records = list(signals.records)
        if change == "order":
            records.reverse()
        elif change == "terminal":
            records[0] = records[1]
        else:
            records[0] = _bypass(records[0], baseline_sha256=_hash(baseline.records[0]))
        signals = _bypass(signals, records=tuple(records))
    with pytest.raises(ValueError):
        module.apply_spatial_cognition_v2(baseline, frozen, signals)


@pytest.mark.parametrize(
    "change",
    [
        "record-order",
        "cause-order",
        "missing-update",
        "xor",
        "pre-rule",
        "pre-cognition",
        "applied-id",
        "rule-purchase",
        "cognition-fold",
        "attention-seed",
        "final-order",
    ],
)
def test_rehashed_dual_evidence_cannot_bypass_group_or_rule_authority(change):
    module = _module()
    baseline, frozen, signals = _evidence(dense=change == "final-order")
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    records = list(result.records)
    data = result.model_dump(mode="python")
    with pytest.raises(ValueError):
        if change == "record-order":
            records[0], records[1] = records[1], records[0]
        elif change == "cause-order":
            records[-1] = _rehash_event(
                records[-1], caused_by_event_ids=tuple(reversed(records[-1].caused_by_event_ids))
            )
        elif change == "missing-update":
            records.pop()
        elif change == "xor":
            index = next(
                i
                for i, r in enumerate(records)
                if getattr(r, "terminal_record_hash", None) is not None
            )
            records[index] = _rehash_event(records[index], unselected_marker="unselected-neutral")
        elif change in {"pre-rule", "pre-cognition"}:
            records[0] = _rehash_event(
                records[0],
                **{
                    "previous_rule_state_sha256"
                    if change == "pre-rule"
                    else "previous_cognition_state_sha256": "f" * 64
                },
            )
        elif change == "applied-id":
            records[0] = _bypass(records[0], event_id="f" * 64)
        elif change == "rule-purchase":
            state = records[-1].rule_state.model_copy(update={"purchase_intention": 0.9})
            records[-1] = _rehash_event(records[-1], rule_state=state.model_dump(mode="python"))
            data["final_rule_states"] = (state,)
        elif change == "cognition-fold":
            state = records[-1].cognition_state.model_copy(update={"synthetic_recall": 0.123})
            records[-1] = _rehash_event(
                records[-1], cognition_state=state.model_dump(mode="python")
            )
            data["final_cognition_states"] = (state,)
        elif change == "attention-seed":
            from adlife.core.simulation.spatial_attention import spatial_notice_draw

            b = records[0].baseline
            data["attention_seed"] = next(
                seed
                for seed in range(100)
                if spatial_notice_draw(
                    agent_id=b.agent_id,
                    at_millisecond=b.model_minute * 60000 + b.millisecond_within_minute,
                    channel=b.channel,
                    placement_id=b.placement_id,
                    seed=seed,
                )
                >= 0.5
            )
        else:
            data["final_rule_states"] = tuple(reversed(result.final_rule_states))
        type(result).model_validate(data | {"records": tuple(records)})


def test_zero_responses_identity_sources_and_frozen_policy_replay(monkeypatch):
    module = _module()
    for mode in ("rules", "mock", "local"):
        baseline, frozen, signals = _evidence(mode=mode)
        expected = module.apply_spatial_cognition_v2(baseline, frozen, signals)
        assert type(expected).model_validate_json(expected.model_dump_json()) == expected
        if mode != "mock":
            for rule, cognitive in zip(
                expected.final_rule_states, expected.final_cognition_states, strict=True
            ):
                assert rule.brand_sentiment == cognitive.synthetic_sentiment
                assert rule.recall_strength == cognitive.synthetic_recall
        for owner in (cognition, importlib.import_module("adlife.core.domain.city_cognition_v2")):
            for name in tuple(vars(owner)):
                if name.startswith("CITY_COGNITION_"):
                    monkeypatch.setattr(owner, name, None)
        assert module.apply_spatial_cognition_v2(baseline, frozen, signals) == expected
        monkeypatch.undo()
    from tests.unit.city.test_spatial_opportunity import _phone

    context, _, baseline = _dense_evidence(
        agent_count=2, placements=[_phone(probability=0.0)], sentiment=-0.0, recall=-0.0
    )
    frozen = cognition.build_city_cognition_input_v2(
        context, (), mode="rules", funded_response_limit=60
    )
    signals = cognition.validate_city_cognition_evaluation_v2(
        frozen, (), (), baseline_evaluation=baseline
    )
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    assert result.records == ()
    assert (
        canonical_json(dict(v=result.final_cognition_states[0].synthetic_sentiment)) == '{"v":-0.0}'
    )
    assert result.final_cognition_states[0].response_count == 0
    assert tuple(module.spatial_response_v2_lines(result)) == ()
    assert (
        module.summarize_spatial_response_artifact_v2(result).stream_sha256
        == sha256(b"").hexdigest()
    )


def test_identity_fallback_skipped_and_safety_precedence_are_exact():
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=4)
    requests = cognition.build_city_cognition_requests_v2(context, mobility, baseline)
    profile = CityCognitionProviderProfileSnapshotV1.model_validate(profile_data())
    frozen = cognition.build_city_cognition_input_v2(
        context, requests, mode="local", funded_response_limit=10, provider_profile=profile
    )
    records = []
    for i, request in enumerate(requests):
        result = cognition.build_city_rules_result_v2(request)
        source = "budget-skipped" if i >= 10 else "fallback" if i == 0 else "cache"
        reason = "budget-skipped" if i >= 10 else "authentication-refused" if i == 0 else None
        if i == 1:
            result = result.model_copy(
                update={"affect": "uncertain", "safety_flags": ("unsafe-content",)}
            )
        records.append(
            cognition.build_city_cognition_terminal_record_v2(
                request, frozen, selection_index=i, source=source, result=result, reason=reason
            )
        )
    signals = cognition.validate_city_cognition_evaluation_v2(
        frozen, requests, tuple(records), baseline_evaluation=baseline
    )
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    statuses = {
        r.neutralization_status
        for r in result.records
        if r.event_type == "spatial.applied-response"
    }
    assert {"safety", "fallback", "budget-skipped", "unselected"} <= statuses
    for record in result.records:
        if record.event_type == "spatial.applied-response":
            assert canonical_json(
                dict(s=record.applied_sentiment_delta, r=record.applied_recall_delta)
            ) == canonical_json(
                dict(s=record.baseline.sentiment_delta, r=record.baseline.recall_delta)
            )


def test_terminal_selection_order_is_independent_of_same_minute_stream_order():
    from tests.unit.city.test_spatial_opportunity import _billboard

    evidence = _dense_evidence(
        roadside=True,
        attention_seed=0,
        placements=[
            _billboard(
                placement_id=f"reverse-{20 - i:02}",
                fraction=0.4 + i * 0.005,
                longitude=0.004 + i * 0.00005,
            )
            for i in range(20)
        ],
    )
    baseline, frozen, signals = _evidence(evidence=evidence)
    by_id = {r.baseline_response_id: r for r in signals.records}
    stream_order = tuple(
        r.event_id
        for r in baseline.records
        if r.event_type == "spatial.response" and r.event_id in by_id
    )
    assert tuple(
        r.placement_id
        for r in baseline.records
        if r.event_type == "spatial.response" and r.event_id in by_id
    ) == ("reverse-15", "reverse-07", "reverse-04", "reverse-02")
    assert tuple(r.placement_id for r in signals.records) == (
        "reverse-04",
        "reverse-07",
        "reverse-02",
        "reverse-15",
    )
    assert stream_order != tuple(by_id)
    result = _module().apply_spatial_cognition_v2(baseline, frozen, signals)
    for record in result.records:
        if record.event_type == "spatial.applied-response" and record.baseline.event_id in by_id:
            assert record.terminal_record_hash == by_id[record.baseline.event_id].record_hash


def test_rehashed_second_group_rule_snapshot_cannot_break_legacy_chain():
    module = _module()
    baseline, frozen, signals = _evidence(dense=True)
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    records = list(result.records)
    indices = [i for i, r in enumerate(records) if r.event_type == "spatial.dual-state-updated"]
    # Find a later update for the same pair, so predecessor continuity is required.
    seen = set()
    chosen = None
    for n, index in enumerate(indices):
        key = (records[index].agent_id, records[index].campaign_id)
        if key in seen:
            chosen = (n, index)
            break
        seen.add(key)
    assert chosen is not None
    n, index = chosen
    start = indices[n - 1] + 1
    previous = records[index].previous_rule_state.model_copy(update={"purchase_intention": 0.99})
    for i in range(start, index):
        b = records[i].baseline.model_copy(update={"state_before_sha256": _hash(previous)})
        records[i] = _rehash_event(
            records[i],
            baseline=b.model_dump(mode="python"),
            previous_rule_state_sha256=_hash(previous),
        )
    records[index] = _rehash_event(
        records[index],
        previous_rule_state=previous.model_dump(mode="python"),
        caused_by_event_ids=tuple(r.event_id for r in records[start:index]),
    )
    with pytest.raises(ValueError, match="chain"):
        type(result).model_validate(result.model_dump(mode="python") | {"records": tuple(records)})
