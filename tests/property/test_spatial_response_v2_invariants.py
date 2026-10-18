"""Provider categories cannot alter deterministic rule authority."""

from functools import lru_cache
from itertools import product

from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation import city_cognition_v2 as cognition
from tests.unit.city.test_city_cognition_v2 import _dense_evidence, _rehash_terminal
from tests.unit.city.test_spatial_response_v2 import _evidence, _module


@lru_cache(maxsize=1)
def _frozen():
    baseline, frozen, signals = _evidence(dense=True)
    context, mobility, same_baseline = _dense_evidence(agent_count=2)
    requests = cognition.build_city_cognition_requests_v2(context, mobility, same_baseline)
    return baseline, frozen, signals, requests


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
