"""Provider categories cannot alter deterministic rule authority."""

import json
import math
import os
import re
import subprocess
import sys
from functools import lru_cache
from hashlib import sha256
from itertools import permutations, product
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_V2_MAX_AGGREGATE_OUTPUT_TOKENS
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation import city_cognition_v2 as cognition
from tests.unit.city.test_city_cognition_v2 import (
    _bypass,
    _context,
    _dense_evidence,
    _rehash_terminal,
)
from tests.unit.city.test_spatial_response_v2 import _evidence, _module


@lru_cache(maxsize=1)
def _frozen():
    baseline, frozen, signals = _evidence(dense=True)
    context, mobility, same_baseline = _dense_evidence(agent_count=2)
    requests = cognition.build_city_cognition_requests_v2(context, mobility, same_baseline)
    return baseline, frozen, signals, requests


@lru_cache(maxsize=1)
def _three_arrivals():
    context, mobility, baseline = _dense_evidence(agent_count=1, minutes=5)
    requests = cognition.build_city_cognition_requests_v2(context, mobility, baseline)
    from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
    from tests.unit.city.test_city_cognition_v2_contract import profile_data

    profile = CityCognitionProviderProfileSnapshotV1.model_validate(profile_data())
    frozen = cognition.build_city_cognition_input_v2(
        context, requests, mode="local", funded_response_limit=10, provider_profile=profile
    )
    records = tuple(
        cognition.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=i,
            source="cache",
            result=cognition.build_city_rules_result_v2(request),
        )
        for i, request in enumerate(requests)
    )
    assert len(records) == 3
    return baseline, frozen, requests, records


def test_provider_completion_order_produces_identical_canonical_evaluation():
    baseline, frozen, requests, records = _three_arrivals()
    expected = cognition.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    for arrivals in permutations(records):
        candidate = cognition.validate_city_cognition_evaluation_v2(
            frozen, requests, arrivals, baseline_evaluation=baseline
        )
        assert canonical_json(candidate) == canonical_json(expected)
        assert tuple(cognition.city_cognition_terminal_lines_v2(candidate)) == tuple(
            cognition.city_cognition_terminal_lines_v2(expected)
        )
        if arrivals != records:
            with pytest.raises(ValueError):
                type(expected).model_validate(
                    expected.model_dump(mode="python") | {"records": arrivals}
                )
            with pytest.raises(ValueError):
                payload = expected.model_dump(mode="json")
                payload["records"] = [r.model_dump(mode="json") for r in arrivals]
                type(expected).model_validate_json(canonical_json(payload))
    with pytest.raises(ValueError):
        cognition.validate_city_cognition_evaluation_v2(
            frozen, tuple(reversed(requests)), records, baseline_evaluation=baseline
        )


@pytest.mark.parametrize("shape", ["too-many", "list", "generator"])
def test_arrival_admission_refuses_invalid_container_before_any_model_work(monkeypatch, shape):
    baseline, frozen, requests, records = _three_arrivals()
    arrivals = (
        records * 21 if shape == "too-many" else list(records) if shape == "list" else iter(records)
    )

    def fail(*args, **kwargs):
        raise AssertionError("model work preceded arrival admission")

    monkeypatch.setattr(cognition, "revalidate_model", fail)
    with pytest.raises(ValueError if shape == "too-many" else TypeError):
        cognition.validate_city_cognition_evaluation_v2(
            frozen, requests, arrivals, baseline_evaluation=baseline
        )


