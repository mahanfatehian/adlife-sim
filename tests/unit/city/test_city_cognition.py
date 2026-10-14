from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from hashlib import sha256

import pytest
from pydantic import BaseModel, ValidationError

from adlife.city.workbench_input import WorkbenchRunDraftV2
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
    CITY_COGNITION_DEFAULT_SELECTION_POLICY,
    CITY_COGNITION_DISCLOSURE,
    CITY_COGNITION_FALLBACK_POLICY_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_TEXT,
    CITY_COGNITION_RESULT_SCHEMA_DOCUMENT,
    CITY_COGNITION_RESULT_SCHEMA_SHA256,
    CITY_COGNITION_SELECTION_POLICY_SHA256,
    MAX_CITY_COGNITION_REQUEST_BYTES,
    MAX_CITY_COGNITION_STATE_UPDATE_CAUSES,
    MAX_CITY_COGNITION_TERMINAL_RECORD_BYTES,
    MAX_CITY_COGNITION_USAGE_INTEGER,
    CityCognitionAgentContextV1,
    CityCognitionCampaignContextV1,
    CityCognitionCommittedStateV1,
    CityCognitionCreativeSnapshotV1,
    CityCognitionEffectiveSettingsV1,
    CityCognitionInputV1,
    CityCognitionProviderProfileSnapshotV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
    CityCognitionRunContextV1,
    CityCognitionTerminalRecordV1,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition import (
    allocate_city_cognition_reservations,
    build_city_budget_skipped_result,
    build_city_cognition_requests,
    build_city_cognition_terminal_record,
    build_city_fallback_result,
    build_city_mock_result,
    build_city_rules_result,
    validate_city_cognition_evaluation,
)
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from adlife.core.simulation.spatial_response import evaluate_spatial_responses
from tests.unit.city.test_workbench_input import draft_v2_data
from tests.unit.city.test_workbench_validation import persona_draft


def _evidence(draft: WorkbenchRunDraftV2 | None = None):
    compiled = construct_workbench_run(persona_draft() if draft is None else draft)
    settings = compiled.workbench_input.draft.settings
    opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
    attention = evaluate_spatial_attention(opportunities, seed=settings.seed)
    responses = evaluate_spatial_responses(
        compiled.response_input,
        compiled.scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
    )
    population = compiled.workbench_input.draft.population
    persona_by_id = {persona.persona_id: persona for persona in population.personas}
    profile_by_id = {profile.agent_id: profile for profile in compiled.response_input.profiles}
    agents = []
    for assignment in population.assignments:
        persona = persona_by_id[assignment.persona_id]
        profile = profile_by_id[assignment.agent_id]
        agents.append(
            CityCognitionAgentContextV1(
                agent_id=assignment.agent_id,
                persona_id=assignment.persona_id,
                fictional=True,
                display_name=persona.display_name,
                age=persona.age,
                occupation=persona.occupation,
                background=persona.background,
                mbti=persona.mbti,
                interests=tuple(sorted(profile.interests)),
                traits=profile.traits,
            )
        )
    template = compiled.workbench_input.creative_template
    campaign_input = compiled.workbench_input.draft.scenario.campaign
    creative = CityCognitionCreativeSnapshotV1(
        template_id=template.template_id,
        template_version=template.template_version,
        product_name=template.product_name,
        product_category=template.product_category,
        message=template.message,
        call_to_action=template.call_to_action,
        disclosure=template.disclosure,
    )
    campaign = CityCognitionCampaignContextV1(
        campaign_id=campaign_input.campaign_id,
        name=campaign_input.name,
        creative_sha256=template.fingerprint,
        creative=creative,
        target_interests=tuple(sorted(campaign_input.target_interests)),
        relative_price=campaign_input.relative_price,
    )
    context = CityCognitionRunContextV1(
        run_input_sha256=compiled.workbench_input.fingerprint,
        response_input_sha256=responses.response_input_sha256,
        scenario_sha256=responses.scenario_sha256,
        city_sha256=responses.city_sha256,
        agents=tuple(agents),
        campaigns=(campaign,),
    )
    return compiled, responses, context


def _input(
    context,
    requests,
    *,
    mode: str = "rules",
    provider_profile: CityCognitionProviderProfileSnapshotV1 | None = None,
    aggregate_output_token_ceiling: int = 0,
) -> CityCognitionInputV1:
    effective_settings = None if provider_profile is None else _effective_settings(provider_profile)
    return CityCognitionInputV1(
        schema_version=1,
        mode=mode,
        run_input_sha256=context.run_input_sha256,
        explainer_id=(
            f"city-{mode}-explainer-v1"
            if mode in {"rules", "mock"}
            else "city-provider-explainer-v1"
        ),
        provider_profile=provider_profile,
        effective_settings=effective_settings,
        prompt_contract_version=1,
        prompt_contract_text=CITY_COGNITION_PROMPT_CONTRACT_TEXT,
        prompt_contract_sha256=CITY_COGNITION_PROMPT_CONTRACT_SHA256,
        result_schema_version=1,
        result_schema_document=CITY_COGNITION_RESULT_SCHEMA_DOCUMENT,
        result_schema_contract_sha256=CITY_COGNITION_RESULT_SCHEMA_SHA256,
        selection_policy_version=1,
        selection_policy=CITY_COGNITION_DEFAULT_SELECTION_POLICY,
        selection_policy_sha256=CITY_COGNITION_SELECTION_POLICY_SHA256,
        fallback_policy_version=1,
        fallback_policy=CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
        fallback_policy_sha256=CITY_COGNITION_FALLBACK_POLICY_SHA256,
        selected_requests_per_agent=4,
        selected_requests_per_run=60,
        concurrency_limit=4,
        max_request_bytes=8_192,
        max_wire_response_bytes=1_048_576,
        max_output_tokens_per_attempt=384,
        reservation_tokens_per_request=1_536,
        initial_attempts=1,
        transient_retries_ceiling=2,
        repair_attempts_ceiling=1,
        attempt_timeout_seconds_ceiling=30,
        aggregate_deadline_seconds=180,
        aggregate_output_token_ceiling=aggregate_output_token_ceiling,
        reservations=allocate_city_cognition_reservations(
            requests,
            aggregate_output_token_ceiling=aggregate_output_token_ceiling,
        ),
    )


def _provider_profile() -> CityCognitionProviderProfileSnapshotV1:
    facts: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "local-test",
        "credential_slot_id": "local-test",
        "display_label": "Local test provider",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "fictional-model",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.0,
        "top_p": 1.0,
        "seed_policy": "omit",
        "seed": None,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "local-ollama",
    }
    return CityCognitionProviderProfileSnapshotV1(
        **facts,
        revision_sha256=city_cognition_provider_profile_revision_sha256(facts),
    )


def _effective_settings(
    profile: CityCognitionProviderProfileSnapshotV1,
) -> CityCognitionEffectiveSettingsV1:
    return CityCognitionEffectiveSettingsV1(
        schema_version=1,
        protocol=profile.protocol,
        temperature=profile.temperature,
        top_p=profile.top_p,
        seed_policy=profile.seed_policy,
        seed=profile.seed,
        max_output_tokens=profile.max_output_tokens,
        attempt_timeout_seconds=profile.attempt_timeout_seconds,
        transient_retries=profile.transient_retries,
        repair_attempts=profile.repair_attempts,
    )


