"""Atomic bounded cognition beside an independently validated rule-state track."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from hashlib import sha256
from math import fsum, prod
from typing import Annotated, Any, Literal, Self, TypeAlias, get_origin

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator

from adlife.core.domain.city_cognition import Hash
from adlife.core.domain.city_cognition_v2 import (
    _CITY_COGNITION_INFLUENCE_POLICY_V1_SHA256,
    CityCognitionAppliedSourceV2,
    CityCognitionAppliedVectorV2,
    CityCognitionBaselineProposalV2,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    CityCognitionNeutralizationStatusV2,
    CityCognitionRuleFactorsV2,
    CityCognitionTerminalRecordV2,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.city_cognition_v2 import apply_city_cognition_influence_v1
from adlife.core.simulation.spatial_response import (
    MAX_SPATIAL_RESPONSE_RECORDS,
    MAX_SPATIAL_RESPONSES,
    SpatialResponseCounts,
    SpatialResponseEvaluation,
    SpatialResponseState,
    SpatialRuleResponse,
    SpatialStateUpdated,
    _state_update_event_id,
    _updated_state,
)

_AgentId: TypeAlias = Annotated[str, Field(pattern=r"^person-[0-9]{3}$")]
_Identifier: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]
_Scope: TypeAlias = Literal["synthetic-bounded-cognition-not-observed-behavior"]
MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2 = 2_147_483_648
MAX_SPATIAL_RESPONSE_STATE_BYTES_V2 = 8_388_608
MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2 = 131_072


def _hash(value: DomainModel | dict[str, Any]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _event_hash(facts: dict[str, Any]) -> str:
    identity = dict(facts)
    if "baseline" in identity:
        identity["baseline"] = _hash(identity["baseline"])
    return _hash(identity)


def _same(left: DomainModel, right: DomainModel) -> bool:
    return canonical_json(left) == canonical_json(right)


class _SpatialResponseModelV2(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")

    @field_validator(
        "caused_by_event_ids",
        "records",
        "final_rule_states",
        "final_cognition_states",
        "states",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def json_tuples(cls, value: object, info: ValidationInfo) -> object:
        annotation = cls.model_fields[info.field_name].annotation if info.field_name else None
        if info.mode == "json" and get_origin(annotation) is tuple and isinstance(value, list):
            return tuple(value)
        return value

    @field_validator(
        "baseline", "previous_rule_state", "rule_state", mode="before", check_fields=False
    )
    @classmethod
    def checked_legacy(cls, value: object) -> object:
        if isinstance(value, SpatialRuleResponse):
            return revalidate_model(value, SpatialRuleResponse, label="embedded baseline response")
        if isinstance(value, SpatialResponseState):
            return revalidate_model(value, SpatialResponseState, label="embedded rule state")
        return value

    @field_validator("final_rule_states", "states", mode="before", check_fields=False)
    @classmethod
    def checked_legacy_states(cls, value: object) -> object:
        if isinstance(value, tuple):
            return tuple(
                revalidate_model(s, SpatialResponseState, label="rule state")
                if isinstance(s, SpatialResponseState)
                else s
                for s in value
            )
        return value

    @field_validator("influence_policy_sha256", check_fields=False)
    @classmethod
    def historical_policy(cls, value: str) -> str:
        if value != _CITY_COGNITION_INFLUENCE_POLICY_V1_SHA256:
            raise ValueError("influence hash must match the frozen V1 policy")
        return value

    @model_validator(mode="after")
    def event_identity(self) -> Self:
        if "event_id" in type(self).model_fields:
            facts = self.model_dump(mode="json")
            event_id = facts.pop("event_id")
            if event_id != _event_hash(facts):
                raise ValueError("V2 event ID must bind every canonical fact")
        return self

    @property
    def fingerprint(self) -> str:
        return _hash(self)


class SpatialCognitionStateV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    agent_id: _AgentId
    campaign_id: _Identifier
    synthetic_sentiment: float = Field(ge=-1, le=1)
    synthetic_recall: float = Field(ge=0, le=1)
    response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    last_response_minute: int | None = Field(ge=0, lt=10080)

    @model_validator(mode="after")
    def coherent_progress(self) -> Self:
        if (self.response_count == 0) != (self.last_response_minute is None):
            raise ValueError("cognition response history is inconsistent")
        return self


class SpatialAppliedResponseV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-response-v2"]
    claim_scope: _Scope
    event_type: Literal["spatial.applied-response"]
    event_id: Hash
    cognition_input_sha256: Hash
    baseline: SpatialRuleResponse
    terminal_record_hash: Hash | None
    unselected_marker: Literal["unselected-neutral"] | None
    source: CityCognitionAppliedSourceV2
    neutralization_status: CityCognitionNeutralizationStatusV2
    influence_policy_sha256: Hash
    previous_rule_state_sha256: Hash
    previous_cognition_state_sha256: Hash
    applied_sentiment_delta: float = Field(ge=-0.2, le=0.2)
    applied_recall_delta: float = Field(ge=0, le=0.3)

    @model_validator(mode="after")
    def coherent_applied(self) -> Self:
        if self.source == "unselected":
            valid = (
                self.terminal_record_hash is None and self.unselected_marker == "unselected-neutral"
            )
        else:
            valid = self.terminal_record_hash is not None and self.unselected_marker is None
        if not valid:
            raise ValueError(
                "applied evidence requires exactly its terminal hash or unselected marker"
            )
        CityCognitionAppliedVectorV2(
            schema_version=2,
            source=self.source,
            neutralization_status=self.neutralization_status,
            applied_sentiment_delta=self.applied_sentiment_delta,
            applied_recall_delta=self.applied_recall_delta,
        )
        if self.baseline.state_before_sha256 != self.previous_rule_state_sha256:
            raise ValueError("applied baseline does not bind its previous rule state")
        if self.neutralization_status != "none" and canonical_json(
            dict(s=self.applied_sentiment_delta, r=self.applied_recall_delta)
        ) != canonical_json(dict(s=self.baseline.sentiment_delta, r=self.baseline.recall_delta)):
            raise ValueError("neutralized response must preserve exact baseline deltas")
        return self


class SpatialDualStateUpdatedV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-response-v2"]
    claim_scope: _Scope
    event_type: Literal["spatial.dual-state-updated"]
    event_id: Hash
    caused_by_event_ids: tuple[Hash, ...] = Field(min_length=1, max_length=MAX_SPATIAL_RESPONSES)
    response_input_sha256: Hash
    scenario_sha256: Hash
    city_sha256: Hash
    cognition_input_sha256: Hash
    influence_policy_sha256: Hash
    campaign_id: _Identifier
    agent_id: _AgentId
    day_index: int = Field(ge=0, le=6)
    model_minute: int = Field(ge=0, lt=10080)
    previous_rule_state: SpatialResponseState
    rule_state: SpatialResponseState
    previous_cognition_state: SpatialCognitionStateV2
    cognition_state: SpatialCognitionStateV2

    @model_validator(mode="after")
    def coherent_update(self) -> Self:
        if self.day_index != self.model_minute // 1440 or len(set(self.caused_by_event_ids)) != len(
            self.caused_by_event_ids
        ):
            raise ValueError("dual update day or causes are inconsistent")
        for state in (
            self.previous_rule_state,
            self.rule_state,
            self.previous_cognition_state,
            self.cognition_state,
        ):
            if (state.agent_id, state.campaign_id) != (self.agent_id, self.campaign_id):
                raise ValueError("dual update state identities disagree")
        for previous, state in (
            (self.previous_rule_state, self.rule_state),
            (self.previous_cognition_state, self.cognition_state),
        ):
            if (
                state.response_count != previous.response_count + len(self.caused_by_event_ids)
                or state.last_response_minute != self.model_minute
                or (
                    previous.last_response_minute is not None
                    and previous.last_response_minute >= self.model_minute
                )
            ):
                raise ValueError("dual update progress does not match causes and minute")
        return self


SpatialResponseRecordV2: TypeAlias = Annotated[
    SpatialAppliedResponseV2 | SpatialDualStateUpdatedV2, Field(discriminator="event_type")
]


class SpatialResponseCountsV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    baseline_response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    applied_response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    dual_state_update_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    roadside_response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    phone_response_count: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    campaign_count: int = Field(ge=1, le=20)
    final_rule_state_count: int = Field(ge=1, le=600)
    final_cognition_state_count: int = Field(ge=1, le=600)

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        if (
            self.baseline_response_count != self.applied_response_count
            or self.baseline_response_count
            != self.roadside_response_count + self.phone_response_count
            or self.final_rule_state_count != self.final_cognition_state_count
        ):
            raise ValueError("dual response counts disagree")
        return self


def _initial_cognition(state: SpatialResponseState) -> SpatialCognitionStateV2:
    if state.response_count != 0 or state.last_response_minute is not None:
        raise ValueError("initial cognition requires a zero-history rule state")
    return SpatialCognitionStateV2(
        schema_version=2,
        agent_id=state.agent_id,
        campaign_id=state.campaign_id,
        synthetic_sentiment=state.brand_sentiment,
        synthetic_recall=state.recall_strength,
        response_count=0,
        last_response_minute=None,
    )


def _fold_cognition(
    previous: SpatialCognitionStateV2, group: list[SpatialAppliedResponseV2], minute: int
) -> SpatialCognitionStateV2:
    return SpatialCognitionStateV2(
        schema_version=2,
        agent_id=previous.agent_id,
        campaign_id=previous.campaign_id,
        synthetic_sentiment=min(
            1.0,
            max(
                -1.0,
                fsum((previous.synthetic_sentiment, *(r.applied_sentiment_delta for r in group))),
            ),
        ),
        synthetic_recall=min(
            1.0,
            max(
                0.0,
                1.0
                - (1.0 - previous.synthetic_recall)
                * prod(sorted(1.0 - r.applied_recall_delta for r in group)),
            ),
        ),
        response_count=previous.response_count + len(group),
        last_response_minute=minute,
    )


def _legacy_update(
    dual: SpatialDualStateUpdatedV2, group: list[SpatialAppliedResponseV2]
) -> SpatialStateUpdated:
    causes = tuple(item.baseline.event_id for item in group)
    return SpatialStateUpdated(
        event_id=_state_update_event_id(
            response_input_sha256=dual.response_input_sha256,
            model_minute=dual.model_minute,
            agent_id=dual.agent_id,
            campaign_id=dual.campaign_id,
            caused_by_event_ids=causes,
        ),
        caused_by_event_ids=causes,
        response_input_sha256=dual.response_input_sha256,
        scenario_sha256=dual.scenario_sha256,
        city_sha256=dual.city_sha256,
        campaign_id=dual.campaign_id,
        agent_id=dual.agent_id,
        day_index=dual.day_index,
        model_minute=dual.model_minute,
        previous_state=dual.previous_rule_state,
        state=dual.rule_state,
    )


class SpatialResponseEvaluationV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-response-v2"]
    claim_scope: _Scope
    response_input_sha256: Hash
    scenario_sha256: Hash
    city_sha256: Hash
    attention_seed: int = Field(ge=0, le=2**63 - 1)
    cognition_input_sha256: Hash
    influence_policy_sha256: Hash
    counts: SpatialResponseCountsV2
    records: tuple[SpatialResponseRecordV2, ...] = Field(max_length=MAX_SPATIAL_RESPONSE_RECORDS)
    final_rule_states: tuple[SpatialResponseState, ...] = Field(min_length=1, max_length=600)
    final_cognition_states: tuple[SpatialCognitionStateV2, ...] = Field(
        min_length=1, max_length=600
    )

    @model_validator(mode="after")
    def coherent_evaluation(self) -> Self:
        _reconstruct_rule_evaluation(self)
        for _ in _bounded_lines(self.records):
            pass
        return self


def _reconstruct_rule_evaluation(
    evaluation: SpatialResponseEvaluationV2,
) -> SpatialResponseEvaluation:
    """Validate observed stream grammar, cognition folds, and complete V1 authority."""
    legacy: list[SpatialRuleResponse | SpatialStateUpdated] = []
    group: list[SpatialAppliedResponseV2] = []
    latest: dict[tuple[str, str], SpatialCognitionStateV2] = {}
    ids: set[str] = set()
    for record in evaluation.records:
        if record.event_id in ids:
            raise ValueError("V2 response event IDs must be unique")
        ids.add(record.event_id)
        if (
            record.cognition_input_sha256 != evaluation.cognition_input_sha256
            or record.influence_policy_sha256 != evaluation.influence_policy_sha256
        ):
            raise ValueError("V2 record disagrees with evaluation provenance")
        if isinstance(record, SpatialAppliedResponseV2):
            group.append(record)
            continue
        if not group or tuple(r.event_id for r in group) != record.caused_by_event_ids:
            raise ValueError("each applied group requires exactly its trailing dual update")
        key = record.agent_id, record.campaign_id
        previous = latest.get(key)
        if previous is None:
            previous = _initial_cognition(record.previous_rule_state)
        if not _same(previous, record.previous_cognition_state):
            raise ValueError("cognition previous state is not the immutable group snapshot")
        for applied in group:
            b = applied.baseline
            if (
                (b.model_minute, b.agent_id, b.campaign_id) != (record.model_minute, *key)
                or applied.previous_rule_state_sha256 != _hash(record.previous_rule_state)
                or applied.previous_cognition_state_sha256 != _hash(previous)
            ):
                raise ValueError("applied group does not share both exact previous states")
        expected = _fold_cognition(previous, group, record.model_minute)
        if not _same(expected, record.cognition_state):
            raise ValueError("cognition state does not match the exact retained-headroom fold")
        baselines = tuple(r.baseline for r in group)
        expected_rule = _updated_state(
            record.previous_rule_state, baselines, model_minute=record.model_minute
        )
        if not _same(expected_rule, record.rule_state):
            raise ValueError("rule state does not match the deterministic baseline fold")
        legacy.extend(baselines)
        legacy.append(_legacy_update(record, group))
        latest[key] = record.cognition_state
        group = []
    if group:
        raise ValueError("an applied group is missing its trailing dual update")
    counts = evaluation.counts
    if len(evaluation.records) != counts.applied_response_count + counts.dual_state_update_count:
        raise ValueError("V2 records do not match counts")
    rule = SpatialResponseEvaluation(
        response_input_sha256=evaluation.response_input_sha256,
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        attention_seed=evaluation.attention_seed,
        counts=SpatialResponseCounts(
            response_count=counts.baseline_response_count,
            state_update_count=counts.dual_state_update_count,
            roadside_response_count=counts.roadside_response_count,
            phone_response_count=counts.phone_response_count,
            campaign_count=counts.campaign_count,
            final_state_count=counts.final_rule_state_count,
        ),
        records=tuple(legacy),
        final_states=evaluation.final_rule_states,
    )
    # Legacy models do not recursively revalidate pre-existing model instances.
    rule = revalidate_model(rule, SpatialResponseEvaluation, label="reconstructed rule evaluation")
    rule_keys = tuple((s.agent_id, s.campaign_id) for s in rule.final_states)
    cognitive_keys = tuple((s.agent_id, s.campaign_id) for s in evaluation.final_cognition_states)
    if cognitive_keys != rule_keys or len(cognitive_keys) != counts.final_cognition_state_count:
        raise ValueError("final cognition states must match canonical complete rule identities")
    for state, rule_state in zip(evaluation.final_cognition_states, rule.final_states, strict=True):
        expected_final = latest.get((state.agent_id, state.campaign_id))
        if expected_final is None:
            expected_final = _initial_cognition(rule_state)
        if (
            not _same(state, expected_final)
            or state.response_count != rule_state.response_count
            or state.last_response_minute != rule_state.last_response_minute
        ):
            raise ValueError("final cognition state does not match the complete state chain")
    return rule


def _proposal(
    baseline: SpatialRuleResponse, update: SpatialStateUpdated, activity: str
) -> CityCognitionBaselineProposalV2:
    factors = {
        field: getattr(baseline, field)
        for field in CityCognitionRuleFactorsV2.model_fields
        if field not in {"schema_version", "baseline_sentiment_delta", "baseline_recall_delta"}
    }
    return CityCognitionBaselineProposalV2.model_validate(
        dict(
            schema_version=2,
            baseline_response_id=baseline.event_id,
            notice_event_id=baseline.caused_by,
            opportunity_id=baseline.opportunity_id,
            baseline_state_update_event_id=update.event_id,
            baseline_state_update_cause_count=len(update.caused_by_event_ids),
            agent_id=baseline.agent_id,
            campaign_id=baseline.campaign_id,
            placement_id=baseline.placement_id,
            channel=baseline.channel,
            activity=activity,
            day_index=baseline.day_index,
            model_minute=baseline.model_minute,
            millisecond_within_minute=baseline.millisecond_within_minute,
            pre_minute_rule_state_sha256=baseline.state_before_sha256,
            rule_factors=dict(
                schema_version=2,
                baseline_sentiment_delta=baseline.sentiment_delta,
                baseline_recall_delta=baseline.recall_delta,
                **factors,
            ),
        )
    )


def _terminal_bindings(
    baseline: SpatialResponseEvaluation,
    frozen: CityCognitionInputV2,
    signals: CityCognitionEvaluationV2,
) -> dict[str, CityCognitionTerminalRecordV2]:
    if (
        signals.cognition_input_sha256,
        signals.run_input_sha256,
        signals.mode,
        signals.effect_scope,
    ) != (frozen.fingerprint, frozen.run_input_sha256, frozen.mode, frozen.effect_scope):
        raise ValueError("cognition evaluation does not match the complete frozen input")
    responses = sorted(
        (r for r in baseline.records if isinstance(r, SpatialRuleResponse)),
        key=lambda r: (r.model_minute, r.agent_id, r.campaign_id, r.event_id),
    )
    counts: Counter[str] = Counter()
    selected: list[SpatialRuleResponse] = []
    for response in responses:
        if counts[response.agent_id] < 4 and len(selected) < 60:
            selected.append(response)
            counts[response.agent_id] += 1
    if tuple(r.baseline_response_id for r in signals.records) != tuple(
        r.event_id for r in selected
    ) or len(frozen.reservations) != len(selected):
        raise ValueError("terminal evidence does not match canonical baseline selection")
    coverage = signals.summary.coverage
    if coverage.eligible_response_count != len(responses) or coverage.funded_request_count != min(
        len(selected), frozen.funded_response_limit
    ):
        raise ValueError("cognition coverage does not match the complete baseline and funding")
    updates = {
        cause: r
        for r in baseline.records
        if isinstance(r, SpatialStateUpdated)
        for cause in r.caused_by_event_ids
    }
    contract = frozen.causal_contract
    profile, settings = frozen.provider_profile, frozen.effective_settings
    for response, terminal, reservation in zip(
        selected, signals.records, frozen.reservations, strict=True
    ):
        if (
            reservation.selection_index,
            reservation.request_id,
            reservation.request_record_sha256,
        ) != (terminal.selection_index, terminal.request_id, terminal.request_record_sha256):
            raise ValueError("terminal does not match its exact frozen reservation")
        allowed = (
            {frozen.mode}
            if frozen.mode in {"rules", "mock"}
            else {"provider", "cache", "fallback"}
            if reservation.funded
            else {"budget-skipped"}
        )
        if terminal.source not in allowed:
            raise ValueError("terminal source does not match the funded reservation")
        if (
            settings is not None
            and terminal.attempt_count > 1 + settings.transient_retries + settings.repair_attempts
        ):
            raise ValueError("terminal attempts exceed effective profile settings")
        if any(
            getattr(terminal, f) != getattr(baseline, f)
            for f in ("response_input_sha256", "scenario_sha256", "city_sha256")
        ):
            raise ValueError("terminal science does not match baseline")
        proposal = _proposal(response, updates[response.event_id], terminal.activity)
        if terminal.baseline_sha256 != proposal.fingerprint or any(
            getattr(terminal, f) != getattr(proposal, f)
            for f in CityCognitionBaselineProposalV2.model_fields
            if f not in {"schema_version", "pre_minute_rule_state_sha256", "rule_factors"}
        ):
            raise ValueError("terminal does not bind the complete V2 proposal and group")
        expected_bindings = dict(
            run_input_sha256=frozen.run_input_sha256,
            causal_contract_sha256=frozen.causal_contract_sha256,
            provider_profile_id=None if profile is None else profile.profile_id,
            provider_revision_sha256=None if profile is None else profile.revision_sha256,
            provider_profile_snapshot_sha256=None if profile is None else profile.fingerprint,
            provider_protocol=None if profile is None else profile.protocol,
            provider_effective_settings_sha256=None if settings is None else settings.fingerprint,
        )
        expected_bindings.update(
            {
                f: getattr(contract, f)
                for f in (
                    "prompt_contract_sha256",
                    "result_schema_contract_sha256",
                    "selection_policy_sha256",
                    "fallback_policy_sha256",
                    "mock_policy_sha256",
                    "influence_policy_sha256",
                )
            }
        )
        if any(getattr(terminal, f) != value for f, value in expected_bindings.items()):
            raise ValueError("terminal policy/profile/run does not match frozen input")
    return {r.baseline_response_id: r for r in signals.records}


def _make_applied(facts: dict[str, Any]) -> SpatialAppliedResponseV2:
    return SpatialAppliedResponseV2.model_validate(facts | {"event_id": _event_hash(facts)})


def _make_dual(facts: dict[str, Any]) -> SpatialDualStateUpdatedV2:
    return SpatialDualStateUpdatedV2.model_validate(facts | {"event_id": _event_hash(facts)})


def apply_spatial_cognition_v2(
    baseline_evaluation: SpatialResponseEvaluation,
    cognition_input: CityCognitionInputV2,
    cognition_evaluation: CityCognitionEvaluationV2,
) -> SpatialResponseEvaluationV2:
    """Commit complete immutable rule/cognition groups using only frozen signals."""
    baseline = revalidate_model(
        baseline_evaluation, SpatialResponseEvaluation, label="baseline evaluation"
    )
    frozen = revalidate_model(cognition_input, CityCognitionInputV2, label="cognition input")
    signals = revalidate_model(
        cognition_evaluation, CityCognitionEvaluationV2, label="cognition evaluation"
    )
    terminals = _terminal_bindings(baseline, frozen, signals)
    records: list[SpatialResponseRecordV2] = []
    group: list[SpatialRuleResponse] = []
    latest: dict[tuple[str, str], SpatialCognitionStateV2] = {}
    common = dict(
        schema_version=2,
        model_id="spatial-response-v2",
        claim_scope=frozen.causal_contract.disclosure,
        cognition_input_sha256=frozen.fingerprint,
        influence_policy_sha256=frozen.causal_contract.influence_policy_sha256,
    )
    for record in baseline.records:
        if isinstance(record, SpatialRuleResponse):
            group.append(record)
            continue
        key = record.agent_id, record.campaign_id
        previous = latest.get(key)
        if previous is None:
            previous = _initial_cognition(record.previous_state)
        applied = []
        for response in group:
            terminal = terminals.get(response.event_id)
            vector = apply_city_cognition_influence_v1(
                response.sentiment_delta,
                response.recall_delta,
                source="unselected" if terminal is None else terminal.source,
                result=None if terminal is None else terminal.result,
                policy=frozen.causal_contract.influence_policy,
            )
            applied.append(
                _make_applied(
                    common
                    | dict(
                        event_type="spatial.applied-response",
                        baseline=response.model_dump(mode="python"),
                        terminal_record_hash=None if terminal is None else terminal.record_hash,
                        unselected_marker="unselected-neutral" if terminal is None else None,
                        source=vector.source,
                        neutralization_status=vector.neutralization_status,
                        previous_rule_state_sha256=_hash(record.previous_state),
                        previous_cognition_state_sha256=previous.fingerprint,
                        applied_sentiment_delta=vector.applied_sentiment_delta,
                        applied_recall_delta=vector.applied_recall_delta,
                    )
                )
            )
        state = _fold_cognition(previous, applied, record.model_minute)
        dual = _make_dual(
            common
            | dict(
                event_type="spatial.dual-state-updated",
                caused_by_event_ids=tuple(r.event_id for r in applied),
                response_input_sha256=baseline.response_input_sha256,
                scenario_sha256=baseline.scenario_sha256,
                city_sha256=baseline.city_sha256,
                campaign_id=record.campaign_id,
                agent_id=record.agent_id,
                day_index=record.day_index,
                model_minute=record.model_minute,
                previous_rule_state=record.previous_state.model_dump(mode="python"),
                rule_state=record.state.model_dump(mode="python"),
                previous_cognition_state=previous.model_dump(mode="python"),
                cognition_state=state.model_dump(mode="python"),
            )
        )
        records.extend(applied)
        records.append(dual)
        latest[key] = state
        group = []
    final = tuple(
        latest.get((s.agent_id, s.campaign_id)) or _initial_cognition(s)
        for s in baseline.final_states
    )
    c = baseline.counts
    counts = SpatialResponseCountsV2(
        schema_version=2,
        baseline_response_count=c.response_count,
        applied_response_count=c.response_count,
        dual_state_update_count=c.state_update_count,
        roadside_response_count=c.roadside_response_count,
        phone_response_count=c.phone_response_count,
        campaign_count=c.campaign_count,
        final_rule_state_count=c.final_state_count,
        final_cognition_state_count=c.final_state_count,
    )
    evaluation = SpatialResponseEvaluationV2.model_validate(
        common
        | dict(
            response_input_sha256=baseline.response_input_sha256,
            scenario_sha256=baseline.scenario_sha256,
            city_sha256=baseline.city_sha256,
            attention_seed=baseline.attention_seed,
            counts=counts,
            records=tuple(records),
            final_rule_states=baseline.final_states,
            final_cognition_states=final,
        )
    )
    if not _same(_reconstruct_rule_evaluation(evaluation), baseline):
        raise ValueError("V2 rule evidence must preserve the exact complete source baseline")
    return evaluation


def _state_keys(
    states: Sequence[SpatialResponseState | SpatialCognitionStateV2],
) -> tuple[tuple[str, str], ...]:
    keys = tuple((s.agent_id, s.campaign_id) for s in states)
    agents = {key[0] for key in keys}
    campaigns = {key[1] for key in keys}
    if (
        keys != tuple(sorted(set(keys)))
        or len(agents) > 30
        or len(campaigns) > 20
        or set(keys) != {(a, c) for a in agents for c in campaigns}
    ):
        raise ValueError("state documents require canonical complete Cartesian identities")
    return keys


def _document_bytes(document: DomainModel) -> bytes:
    return (canonical_json(document) + "\n").encode("utf-8")


class SpatialRuleStateDocumentV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-rule-state-v2"]
    states: tuple[SpatialResponseState, ...] = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def canonical_states(self) -> Self:
        _state_keys(self.states)
        if len(_document_bytes(self)) > MAX_SPATIAL_RESPONSE_STATE_BYTES_V2:
            raise ValueError("rule state document exceeds its byte limit")
        return self


class SpatialCognitionStateDocumentV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-cognition-state-v2"]
    states: tuple[SpatialCognitionStateV2, ...] = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def canonical_states(self) -> Self:
        _state_keys(self.states)
        if len(_document_bytes(self)) > MAX_SPATIAL_RESPONSE_STATE_BYTES_V2:
            raise ValueError("cognition state document exceeds its byte limit")
        return self


class SpatialResponseStateDocumentV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-response-state-v2"]
    claim_scope: _Scope
    response_input_sha256: Hash
    scenario_sha256: Hash
    city_sha256: Hash
    cognition_input_sha256: Hash
    rule: SpatialRuleStateDocumentV2
    cognition: SpatialCognitionStateDocumentV2

    @model_validator(mode="after")
    def complete_states(self) -> Self:
        if _state_keys(self.rule.states) != _state_keys(self.cognition.states):
            raise ValueError("rule/cognition state documents must cover identical identities")
        for rule, cognitive in zip(self.rule.states, self.cognition.states, strict=True):
            if (rule.response_count, rule.last_response_minute) != (
                cognitive.response_count,
                cognitive.last_response_minute,
            ):
                raise ValueError("rule/cognition state progress must match")
        if len(_document_bytes(self)) > MAX_SPATIAL_RESPONSE_STATE_BYTES_V2:
            raise ValueError("dual state document exceeds its byte limit")
        return self


class SpatialResponseArtifactSummaryV2(_SpatialResponseModelV2):
    schema_version: Literal[2]
    model_id: Literal["spatial-response-artifact-v2"]
    claim_scope: _Scope
    response_model_id: Literal["spatial-response-v2"]
    response_input_sha256: Hash
    scenario_sha256: Hash
    city_sha256: Hash
    cognition_input_sha256: Hash
    influence_policy_sha256: Hash
    attention_seed: int = Field(ge=0, le=2**63 - 1)
    stream_sha256: Hash
    stream_bytes: int = Field(ge=0, le=2_147_483_648)
    state_document_sha256: Hash
    state_document_bytes: int = Field(ge=1, le=8_388_608)
    rule_state_document_sha256: Hash
    cognition_state_document_sha256: Hash
    counts: SpatialResponseCountsV2

    @model_validator(mode="after")
    def bounded_summary(self) -> Self:
        if len(_document_bytes(self)) > MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2:
            raise ValueError("response summary exceeds its byte limit")
        return self


def _bounded_lines(records: tuple[SpatialResponseRecordV2, ...]) -> Iterator[bytes]:
    size = 0
    for record in records:
        line = _document_bytes(record)
        size += len(line)
        if size > MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2:
            raise ValueError("V2 response stream exceeds its byte limit")
        yield line


def _checked_evaluation(evaluation: SpatialResponseEvaluationV2) -> SpatialResponseEvaluationV2:
    return revalidate_model(evaluation, SpatialResponseEvaluationV2, label="V2 response evaluation")


def spatial_response_v2_lines(evaluation: SpatialResponseEvaluationV2) -> Iterator[bytes]:
    """Yield validated canonical lines without joining a potentially large stream."""
    yield from _bounded_lines(_checked_evaluation(evaluation).records)


def spatial_rule_state_document_v2(
    evaluation: SpatialResponseEvaluationV2,
) -> SpatialRuleStateDocumentV2:
    evaluation = _checked_evaluation(evaluation)
    return SpatialRuleStateDocumentV2(
        schema_version=2, model_id="spatial-rule-state-v2", states=evaluation.final_rule_states
    )


def spatial_cognition_state_document_v2(
    evaluation: SpatialResponseEvaluationV2,
) -> SpatialCognitionStateDocumentV2:
    evaluation = _checked_evaluation(evaluation)
    return SpatialCognitionStateDocumentV2(
        schema_version=2,
        model_id="spatial-cognition-state-v2",
        states=evaluation.final_cognition_states,
    )


def spatial_response_state_document_v2(
    evaluation: SpatialResponseEvaluationV2,
) -> SpatialResponseStateDocumentV2:
    evaluation = _checked_evaluation(evaluation)
    return SpatialResponseStateDocumentV2(
        schema_version=2,
        model_id="spatial-response-state-v2",
        claim_scope=evaluation.claim_scope,
        response_input_sha256=evaluation.response_input_sha256,
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        cognition_input_sha256=evaluation.cognition_input_sha256,
        rule=spatial_rule_state_document_v2(evaluation),
        cognition=spatial_cognition_state_document_v2(evaluation),
    )


def summarize_spatial_response_artifact_v2(
    evaluation: SpatialResponseEvaluationV2,
) -> SpatialResponseArtifactSummaryV2:
    evaluation = _checked_evaluation(evaluation)
    digest = sha256()
    size = 0
    for line in _bounded_lines(evaluation.records):
        size += len(line)
        digest.update(line)
    state = spatial_response_state_document_v2(evaluation)
    return SpatialResponseArtifactSummaryV2(
        schema_version=2,
        model_id="spatial-response-artifact-v2",
        claim_scope=evaluation.claim_scope,
        response_model_id=evaluation.model_id,
        response_input_sha256=evaluation.response_input_sha256,
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        cognition_input_sha256=evaluation.cognition_input_sha256,
        influence_policy_sha256=evaluation.influence_policy_sha256,
        attention_seed=evaluation.attention_seed,
        stream_sha256=digest.hexdigest(),
        stream_bytes=size,
        state_document_sha256=sha256(_document_bytes(state)).hexdigest(),
        state_document_bytes=len(_document_bytes(state)),
        rule_state_document_sha256=sha256(_document_bytes(state.rule)).hexdigest(),
        cognition_state_document_sha256=sha256(_document_bytes(state.cognition)).hexdigest(),
        counts=evaluation.counts,
    )
