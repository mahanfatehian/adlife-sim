"""Inner thoughts: prompt contract v4, result schema V3 and causal contract V5.

A run that asks for thoughts freezes contract V5. Each question then also asks the AI for
the person's inner thought in the chosen languages, and each answer keeps the requested
thoughts that pass every screen beside its unchanged V2 labels. No number ever reads a
thought, and every earlier contract and document stays byte-identical.
"""

from __future__ import annotations

import importlib
import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from adlife.adapters.cognition import city_prompts_v2 as prompts
from adlife.core.domain import city_cognition_v2 as domain
from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_cognition_v2 import _dense_evidence
from tests.unit.city.test_city_cognition_v2_contract import profile_data
from tests.unit.city.test_city_cognition_v2_persona_v3 import (
    V4_CONTRACT_SHA256,
    _with_profiles,
)
from tests.unit.cognition.test_city_openai_compatible import (
    _BackendFactory,
    _case,
    _http_response,
    _remote_credential,
)
from tests.unit.cognition.test_city_openai_compatible_v2 import _vllm_completion
from tests.unit.cognition.test_city_prompts_v2 import _profile

V4 = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V4
V5 = domain.CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V5
PROMPT_V4_SHA256 = "4b751d667458440d916bafd6dbf4695d4acc7f24aaa433b7f099c21f5181319c"
ENGLISH = "Another phone ad on my way home. Nice design, but not this month."
GERMAN = "Schon wieder Werbung auf dem Heimweg. Schick, aber nicht diesen Monat."
PERSIAN = "باز هم تبلیغ گوشی در راه خانه. قشنگ است، ولی این ماه نه."


def _module():
    return importlib.import_module("adlife.core.simulation.city_cognition_v2")


def _thought_context(context, languages):
    return domain.CityCognitionRunContextV2.model_validate(
        _with_profiles(context).model_dump(mode="python") | {"thought_languages": languages}
    )


def _v5_run(
    *, languages=("en", "fa"), mode="remote", limit=60, agent_count=3, minutes=20, profile=None
):
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=agent_count, minutes=minutes)
    context = _thought_context(context, languages)
    requests = module.build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=V5, funded_response_limit=limit
    )
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        profile_data(mode) if profile is None else profile.model_dump(mode="python")
    )
    frozen = module.build_city_cognition_input_v2(
        context,
        requests,
        mode=snapshot.mode,
        funded_response_limit=limit,
        provider_profile=snapshot,
        causal_contract=V5,
    )
    return context, mobility, baseline, requests, frozen


def _parsed(document):
    return domain.CityCognitionResultV3.model_validate_json(json.dumps(document))


def _answer(request_id, *, thoughts=None, **labels):
    document = {
        "affect": "curious",
        "credibility": "medium",
        "disclosure": "synthetic-bounded-cognition-not-observed-behavior",
        "relevance": "high",
        "request_id": request_id,
        "safety_flags": [],
        "salience": "low",
        "thoughts": {"de": None, "en": ENGLISH, "fa": PERSIAN} if thoughts is None else thoughts,
        "schema_version": 3,
    }
    return document | labels


# --- the contract ------------------------------------------------------------------------


def test_contract_v5_adds_the_thoughts_prompt_schema_and_budget_beside_untouched_v4() -> None:
    assert V4.fingerprint == V4_CONTRACT_SHA256
    assert domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT is V4
    assert domain.CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT is V5
    assert sha256(domain.CITY_COGNITION_PROMPT_CONTRACT_V4_TEXT.encode()).hexdigest() == (
        PROMPT_V4_SHA256
    )
    left, right = V4.model_dump(mode="json"), V5.model_dump(mode="json")
    assert {key for key in left if left[key] != right[key]} == {
        "prompt_contract_id",
        "prompt_contract_version",
        "prompt_contract_text",
        "prompt_contract_sha256",
        "result_schema_id",
        "result_schema_version",
        "result_schema_document",
        "result_schema_contract_sha256",
        "selection_policy_version",
        "selection_policy",
        "selection_policy_sha256",
    }
    assert (V5.prompt_contract_version, V5.result_schema_version) == (4, 3)
    assert V5.selection_policy_version == 4
    assert V5.selection_policy.output_tokens_per_attempt == 640
    assert domain.city_cognition_contract_prompt_version(V5.fingerprint) == 4
    assert domain.city_cognition_contract_selection_version(V5.fingerprint) == 4
    parsed = domain.CityCognitionCausalContractV2.model_validate_json(canonical_json(V5))
    assert canonical_json(parsed) == canonical_json(V5)


