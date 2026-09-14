"""The consolidated adversarial credential-leak corpus.

Every shape here was confirmed to survive redaction and reach a real ``<key>.json`` on
disk before this module existed. The corpus is deliberately a single flat table so that a
later task can add a leak shape in ONE line:

    LeakShape("S4", "my-vendor", '{"token":"zzz_0000"}', "zzz_0000"),

and immediately get five independent assertions about it - that the redactor removes it,
that the raw-body screen recognises it, that redaction is idempotent, that a
``CognitionRecord`` will not carry it, and that the bytes a real ``CognitionCache.put``
writes to disk do not contain it.

WHAT THIS CORPUS DOES AND DOES NOT PROVE
----------------------------------------
It proves that the LISTED shapes are removed. It does not prove that an arbitrary opaque
token under an unlabelled field is removed, because nothing can: an unlabelled random
string is indistinguishable from ordinary data. The screens are defense in depth on a
best-effort basis, and the module docstrings say exactly that.

Every value below is obviously fake - zeros, repeated letters, ``example.invalid`` - and
none of it resembles a live credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest
from pydantic import ValidationError

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.cognition.rules import (
    RuleCognitionInputs,
    RuleCognitionProvider,
    rule_cognition_result,
)
from adlife.core.domain.campaign import Campaign
from adlife.core.domain.person import (
    AUTH_HEADER_LABELS,
    REDACTION_PLACEHOLDER,
    SECRET_LABEL_WORDS,
    VENDOR_SECRET_SHAPES,
    PersonProfile,
    contains_provider_secret_text,
    contains_secret_or_email_text,
    contains_sensitive_text,
)
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.cognition import (
    MAX_RAW_RESPONSE_CHARS,
    CognitionError,
    CognitionRecord,
    CognitionRequest,
    ProviderMetadata,
    ProviderUsage,
    redact_provider_body,
)
from adlife.core.simulation.decision import RuleResponse


class LeakShape(NamedTuple):
    """One adversarial body, the finding that produced it, and the token that must go."""

    finding: str
    label: str
    body: str
    secret: str


LEAK_CORPUS: tuple[LeakShape, ...] = (
    # --- S1: the label-to-value window was bounded at 32 characters ------------------
    LeakShape(
        "S1",
        "label-and-value-separated-by-prose",
        '{"error":{"message":"Incorrect API key provided in your request headers '
        'today: abcdefghijklmnopqrstuvwxyz012345"}}',
        "abcdefghijklmnopqrstuvwxyz012345",
    ),
    LeakShape(
        "S1",
        "long-json-key-carrying-the-label",
        '{"error":{"code":401,"message":"Invalid credentials"},'
        '"api_key_for_the_openai_production_environment_service":"0000abcdef1234567890"}',
        "0000abcdef1234567890",
    ),
    # --- S2: the label and the value in different JSON members -----------------------
    LeakShape(
        "S2",
        "label-and-value-in-sibling-members",
        '{"error":{"code":401,"param":"api_key"},'
        '"echoed":{"name":"api_key","value":"0000abcdef1234567890"}}',
        "0000abcdef1234567890",
    ),
    LeakShape(
        "S2",
        "label-and-value-as-array-elements",
        '["api_key","0000abcdef1234567890"]',
        "0000abcdef1234567890",
    ),
    # --- S3: realistic auth header names --------------------------------------------
    LeakShape(
        "S3",
        "x-api-token",
        '{"received_headers":{"x-api-token":"0000abcdef1234567890"}}',
        "0000abcdef1234567890",
    ),
    LeakShape(
        "S3",
        "x-auth-token",
        '{"received_headers":{"x-auth-token":"0000abcdef1234567890"}}',
        "0000abcdef1234567890",
    ),
    LeakShape(
        "S3",
        "x-session-token",
        '{"received_headers":{"x-session-token":"0000abcdef1234567890"}}',
        "0000abcdef1234567890",
    ),
    LeakShape(
        "S3",
        "proxy-authorization",
        '{"received_headers":{"proxy-authorization":"Basic 0000abcdef1234567890"}}',
        "0000abcdef1234567890",
    ),
    LeakShape(
        "S3",
        "cookie",
        '{"received_headers":{"cookie":"session=0000abcdef1234567890"}}',
        "0000abcdef1234567890",
    ),
    LeakShape(
        "S3",
        "set-cookie",
        '{"received_headers":{"set-cookie":"session=0000abcdef1234567890; Path=/"}}',
        "0000abcdef1234567890",
    ),
    # --- S4: vendor value shapes outside sk|pk|rk ------------------------------------
    LeakShape(
        "S4",
        "github-personal-token",
        '{"detail":"ghp_0000000000000000000000000000000000 was rejected"}',
        "ghp_0000000000000000000000000000000000",
    ),
    LeakShape(
        "S4",
        "github-oauth-token",
        '{"detail":"gho_0000000000000000000000000000000000 was rejected"}',
        "gho_0000000000000000000000000000000000",
    ),
    LeakShape(
        "S4",
        "github-server-token",
        '{"detail":"ghs_0000000000000000000000000000000000 was rejected"}',
        "ghs_0000000000000000000000000000000000",
    ),
    LeakShape(
        "S4",
        "slack-bot-token",
        '{"detail":"xoxb-0000-0000-000000000000 was rejected"}',
        "xoxb-0000-0000-000000000000",
    ),
    LeakShape(
        "S4",
        "slack-user-token",
        '{"detail":"xoxp-0000-0000-000000000000 was rejected"}',
        "xoxp-0000-0000-000000000000",
    ),
    LeakShape(
        "S4",
        "slack-app-token",
        '{"detail":"xapp-0000-0000-000000000000 was rejected"}',
        "xapp-0000-0000-000000000000",
    ),
    LeakShape(
        "S4",
        "aws-access-key-id",
        '{"aws_access_key_id":"AKIA0000000000000000"}',
        "AKIA0000000000000000",
    ),
    LeakShape(
        "S4",
        "google-api-key",
        '{"detail":"AIza0000000000000000000000000000000 was rejected"}',
        "AIza0000000000000000000000000000000",
    ),
    LeakShape(
        "S4",
        "pem-private-key-block",
        '{"detail":"-----BEGIN RSA PRIVATE KEY-----\\n000000000000\\n'
        '-----END RSA PRIVATE KEY-----"}',
        "000000000000",
    ),
    # --- S5: a matched shape must be removed to the END of the token -----------------
    LeakShape(
        "S5",
        "hyphenated-vendor-key",
        "sk-live-AAAABBBB-CCCCDDDD-EEEEFFFF",
        "CCCCDDDD",
    ),
    LeakShape(
        "S5",
        "comma-separated-labelled-value",
        "api_key=AAAABBBB,CCCCDDDD",
        "CCCCDDDD",
    ),
    LeakShape(
        "S5",
        "space-separated-labelled-value",
        "api_key: AAAABBBB CCCCDDDD",
        "CCCCDDDD",
    ),
    # --- S6: the JSON shape the residual screen could not see ------------------------
    LeakShape(
        "S6",
        "quoted-json-key-terminator",
        '{"api_key":"AAAABBBBCCCC"}',
        "AAAABBBBCCCC",
    ),
)
"""Add a shape here and every assertion below covers it."""

_CORPUS_IDS = [f"{shape.finding}-{shape.label}" for shape in LEAK_CORPUS]


def _record_with(
    request: CognitionRequest,
    metadata: ProviderMetadata,
    raw_response: str | None,
) -> CognitionRecord:
    """One cognition record carrying ``raw_response``, keyed for a real cache write."""
    return CognitionRecord(
        key=CognitionCache.make_key(request, metadata),
        request=request,
        provider_metadata=metadata,
        raw_response=raw_response,
        result=rule_cognition_result(request, _rule_response(request.campaign_id)),
        usage=ProviderUsage(
            provider_kind="mock",
            model_id="mock-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        ),
    )


def _rule_response(campaign_id: str) -> RuleResponse:
    return RuleResponse(
        campaign_id=campaign_id,
        sentiment_delta=0.05,
        recall_delta=0.10,
        purchase_intention=0.20,
        share_probability=0.05,
        valence=0.40,
        relevance=0.60,
        credibility=0.55,
    )


# --- the corpus, through the redactor -------------------------------------------------


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_is_removed_by_the_redactor(shape: LeakShape) -> None:
    """``redact_provider_body`` must not leave the credential token behind."""
    redacted = redact_provider_body(shape.body)
    assert shape.secret not in redacted
    assert REDACTION_PLACEHOLDER in redacted


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_is_recognised_by_the_raw_body_screen(shape: LeakShape) -> None:
    """The fail-closed re-check has to know every shape the redactor knows.

    A screen that cannot see a shape cannot fail closed on it, which is what made the
    residual check near-inert for any body a real provider would return.
    """
    assert contains_provider_secret_text(shape.body)


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_redacting_a_corpus_shape_is_idempotent(shape: LeakShape) -> None:
    once = redact_provider_body(shape.body)
    assert redact_provider_body(once) == once


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_a_cognition_record(
    shape: LeakShape,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    record = _record_with(cognition_request, provider_metadata, shape.body)
    assert record.raw_response is not None
    assert shape.secret not in record.raw_response
    assert not contains_provider_secret_text(record.raw_response)


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_the_bytes_on_disk(
    shape: LeakShape,
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """The assertion is on the file, not on the model: this is the published channel."""
    cache = CognitionCache(tmp_path)
    record = _record_with(cognition_request, provider_metadata, shape.body)
    cache.put(record.key, record)
    stored = (tmp_path / f"{record.key}.json").read_bytes()
    assert shape.secret.encode("utf-8") not in stored
    reloaded = cache.get(record.key)
    assert reloaded is not None
    assert reloaded.raw_response == record.raw_response


# --- S6: the residual screen must be able to fire on JSON -----------------------------


@pytest.mark.parametrize(
    "body",
    [
        '{"api_key":"AAAABBBBCCCC"}',
        '{"password":"AAAABBBBCCCC"}',
        '{"access_token" : "AAAABBBBCCCC"}',
    ],
)
def test_the_narrow_screen_fires_on_a_quoted_json_key(body: str) -> None:
    """In JSON the character after a quoted key is ``"``, not ``:``.

    The screen matched ``label`` then ``\\s*[:=]``, so it could not fire on any JSON body
    at all - which made the fail-closed re-check a check on a shape no provider emits.
    """
    assert contains_secret_or_email_text(body)
    assert contains_sensitive_text(body)


@pytest.mark.parametrize("shape", VENDOR_SECRET_SHAPES, ids=lambda shape: shape[0])
def test_the_narrow_screen_knows_every_vendor_value_shape(
    shape: tuple[str, object],
) -> None:
    """The redactor and the screen are driven from ONE table, so both know every row."""
    name, _pattern = shape
    assert name in {row[0] for row in VENDOR_SECRET_SHAPES}


def test_the_label_tables_are_named_and_documented() -> None:
    """S3 and S4 require one documented list, not literals scattered over the module."""
    assert "authorization" in AUTH_HEADER_LABELS
    assert "proxy[_ -]?authorization" in AUTH_HEADER_LABELS
    assert any("cookie" in label for label in AUTH_HEADER_LABELS)
    assert any("api" in label for label in SECRET_LABEL_WORDS)
    assert len(VENDOR_SECRET_SHAPES) >= 8


# --- S7: the persisted request is a second channel ------------------------------------


_LEAKING_CAMPAIGN: dict[str, object] = {
    "call_to_action": "Try it today",
    "campaign_id": "brand-x-launch",
    "message": "A calmer routine",
    "product_category": "wellness",
    "product_name": "Calm Phone",
    "api_key": "sk-live-abcdefghij",
}


def test_a_campaign_member_keyed_by_a_secret_label_is_refused(
    cognition_request: CognitionRequest,
) -> None:
    """The exact dict the verifier persisted to disk through the request channel."""
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": dict(_LEAKING_CAMPAIGN)})


def test_a_persona_member_keyed_by_a_secret_label_is_refused(
    cognition_request: CognitionRequest,
) -> None:
    persona = dict(cognition_request.fictional_persona) | {"api_key": "sk-live-abcdefghij"}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"fictional_persona": persona})


def test_a_campaign_member_keyed_by_the_environment_variable_is_refused(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Specification section 6.4 names ``ADLIFE_API_KEY`` itself."""
    campaign = dict(cognition_campaign) | {"ADLIFE_API_KEY": "fictional-token-00000000"}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_nested_campaign_member_keyed_by_a_secret_label_is_refused(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Depth is not a hiding place: the walk is recursive."""
    campaign = dict(cognition_campaign) | {
        "vendor": {"integration": {"access_token": "fictional-token-11111111"}}
    }
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_campaign_array_pairing_a_label_with_a_value_is_refused(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """``["api_key", "0000..."]`` is the same leak with the colon spelled as a comma."""
    campaign = dict(cognition_campaign) | {"echo": ["api_key", "0000abcdef1234567890"]}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_campaign_value_of_a_vendor_key_shape_is_refused(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Value-shape screening now applies to the persisted request, not only to a body."""
    note = "rejected ghp_0000000000000000000000000000000000"
    campaign = dict(cognition_campaign) | {"note": note}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


@pytest.mark.parametrize(
    "member",
    [
        {"api_key": {"value": "fictional-token-00000000"}},
        {"api_key": ["fictional-token-00000000"]},
        {"api_key": 100000000},
        {"echo": [{"ADLIFE_API_KEY": "fictional-token-00000000"}]},
        {"echo": [["api_key", "0000abcdef1234567890"]]},
    ],
    ids=["nested-mapping", "nested-array", "numeric", "label-inside-an-array", "nested-pair"],
)
def test_a_secret_label_hides_behind_no_container(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    member: dict[str, object],
) -> None:
    """Whatever a credential label introduces is refused, at whatever depth and type."""
    campaign = dict(cognition_campaign) | member
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_an_empty_member_under_a_secret_label_is_not_a_leak(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """The screen refuses a credential, not the absence of one."""
    campaign = dict(cognition_campaign) | {"api_key": None, "access_token": ""}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["api_key"] is None


def test_an_ordinary_campaign_member_is_still_accepted(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """The structural screen must not refuse ordinary prompt structure."""
    campaign = dict(cognition_campaign) | {"delivery_window": "7 to 10 days", "keys": []}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["delivery_window"] == "7 to 10 days"


# --- S8: the terminal fallback never raises a bare ValidationError ---------------------


_LEGAL_CAMPAIGN_SLUGS: tuple[str, ...] = (
    "spring-1234567890",
    "promo-1234-5678-90",
    "sale-0800-123-4567",
    "q3-2026-0123456789",
)
"""Ordinary slugs that satisfy the domain rule ``^[a-z0-9][a-z0-9-]{0,79}$``."""


@pytest.mark.parametrize("campaign_id", _LEGAL_CAMPAIGN_SLUGS)
def test_the_terminal_fallback_answers_an_ordinary_campaign_slug(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    campaign_id: str,
) -> None:
    """A legal campaign slug admitted into the prompt must survive the answer screen."""
    campaign = dict(cognition_campaign) | {"campaign_id": campaign_id}
    request = cognition_request.model_copy(update={"campaign": campaign})
    result = rule_cognition_result(request, _rule_response(campaign_id))
    assert campaign_id in result.memory_summary


def test_a_rule_result_that_cannot_be_represented_raises_a_cognition_error(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """Specification section 12: a provider failure may never abort a run.

    ``1234567890`` is a legal campaign slug and a bare national-identifier shape, so the
    composed ``memory_summary`` cannot pass the strict persona screen. The refusal must
    still arrive as a ``CognitionError``, because a service catching that base class is
    what section 12's "never abort a run" is implemented with.
    """
    campaign = dict(cognition_campaign) | {"campaign_id": "1234567890"}
    request = cognition_request.model_copy(update={"campaign": campaign})
    with pytest.raises(CognitionError) as error:
        rule_cognition_result(request, _rule_response("1234567890"))
    assert not isinstance(error.value, ValidationError)


async def test_the_rule_provider_surfaces_the_same_failure_as_a_cognition_error(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> None:
    campaign_data = dict(cognition_campaign) | {"campaign_id": "1234567890"}
    request = cognition_request.model_copy(update={"campaign": campaign_data})
    campaign = valid_campaign.model_copy(update={"campaign_id": "1234567890"})
    provider = RuleCognitionProvider(
        {
            request.request_id: RuleCognitionInputs(
                profile=valid_profile,
                state=consumer_state,
                campaign=campaign,
                placement=campaign.placements[0],
            )
        }
    )
    with pytest.raises(CognitionError):
        await provider.evaluate(request)
    with pytest.raises(CognitionError):
        await provider.answer(request)


# --- S10: the documented side effect of the non-word-boundary label anchor ------------


@pytest.mark.parametrize("copy_text", ["top_secret: launch date", "brand_secret=launch"])
def test_a_compound_secret_label_is_refused_in_campaign_copy(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    copy_text: str,
) -> None:
    """Finding S10, recorded rather than reverted.

    The label anchors are non-alphanumeric lookarounds rather than ``\\b``, because ``\\b``
    cannot match between ``_`` and a letter and ``ADLIFE_API_KEY`` would otherwise not be
    a label at all. The cost is that ``secret`` matches inside ``top_secret`` and
    ``brand_secret``, so campaign copy of that exact shape is refused at request build.
    It is fail-closed, it refuses a prompt that has not been sent, and it is the
    documented price of covering ``CLIENT_SECRET=``.
    """
    campaign = dict(cognition_campaign) | {"message": copy_text}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


@pytest.mark.parametrize(
    "copy_text",
    [
        "Our secret sauce makes a calmer routine.",
        "The top secret is patience.",
        "A brand secret worth sharing.",
    ],
)
def test_the_word_secret_alone_is_not_a_secret_label(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    copy_text: str,
) -> None:
    """The side effect is narrow: a label only counts when an assignment follows it."""
    campaign = dict(cognition_campaign) | {"message": copy_text}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["message"] == copy_text


# --- S11: the deliberate loosening on the five paraphrasing answer fields -------------


_PARAPHRASING_FIELDS: tuple[str, ...] = (
    "interpretation",
    "purchase_reason",
    "discussion_hook",
)
_DIGIT_RUN_TEXTS: tuple[str, ...] = ("call 0800 123 4567 today", "offer code 1234567890")


@pytest.mark.parametrize("field", _PARAPHRASING_FIELDS)
@pytest.mark.parametrize("text", _DIGIT_RUN_TEXTS)
def test_a_paraphrasing_answer_field_admits_digit_run_text(
    cognition_request: CognitionRequest,
    field: str,
    text: str,
) -> None:
    """Finding S11, recorded rather than reverted.

    ``interpretation``, ``purchase_reason``, ``discussion_hook``, ``grounded_reasons`` and
    ``safety_flags`` restate campaign copy the prompt carried, so they carry the NARROW
    rule that copy carries. The consequence, stated plainly: provider-authored text of a
    phone or ten-digit-identifier shape IS admitted into these fields and IS persisted in
    the cognition cache. Nothing about credentials, contact addresses or filesystem paths
    is relaxed.
    """
    result = rule_cognition_result(cognition_request, _rule_response(cognition_request.campaign_id))
    updated = result.model_copy(update={field: text})
    assert getattr(updated, field) == text


@pytest.mark.parametrize("text", _DIGIT_RUN_TEXTS)
def test_the_looser_answer_fields_still_refuse_a_credential(
    cognition_request: CognitionRequest,
    text: str,
) -> None:
    result = rule_cognition_result(cognition_request, _rule_response(cognition_request.campaign_id))
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update={"interpretation": f"{text} api_key=sk-live-abcdef1234"})
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update={"grounded_reasons": ("ghp_0000000000000000000000000000000000",)})
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update={"safety_flags": ("analyst@example.invalid",)})


@pytest.mark.parametrize("text", _DIGIT_RUN_TEXTS)
def test_memory_summary_still_refuses_the_text_the_looser_fields_admit(
    cognition_request: CognitionRequest,
    text: str,
) -> None:
    """The pair is the point: ``memory_summary`` feeds a later prompt's memories."""
    result = rule_cognition_result(cognition_request, _rule_response(cognition_request.campaign_id))
    with pytest.raises(ValidationError, match="sensitive"):
        result.model_copy(update={"memory_summary": text})
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"relevant_memories": (text,)})


# --- S9: redaction may not expand a record past the bound it was written under --------


def test_a_body_whose_redaction_expands_still_reads_back(
    tmp_path: Path,
    cognition_request: CognitionRequest,
    provider_metadata: ProviderMetadata,
) -> None:
    """A record that can be written must be readable.

    ``[redacted]`` is longer than the shortest match, so a body at the field bound grew
    past it: the write succeeded and the read raised ``CorruptCacheRecord``.
    """
    body = "a@b.co " * 9300
    assert len(body) == 65100
    record = _record_with(cognition_request, provider_metadata, body)
    assert record.raw_response is not None
    assert len(record.raw_response) <= MAX_RAW_RESPONSE_CHARS
    assert "a@b.co" not in record.raw_response

    cache = CognitionCache(tmp_path)
    cache.put(record.key, record)
    reloaded = cache.get(record.key)
    assert reloaded is not None
    assert reloaded.raw_response == record.raw_response