@pytest.mark.parametrize(
    "change",
    ["duplicate-index", "duplicate-id", "out-of-range", "cross-index", "nested", "missing"],
)
def test_completion_normalization_refuses_duplicate_or_cross_bound_arrivals(change):
    baseline, frozen, requests, records = _three_arrivals()
    changed = list(records)
    if change == "duplicate-index":
        changed[1] = _rehash_terminal(changed[1], selection_index=0)
    elif change == "duplicate-id":
        result = changed[1].result.model_dump(mode="python") | {"request_id": changed[0].request_id}
        changed[1] = _rehash_terminal(changed[1], request_id=changed[0].request_id, result=result)
    elif change == "out-of-range":
        changed[0] = _rehash_terminal(changed[0], selection_index=59)
    elif change == "cross-index":
        changed[0] = _rehash_terminal(changed[0], selection_index=1)
        changed[1] = _rehash_terminal(changed[1], selection_index=0)
    elif change == "nested":
        changed[0] = _bypass(changed[0], result=_bypass(changed[0].result, affect=True))
    else:
        changed.pop()
    with pytest.raises(ValueError):
        cognition.validate_city_cognition_evaluation_v2(
            frozen, requests, tuple(reversed(changed)), baseline_evaluation=baseline
        )


def _artifact_digest(baseline, frozen, signals):
    module = _module()
    response = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    digest = sha256()
    for line in cognition.city_cognition_terminal_lines_v2(signals):
        digest.update(line)
    for line in module.spatial_response_v2_lines(response):
        digest.update(line)
    for document in (
        frozen,
        signals.summary,
        module.spatial_response_state_document_v2(response),
        module.summarize_spatial_response_artifact_v2(response),
    ):
        digest.update((canonical_json(document) + "\n").encode())
    return digest.hexdigest()


@settings(max_examples=20, deadline=None)
@given(
    sentiment=st.floats(min_value=-1, max_value=1, allow_nan=False, allow_infinity=False, width=32),
    recall=st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False, width=32),
    traits=st.lists(
        st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False, width=32),
        min_size=6,
        max_size=6,
    ),
    mode=st.sampled_from(("rules", "mock", "local")),
)
def test_v2_states_and_deltas_are_finite_and_bounded(sentiment, recall, traits, mode):
    from adlife.core.domain.spatial_response import SpatialResponseTraits

    fields = (
        "price_sensitivity",
        "novelty_seeking",
        "advertising_skepticism",
        "mobile_recall_encoding",
        "roadside_recall_encoding",
        "impulsivity",
    )
    evidence = _dense_evidence(
        agent_count=1,
        traits=SpatialResponseTraits(**dict(zip(fields, traits, strict=True))),
        sentiment=sentiment,
        recall=recall,
    )
    baseline, frozen, signals = _evidence(evidence=evidence, mode=mode)
    response = _module().apply_spatial_cognition_v2(baseline, frozen, signals)
    assert response.records and response.final_rule_states and response.final_cognition_states
    assert any(r.event_type == "spatial.applied-response" for r in response.records)
    assert any(r.event_type == "spatial.dual-state-updated" for r in response.records)
    for record in response.records:
        if record.event_type == "spatial.applied-response":
            assert (
                math.isfinite(record.applied_sentiment_delta)
                and -0.2 <= record.applied_sentiment_delta <= 0.2
            )
            assert (
                math.isfinite(record.applied_recall_delta)
                and 0 <= record.applied_recall_delta <= 0.3
            )
    for state in response.final_cognition_states:
        assert math.isfinite(state.synthetic_sentiment) and -1 <= state.synthetic_sentiment <= 1
        assert math.isfinite(state.synthetic_recall) and 0 <= state.synthetic_recall <= 1
    for state in response.final_rule_states:
        assert math.isfinite(state.brand_sentiment) and -1 <= state.brand_sentiment <= 1
        assert math.isfinite(state.recall_strength) and 0 <= state.recall_strength <= 1
        assert math.isfinite(state.purchase_intention) and 0 <= state.purchase_intention <= 1
    for rule, cognitive in zip(
        response.final_rule_states, response.final_cognition_states, strict=True
    ):
        assert (rule.agent_id, rule.campaign_id) == (cognitive.agent_id, cognitive.campaign_id)
        assert rule.response_count == cognitive.response_count
        assert rule.last_response_minute == cognitive.last_response_minute