def _rules_records(requests):
    return tuple(
        build_city_cognition_terminal_record(
            request,
            selection_index=selection_index,
            source="rules",
            result=build_city_rules_result(request),
        )
        for selection_index, request in enumerate(requests)
    )


def _single_rules_evaluation():
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    cognition_input = _input(context, (request,))
    return validate_city_cognition_evaluation(
        cognition_input,
        (request,),
        _rules_records((request,)),
    )


def _rebuild_request(
    request: CityCognitionRequestV1,
    **updates: object,
) -> CityCognitionRequestV1:
    document = request.model_dump(mode="python")
    document.update(
        {
            key: value.model_dump(mode="python") if isinstance(value, BaseModel) else value
            for key, value in updates.items()
        }
    )
    request_facts = {key: value for key, value in document.items() if key != "request_id"}
    document["request_id"] = (
        f"cog-{sha256(canonical_json(request_facts).encode('utf-8')).hexdigest()}"
    )
    return CityCognitionRequestV1.model_validate(document)


def _rebuild_record(
    record: CityCognitionTerminalRecordV1,
    **updates: object,
) -> CityCognitionTerminalRecordV1:
    document = record.model_dump(mode="python")
    document.update(
        {
            key: value.model_dump(mode="python") if isinstance(value, BaseModel) else value
            for key, value in updates.items()
        }
    )
    record_facts = {key: value for key, value in document.items() if key != "record_hash"}
    document["record_hash"] = sha256(canonical_json(record_facts).encode("utf-8")).hexdigest()
    return CityCognitionTerminalRecordV1.model_validate(document)


def _evaluation_document_with_records(evaluation, records) -> dict[str, object]:
    document = evaluation.model_dump(mode="json")
    record_documents = [record.model_dump(mode="json") for record in records]
    stream = b"".join(
        (canonical_json(record_document) + "\n").encode("utf-8")
        for record_document in record_documents
    )
    document["records"] = record_documents
    document["summary"]["terminal_stream_sha256"] = sha256(stream).hexdigest()
    document["summary"]["terminal_stream_bytes"] = len(stream)
    return document


def test_request_selection_is_causal_canonical_bounded_and_content_addressed() -> None:
    compiled, responses, context = _evidence()

    requests = build_city_cognition_requests(context, compiled.mobility, responses)

    assert requests
    assert len(requests) <= 60
    assert max(Counter(request.agent_id for request in requests).values()) <= 4
    assert tuple(
        (request.model_minute, request.agent_id, request.campaign_id, request.response_event_id)
        for request in requests
    ) == tuple(
        sorted(
            (
                request.model_minute,
                request.agent_id,
                request.campaign_id,
                request.response_event_id,
            )
            for request in requests
        )
    )
    response_by_id = {
        record.event_id: record
        for record in responses.records
        if record.event_type == "spatial.response"
    }
    update_by_cause = {
        cause: record
        for record in responses.records
        if record.event_type == "spatial.state-updated"
        for cause in record.caused_by_event_ids
    }
    for request in requests:
        response = response_by_id[request.response_event_id]
        update = update_by_cause[response.event_id]
        position = next(
            item
            for item in compiled.mobility.frame(response.model_minute)
            if item.agent_id == response.agent_id
        )
        assert request.request_id == f"cog-{request.request_facts_sha256}"
        assert "selection_index" not in type(request).model_fields
        assignment = next(
            item
            for item in compiled.workbench_input.draft.population.assignments
            if item.agent_id == request.agent_id
        )
        assert request.persona.persona_id == assignment.persona_id
        assert request.state_update_event_id == update.event_id
        assert request.millisecond_within_minute == response.millisecond_within_minute
        assert request.state_update_cause_count == len(update.caused_by_event_ids)
        assert request.committed_state.model_dump(mode="json") == update.state.model_dump(
            mode="json"
        )
        assert isinstance(request.committed_state, CityCognitionCommittedStateV1)
        assert type(request.committed_state).__module__ == "adlife.core.domain.city_cognition"
        assert request.activity == position.activity
        assert request.disclosure == CITY_COGNITION_DISCLOSURE


def test_request_canonical_byte_ceiling_rejects_valid_length_multibyte_aggregate() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]

    def bounded_text(length: int, suffix: str = "") -> str:
        return "界" * (length - len(suffix)) + suffix

    persona_document = request.persona.model_dump(mode="python")
    persona_document.update(
        {
            "display_name": bounded_text(80),
            "occupation": bounded_text(80),
            "background": bounded_text(800),
            "interests": tuple(bounded_text(80, f"{index:02d}") for index in range(12)),
        }
    )
    persona = CityCognitionAgentContextV1.model_validate(persona_document)
    creative_document = request.campaign.creative.model_dump(mode="python")
    creative_document.update(
        {
            "product_name": bounded_text(80),
            "product_category": bounded_text(80),
            "message": bounded_text(240),
            "call_to_action": bounded_text(120),
        }
    )
    creative = CityCognitionCreativeSnapshotV1.model_validate(creative_document)
    campaign_document = request.campaign.model_dump(mode="python")
    campaign_document.update(
        {
            "name": bounded_text(120),
            "creative_sha256": creative.fingerprint,
            "creative": creative.model_dump(mode="python"),
            "target_interests": tuple(bounded_text(80, f"{index:02d}") for index in range(12)),
        }
    )
    campaign = CityCognitionCampaignContextV1.model_validate(campaign_document)
    request_document = request.model_dump(mode="python")
    request_document.update(
        {
            "persona": persona.model_dump(mode="python"),
            "campaign": campaign.model_dump(mode="python"),
        }
    )
    request_facts = {key: value for key, value in request_document.items() if key != "request_id"}
    request_document["request_id"] = (
        f"cog-{sha256(canonical_json(request_facts).encode('utf-8')).hexdigest()}"
    )
    canonical_bytes = len(canonical_json(request_document).encode("utf-8"))
    assert canonical_bytes > MAX_CITY_COGNITION_REQUEST_BYTES

    with pytest.raises(ValidationError, match="canonical size limit"):
        CityCognitionRequestV1.model_validate(request_document)