def test_result_schema_v3_has_one_nullable_slot_per_language() -> None:
    schema = json.loads(domain.CITY_COGNITION_RESULT_SCHEMA_V3_DOCUMENT)
    v2 = json.loads(domain.CITY_COGNITION_RESULT_SCHEMA_V2_DOCUMENT)
    assert schema["properties"]["schema_version"] == {"type": "integer", "const": 3}
    assert schema["properties"]["thoughts"] == {
        "type": "object",
        "additionalProperties": False,
        "properties": {language: {"type": ["string", "null"]} for language in ("de", "en", "fa")},
        "required": ["de", "en", "fa"],
    }
    labels = {key for key in schema["properties"] if key not in {"schema_version", "thoughts"}}
    assert {key: schema["properties"][key] for key in labels} == {
        key: v2["properties"][key] for key in labels
    }
    assert schema["required"] == [*v2["required"], "thoughts"]
    # The prompt names the key order, the languages and the length it asks for.
    text = domain.CITY_COGNITION_PROMPT_CONTRACT_V4_TEXT
    assert "salience, thoughts, and last schema_version, which is always the number 3" in text
    assert "thought_languages (de German, en English, fa Persian)" in text
    assert "at most 200 characters" in text


@pytest.mark.parametrize(
    "updates",
    [
        {"selection_policy_version": 3},
        {"result_schema_version": 2},
        {"result_schema_id": "city-cognition-signal-result-v2"},
        {"prompt_contract_version": 3},
    ],
)
def test_contract_v5_refuses_a_mixed_generation(updates) -> None:
    with pytest.raises(ValidationError):
        domain.CityCognitionCausalContractV2.model_validate(V5.model_dump(mode="python") | updates)
    # A persona prompt v3 contract cannot borrow the thoughts budget either.
    with pytest.raises(ValidationError):
        domain.CityCognitionCausalContractV2.model_validate(
            V4.model_dump(mode="python")
            | {
                "selection_policy_version": 4,
                "selection_policy": V5.selection_policy,
                "selection_policy_sha256": V5.selection_policy_sha256,
            }
        )


def test_a_new_run_freezes_v5_only_when_it_asks_for_thoughts() -> None:
    assert domain.city_cognition_causal_contract_for(()) is V4
    assert domain.city_cognition_causal_contract_for(("de",)) is V5
    assert domain.city_cognition_thought_languages(["fa", "en"]) == ("en", "fa")
    for invalid in (["en", "en"], ["fr"], "en", [1]):
        with pytest.raises(ValueError):
            domain.city_cognition_thought_languages(invalid)


# --- one thought ---------------------------------------------------------------------------


def test_a_thought_is_short_screened_text_in_its_own_script() -> None:
    thoughts = domain.CityCognitionThoughtsV1(en=ENGLISH, de=GERMAN, fa=PERSIAN)
    assert thoughts.languages == ("en", "de", "fa")
    assert canonical_json(domain.CityCognitionThoughtsV1(fa=PERSIAN)) == canonical_json(
        {"fa": PERSIAN}
    )
    for updates in (
        {"en": "x" * 241},
        {"en": "   "},
        {"en": PERSIAN},
        {"de": "12345"},
        {"fa": ENGLISH},
        {"en": "See www.example.org for more."},
        {"en": "Mail me at person@example.org later."},
        {"en": "Call 555 123 4567 after work."},
        {"en": "Password: " + "hunter2-but-longer"},
        {"en": "I keep it in C:\\Users\\me\\notes.txt"},
        {},
    ):
        with pytest.raises(ValidationError):
            domain.CityCognitionThoughtsV1.model_validate(updates)
    # Everyday words a person thinks are not a credential.
    assert domain.CityCognitionThoughtsV1(en="I only buy basic groceries now.").en


def test_an_answer_keeps_only_the_requested_thoughts_that_pass_every_screen() -> None:
    request_id = "cog-" + "a" * 64
    answer = _parsed(_answer(request_id, thoughts={"de": GERMAN, "en": ENGLISH, "fa": "nope"}))
    signal = answer.signal_result()
    assert type(signal) is domain.CityCognitionResultV2
    assert (signal.schema_version, signal.affect) == (2, "curious")
    # German was not asked for, and the Persian slot holds Latin text: only English stays.
    kept = answer.kept_thoughts(("en", "fa"))
    assert kept is not None and kept.languages == ("en",)
    assert answer.kept_thoughts(("de",)).de == GERMAN
    assert answer.kept_thoughts(("fa",)) is None
    for labels in ({"affect": "uncertain"}, {"safety_flags": ["stereotype-risk"]}):
        flagged = _parsed(_answer(request_id, **labels))
        assert flagged.kept_thoughts(("en", "fa")) is None