@lru_cache(maxsize=1)
def _permutation_evidence():
    from tests.unit.city.test_spatial_opportunity import _phone

    context, mobility, baseline = _dense_evidence(
        agent_count=2,
        placements=[
            _phone(placement_id=f"phone-{i}", campaign_id=f"campaign-{i}", cap=20) for i in range(2)
        ],
    )
    _, frozen, signals = _evidence(evidence=(context, mobility, baseline))
    requests = cognition.build_city_cognition_requests_v2(context, mobility, baseline)
    return context, mobility, baseline, frozen, signals, requests


@settings(max_examples=12, deadline=None)
@given(order=st.permutations(tuple(range(8))))
def test_v2_input_permutations_produce_identical_bytes(order):
    context, mobility, baseline, frozen, original, requests = _permutation_evidence()
    assert len(context.agents) > 1 and len(context.campaigns) > 1
    permuted = context.model_copy(
        update={
            "agents": tuple(
                a.model_copy(update={"interests": tuple(reversed(a.interests))})
                for a in reversed(context.agents)
            ),
            "campaigns": tuple(
                c.model_copy(update={"target_interests": tuple(reversed(c.target_interests))})
                for c in reversed(context.campaigns)
            ),
            "initial_states": tuple(reversed(context.initial_states)),
        }
    )
    rebuilt_requests = cognition.build_city_cognition_requests_v2(permuted, mobility, baseline)
    assert tuple(map(canonical_json, rebuilt_requests)) == tuple(map(canonical_json, requests))
    rebuilt_input = cognition.build_city_cognition_input_v2(
        permuted,
        rebuilt_requests,
        mode=frozen.mode,
        funded_response_limit=frozen.funded_response_limit,
        provider_profile=frozen.provider_profile,
    )
    assert canonical_json(rebuilt_input) == canonical_json(frozen)
    signals = cognition.validate_city_cognition_evaluation_v2(
        frozen, requests, tuple(original.records[i] for i in order), baseline_evaluation=baseline
    )
    assert tuple(cognition.city_cognition_terminal_lines_v2(signals)) == tuple(
        cognition.city_cognition_terminal_lines_v2(original)
    )
    module = _module()
    candidate = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    expected = module.apply_spatial_cognition_v2(baseline, frozen, original)
    assert tuple(module.spatial_response_v2_lines(candidate)) == tuple(
        module.spatial_response_v2_lines(expected)
    )
    for projection in (
        module.spatial_rule_state_document_v2,
        module.spatial_cognition_state_document_v2,
        module.spatial_response_state_document_v2,
        module.summarize_spatial_response_artifact_v2,
    ):
        assert canonical_json(projection(candidate)) == canonical_json(projection(expected))
    assert canonical_json(signals.summary) == canonical_json(original.summary)
    assert _artifact_digest(baseline, frozen, signals) == _artifact_digest(
        baseline, frozen, original
    )


def _hash_seed_digest():
    baseline, frozen, signals, _ = _frozen()
    return _artifact_digest(baseline, frozen, signals)


def test_v2_core_artifact_bytes_are_stable_across_python_hash_seeds():
    digests = []
    for seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from tests.property.test_spatial_response_v2_invariants "
                "import _hash_seed_digest; print(_hash_seed_digest())",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        digests.append(completed.stdout.strip())
        assert re.fullmatch(r"[0-9a-f]{64}", digests[-1])
        assert completed.stderr == ""
    assert digests[0] == digests[1] == _hash_seed_digest()