def test_terminal_maximum_field_document_remains_below_canonical_byte_ceiling() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]

    def bounded_text(length: int, suffix: str = "") -> str:
        return "界" * (length - len(suffix)) + suffix

    result = CityCognitionResultV1(
        schema_version=1,
        request_id=request.request_id,
        interpretation=bounded_text(480),
        decision_factors=tuple(bounded_text(120, str(index)) for index in range(3)),
        safety_flags=("stereotype-risk", "unsupported-inference", "unsafe-content"),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source="provider",
        result=result,
        attempt_count=4,
        provider_profile=_provider_profile(),
        prompt_tokens=MAX_CITY_COGNITION_USAGE_INTEGER,
        completion_tokens=MAX_CITY_COGNITION_USAGE_INTEGER,
    )
    document = record.model_dump(mode="python")
    document.update(
        {
            "selection_index": 59,
            "request_sha256": "f" * 64,
            "run_input_sha256": "f" * 64,
            "response_input_sha256": "f" * 64,
            "scenario_sha256": "f" * 64,
            "city_sha256": "f" * 64,
            "response_event_id": "f" * 64,
            "notice_event_id": "f" * 64,
            "opportunity_id": "f" * 64,
            "state_update_event_id": "f" * 64,
            "agent_id": "person-030",
            "campaign_id": "c" * 80,
            "placement_id": "p" * 80,
            "model_minute": 10_079,
            "millisecond_within_minute": 59_999,
            "state_update_cause_count": MAX_CITY_COGNITION_STATE_UPDATE_CAUSES,
            "provider_profile_id": "p" * 64,
            "provider_revision_sha256": "f" * 64,
            "provider_profile_snapshot_sha256": "f" * 64,
            "provider_effective_settings_sha256": "f" * 64,
            "result": result.model_dump(mode="python"),
        }
    )
    record_facts = {key: value for key, value in document.items() if key != "record_hash"}
    document["record_hash"] = sha256(canonical_json(record_facts).encode("utf-8")).hexdigest()

    maximum_record = CityCognitionTerminalRecordV1.model_validate(document)
    canonical_bytes = len(canonical_json(maximum_record).encode("utf-8"))
    assert canonical_bytes <= MAX_CITY_COGNITION_TERMINAL_RECORD_BYTES


def test_request_identity_is_independent_of_selection_receipt_index() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    assert len(requests) >= 2
    selected = requests[1]
    rebuilt = type(selected).model_validate_json(canonical_json(selected))

    without_earlier = allocate_city_cognition_reservations(
        (rebuilt,),
        aggregate_output_token_ceiling=0,
    )[0]
    with_earlier = allocate_city_cognition_reservations(
        (requests[0], rebuilt),
        aggregate_output_token_ceiling=0,
    )[1]

    assert without_earlier.selection_index == 0
    assert with_earlier.selection_index == 1
    assert without_earlier.request_id == with_earlier.request_id == selected.request_id


def test_dense_response_selection_applies_four_per_agent_then_sixty_globally() -> None:
    document = draft_v2_data(agent_count=20)
    phone = document["scenario"]["phone"]
    assert isinstance(phone, dict)
    phone.update(
        {
            "active_windows": [
                {"day": 1, "start_minute": 0, "end_minute": 1_440},
                {"day": 2, "start_minute": 0, "end_minute": 1_440},
            ],
            "frequency_cap_per_agent_per_day": 100,
            "eligible_activities": ["home", "commute", "work", "leisure"],
            "opportunity_probability_per_minute": 1.0,
        }
    )
    draft = WorkbenchRunDraftV2.model_validate_json(json.dumps(document))
    compiled, responses, context = _evidence(draft)
    ordered = sorted(
        (record for record in responses.records if record.event_type == "spatial.response"),
        key=lambda item: (item.model_minute, item.agent_id, item.campaign_id, item.event_id),
    )
    expected: list[str] = []
    counts: Counter[str] = Counter()
    for response in ordered:
        if counts[response.agent_id] == 4:
            continue
        counts[response.agent_id] += 1
        expected.append(response.event_id)
        if len(expected) == 60:
            break

    requests = build_city_cognition_requests(context, compiled.mobility, responses)

    assert len(requests) == 60
    assert [item.response_event_id for item in requests] == expected
    assert max(Counter(item.agent_id for item in requests).values()) == 4


def test_context_tuple_order_cannot_change_request_bytes() -> None:
    compiled, responses, context = _evidence()
    reversed_context = CityCognitionRunContextV1(
        run_input_sha256=context.run_input_sha256,
        response_input_sha256=context.response_input_sha256,
        scenario_sha256=context.scenario_sha256,
        city_sha256=context.city_sha256,
        agents=tuple(reversed(context.agents)),
        campaigns=tuple(reversed(context.campaigns)),
    )

    first = build_city_cognition_requests(context, compiled.mobility, responses)
    second = build_city_cognition_requests(reversed_context, compiled.mobility, responses)

    assert [canonical_json(item) for item in first] == [canonical_json(item) for item in second]


@pytest.mark.parametrize("unsafe_text", ["../private.txt", "first\u2028second"])
def test_all_untrusted_prompt_surfaces_refuse_paths_and_line_separators(
    unsafe_text: str,
) -> None:
    _, _, context = _evidence()
    agent = context.agents[0]
    campaign = context.campaigns[0]

    with pytest.raises(ValidationError):
        agent.model_copy(update={"background": unsafe_text})
    with pytest.raises(ValidationError):
        campaign.model_copy(update={"name": unsafe_text})
    with pytest.raises(ValidationError):
        campaign.creative.model_copy(update={"message": unsafe_text})


@pytest.mark.parametrize("value_field", ["occupation", "background"])
def test_agent_context_refuses_labelled_secrets_split_across_values(
    value_field: str,
) -> None:
    _, _, context = _evidence()
    secret = "opaquevalue1234567890"
    document = context.agents[0].model_dump(mode="python")
    document.update({"display_name": "api_key", value_field: secret})

    with pytest.raises(ValidationError, match="labelled credential") as caught:
        CityCognitionAgentContextV1.model_validate(document)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


@pytest.mark.parametrize("value_field", ["product_category", "message"])
def test_creative_snapshot_refuses_labelled_secrets_split_across_values(
    value_field: str,
) -> None:
    _, _, context = _evidence()
    secret = "opaquevalue1234567890"
    document = context.campaigns[0].creative.model_dump(mode="python")
    document.update({"product_name": "api_key", value_field: secret})

    with pytest.raises(ValidationError, match="labelled credential") as caught:
        CityCognitionCreativeSnapshotV1.model_validate(document)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