# --- requests, input and records -----------------------------------------------------------


def test_v5_requests_ask_for_the_run_languages_and_differ_from_v4_requests() -> None:
    module = _module()
    _context, mobility, baseline, requests, frozen = _v5_run(languages=("en", "fa"))
    assert requests
    assert {request.causal_identity.thought_languages for request in requests} == {("en", "fa")}
    plain_context, _, _ = _dense_evidence(agent_count=3, minutes=20)
    v4_requests = module.build_city_cognition_requests_v2(
        _with_profiles(plain_context),
        mobility,
        baseline,
        causal_contract=V4,
        funded_response_limit=60,
    )
    assert {request.request_id for request in requests}.isdisjoint(
        request.request_id for request in v4_requests
    )
    assert all(request.causal_identity.thought_languages is None for request in v4_requests)
    assert "thought_languages" not in canonical_json(v4_requests[0])
    # The data block the AI reads names the languages; the system prompt is prompt v4.
    block = prompts.build_city_cognition_provider_projection_v2(requests[0])
    assert tuple(block["thought_languages"]) == ("en", "fa")
    messages = prompts.build_city_cognition_messages_v2(requests[0], frozen)
    assert messages[0]["content"] == domain.CITY_COGNITION_PROMPT_CONTRACT_V4_TEXT
    response_format = prompts.city_cognition_response_format_v2(frozen)
    assert response_format["json_schema"]["name"] == "city-cognition-signal-result-v3"
    assert response_format["json_schema"]["schema"] == json.loads(
        domain.CITY_COGNITION_RESULT_SCHEMA_V3_DOCUMENT
    )
    # Each language choice is its own causal question: requests never mix languages.
    other = _v5_run(languages=("de",))[3]
    assert {request.request_id for request in requests}.isdisjoint(
        request.request_id for request in other
    )


def test_the_thoughts_contract_needs_the_run_languages_and_vice_versa() -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=2, minutes=20)
    with pytest.raises(ValueError):
        module.build_city_cognition_requests_v2(
            _with_profiles(context),
            mobility,
            baseline,
            causal_contract=V5,
            funded_response_limit=60,
        )
    with pytest.raises(ValueError):
        module.build_city_cognition_requests_v2(
            _thought_context(context, ("en",)),
            mobility,
            baseline,
            causal_contract=V4,
            funded_response_limit=60,
        )


def test_v5_input_grants_every_attempt_the_thoughts_budget() -> None:
    _, _, _, requests, frozen = _v5_run()
    limits = domain.CITY_COGNITION_SELECTION_LIMITS[4]
    assert frozen.max_output_tokens_per_attempt == 640
    assert frozen.reservation_tokens_per_request == 3840 == limits.reservation_tokens_per_request
    assert frozen.aggregate_output_token_ceiling == 60 * 3840
    # A guided profile caps label-only answers at 256 tokens; the thoughts budget overrides it.
    assert frozen.provider_profile.max_output_tokens <= 384
    assert frozen.effective_settings.max_output_tokens == 640
    payload = prompts.build_city_cognition_payload_v2(requests[0], frozen)
    assert payload["max_tokens"] == 640
    with pytest.raises(ValidationError):
        domain.CityCognitionInputV2.model_validate(
            frozen.model_dump(mode="python") | {"max_output_tokens_per_attempt": 256}
        )


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_thoughts_need_an_ai_source(mode) -> None:
    module = _module()
    context, mobility, baseline = _dense_evidence(agent_count=2, minutes=20)
    context = _thought_context(context, ("en",))
    requests = module.build_city_cognition_requests_v2(
        context, mobility, baseline, causal_contract=V5, funded_response_limit=1000
    )
    with pytest.raises(ValidationError):
        module.build_city_cognition_input_v2(
            context, requests, mode=mode, funded_response_limit=1000, causal_contract=V5
        )