@lru_cache(maxsize=1)
def _representative_capacity_evidence():
    from adlife.core.domain.city_cognition import CityCognitionCreativeSnapshotV1
    from adlife.core.simulation.city_mobility import CityMobility
    from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
    from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
    from adlife.core.simulation.spatial_response import evaluate_spatial_responses
    from tests.unit.city.test_city_cognition_v2_contract import causal_data
    from tests.unit.city.test_city_pack import load_pack, pack_data
    from tests.unit.city.test_spatial_opportunity import _phone, _scenario
    from tests.unit.city.test_spatial_response import _response_input

    pack = load_pack(pack_data())
    mobility = CityMobility(pack, seed=42, agent_count=30, days=7)
    creative = CityCognitionCreativeSnapshotV1.model_validate(causal_data()["campaign"]["creative"])
    placements = [
        _phone(
            placement_id=f"phone-{i:02d}",
            campaign_id=f"campaign-{i:02d}",
            cap=1,
            windows=[{"start_minute": 0, "end_minute": 1}],
        )
        for i in range(20)
    ]
    template = _scenario(pack, placements[:1], days=7)
    campaign = template.campaigns[0].model_dump(mode="python")
    scenario = type(template).model_validate_json(
        canonical_json(
            template.model_dump(mode="python")
            | {
                "campaigns": tuple(
                    campaign
                    | {"campaign_id": p["campaign_id"], "creative_sha256": creative.fingerprint}
                    for p in placements
                ),
                "placements": tuple(placements),
            }
        )
    )
    ids = tuple(a.agent_id for a in mobility.agents)
    inputs = _response_input(scenario, agent_ids=ids)
    opportunities = evaluate_spatial_opportunities(mobility, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=10)
    baseline = evaluate_spatial_responses(inputs, scenario, opportunities, attention, agent_ids=ids)
    legacy = SimpleNamespace(
        run_input_sha256="b" * 64,
        response_input_sha256=inputs.fingerprint,
        scenario_sha256=scenario.fingerprint,
        city_sha256=pack.fingerprint,
        agents=tuple(
            SimpleNamespace(
                agent_id=a.agent_id, interests=tuple(sorted(a.interests)), traits=a.traits
            )
            for a in inputs.profiles
        ),
        campaigns=tuple(
            SimpleNamespace(
                campaign_id=c.campaign_id,
                creative_sha256=c.creative_sha256,
                creative=creative,
                target_interests=tuple(sorted(c.target_interests)),
                relative_price=c.relative_price,
            )
            for c in inputs.campaigns
        ),
    )
    context = _context(legacy, baseline)
    evidence = _evidence(mode="rules", evidence=(context, mobility, baseline))
    return scenario, opportunities, attention, evidence