@pytest.mark.parametrize("label_field", ["name", "target_interests"])
def test_campaign_context_refuses_labels_paired_with_nested_creative_values(
    label_field: str,
) -> None:
    _, _, context = _evidence()
    secret = "opaquevalue1234567890"
    campaign = context.campaigns[0]
    creative = campaign.creative.model_copy(update={"message": secret})
    document = campaign.model_dump(mode="python")
    document.update(
        {
            "creative": creative.model_dump(mode="python"),
            "creative_sha256": creative.fingerprint,
            label_field: "api_key" if label_field == "name" else ("api_key",),
        }
    )

    with pytest.raises(ValidationError, match="labelled credential") as caught:
        CityCognitionCampaignContextV1.model_validate(document)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_request_refuses_a_secret_pair_split_across_persona_and_campaign() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    secret = "opaquevalue1234567890"
    persona_document = request.persona.model_dump(mode="python")
    persona_document["display_name"] = "api_key"
    persona_document["traits"] = request.persona.traits
    unsafe_persona = CityCognitionAgentContextV1.model_construct(**persona_document)
    creative = request.campaign.creative.model_copy(update={"message": secret})
    campaign = request.campaign.model_copy(
        update={"creative": creative, "creative_sha256": creative.fingerprint}
    )
    document = request.model_dump(mode="python")
    document.update(
        {
            "persona": unsafe_persona,
            "campaign": campaign,
            "rule_factors": request.rule_factors,
            "committed_state": request.committed_state,
        }
    )
    placeholder = CityCognitionRequestV1.model_construct(**document)
    document["request_id"] = f"cog-{placeholder.request_facts_sha256}"

    with pytest.raises(ValidationError, match="labelled credential") as caught:
        CityCognitionRequestV1(**document)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_request_refuses_a_placement_label_paired_with_creative_secret_text() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    secret = "opaquevalue1234567890"
    creative = request.campaign.creative.model_copy(update={"message": secret})
    campaign = request.campaign.model_copy(
        update={"creative": creative, "creative_sha256": creative.fingerprint}
    )
    document = request.model_dump(mode="python")
    document.update(
        {
            "placement_id": "api-key",
            "campaign": campaign,
            "persona": request.persona,
            "rule_factors": request.rule_factors,
            "committed_state": request.committed_state,
        }
    )
    placeholder = CityCognitionRequestV1.model_construct(**document)
    document["request_id"] = f"cog-{placeholder.request_facts_sha256}"

    with pytest.raises(ValidationError, match="labelled credential") as caught:
        CityCognitionRequestV1.model_validate(document)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_cross_field_secret_screen_preserves_safe_security_prose() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    secret_shaped_prose = "opaquevalue1234567890"
    persona = request.persona.model_copy(
        update={
            "display_name": "API key safety researcher",
            "occupation": secret_shaped_prose,
        }
    )
    creative = request.campaign.creative.model_copy(
        update={
            "product_name": "API key safety workshop",
            "message": secret_shaped_prose,
        }
    )
    campaign = request.campaign.model_copy(
        update={
            "name": "API key safety campaign",
            "creative": creative,
            "creative_sha256": creative.fingerprint,
        }
    )

    rebuilt = _rebuild_request(request, persona=persona, campaign=campaign)

    assert rebuilt.persona == persona
    assert rebuilt.campaign == campaign


def test_user_authored_cookie_product_and_copy_are_not_transport_secret_labels() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    creative = request.campaign.creative.model_copy(
        update={
            "product_name": "Cookies",
            "product_category": "Cookies",
            "message": "Cookies for a fictional afternoon break.",
        }
    )
    campaign = request.campaign.model_copy(
        update={
            "name": "Cookies",
            "creative": creative,
            "creative_sha256": creative.fingerprint,
            "target_interests": ("Cookies",),
        }
    )

    rebuilt = _rebuild_request(request, campaign=campaign)

    assert rebuilt.campaign.creative.product_name == "Cookies"
    assert rebuilt.campaign.target_interests == ("Cookies",)


def test_prompt_models_and_parent_containers_refuse_transport_credentials() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    secret = "opaquevalue1234567890"
    unsafe_text = f"Authorization: Bearer {secret}"

    creative_document = request.campaign.creative.model_dump(mode="python")
    creative_document["message"] = unsafe_text
    with pytest.raises(ValidationError, match="credential") as creative_error:
        CityCognitionCreativeSnapshotV1.model_validate(creative_document)
    unsafe_creative = CityCognitionCreativeSnapshotV1.model_construct(**creative_document)

    campaign_document = request.campaign.model_dump(mode="python")
    campaign_document["name"] = unsafe_text
    with pytest.raises(ValidationError, match="credential") as campaign_error:
        CityCognitionCampaignContextV1.model_validate(campaign_document)
    nested_campaign_document = request.campaign.model_dump(mode="python")
    nested_campaign_document.update(
        {
            "creative": unsafe_creative,
            "creative_sha256": unsafe_creative.fingerprint,
        }
    )
    with pytest.raises(ValidationError, match="credential") as nested_campaign_error:
        CityCognitionCampaignContextV1.model_validate(nested_campaign_document)

    agent_document = request.persona.model_dump(mode="python")
    agent_document.update({"background": unsafe_text, "traits": request.persona.traits})
    with pytest.raises(ValidationError, match="credential") as agent_error:
        CityCognitionAgentContextV1.model_validate(agent_document)
    unsafe_agent = CityCognitionAgentContextV1.model_construct(**agent_document)

    context_document = context.model_dump(mode="python")
    context_document["agents"] = tuple(
        unsafe_agent if item.agent_id == unsafe_agent.agent_id else item for item in context.agents
    )
    context_document["campaigns"] = context.campaigns
    with pytest.raises(ValidationError, match="credential") as context_error:
        CityCognitionRunContextV1.model_validate(context_document)

    request_document = request.model_dump(mode="python")
    request_document.update(
        {
            "persona": unsafe_agent,
            "campaign": request.campaign,
            "rule_factors": request.rule_factors,
            "committed_state": request.committed_state,
        }
    )
    placeholder = CityCognitionRequestV1.model_construct(**request_document)
    request_document["request_id"] = f"cog-{placeholder.request_facts_sha256}"
    with pytest.raises(ValidationError, match="credential") as request_error:
        CityCognitionRequestV1.model_validate(request_document)

    for error in (
        creative_error.value,
        campaign_error.value,
        nested_campaign_error.value,
        agent_error.value,
        context_error.value,
        request_error.value,
    ):
        assert secret not in str(error)
        assert secret not in repr(error)


def test_fictional_persona_disclosure_requires_the_boolean_true_token() -> None:
    _, _, context = _evidence()
    document = context.agents[0].model_dump(mode="python")
    document["fictional"] = 1

    with pytest.raises(ValidationError):
        CityCognitionAgentContextV1.model_validate(document)


def test_descriptive_persona_changes_request_identity_but_not_numeric_science() -> None:
    compiled, responses, context = _evidence()
    before = canonical_json(responses)
    first = build_city_cognition_requests(context, compiled.mobility, responses)
    selected_agent_id = first[0].agent_id
    selected_agent = next(item for item in context.agents if item.agent_id == selected_agent_id)
    changed_context = context.model_copy(
        update={
            "agents": tuple(
                item.model_copy(
                    update={
                        "display_name": "Fictional Roya",
                        "background": "A different invented story.",
                    }
                )
                if item.persona_id == selected_agent.persona_id
                else item
                for item in context.agents
            )
        }
    )

    second = build_city_cognition_requests(changed_context, compiled.mobility, responses)

    assert canonical_json(responses) == before
    assert [request.response_event_id for request in first] == [
        request.response_event_id for request in second
    ]
    assert any(
        left.request_id != right.request_id for left, right in zip(first, second, strict=True)
    )


def test_reservation_winners_are_fixed_in_selection_order_without_redistribution() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    ceiling = min(2, len(requests)) * 1_536

    reservations = allocate_city_cognition_reservations(
        requests,
        aggregate_output_token_ceiling=ceiling,
    )

    assert [item.reserved_output_tokens for item in reservations[:2]] == [1_536] * min(
        2, len(requests)
    )
    assert all(item.reserved_output_tokens == 0 for item in reservations[2:])
    assert [item.request_id for item in reservations] == [item.request_id for item in requests]
    for invalid in (-1, 1, 1_535, 92_161, True):
        with pytest.raises((TypeError, ValueError)):
            allocate_city_cognition_reservations(
                requests,
                aggregate_output_token_ceiling=invalid,
            )


def test_reservation_allocator_refuses_duplicate_requests_at_its_boundary() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]

    with pytest.raises(ValueError, match="unique"):
        allocate_city_cognition_reservations(
            (request, request),
            aggregate_output_token_ceiling=0,
        )