def test_a_record_keeps_thoughts_only_from_a_usable_ai_answer() -> None:
    module = _module()
    _, _, baseline, requests, frozen = _v5_run(languages=("en", "fa"))
    request = requests[0]
    answer = _parsed(_answer(request.request_id))
    thoughts = answer.kept_thoughts(("en", "fa"))
    record = module.build_city_cognition_terminal_record_v2(
        request,
        frozen,
        selection_index=0,
        source="provider",
        result=answer.signal_result(),
        attempt_count=1,
        prompt_tokens=900,
        completion_tokens=180,
        thoughts=thoughts,
    )
    assert record.thoughts == thoughts
    assert json.loads(canonical_json(record))["thoughts"] == {"en": ENGLISH, "fa": PERSIAN}
    # A record without thoughts has no member at all, so it hashes as before.
    plain = module.build_city_cognition_terminal_record_v2(
        request,
        frozen,
        selection_index=0,
        source="provider",
        result=answer.signal_result(),
        attempt_count=1,
        prompt_tokens=900,
        completion_tokens=180,
    )
    assert "thoughts" not in json.loads(canonical_json(plain))
    with pytest.raises(ValueError):
        module.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=0,
            source="provider",
            result=answer.signal_result(),
            attempt_count=1,
            thoughts=domain.CityCognitionThoughtsV1(de=GERMAN),
        )
    with pytest.raises(ValueError):
        module.build_city_cognition_terminal_record_v2(
            request,
            frozen,
            selection_index=0,
            source="fallback",
            result=module.build_city_fallback_result_v2(request, reason="timeout-exhausted"),
            attempt_count=2,
            reason="timeout-exhausted",
            thoughts=thoughts,
        )
    # The whole evaluation rebuilds every record, thoughts included, from its request.
    records = tuple(
        module.build_city_cognition_terminal_record_v2(
            item,
            frozen,
            selection_index=index,
            source="provider",
            result=_parsed(_answer(item.request_id)).signal_result(),
            attempt_count=1,
            thoughts=thoughts,
        )
        for index, item in enumerate(requests)
    )
    evaluation = module.validate_city_cognition_evaluation_v2(
        frozen, requests, records, baseline_evaluation=baseline
    )
    assert all(record.thoughts == thoughts for record in evaluation.records)


# --- the AI adapter ------------------------------------------------------------------------


def _v5_provider(body: bytes):
    module = importlib.import_module("adlife.adapters.cognition.city_openai_compatible_v2")
    _, _, _, requests, frozen = _v5_run(languages=("en", "fa"), profile=_profile())
    _, _, policy, endpoint, _resolver = _case()
    provider = module.CityOpenAICompatibleProviderV2(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=frozen,
        credential=_remote_credential(frozen),
        network_backend_factory=_BackendFactory((_http_response(body),)),
    )
    return requests[0], provider


async def _attempt(answer_for):
    _, _, _, requests, _ = _v5_run(languages=("en", "fa"), profile=_profile())
    body = _vllm_completion(json.dumps(answer_for(requests[0].request_id), ensure_ascii=False))
    request, provider = _v5_provider(body)
    assert request.request_id == requests[0].request_id
    async with provider:
        return await provider.call(request)


async def test_the_adapter_keeps_the_labels_and_the_screened_thoughts() -> None:
    attempt = await _attempt(_answer)
    assert attempt.status == "valid"
    assert type(attempt.result) is domain.CityCognitionResultV2
    assert attempt.thoughts == domain.CityCognitionThoughtsV1(en=ENGLISH, fa=PERSIAN)


async def test_prose_in_a_thought_never_discards_the_answer() -> None:
    def everyday(request_id):
        return _answer(
            request_id,
            thoughts={
                "de": None,
                "en": "I only buy basic groceries now; my secret dream: a bakery.",
                "fa": PERSIAN,
            },
        )

    attempt = await _attempt(everyday)
    assert attempt.status == "valid"
    assert attempt.thoughts is not None and attempt.thoughts.languages == ("en", "fa")


async def test_a_bad_thought_is_dropped_and_the_labels_stay() -> None:
    def contact(request_id):
        return _answer(
            request_id,
            thoughts={"de": None, "en": "Write to me: person@example.org", "fa": None},
        )

    attempt = await _attempt(contact)
    assert attempt.status == "valid"
    assert attempt.result is not None and attempt.result.affect == "curious"
    assert attempt.thoughts is None


async def test_an_answer_without_the_thoughts_object_is_a_schema_error() -> None:
    def labels_only(request_id):
        document = _answer(request_id)
        del document["thoughts"]
        return document

    attempt = await _attempt(labels_only)
    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "invalid-schema"