def _structural_capacity_high_water_documents():
    """Valid standalone width witnesses, not a claimed single cross-bound run."""
    from adlife.core.domain.city_cognition import (
        MAX_CITY_COGNITION_USAGE_INTEGER,
        CityCognitionProviderProfileSnapshotV1,
        city_cognition_provider_profile_revision_sha256,
    )
    from adlife.core.domain.city_cognition_v2 import CityCognitionEvaluationV2, CityCognitionInputV2
    from adlife.core.simulation.spatial_response import MAX_SPATIAL_RESPONSES, SpatialResponseState
    from tests.unit.city.test_city_cognition_v2_contract import (
        evaluation_data,
        input_data,
        profile_data,
        rehash_record,
        settings_data,
        terminal_data,
    )

    module = _module()
    glyph = chr(0x1F337)
    profile_facts = profile_data("remote", tokens=384) | dict(
        profile_id="p" * 64,
        credential_slot_id="c" * 64,
        origin_approval_id="o" * 64,
        display_label=glyph * 80,
        base_url="https://example.com/" + "x" * 2028,
        model_id=glyph * 120,
        temperature=1.2345678901234567e-300,
        top_p=1.2345678901234567e-300,
        seed_policy="fixed",
        seed=2**63 - 1,
    )
    profile_facts["revision_sha256"] = city_cognition_provider_profile_revision_sha256(
        profile_facts
    )
    profile = CityCognitionProviderProfileSnapshotV1.model_validate(profile_facts)
    remote = CityCognitionInputV2.model_validate(
        input_data("remote", count=60, limit=60)
        | {"provider_profile": profile, "effective_settings": settings_data(profile_facts)}
    )
    records = []
    for index in range(60):
        facts = terminal_data("provider", index)
        records.append(
            rehash_record(
                facts
                | dict(
                    result=facts["result"]
                    | dict(
                        affect="uncertain",
                        relevance="uncertain",
                        credibility="uncertain",
                        salience="uncertain",
                        safety_flags=("unsafe-content", "stereotype-risk", "unsupported-inference"),
                    ),
                    baseline_state_update_cause_count=MAX_SPATIAL_RESPONSES,
                    campaign_id="c" * 80,
                    placement_id="p" * 80,
                    channel="roadside-billboard",
                    activity="commute",
                    day_index=6,
                    model_minute=10079,
                    millisecond_within_minute=59999,
                    attempt_count=4,
                    provider_profile_id="q" * 64,
                    prompt_tokens=MAX_CITY_COGNITION_USAGE_INTEGER,
                    completion_tokens=MAX_CITY_COGNITION_USAGE_INTEGER,
                )
            )
        )
    terminals = CityCognitionEvaluationV2.model_validate(
        evaluation_data(tuple(records), "remote", MAX_SPATIAL_RESPONSES - len(records))
    )
    campaigns = tuple(f"c{i:02d}-" + "x" * 76 for i in range(20))
    rules = []
    cognitive = []
    for agent in range(30):
        for campaign in campaigns:
            identity = dict(
                agent_id=f"person-{agent:03d}",
                campaign_id=campaign,
                response_count=MAX_SPATIAL_RESPONSES,
                last_response_minute=10079,
            )
            rules.append(
                SpatialResponseState(
                    **identity,
                    brand_sentiment=-1.2345678901234567e-300,
                    recall_strength=1.2345678901234567e-300,
                    purchase_intention=1.2345678901234567e-300,
                )
            )
            cognitive.append(
                module.SpatialCognitionStateV2(
                    schema_version=2,
                    **identity,
                    synthetic_sentiment=-1.2345678901234567e-300,
                    synthetic_recall=1.2345678901234567e-300,
                )
            )
    rule_document = module.SpatialRuleStateDocumentV2(
        schema_version=2, model_id="spatial-rule-state-v2", states=tuple(rules)
    )
    cognition_document = module.SpatialCognitionStateDocumentV2(
        schema_version=2, model_id="spatial-cognition-state-v2", states=tuple(cognitive)
    )
    hashes = dict(
        response_input_sha256="a" * 64,
        scenario_sha256="b" * 64,
        city_sha256="c" * 64,
        cognition_input_sha256="d" * 64,
    )
    scope = "synthetic-bounded-cognition-not-observed-behavior"
    combined = module.SpatialResponseStateDocumentV2(
        schema_version=2,
        model_id="spatial-response-state-v2",
        claim_scope=scope,
        **hashes,
        rule=rule_document,
        cognition=cognition_document,
    )
    counts = module.SpatialResponseCountsV2(
        schema_version=2,
        baseline_response_count=MAX_SPATIAL_RESPONSES,
        applied_response_count=MAX_SPATIAL_RESPONSES,
        dual_state_update_count=MAX_SPATIAL_RESPONSES,
        roadside_response_count=MAX_SPATIAL_RESPONSES // 2,
        phone_response_count=MAX_SPATIAL_RESPONSES // 2,
        campaign_count=len(campaigns),
        final_rule_state_count=len(rules),
        final_cognition_state_count=len(cognitive),
    )
    summary = module.SpatialResponseArtifactSummaryV2(
        schema_version=2,
        model_id="spatial-response-artifact-v2",
        claim_scope=scope,
        response_model_id="spatial-response-v2",
        **hashes,
        influence_policy_sha256=remote.causal_contract.influence_policy_sha256,
        attention_seed=2**63 - 1,
        stream_sha256="e" * 64,
        stream_bytes=module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2,
        state_document_sha256=sha256((canonical_json(combined) + "\n").encode()).hexdigest(),
        state_document_bytes=module.MAX_SPATIAL_RESPONSE_STATE_BYTES_V2,
        rule_state_document_sha256=sha256(
            (canonical_json(rule_document) + "\n").encode()
        ).hexdigest(),
        cognition_state_document_sha256=sha256(
            (canonical_json(cognition_document) + "\n").encode()
        ).hexdigest(),
        counts=counts,
    )
    return remote, terminals, rule_document, cognition_document, combined, summary