def test_reservation_allocator_refuses_duplicate_response_events() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    duplicate_event = _rebuild_request(request, opportunity_id="f" * 64)
    assert duplicate_event.request_id != request.request_id
    assert duplicate_event.fingerprint != request.fingerprint
    assert duplicate_event.response_event_id == request.response_event_id

    with pytest.raises(ValueError, match="response events"):
        allocate_city_cognition_reservations(
            (request, duplicate_event),
            aggregate_output_token_ceiling=0,
        )


def test_terminal_validation_is_complete_ordered_and_does_not_touch_numeric_evidence() -> None:
    compiled, responses, context = _evidence()
    before = canonical_json(responses)
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    cognition_input = _input(context, requests)
    records = _rules_records(requests)

    evaluation = validate_city_cognition_evaluation(cognition_input, requests, records)

    assert canonical_json(responses) == before
    assert evaluation.records == records
    assert evaluation.summary.selected_request_count == len(requests)
    assert evaluation.summary.outcome_counts.rules == len(requests)
    assert evaluation.summary.outcome_counts.total == len(requests)
    assert evaluation.summary.reason_counts.total == 0

    for request, record in zip(requests, records, strict=True):
        assert record.response_input_sha256 == request.response_input_sha256
        assert record.scenario_sha256 == request.scenario_sha256
        assert record.city_sha256 == request.city_sha256
        assert record.state_update_event_id == request.state_update_event_id
        assert record.placement_id == request.placement_id
        assert record.model_minute == request.model_minute
        assert record.millisecond_within_minute == request.millisecond_within_minute
        assert record.state_update_cause_count == request.state_update_cause_count

    mutations = [records[:-1], tuple(reversed(records))]
    if records:
        other = type(records[0]).model_construct(
            **{
                **{name: getattr(records[0], name) for name in type(records[0]).model_fields},
                "request_id": f"cog-{'f' * 64}",
            }
        )
        mutations.append((other, *records[1:]))
    for invalid in mutations:
        with pytest.raises((ValidationError, ValueError)):
            validate_city_cognition_evaluation(cognition_input, requests, invalid)

    if records:
        document = records[0].model_dump(mode="json")
        document["city_sha256"] = "f" * 64
        document["record_hash"] = sha256(
            canonical_json(
                {key: value for key, value in document.items() if key != "record_hash"}
            ).encode("utf-8")
        ).hexdigest()
        cross_bound = type(records[0]).model_validate_json(json.dumps(document))
        with pytest.raises(ValueError, match="cross-bound"):
            validate_city_cognition_evaluation(
                cognition_input,
                requests,
                (cross_bound, *records[1:]),
            )

        document = evaluation.model_dump(mode="json")
        outcome_counts = document["summary"]["outcome_counts"]
        outcome_counts["rules"] -= 1
        outcome_counts["mock"] += 1
        with pytest.raises(ValidationError, match="summary does not match"):
            type(evaluation).model_validate_json(json.dumps(document))


def test_evaluation_direct_validation_binds_every_record_to_its_run_hash() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    cognition_input = _input(context, requests)
    evaluation = validate_city_cognition_evaluation(
        cognition_input,
        requests,
        _rules_records(requests),
    )
    document = evaluation.model_dump(mode="json")
    document["run_input_sha256"] = "f" * 64
    assert document["run_input_sha256"] != evaluation.run_input_sha256

    with pytest.raises(ValidationError, match="run input"):
        type(evaluation).model_validate_json(json.dumps(document))


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "reason",
        "provider_profile_id",
        "provider_revision_sha256",
        "provider_profile_snapshot_sha256",
        "provider_protocol",
        "provider_effective_settings_sha256",
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "prompt_tokens",
        "completion_tokens",
        "disclosure",
    ],
)
def test_terminal_record_requires_every_nullable_and_contract_field(field: str) -> None:
    evaluation = _single_rules_evaluation()
    record_document = evaluation.records[0].model_dump(mode="json")
    del record_document[field]

    with pytest.raises(ValidationError, match="required"):
        type(evaluation.records[0]).model_validate_json(json.dumps(record_document))

    evaluation_document = evaluation.model_dump(mode="json")
    del evaluation_document["records"][0][field]
    with pytest.raises(ValidationError, match="required"):
        type(evaluation).model_validate_json(json.dumps(evaluation_document))


@pytest.mark.parametrize("field", ["schema_version", "disclosure"])
def test_summary_requires_every_persisted_constant_field(field: str) -> None:
    evaluation = _single_rules_evaluation()
    summary_document = evaluation.summary.model_dump(mode="json")
    del summary_document[field]

    with pytest.raises(ValidationError, match="required"):
        type(evaluation.summary).model_validate_json(json.dumps(summary_document))

    evaluation_document = evaluation.model_dump(mode="json")
    del evaluation_document["summary"][field]
    with pytest.raises(ValidationError, match="required"):
        type(evaluation).model_validate_json(json.dumps(evaluation_document))


@pytest.mark.parametrize("counts_field", ["outcome_counts", "reason_counts"])
def test_summary_nested_counts_require_their_schema_version(counts_field: str) -> None:
    evaluation = _single_rules_evaluation()
    counts = getattr(evaluation.summary, counts_field)
    counts_document = counts.model_dump(mode="json")
    del counts_document["schema_version"]

    with pytest.raises(ValidationError, match="required"):
        type(counts).model_validate_json(json.dumps(counts_document))

    evaluation_document = evaluation.model_dump(mode="json")
    del evaluation_document["summary"][counts_field]["schema_version"]
    with pytest.raises(ValidationError, match="required"):
        type(evaluation).model_validate_json(json.dumps(evaluation_document))


def test_evaluation_direct_validation_enforces_the_closed_mode_source_matrix() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]

    rules_input = _input(context, (request,), mode="rules")
    rules_evaluation = validate_city_cognition_evaluation(
        rules_input,
        (request,),
        _rules_records((request,)),
    )
    for incompatible_mode in ("mock", "local", "remote"):
        document = rules_evaluation.model_dump(mode="json")
        document["mode"] = incompatible_mode
        with pytest.raises(ValidationError, match="mode"):
            type(rules_evaluation).model_validate_json(json.dumps(document))

    mock_input = _input(context, (request,), mode="mock")
    mock_record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source="mock",
        result=build_city_mock_result(request),
    )
    mock_evaluation = validate_city_cognition_evaluation(
        mock_input,
        (request,),
        (mock_record,),
    )
    for incompatible_mode in ("rules", "local", "remote"):
        document = mock_evaluation.model_dump(mode="json")
        document["mode"] = incompatible_mode
        with pytest.raises(ValidationError, match="mode"):
            type(mock_evaluation).model_validate_json(json.dumps(document))

    profile = _provider_profile()
    provider_input = _input(
        context,
        (request,),
        mode="local",
        provider_profile=profile,
        aggregate_output_token_ceiling=1_536,
    )
    provider_result = CityCognitionResultV1(
        schema_version=1,
        request_id=request.request_id,
        interpretation="A bounded provider explanation.",
        decision_factors=("Rule-owned evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    provider_record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source="provider",
        result=provider_result,
        attempt_count=1,
        provider_profile=profile,
    )
    provider_evaluation = validate_city_cognition_evaluation(
        provider_input,
        (request,),
        (provider_record,),
    )
    for incompatible_mode in ("rules", "mock"):
        document = provider_evaluation.model_dump(mode="json")
        document["mode"] = incompatible_mode
        with pytest.raises(ValidationError, match="mode"):
            type(provider_evaluation).model_validate_json(json.dumps(document))
    remote_document = provider_evaluation.model_dump(mode="json")
    remote_document["mode"] = "remote"
    assert (
        type(provider_evaluation).model_validate_json(json.dumps(remote_document)).mode == "remote"
    )

    empty_evaluation = validate_city_cognition_evaluation(
        _input(context, (), mode="rules"),
        (),
        (),
    )
    for mode in ("rules", "mock", "local", "remote"):
        document = empty_evaluation.model_dump(mode="json")
        document["mode"] = mode
        assert type(empty_evaluation).model_validate_json(json.dumps(document)).mode == mode


def test_evaluation_direct_validation_requires_canonical_scientific_order() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)[:2]
    evaluation = validate_city_cognition_evaluation(
        _input(context, requests),
        requests,
        _rules_records(requests),
    )
    reversed_records = tuple(
        _rebuild_record(record, selection_index=index)
        for index, record in enumerate(reversed(evaluation.records))
    )
    document = _evaluation_document_with_records(evaluation, reversed_records)

    with pytest.raises(ValidationError, match="selection order"):
        type(evaluation).model_validate_json(json.dumps(document))


def test_evaluation_direct_validation_refuses_duplicate_response_events() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)[:2]
    evaluation = validate_city_cognition_evaluation(
        _input(context, requests),
        requests,
        _rules_records(requests),
    )
    records = (
        evaluation.records[0],
        _rebuild_record(
            evaluation.records[1],
            response_event_id=evaluation.records[0].response_event_id,
        ),
    )
    document = _evaluation_document_with_records(evaluation, records)

    with pytest.raises(ValidationError, match="response events"):
        type(evaluation).model_validate_json(json.dumps(document))


def test_evaluation_direct_validation_requires_one_scientific_provenance() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)[:2]
    evaluation = validate_city_cognition_evaluation(
        _input(context, requests),
        requests,
        _rules_records(requests),
    )
    records = (
        evaluation.records[0],
        _rebuild_record(evaluation.records[1], city_sha256="f" * 64),
    )
    document = _evaluation_document_with_records(evaluation, records)

    with pytest.raises(ValidationError, match="scientific provenance"):
        type(evaluation).model_validate_json(json.dumps(document))


def test_evaluation_direct_validation_requires_one_provider_binding() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)[:2]
    profile = _provider_profile()
    provider_input = _input(
        context,
        requests,
        mode="local",
        provider_profile=profile,
        aggregate_output_token_ceiling=3_072,
    )
    records = tuple(
        build_city_cognition_terminal_record(
            request,
            selection_index=index,
            source="provider",
            result=CityCognitionResultV1(
                schema_version=1,
                request_id=request.request_id,
                interpretation="A bounded provider explanation.",
                decision_factors=("Rule-owned evidence",),
                safety_flags=(),
                disclosure=CITY_COGNITION_DISCLOSURE,
            ),
            attempt_count=1,
            provider_profile=profile,
        )
        for index, request in enumerate(requests)
    )
    evaluation = validate_city_cognition_evaluation(provider_input, requests, records)
    mixed_records = (
        evaluation.records[0],
        _rebuild_record(evaluation.records[1], provider_revision_sha256="f" * 64),
    )
    document = _evaluation_document_with_records(evaluation, mixed_records)

    with pytest.raises(ValidationError, match="provider binding"):
        type(evaluation).model_validate_json(json.dumps(document))


def test_public_evaluator_requires_one_scientific_provenance() -> None:
    compiled, responses, context = _evidence()
    requests = list(build_city_cognition_requests(context, compiled.mobility, responses)[:2])
    requests[1] = _rebuild_request(requests[1], city_sha256="f" * 64)
    checked = tuple(requests)

    with pytest.raises(ValueError, match="scientific provenance"):
        validate_city_cognition_evaluation(
            _input(context, checked),
            checked,
            _rules_records(checked),
        )


def test_public_evaluator_requires_one_persona_snapshot_per_agent() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    agent_id = next(
        agent_id
        for agent_id, count in Counter(item.agent_id for item in requests).items()
        if count > 1
    )
    same_agent = tuple(item for item in requests if item.agent_id == agent_id)[:2]
    persona_document = same_agent[1].persona.model_dump(mode="python")
    persona_document["display_name"] = f"{same_agent[1].persona.display_name} revised"
    changed_persona = CityCognitionAgentContextV1.model_validate(persona_document)
    inconsistent = (
        same_agent[0],
        _rebuild_request(same_agent[1], persona=changed_persona),
    )

    with pytest.raises(ValueError, match="persona snapshot"):
        validate_city_cognition_evaluation(
            _input(context, inconsistent),
            inconsistent,
            _rules_records(inconsistent),
        )


def test_repeated_persona_id_requires_one_template_but_may_assign_multiple_agents() -> None:
    _, _, context = _evidence()
    source_agent = context.agents[0]
    alias_document = source_agent.model_dump(mode="python")
    alias_document["agent_id"] = context.agents[1].agent_id
    alias = CityCognitionAgentContextV1.model_validate(alias_document)

    accepted = CityCognitionRunContextV1(
        run_input_sha256=context.run_input_sha256,
        response_input_sha256=context.response_input_sha256,
        scenario_sha256=context.scenario_sha256,
        city_sha256=context.city_sha256,
        agents=(source_agent, alias),
        campaigns=context.campaigns,
    )
    assert tuple(agent.persona_id for agent in accepted.agents) == (
        source_agent.persona_id,
        source_agent.persona_id,
    )

    inconsistent_document = alias.model_dump(mode="python")
    inconsistent_document["display_name"] = f"{alias.display_name} revised"
    inconsistent = CityCognitionAgentContextV1.model_validate(inconsistent_document)
    with pytest.raises(ValidationError, match="persona template"):
        CityCognitionRunContextV1(
            run_input_sha256=context.run_input_sha256,
            response_input_sha256=context.response_input_sha256,
            scenario_sha256=context.scenario_sha256,
            city_sha256=context.city_sha256,
            agents=(source_agent, inconsistent),
            campaigns=context.campaigns,
        )


def test_public_evaluator_requires_one_template_per_repeated_persona_id() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    first = requests[0]
    second = next(item for item in requests if item.agent_id != first.agent_id)
    persona_document = second.persona.model_dump(mode="python")
    persona_document["persona_id"] = first.persona.persona_id
    inconsistent_persona = CityCognitionAgentContextV1.model_validate(persona_document)
    inconsistent = tuple(
        sorted(
            (first, _rebuild_request(second, persona=inconsistent_persona)),
            key=lambda item: (
                item.model_minute,
                item.agent_id,
                item.campaign_id,
                item.response_event_id,
            ),
        )
    )

    with pytest.raises(ValueError, match="persona template"):
        validate_city_cognition_evaluation(
            _input(context, inconsistent),
            inconsistent,
            _rules_records(inconsistent),
        )