def test_maximum_supported_v10_core_documents_fit_frozen_ceilings(monkeypatch):
    from adlife.core.simulation.spatial_opportunity import MAX_SPATIAL_OPPORTUNITIES
    from adlife.core.simulation.spatial_response import (
        MAX_SPATIAL_RESPONSES,
        MAX_SPATIAL_STATE_UPDATES,
    )
    from tests.unit.city.test_spatial_opportunity import _phone

    module = _module()
    scenario, opportunities, attention, (baseline, frozen, signals) = (
        _representative_capacity_evidence()
    )
    response = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    assert scenario.days == 7 and len(scenario.campaigns) == 20
    assert len({s.agent_id for s in baseline.final_states}) == 30
    assert len(response.final_rule_states) == len(response.final_cognition_states) == 600
    assert opportunities.counts.opportunity_count == 600
    assert baseline.counts.response_count == baseline.counts.state_update_count == 300
    assert len(response.records) == 600
    assert baseline.counts.response_count == attention.counts.noticed_count
    assert baseline.counts.response_count <= opportunities.counts.opportunity_count
    # Independent upstream phone/roadside maxima exceed the admitted opportunity cap.
    raw_candidates = 30 * 7 * (20 * 100 + 480 * 2)
    eligible = min(raw_candidates, MAX_SPATIAL_OPPORTUNITIES)
    structural_group_ceiling = min(30 * 7 * 1440 * 20, eligible, MAX_SPATIAL_STATE_UPDATES)
    assert eligible == structural_group_ceiling == MAX_SPATIAL_RESPONSES == 520_800
    assert len(signals.records) == 60 and signals.summary.coverage.funded_request_count == 60
    assert all(r.reserved_output_tokens == 0 for r in frozen.reservations)
    count = 0
    digest = sha256()
    for line in module.spatial_response_v2_lines(response):
        assert line.endswith(b"\n") and not line.endswith(b"\n\n")
        count += len(line)
        digest.update(line)
    summary = module.summarize_spatial_response_artifact_v2(response)
    assert count == sum(len(canonical_json(r).encode("utf-8")) + 1 for r in response.records)
    assert count == 1_151_760 and summary.state_document_bytes == 237_033
    assert summary.stream_bytes == count < module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2
    assert summary.stream_sha256 == digest.hexdigest()
    for document in (
        module.spatial_rule_state_document_v2(response),
        module.spatial_cognition_state_document_v2(response),
        module.spatial_response_state_document_v2(response),
    ):
        assert (
            len((canonical_json(document) + "\n").encode())
            <= module.MAX_SPATIAL_RESPONSE_STATE_BYTES_V2
        )
    assert (
        len((canonical_json(summary) + "\n").encode())
        <= module.MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2
    )
    assert len(canonical_json(frozen).encode()) <= 131_072
    # The frozen input embeds the prompt contract text; pin every other byte.
    prompt_bytes = len(
        json.dumps(frozen.causal_contract.prompt_contract_text, ensure_ascii=False).encode()
    )
    assert len(canonical_json(frozen).encode()) - prompt_bytes == 20_345
    assert sum(map(len, cognition.city_cognition_terminal_lines_v2(signals))) <= 1_048_576
    assert signals.summary.terminal_stream_bytes == 153_530
    assert len((canonical_json(summary) + "\n").encode()) == 1314
    assert len(canonical_json(signals.summary).encode()) <= 16_384
    assert all(
        len(line) - 1 <= 8192 for line in cognition.city_cognition_terminal_lines_v2(signals)
    )
    assert module.MAX_SPATIAL_RESPONSE_STATE_BYTES_V2 == 8_388_608
    assert module.MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2 == 131_072
    assert frozen.aggregate_output_token_ceiling == 0
    assert (
        60 * frozen.reservation_tokens_per_request == CITY_COGNITION_V2_MAX_AGGREGATE_OUTPUT_TOKENS
    )

    # A real admitted max-text pair witnesses why independently valid maxima do not compose.
    wide = _dense_evidence(
        agent_count=1,
        placements=[_phone(placement_id="p" * 80, campaign_id="c" * 80, cap=100)],
    )
    wide_baseline, wide_frozen, wide_signals = _evidence(mode="rules", evidence=wide)
    wide_response = module.apply_spatial_cognition_v2(wide_baseline, wide_frozen, wide_signals)
    selected_size, unselected_size = (
        max(
            len(canonical_json(r).encode("utf-8")) + len(b"\n")
            for r in wide_response.records
            if getattr(r, "source", None) == source
        )
        for source in ("rules", "unselected")
    )
    dual_size = max(
        len(canonical_json(r).encode("utf-8")) + len(b"\n")
        for r in wide_response.records
        if r.event_type == "spatial.dual-state-updated"
    )
    assert (selected_size, unselected_size, dual_size) == (2292, 2284, 2154)
    independent_max_bytes = (
        60 * selected_size
        + (eligible - 60) * unselected_size
        + structural_group_ceiling * dual_size
    )
    assert independent_max_bytes == 2_311_310_880
    assert independent_max_bytes > module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2

    assert module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2 == 2_147_483_648
    applied_bytes = 60 * selected_size + (eligible - 60) * unselected_size
    assert applied_bytes == 1_189_507_680
    stream_fitting_groups = (
        module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2 - applied_bytes
    ) // dual_size
    assert stream_fitting_groups == 444_742 < structural_group_ceiling
    admitted_bytes = applied_bytes + stream_fitting_groups * dual_size
    next_group_bytes = applied_bytes + (stream_fitting_groups + 1) * dual_size
    assert admitted_bytes == 2_147_481_948
    assert module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2 - admitted_bytes == 1700
    assert next_group_bytes == 2_147_484_102
    assert next_group_bytes - module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2 == 454

    remote, terminals, rule_doc, cognition_doc, combined_doc, high_summary = (
        _structural_capacity_high_water_documents()
    )
    assert len(remote.reservations) == 60
    remote_prompt_bytes = len(
        json.dumps(remote.causal_contract.prompt_contract_text, ensure_ascii=False).encode()
    )
    assert len(canonical_json(remote).encode()) - remote_prompt_bytes == 24_346
    assert len(canonical_json(remote).encode()) < 131_072
    terminal_sizes = tuple(map(len, cognition.city_cognition_terminal_lines_v2(terminals)))
    assert len(terminal_sizes) == 60
    assert (min(terminal_sizes), max(terminal_sizes), sum(terminal_sizes)) == (3090, 3091, 185_450)
    record_sizes = tuple(size - 1 for size in terminal_sizes)
    assert record_sizes == tuple(
        len(canonical_json(record).encode()) for record in terminals.records
    )
    assert (min(record_sizes), max(record_sizes), sum(record_sizes)) == (3089, 3090, 185_390)
    assert max(record_sizes) <= 8192 and sum(terminal_sizes) <= 1_048_576
    assert len(canonical_json(terminals.summary).encode()) == 1350 < 16_384
    state_sizes = tuple(
        len(canonical_json(doc).encode()) + 1 for doc in (rule_doc, cognition_doc, combined_doc)
    )
    assert state_sizes == (195_067, 171_072, 366_633)
    assert max(state_sizes) < module.MAX_SPATIAL_RESPONSE_STATE_BYTES_V2
    assert len(canonical_json(high_summary).encode()) == 1351
    assert (
        len((canonical_json(high_summary) + "\n").encode())
        == 1352
        < module.MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2
    )
    for document, fingerprint in (
        (rule_doc, high_summary.rule_state_document_sha256),
        (cognition_doc, high_summary.cognition_state_document_sha256),
        (combined_doc, high_summary.state_document_sha256),
    ):
        assert fingerprint == sha256((canonical_json(document) + "\n").encode()).hexdigest()
    # Exercise the same inclusive counter with real small canonical payloads;
    # the analytical independent-max witness above needs no gigabyte allocation.
    small_baseline, small_input, small_signals = _evidence(mode="rules")
    small = module.apply_spatial_cognition_v2(small_baseline, small_input, small_signals)
    exact = sum(map(len, module.spatial_response_v2_lines(small)))
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", exact)
    assert sum(map(len, module.spatial_response_v2_lines(small))) == exact
    type(small).model_validate_json(canonical_json(small))
    monkeypatch.setattr(module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", exact - 1)
    with pytest.raises(ValueError, match="byte limit"):
        tuple(module.spatial_response_v2_lines(small))
    with pytest.raises(ValueError, match="byte limit"):
        type(small).model_validate_json(canonical_json(small))


@settings(max_examples=40, deadline=None)
@given(
    affect=st.sampled_from(("annoyed", "skeptical", "neutral", "curious", "positive", "uncertain")),
    relevance=st.sampled_from(("none", "low", "medium", "high", "uncertain")),
    credibility=st.sampled_from(("low", "medium", "high", "uncertain")),
    salience=st.sampled_from(("none", "low", "medium", "high", "uncertain")),
    flags=st.sampled_from(
        ((), ("unsafe-content",), ("stereotype-risk",), ("unsupported-inference",))
    ),
)
def test_provider_categories_never_change_rule_state_or_purchase_intention(
    affect, relevance, credibility, salience, flags
):
    _assert_noninterference(affect, relevance, credibility, salience, flags)


def _assert_noninterference(affect, relevance, credibility, salience, flags):
    module = _module()
    baseline, frozen, original, requests = _frozen()
    budget_before = canonical_json(frozen)
    records = tuple(
        _rehash_terminal(
            record,
            result=record.result.model_copy(
                update=dict(
                    affect=affect,
                    relevance=relevance,
                    credibility=credibility,
                    salience=salience,
                    safety_flags=flags,
                )
            ).model_dump(mode="python"),
        )
        for record in original.records
    )
    # Task 4 regenerates exact summaries for the modified, valid closed signals.
    signals = cognition.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    result = module.apply_spatial_cognition_v2(baseline, frozen, signals)
    assert canonical_json(frozen) == budget_before
    observed = tuple(
        r.baseline for r in result.records if r.event_type == "spatial.applied-response"
    )
    expected = tuple(r for r in baseline.records if r.event_type == "spatial.response")
    assert tuple(canonical_json(r) for r in observed) == tuple(canonical_json(r) for r in expected)
    assert tuple(canonical_json(s) for s in result.final_rule_states) == tuple(
        canonical_json(s) for s in baseline.final_states
    )
    assert (
        result.counts.baseline_response_count
        == result.counts.applied_response_count
        == baseline.counts.response_count
    )
    assert result.counts.dual_state_update_count == baseline.counts.state_update_count
    identity = _module().apply_spatial_cognition_v2(baseline, frozen, original)
    assert canonical_json(result.counts) == canonical_json(identity.counts)
    for update, previous in zip(
        (r for r in result.records if r.event_type == "spatial.dual-state-updated"),
        (r for r in baseline.records if r.event_type == "spatial.state-updated"),
        strict=True,
    ):
        assert canonical_json(update.previous_rule_state) == canonical_json(previous.previous_state)
        assert canonical_json(update.rule_state) == canonical_json(previous.state)
        assert update.rule_state.purchase_intention == previous.state.purchase_intention
    return signals.summary.terminal_stream_sha256, tuple(
        canonical_json(s) for s in result.final_cognition_states
    )


def test_all_240_applicable_vectors_vary_signals_but_preserve_complete_rule_authority():
    hashes = set()
    states = set()
    for affect, relevance, credibility, salience in product(
        ("annoyed", "skeptical", "neutral", "curious", "positive"),
        ("none", "low", "medium", "high"),
        ("low", "medium", "high"),
        ("none", "low", "medium", "high"),
    ):
        digest, state = _assert_noninterference(affect, relevance, credibility, salience, ())
        hashes.add(digest)
        states.add(state)
    assert len(hashes) == 240
    assert len(states) > 1