def test_public_evaluator_requires_one_campaign_snapshot_per_campaign() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)[:2]
    campaign_document = requests[1].campaign.model_dump(mode="python")
    campaign_document["name"] = f"{requests[1].campaign.name} revised"
    changed_campaign = CityCognitionCampaignContextV1.model_validate(campaign_document)
    inconsistent = (
        requests[0],
        _rebuild_request(requests[1], campaign=changed_campaign),
    )

    with pytest.raises(ValueError, match="campaign snapshot"):
        validate_city_cognition_evaluation(
            _input(context, inconsistent),
            inconsistent,
            _rules_records(inconsistent),
        )


def test_public_evaluator_refuses_more_selected_responses_than_update_causes() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    duplicate_update = _rebuild_request(request, response_event_id="0" * 64)
    requests = tuple(
        sorted(
            (request, duplicate_update),
            key=lambda item: (
                item.model_minute,
                item.agent_id,
                item.campaign_id,
                item.response_event_id,
            ),
        )
    )
    assert request.state_update_cause_count == 1

    with pytest.raises(ValueError, match="cause count"):
        validate_city_cognition_evaluation(
            _input(context, requests),
            requests,
            _rules_records(requests),
        )


@pytest.mark.parametrize(
    "inconsistent_field",
    [
        "state_update_event_id",
        "state_before_sha256",
        "state_update_cause_count",
        "committed_state",
    ],
)
def test_public_evaluator_requires_shared_state_update_coherence(
    inconsistent_field: str,
) -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[1]
    updates: dict[str, object] = {
        "response_event_id": "0" * 64,
    }
    if inconsistent_field in {"state_update_event_id", "state_before_sha256"}:
        updates[inconsistent_field] = "f" * 64
    elif inconsistent_field == "state_update_cause_count":
        updates[inconsistent_field] = 2
    else:
        state_document = request.committed_state.model_dump(mode="python")
        state_document["brand_sentiment"] = (
            -0.25 if request.committed_state.brand_sentiment != -0.25 else 0.25
        )
        updates[inconsistent_field] = CityCognitionCommittedStateV1.model_validate(state_document)
    duplicate_update = _rebuild_request(request, **updates)
    inconsistent = tuple(
        sorted(
            (request, duplicate_update),
            key=lambda item: (
                item.model_minute,
                item.agent_id,
                item.campaign_id,
                item.response_event_id,
            ),
        )
    )

    with pytest.raises(ValueError, match="state update"):
        validate_city_cognition_evaluation(
            _input(context, inconsistent),
            inconsistent,
            _rules_records(inconsistent),
        )


@pytest.mark.parametrize("source", ["provider", "fallback"])
def test_public_evaluator_caps_attempts_by_frozen_effective_settings(source: str) -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    profile_document = _provider_profile().model_dump(mode="python")
    profile_document.update({"transient_retries": 0, "repair_attempts": 0})
    profile_document["revision_sha256"] = city_cognition_provider_profile_revision_sha256(
        profile_document
    )
    profile = CityCognitionProviderProfileSnapshotV1.model_validate(profile_document)
    cognition_input = _input(
        context,
        (request,),
        mode="local",
        provider_profile=profile,
        aggregate_output_token_ceiling=1_536,
    )
    if source == "provider":
        result = CityCognitionResultV1(
            schema_version=1,
            request_id=request.request_id,
            interpretation="A bounded provider explanation.",
            decision_factors=("Rule-owned evidence",),
            safety_flags=(),
            disclosure=CITY_COGNITION_DISCLOSURE,
        )
        reason = None
    else:
        result = build_city_fallback_result(request, reason="timeout-exhausted")
        reason = "timeout-exhausted"
    record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source=source,
        result=result,
        attempt_count=2,
        reason=reason,
        provider_profile=profile,
    )

    with pytest.raises(ValueError, match="attempt"):
        validate_city_cognition_evaluation(cognition_input, (request,), (record,))


def test_rules_mock_fallback_and_budget_text_are_deterministic_and_noncausal() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]

    results = (
        build_city_rules_result(request),
        build_city_mock_result(request),
        build_city_fallback_result(request, reason="timeout-exhausted"),
        build_city_budget_skipped_result(request),
    )

    assert all(result.request_id == request.request_id for result in results)
    assert all(result.disclosure == CITY_COGNITION_DISCLOSURE for result in results)
    assert results == (
        build_city_rules_result(request),
        build_city_mock_result(request),
        build_city_fallback_result(request, reason="timeout-exhausted"),
        build_city_budget_skipped_result(request),
    )
    request_text = " ".join(
        filter(
            None,
            (
                request.persona.display_name,
                request.persona.background,
                request.campaign.creative.message,
            ),
        )
    )
    assert all(request_text not in canonical_json(result) for result in results)


def test_provider_prose_is_bound_to_the_request_and_cannot_supply_new_state() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)
    request = requests[0]

    def scientific_authority_snapshot() -> str:
        settings = compiled.workbench_input.draft.settings
        opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
        attention = evaluate_spatial_attention(opportunities, seed=settings.seed)
        rebuilt_responses = evaluate_spatial_responses(
            compiled.response_input,
            compiled.scenario,
            opportunities,
            attention,
            agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
        )
        response_minutes = sorted(
            {
                record.model_minute
                for record in rebuilt_responses.records
                if record.event_type == "spatial.response"
            }
        )
        return canonical_json(
            {
                "mobility_metadata": compiled.mobility.metadata(),
                "mobility_place_assignments": compiled.mobility.place_assignment_document(),
                "mobility_frames_at_responses": {
                    str(minute): [asdict(position) for position in compiled.mobility.frame(minute)]
                    for minute in response_minutes
                },
                "opportunity_evidence": opportunities.model_dump(mode="json"),
                "attention_evidence": attention.model_dump(mode="json"),
                "response_events_states_and_metrics": rebuilt_responses.model_dump(mode="json"),
            }
        )

    science_before = scientific_authority_snapshot()
    first = CityCognitionResultV1(
        schema_version=1,
        request_id=request.request_id,
        interpretation="One bounded explanation.",
        decision_factors=("Rule-owned evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    second = first.model_copy(update={"interpretation": "Different bounded wording."})
    profile = _provider_profile()
    first_record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source="provider",
        result=first,
        attempt_count=1,
        provider_profile=profile,
    )
    science_after_first = scientific_authority_snapshot()
    second_record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source="provider",
        result=second,
        attempt_count=1,
        provider_profile=profile,
    )
    science_after_second = scientific_authority_snapshot()

    assert science_before == science_after_first == science_after_second
    assert canonical_json(responses) == canonical_json(
        evaluate_spatial_responses(
            compiled.response_input,
            compiled.scenario,
            evaluate_spatial_opportunities(compiled.mobility, compiled.scenario),
            evaluate_spatial_attention(
                evaluate_spatial_opportunities(compiled.mobility, compiled.scenario),
                seed=compiled.workbench_input.draft.settings.seed,
            ),
            agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
        )
    )
    assert first != second
    assert first_record.record_hash != second_record.record_hash
    assert first_record.request_sha256 == second_record.request_sha256 == request.fingerprint
    assert set(type(first).model_fields).isdisjoint(
        {"events", "movement", "budget", "purchase_probability", "purchase_intention"}
    )


def test_terminal_builder_refuses_a_wrong_provider_profile_type_cleanly() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]

    with pytest.raises(TypeError, match="provider profile"):
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="provider",
            result=CityCognitionResultV1(
                schema_version=1,
                request_id=request.request_id,
                interpretation="A bounded provider explanation.",
                decision_factors=("Rule-owned evidence",),
                safety_flags=(),
                disclosure=CITY_COGNITION_DISCLOSURE,
            ),
            attempt_count=1,
            provider_profile=object(),  # type: ignore[arg-type]
        )


def test_terminal_builder_refuses_bypass_constructed_secret_profile_without_leaking() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    secret = "opaquevalue1234567890"
    profile_document = _provider_profile().model_dump(mode="python")
    profile_document["base_url"] = f"https://user:{secret}@example.test/v1"
    invalid_profile = CityCognitionProviderProfileSnapshotV1.model_construct(**profile_document)

    with pytest.raises(ValueError, match="provider profile") as caught:
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="provider",
            result=CityCognitionResultV1(
                schema_version=1,
                request_id=request.request_id,
                interpretation="A bounded provider explanation.",
                decision_factors=("Rule-owned evidence",),
                safety_flags=(),
                disclosure=CITY_COGNITION_DISCLOSURE,
            ),
            attempt_count=1,
            provider_profile=invalid_profile,
        )

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_terminal_builder_refuses_bypass_constructed_secret_result_without_leaking() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    secret = "opaquevalue1234567890"
    invalid_result = CityCognitionResultV1.model_construct(
        schema_version=1,
        request_id=request.request_id,
        interpretation=f"Authorization: Bearer {secret}",
        decision_factors=("Rule-owned evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )

    with pytest.raises(ValueError, match="city cognition result failed validation") as caught:
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="provider",
            result=invalid_result,
            attempt_count=1,
            provider_profile=_provider_profile(),
        )

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_terminal_and_evaluation_parents_never_render_a_nested_result_secret() -> None:
    evaluation = _single_rules_evaluation()
    secret = "opaquevalue1234567890"

    for json_mode in (False, True):
        mode = "json" if json_mode else "python"
        record_document = evaluation.records[0].model_dump(mode=mode)
        record_document["result"]["interpretation"] = f"Authorization: Bearer {secret}"
        evaluation_document = evaluation.model_dump(mode=mode)
        evaluation_document["records"][0]["result"]["interpretation"] = (
            f"Authorization: Bearer {secret}"
        )

        with pytest.raises(ValidationError, match="credential") as record_error:
            if json_mode:
                type(evaluation.records[0]).model_validate_json(json.dumps(record_document))
            else:
                type(evaluation.records[0]).model_validate(record_document)
        with pytest.raises(ValidationError, match="credential") as evaluation_error:
            if json_mode:
                type(evaluation).model_validate_json(json.dumps(evaluation_document))
            else:
                type(evaluation).model_validate(evaluation_document)

        for error in (record_error.value, evaluation_error.value):
            assert secret not in str(error)
            assert secret not in repr(error)


def test_terminal_source_matrix_is_exact() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    profile = _provider_profile()
    provider_result = CityCognitionResultV1(
        schema_version=1,
        request_id=request.request_id,
        interpretation="A bounded provider explanation.",
        decision_factors=("Rule-owned evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )

    valid = (
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="rules",
            result=build_city_rules_result(request),
        ),
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="mock",
            result=build_city_mock_result(request),
        ),
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="provider",
            result=provider_result,
            attempt_count=1,
            provider_profile=profile,
        ),
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="cache",
            result=provider_result,
            provider_profile=profile,
        ),
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="fallback",
            result=build_city_fallback_result(request, reason="timeout-exhausted"),
            attempt_count=4,
            reason="timeout-exhausted",
            provider_profile=profile,
        ),
        build_city_cognition_terminal_record(
            request,
            selection_index=0,
            source="budget-skipped",
            result=build_city_budget_skipped_result(request),
            reason="budget-skipped",
            provider_profile=profile,
        ),
    )
    assert [item.source for item in valid] == [
        "rules",
        "mock",
        "provider",
        "cache",
        "fallback",
        "budget-skipped",
    ]
    for record in valid[2:]:
        assert record.provider_protocol == "openai-chat-completions-json-schema-v1"
        assert record.provider_profile_id == profile.profile_id
        assert record.provider_revision_sha256 == profile.revision_sha256
        assert record.provider_profile_snapshot_sha256 == profile.fingerprint

    invalid_arguments = (
        {"source": "rules", "attempt_count": 1},
        {"source": "provider", "attempt_count": 1},
        {"source": "cache", "attempt_count": 1, "provider_profile": profile},
        {"source": "fallback", "provider_profile": profile},
        {
            "source": "budget-skipped",
            "reason": "timeout-exhausted",
            "provider_profile": profile,
        },
    )
    for arguments in invalid_arguments:
        with pytest.raises(ValidationError):
            build_city_cognition_terminal_record(
                request,
                selection_index=0,
                result=provider_result,
                **arguments,
            )


def test_summary_counts_every_bounded_fallback_reason() -> None:
    compiled, responses, context = _evidence()
    requests = build_city_cognition_requests(context, compiled.mobility, responses)[:1]
    assert len(requests) == 1
    profile = _provider_profile()
    reasons = (
        "endpoint-resolution-refused",
        "authentication-refused",
        "timeout-exhausted",
        "rate-limit-exhausted",
        "server-error-exhausted",
        "response-too-large",
        "protocol-incompatible",
        "invalid-result",
        "budget-skipped",
    )
    for reason in reasons:
        skipped = reason == "budget-skipped"
        ceiling = 0 if skipped else 1_536
        cognition_input = _input(
            context,
            requests,
            mode="local",
            provider_profile=profile,
            aggregate_output_token_ceiling=ceiling,
        )
        result = (
            build_city_budget_skipped_result(requests[0])
            if skipped
            else build_city_fallback_result(requests[0], reason=reason)
        )
        record = build_city_cognition_terminal_record(
            requests[0],
            selection_index=0,
            source="budget-skipped" if skipped else "fallback",
            result=result,
            attempt_count=0 if skipped else 1,
            reason=reason,
            provider_profile=profile,
        )

        evaluation = validate_city_cognition_evaluation(
            cognition_input,
            requests,
            (record,),
        )

        assert getattr(evaluation.summary.reason_counts, reason.replace("-", "_")) == 1
        assert evaluation.summary.reason_counts.total == 1


def test_terminal_record_refuses_a_rehashed_version_one_policy_substitution() -> None:
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    record = build_city_cognition_terminal_record(
        request,
        selection_index=0,
        source="rules",
        result=build_city_rules_result(request),
    )
    document = record.model_dump(mode="json")
    assert document["prompt_contract_sha256"] == CITY_COGNITION_PROMPT_CONTRACT_SHA256
    document["prompt_contract_sha256"] = "f" * 64
    document["record_hash"] = sha256(
        canonical_json(
            {key: value for key, value in document.items() if key != "record_hash"}
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValidationError):
        type(record).model_validate_json(json.dumps(document))
