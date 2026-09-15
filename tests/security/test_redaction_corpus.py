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

import ast
import contextlib
import io
import json
import logging
import sqlite3
import traceback
from pathlib import Path
from typing import NamedTuple

import h11
import httpx
import pytest
from pydantic import ValidationError

import adlife
from adlife.adapters.cognition import openai_compatible, prompts
from adlife.adapters.cognition.cache import CognitionCache, CorruptCacheRecord
from adlife.adapters.cognition.openai_compatible import (
    MAX_ECHOED_BODY_CHARS,
    OpenAICompatibleProvider,
    ProviderHttpError,
    UnusableApiKey,
    coerce_cognition_result,
)
from adlife.adapters.cognition.prompts import MAX_REPAIR_BODY_CHARS, build_repair_messages
from adlife.adapters.cognition.rules import (
    RuleCognitionInputs,
    RuleCognitionProvider,
    rule_cognition_result,
)
from adlife.adapters.cognition.service import CognitionBudget, CognitionService
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.output.plain import PlainEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.campaign import Campaign
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import (
    AUTH_HEADER_LABELS,
    REDACTION_PLACEHOLDER,
    SECRET_LABEL_WORDS,
    VENDOR_SECRET_SHAPES,
    PersonProfile,
    contains_labelled_secret_member,
    contains_provider_secret_text,
    contains_secret_or_email_text,
    contains_sensitive_text,
    redact_secret_text,
)
from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.serialization import persisted_text_objection
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
from adlife.core.ports.event_sink import EventSinkError
from adlife.core.ports.run_store import CorruptRunArtifact, InvalidEventBatch
from adlife.core.simulation.decision import RuleResponse
from adlife.core.simulation.rng import RandomOracle


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


S7_TOKEN = "sk-live-abcdefghij"
"""The short vendor-key token the S7 fixtures carry, named so its coverage is explicit.

Ten characters follow ``sk-live-``, which is far below
:data:`~adlife.core.domain.person.MIN_UNSEGMENTED_VENDOR_KEY_CHARS`. It is detected
anyway, for three independent reasons that
``test_the_s7_token_is_covered_by_three_named_screens`` asserts one at a time.
"""

_LEAKING_CAMPAIGN: dict[str, object] = {
    "call_to_action": "Try it today",
    "campaign_id": "brand-x-launch",
    "message": "A calmer routine",
    "product_category": "wellness",
    "product_name": "Calm Phone",
    "api_key": S7_TOKEN,
}


def test_the_s7_token_is_covered_by_three_named_screens() -> None:
    """Name the screens that catch the S7 token instead of relying on one incidentally.

    The token is short, so the narrowing of the segment-less vendor branch (S12) could
    plausibly have uncovered it. It did not, and each reason is asserted separately so a
    later narrowing cannot remove all three without a test going red:

    * SHAPE - the token carries a ``live`` segment, and the SEGMENTED branch of
      ``openai-style-key`` is untouched by S12 and keeps its eight-character tail.
    * STRUCTURAL - ``contains_labelled_secret_member`` walks JSON and sees the label in
      the mapping KEY with the credential in the sibling VALUE, which no per-string
      matcher can see at all. This is the screen the two S7 tests below actually exercise.
    * LABELLED VALUE - in a raw provider body, ``contains_provider_secret_text`` sees the
      label and the value it introduces.
    """
    assert contains_secret_or_email_text(S7_TOKEN)
    assert contains_labelled_secret_member({"api_key": S7_TOKEN})
    assert contains_provider_secret_text(f'{{"api_key":"{S7_TOKEN}"}}')


def test_a_campaign_member_keyed_by_a_secret_label_is_refused(
    cognition_request: CognitionRequest,
) -> None:
    """The exact dict the verifier persisted to disk through the request channel."""
    assert contains_labelled_secret_member(_LEAKING_CAMPAIGN)
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": dict(_LEAKING_CAMPAIGN)})


def test_a_persona_member_keyed_by_a_secret_label_is_refused(
    cognition_request: CognitionRequest,
) -> None:
    persona = dict(cognition_request.fictional_persona) | {"api_key": S7_TOKEN}
    assert contains_labelled_secret_member(persona)
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


# --- S12: the vendor key shape must not mistake ordinary campaign copy for a key ------


ORDINARY_COPY_NOT_A_VENDOR_KEY: tuple[str, ...] = (
    "Buy the pk-collection bundle now",
    "Visit example.invalid/sk-2026-collection",
    "Our rk-series headphones are here",
    "Try the sk-8 blend today",
)
"""Campaign copy that opens a hyphenated word with ``sk``, ``pk`` or ``rk``.

This is an ADVERTISING simulator and campaign copy is user-authored free text, so a
product line called ``rk-series``, a landing path called ``/sk-2026-collection`` and a
bundle called ``pk-collection`` are all ordinary input. Refusing them at request build is
a usability defect rather than a security win: nothing has been sent, and the operator is
told their advertisement carries a credential when it plainly does not.

The first two rows were REFUSED before this round; the last two were already accepted and
are carried here so the narrowing is pinned from both sides of its boundary.
"""

REALISTIC_VENDOR_KEYS_STILL_REFUSED: tuple[str, ...] = (
    "sk-test-0000abcdefghijklmnopqrstuvwxyz0123456789",
    "sk-proj-0000abcdefghijklmnopqrstuvwxyz01234567",
)
"""Obviously fake tokens carrying the LENGTH and SHAPE a real vendor key carries.

Zeros and the alphabet in order: nothing here resembles a live credential. They exist so
the narrowing above is pinned against the only way it could go wrong - by admitting a
real key.
"""


@pytest.mark.parametrize("copy_text", ORDINARY_COPY_NOT_A_VENDOR_KEY)
def test_ordinary_campaign_copy_is_not_a_vendor_key_shape(copy_text: str) -> None:
    """None of the three text screens may object to ordinary product copy."""
    assert not contains_secret_or_email_text(copy_text)
    assert not contains_provider_secret_text(copy_text)
    assert not contains_sensitive_text(copy_text)


@pytest.mark.parametrize("copy_text", ORDINARY_COPY_NOT_A_VENDOR_KEY)
def test_ordinary_campaign_copy_survives_the_redactor_unchanged(copy_text: str) -> None:
    """A screen that does not object must be matched by a redactor that does not cut."""
    assert redact_secret_text(copy_text) == copy_text
    assert redact_provider_body(copy_text) == copy_text


@pytest.mark.parametrize("copy_text", ORDINARY_COPY_NOT_A_VENDOR_KEY)
def test_ordinary_campaign_copy_is_accepted_at_request_build(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    copy_text: str,
) -> None:
    """The defect this round fixes: the prompt boundary refused the advertisement."""
    campaign = dict(cognition_campaign) | {"message": copy_text}
    updated = cognition_request.model_copy(update={"campaign": campaign})
    assert updated.campaign["message"] == copy_text


@pytest.mark.parametrize("key_text", REALISTIC_VENDOR_KEYS_STILL_REFUSED)
def test_a_realistic_vendor_key_is_still_seen_by_every_text_screen(key_text: str) -> None:
    """The narrowing may not cost one row of detection on a real key shape."""
    assert contains_secret_or_email_text(key_text)
    assert contains_provider_secret_text(key_text)
    assert contains_sensitive_text(key_text)


@pytest.mark.parametrize("key_text", REALISTIC_VENDOR_KEYS_STILL_REFUSED)
def test_a_realistic_vendor_key_is_still_removed_by_the_redactor(key_text: str) -> None:
    body = f'{{"detail":"{key_text} was rejected"}}'
    redacted = redact_provider_body(body)
    assert key_text not in redacted
    assert REDACTION_PLACEHOLDER in redacted


@pytest.mark.parametrize("key_text", REALISTIC_VENDOR_KEYS_STILL_REFUSED)
def test_a_realistic_vendor_key_is_still_refused_at_request_build(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
    key_text: str,
) -> None:
    campaign = dict(cognition_campaign) | {"note": f"the gateway rejected {key_text}"}
    with pytest.raises(ValidationError, match="sensitive"):
        cognition_request.model_copy(update={"campaign": campaign})


def test_a_short_segmentless_token_keeps_every_screen_except_the_shape_screen() -> None:
    """What a short unlabelled ``sk-``/``pk-``/``rk-`` token IS and IS NOT protected by.

    This is the residual risk of the S12 narrowing, written as an assertion rather than
    left in a docstring. ``sk-2026-collection`` carries no vendor segment and eighteen
    characters after the prefix, so:

    * IS NOT protected by the value-SHAPE screen. Standing alone in text it is accepted,
      which is the whole point - it is a landing path, not a credential. A hand-made or
      truncated credential of that same shape would be missed the same way. No published
      vendor key of this family is short enough to land here.
    * IS protected by every LABEL-driven screen. A credential label in the mapping key
      (S7), a label introducing it in a body (S1/S6), a label in a sibling member (S2), a
      transport header name (S3) and a ``Bearer`` prefix all still catch it.
    """
    token = "sk-2026-collection"

    assert not contains_secret_or_email_text(token)
    assert not contains_provider_secret_text(token)
    assert not contains_sensitive_text(token)

    assert contains_labelled_secret_member({"api_key": token})
    assert contains_provider_secret_text(f'{{"api_key":"{token}"}}')
    assert contains_provider_secret_text(f'{{"name":"api_key","value":"{token}"}}')
    assert contains_provider_secret_text(f'{{"received_headers":{{"x-api-key":"{token}"}}}}')
    bearer_body = f'{{"received_headers":{{"authorization":"Bearer {token}"}}}}'
    assert contains_provider_secret_text(bearer_body)


def test_the_segmented_vendor_branch_keeps_its_original_short_tail() -> None:
    """S12 narrowed the segment-less branch ONLY; the segmented branch is unchanged.

    Every short vendor token already in this repository's tests carries a segment, so this
    is the assertion that says the narrowing cost none of them their shape coverage.
    """
    segmented = ("sk-live-abcdef1234", "sk-test-0000000000000000", "pk_test_0000000000000000")
    for token in segmented:
        assert contains_secret_or_email_text(token), token
        assert redact_secret_text(token) == REDACTION_PLACEHOLDER, token


# --- T10: the network provider's own diagnostic channels ------------------------------
#
# Task 10 is the first code that touches a network, and it opens four NEW paths a
# provider body can travel: a raised ProviderCallError's message, the repair prompt that
# echoes a rejected answer back to the model, the CognitionResolution the run and report
# layers read, and the log records the service writes. Every shape already in
# LEAK_CORPUS is replayed down each of them here, so adding one row above still gains
# assertions about the network boundary as well as about the cache.


async def _no_sleep(_: float) -> None:
    """The service's retry sleep, made instant so a security test never waits."""
    return None


def _network_provider(
    handler: object,
    *,
    api_key: str = "sk-live-AAAABBBBCCCCDDDD",
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        model="test-model",
        api_key=api_key,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
async def test_the_corpus_shape_never_reaches_a_provider_error_message(
    shape: LeakShape,
    cognition_request: CognitionRequest,
) -> None:
    """An error body is exactly where a provider echoes a credential back."""
    provider = _network_provider(lambda _: httpx.Response(500, text=shape.body))
    with pytest.raises(ProviderHttpError) as caught:
        await provider.evaluate(cognition_request)
    assert shape.secret not in str(caught.value)
    assert shape.secret not in repr(caught.value)


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_a_repair_prompt(
    shape: LeakShape,
    cognition_request: CognitionRequest,
) -> None:
    """The repair prompt is a second copy of a rejected body, sent back over the wire."""
    messages = build_repair_messages(
        cognition_request,
        invalid_content=shape.body,
        error=f"schema rejected: {shape.body}",
    )
    assert shape.secret not in json.dumps(messages)


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
async def test_the_corpus_shape_never_reaches_a_cognition_resolution(
    shape: LeakShape,
    cognition_request: CognitionRequest,
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A resolution is handed to the event, storage and report layers."""
    fallback = RuleCognitionProvider.for_requests(
        [cognition_request],
        {
            cognition_request.request_id: RuleCognitionInputs(
                profile=valid_profile,
                state=consumer_state,
                campaign=valid_campaign,
                placement=valid_campaign.placements[0],
            )
        },
    )
    service = CognitionService(
        fallback=fallback,
        budget=CognitionBudget(per_agent=6, total=180),
        oracle=RandomOracle(root_seed=7),
        sleep=_no_sleep,
    )
    provider = _network_provider(
        lambda _: httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": shape.body}}]},
        )
    )
    with caplog.at_level(logging.DEBUG):
        resolution = await service.evaluate_one(cognition_request, provider)
    assert resolution.source == "fallback"
    assert resolution.raw_response is not None
    assert shape.secret not in resolution.raw_response
    assert not contains_provider_secret_text(resolution.raw_response)
    assert all(shape.secret not in record.getMessage() for record in caplog.records)


# --- T10 fix round 1: three more channels the network boundary opens ------------------
#
# Every row above is short, so none of them reached the bound an HTTP error body is cut
# at, the JSON KEY position a provider chooses for itself, or the exception chain a
# schema rejection leaves behind. All three are exercised from the same rows here.


STRADDLE_SURVIVORS = (4, 12)
"""How many characters of a corpus secret fall on the near side of an echo bound.

A single offset pins almost nothing: a fragment long enough for the vendor shape to
match again is removed by the redaction that follows a bound, and a fragment of one or
two characters is indistinguishable from ordinary prose. Both offsets here were measured
to leave a readable fragment of eleven corpus rows under the defective ordering.
"""

MAX_INNOCENT_FRAGMENT = 3
"""The longest secret prefix a redacted diagnostic may share with ordinary prose.

Measured over this corpus at every offset above: the shipped ordering leaves at most ONE
character, and the defective ordering leaves four or more. Asserting only that the WHOLE
secret is absent - which is what the first version of this test asserted - holds for
every fragment and therefore holds for the defect too.
"""


def _longest_surviving_fragment(text: str, secret: str) -> int:
    """How many LEADING characters of ``secret`` are still readable in ``text``.

    A bound leaves a PREFIX behind, so the prefix length is what a bound applied before
    redaction produces and what an assertion about that defect has to measure.
    """
    for length in range(len(secret), 0, -1):
        if secret[:length] in text:
            return length
    return 0


async def _measured_echo_cut(request: CognitionRequest) -> int:
    """Where the echo bound actually cuts, measured through the provider.

    MEASURED rather than restated from the constants. The first version of this helper
    subtracted a hand-written nine from :data:`MAX_ECHOED_BODY_CHARS`, and the fix that
    introduced a truncation marker moved the cut three characters without moving the
    padding: every corpus row then straddled a point the bound no longer cut at, and the
    module stopped testing the ordering it is named for. The padding character below is
    redaction-free, so every one that survives was kept by the bound, not by a pattern.
    """
    provider = _network_provider(
        lambda _: httpx.Response(500, text="z" * (MAX_ECHOED_BODY_CHARS * 3))
    )
    with pytest.raises(ProviderHttpError) as caught:
        await provider.evaluate(request)
    await provider.aclose()
    return str(caught.value).count("z")


def _measured_repair_cut(request: CognitionRequest) -> int:
    """Where the repair-echo bound actually cuts, measured through the prompt builder."""
    echoed = build_repair_messages(
        request,
        invalid_content="z" * (MAX_REPAIR_BODY_CHARS * 3),
        error="schema rejected",
    )[-2]["content"]
    return echoed.count("z")


def _straddling_body(shape: LeakShape, *, cut: int, surviving: int) -> str:
    """Pad a shape so that ``surviving`` characters of its secret precede ``cut``.

    The padding ends in a space rather than running straight into the shape, because a
    label pattern needs its word boundary: ``zzzzapi_key`` is not ``api_key``, and a test
    that glued the two together would be proving something about its own padding.
    """
    head = shape.body.index(shape.secret)
    width = cut - surviving - head
    assert width >= 1, "the corpus row is too long to straddle this bound"
    return "z" * (width - 1) + " " + shape.body


@pytest.mark.parametrize("surviving", STRADDLE_SURVIVORS)
@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
async def test_the_corpus_shape_survives_no_boundary_of_the_error_body_bound(
    shape: LeakShape,
    surviving: int,
    cognition_request: CognitionRequest,
) -> None:
    """A bound applied before redaction bounds the wrong string (finding S9).

    The shape is padded so that exactly ``surviving`` characters of its secret fall on
    the near side of the measured cut. Cutting first leaves that fragment, no vendor
    pattern matches it any more, and redacting afterwards therefore leaves it in the
    message and in the retry log line the service writes from it.
    """
    cut = await _measured_echo_cut(cognition_request)
    body = _straddling_body(shape, cut=cut, surviving=surviving)
    assert body.index(shape.secret) == cut - surviving
    provider = _network_provider(lambda _: httpx.Response(500, text=body))
    with pytest.raises(ProviderHttpError) as caught:
        await provider.evaluate(cognition_request)
    await provider.aclose()
    message = str(caught.value)
    assert shape.secret not in message
    assert not contains_provider_secret_text(message)
    assert _longest_surviving_fragment(message, shape.secret) <= MAX_INNOCENT_FRAGMENT


@pytest.mark.parametrize("surviving", STRADDLE_SURVIVORS)
@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_survives_no_boundary_of_the_repair_echo_bound(
    shape: LeakShape,
    surviving: int,
    cognition_request: CognitionRequest,
) -> None:
    """The same ordering rule, on the copy that is posted back to the endpoint.

    Every row above is far shorter than the repair bound, so none of them reached it.
    This is the only echoed body the repository transmits rather than logs, and it has no
    second redaction behind it: the assistant turn goes straight into the HTTPS request
    that ``repair_call`` sends.
    """
    cut = _measured_repair_cut(cognition_request)
    body = _straddling_body(shape, cut=cut, surviving=surviving)
    assert body.index(shape.secret) == cut - surviving
    messages = build_repair_messages(
        cognition_request,
        invalid_content=body,
        error=f"schema rejected: {body}",
    )
    assert shape.secret not in json.dumps(messages)
    # Only the two ECHOED turns are searched for a fragment. The replayed original
    # exchange carries the request id, a creative digest and a fenced data block, and a
    # short prefix such as ``0000`` occurs there in text nobody echoed.
    echoed = " ".join((messages[-2]["content"], messages[-1]["content"]))
    assert _longest_surviving_fragment(echoed, shape.secret) <= MAX_INNOCENT_FRAGMENT


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_the_undefined_field_warning(
    shape: LeakShape,
    cognition_request: CognitionRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A provider names its own JSON keys, so a KEY is provider-controlled text too.

    An undefined field is dropped with a warning that echoes its NAME, and that was the
    one diagnostic in the provider module that never met the published redactor.
    """
    content = {
        "request_id": cognition_request.request_id,
        shape.body: "dropped",
    }
    with caplog.at_level(logging.WARNING), pytest.raises(CognitionError):
        coerce_cognition_result(json.dumps(content), request_id=cognition_request.request_id)
    written = "\n".join(record.getMessage() for record in caplog.records)
    assert "undefined field" in written
    assert shape.secret not in written


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_a_formatted_traceback(
    shape: LeakShape,
    cognition_request: CognitionRequest,
) -> None:
    """A pydantic error prints the value it rejected, and a traceback prints the chain.

    ``emotion`` is a closed enumeration, so any body put there is rejected and the
    rejected value is what the underlying ``ValidationError`` renders.
    """
    content = {
        "schema_version": 1,
        "request_id": cognition_request.request_id,
        "interpretation": "A calm fictional handset.",
        "emotion": shape.body,
        "valence": 0.3,
        "relevance": 0.5,
        "credibility": 0.7,
        "sentiment_delta": 0.06,
        "recall_delta": 0.12,
        "purchase_reason": "Rule-derived intention.",
        "share_probability": 0.04,
        "discussion_hook": "A phone pitched at a calmer routine.",
        "grounded_reasons": ["matches technology interest"],
        "memory_summary": "Saw campaign-phone on mobile-feed.",
        "rule_modifier": 0.05,
        "safety_flags": [],
    }
    with pytest.raises(CognitionError) as caught:
        coerce_cognition_result(json.dumps(content), request_id=cognition_request.request_id)
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert shape.secret not in rendered


# --- T10 fix round 3, C1: the exception CHAIN is a channel of its own -----------------
#
# Fix round 1 closed this shape on the pydantic path: a ``ValidationError`` renders the
# value it rejected, so ``InvalidProviderResponse`` suppresses it with ``from None``
# rather than chaining it. The transport path had the same defect and a worse payload -
# ``h11`` refuses an illegal header value with a message that QUOTES the whole header,
# so a credential carrying one character illegal in a header value reached every
# formatted traceback through ``ProviderUnavailable.__cause__``.
#
# Two closures, because one alone is not enough. The chain is suppressed everywhere a
# cognition failure translates another exception (pinned structurally below), and the
# credential is screened BEFORE it is written into a header at all, so the underlying
# refusal is never raised in the first place.


ILLEGAL_HEADER_CREDENTIAL = "sk-test-0000111122223333"
"""An obviously fake credential. The tests below append an illegal character to it."""

LEGAL_TEST_CREDENTIAL = "sk-test-0000aaaabbbbcccc"
"""An obviously fake credential a header can carry, for building a client with."""


class _H11ValidatingTransport(httpx.AsyncBaseTransport):
    """The header validation a real connection performs, with no socket opened.

    ``httpx`` accepts an illegal header VALUE at client construction and at
    ``build_request``; only the wire layer objects. ``httpcore`` hands the raw headers to
    ``h11``, whose ``LocalProtocolError`` message is ``Illegal header value <the whole
    value>``, and ``httpx._transports.default.map_httpcore_exceptions`` re-raises that as
    :class:`httpx.LocalProtocolError`, which IS an :class:`httpx.HTTPError`. This
    transport runs exactly that validation through the real ``h11`` state machine and
    re-raises it exactly the way the real stack does, so the exception under test is the
    one a live call produces rather than a hand-written stand-in.
    """

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        connection = h11.Connection(our_role=h11.CLIENT)
        try:
            connection.send(
                h11.Request(
                    method=request.method,
                    target=request.url.raw_path,
                    headers=list(request.headers.raw),
                )
            )
        except h11.LocalProtocolError as error:
            raise httpx.LocalProtocolError(str(error)) from error
        return httpx.Response(200, json={})


def test_the_wire_layer_really_does_quote_the_header_it_refuses() -> None:
    """The premise of the test below, asserted rather than assumed.

    If a future ``h11`` stops quoting the offending value, this fails and the test that
    follows becomes vacuous - which is exactly when its justification needs re-reading.
    """
    connection = h11.Connection(our_role=h11.CLIENT)
    with pytest.raises(h11.LocalProtocolError) as caught:
        connection.send(
            h11.Request(
                method="POST",
                target="/v1/chat/completions",
                headers=[
                    ("host", "provider.invalid"),
                    ("authorization", f"Bearer {ILLEGAL_HEADER_CREDENTIAL}\nx-injected: 1"),
                ],
            )
        )
    assert ILLEGAL_HEADER_CREDENTIAL in str(caught.value)


async def test_a_transport_refusal_never_reaches_a_formatted_traceback(
    cognition_request: CognitionRequest,
) -> None:
    """C1: the live key reached every traceback through ``ProviderUnavailable.__cause__``.

    The credential screen is bypassed deliberately - the client is built with a legal
    credential and the illegal one is written into the transport default headers
    afterwards - because what is under test here is the CHAIN, not the screen. Both
    closures are needed: a transport failure that quotes a request is not limited to the
    one header this module writes.
    """
    transport = _H11ValidatingTransport()
    provider = OpenAICompatibleProvider(
        base_url="https://provider.invalid/v1",
        model="test-model",
        api_key=LEGAL_TEST_CREDENTIAL,
        timeout_seconds=5.0,
        transport=transport,
    )
    provider._client.headers["Authorization"] = f"Bearer {ILLEGAL_HEADER_CREDENTIAL}\nx-injected: 1"
    with pytest.raises(CognitionError) as caught:
        await provider.evaluate(cognition_request)
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    await provider.aclose()
    assert ILLEGAL_HEADER_CREDENTIAL not in rendered
    assert ILLEGAL_HEADER_CREDENTIAL not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("label", "suffix"),
    [
        ("newline", "\nx-injected: 1"),
        ("carriage-return", "\rx-injected: 1"),
        ("null", "\x00"),
        ("non-ascii", "—"),
        ("delete", "\x7f"),
    ],
)
def test_a_credential_a_header_cannot_carry_is_refused_before_it_reaches_one(
    label: str,
    suffix: str,
) -> None:
    """The second closure: screen the credential SHAPE before it is written anywhere.

    ``non-ascii`` is a separate defect of the same family: ``httpx`` encodes a header
    value as ASCII at client construction, so a credential carrying one raised a bare
    ``UnicodeEncodeError`` out of the constructor - not a
    :class:`~adlife.core.ports.cognition.CognitionError`, which is the one shape
    specification section 12 forbids from escaping this boundary.

    The message must name nothing about the value: not the value, not a fragment of it,
    not the offending character and not its position.
    """
    credential = f"{ILLEGAL_HEADER_CREDENTIAL}{suffix}"
    with pytest.raises(UnusableApiKey) as caught:
        OpenAICompatibleProvider(
            base_url="https://provider.invalid/v1",
            model="test-model",
            api_key=credential,
            timeout_seconds=5.0,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        )
    message = str(caught.value)
    assert isinstance(caught.value, CognitionError)
    assert ILLEGAL_HEADER_CREDENTIAL not in message
    assert suffix.strip() not in message
    assert caught.value.__cause__ is None
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert ILLEGAL_HEADER_CREDENTIAL not in rendered


def test_a_credential_a_header_can_carry_is_still_accepted() -> None:
    """The screen must not refuse the credentials real endpoints issue.

    Every shape here is printable ASCII with no space: the vendor prefixes, a JWT, a
    base64url token with padding, and the local placeholder.
    """
    for credential in (
        LEGAL_TEST_CREDENTIAL,
        "sk-test-proj-0000_1111-2222.3333",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIwMDAwIn0.AAAABBBBCCCCDDDD",
        "0000aaaa1111bbbb2222cccc3333dddd==",
        "ollama",
    ):
        provider = OpenAICompatibleProvider(
            base_url="https://provider.invalid/v1",
            model="test-model",
            api_key=credential,
            timeout_seconds=5.0,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        )
        assert provider.provider_metadata.kind == "remote-llm"


def test_a_corrupt_cache_record_does_not_chain_the_value_it_rejected(
    tmp_path: Path,
) -> None:
    """The same shape on the cache path: pydantic renders the input it refused.

    A cache file holds provider-derived text, so the value a rejected record carries can
    be a credential an endpoint echoed. ``CorruptCacheRecord`` names the key and nothing
    else; without the suppression the whole rejected document travelled out on
    ``__cause__``.
    """
    secret = "0000abcdef1234567890"
    key = "a" * 64
    cache = CognitionCache(tmp_path)
    path = cache.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"key": key, "x_authorization_echo": f"Bearer {secret}"}),
        encoding="utf-8",
    )
    with pytest.raises(CorruptCacheRecord) as caught:
        cache.get(key)
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


def test_the_rule_fallback_does_not_chain_the_value_it_rejected(
    cognition_request: CognitionRequest,
    cognition_campaign: dict[str, object],
) -> None:
    """The terminal fallback carried the same defect as the provider it backs up.

    ``UnrepresentableRuleResult`` reports how many fields were rejected and never which
    values, exactly as ``InvalidProviderResponse`` does - and then chained the
    ``ValidationError`` that renders every one of them.

    The slug is held in a VARIABLE rather than written at the raising call, because a
    formatted traceback quotes its own source lines: a literal there would be found in
    the rendering whether or not the chain carried it, and the assertion would pin
    nothing.
    """
    campaign_id = "1234567890"
    campaign = dict(cognition_campaign) | {"campaign_id": campaign_id}
    request = cognition_request.model_copy(update={"campaign": campaign})
    with pytest.raises(CognitionError) as caught:
        rule_cognition_result(request, _rule_response(campaign_id))
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert campaign_id not in rendered
    assert "memory_summary" not in rendered
    assert caught.value.__cause__ is None


COGNITION_BOUNDARY_MODULES: tuple[Path, ...] = (
    *sorted((Path(adlife.__file__).resolve().parent / "adapters" / "cognition").glob("*.py")),
    Path(adlife.__file__).resolve().parent / "core" / "ports" / "cognition.py",
)
"""Every module that handles provider, header, URL or credential material."""


def test_no_cognition_boundary_failure_chains_the_exception_it_translates() -> None:
    """The class of defect, closed structurally rather than one call site at a time.

    Every exception these modules translate was built from provider-controlled input - a
    header this repository composed, a URL a caller configured, a body an endpoint sent,
    or a document pydantic rejected and therefore renders. Chaining ANY of them puts that
    material into ``__cause__``, which every formatted traceback, ``logger.exception``
    call and crash report prints, downstream of the redaction that screens the message
    itself.

    So the rule is structural, and this walks the source to enforce it: inside these
    modules a ``raise`` may carry no cause but ``None``. It is a rule about the CHAIN and
    not about diagnostics - each of these failures already reports the status, the
    exception class name, the rejected-field count or the cache key in its own screened
    message.
    """
    assert COGNITION_BOUNDARY_MODULES
    offenders: list[str] = []
    for path in COGNITION_BOUNDARY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.cause is None:
                continue
            cause = ast.unparse(node.cause)
            if cause != "None":
                offenders.append(f"{path.name}:{node.lineno}: raise ... from {cause}")
    assert offenders == []


# --- T10 fix round 3, I4 + I8: the fail-closed arm of both settling loops --------------
#
# Two functions run the same redact-then-bound loop to a fixed point - ``_excerpt``, which
# builds a diagnostic out of a failing provider body, and ``_bounded_echo``, which builds
# the assistant turn a repair prompt quotes - and each drops the text entirely if it has
# not settled after four passes. Keeping a credential out of an echoed string is the whole
# job of both, and NO test executed either final arm: flipping either to ``return text``
# left the suite green while shipping unsettled, unredacted text to a third party.
#
# WHAT THESE TESTS SUBSTITUTE AND WHY. With the shipped redactor no body is known that
# fails to settle: redaction is idempotent, truncation is monotonically shortening, and a
# search over the cut boundary found no input that changes for four consecutive passes.
# That is precisely why the arm is a DEFENSIVE bound on a seam rather than a branch on
# ordinary input, and it is why the only honest way to execute it is to hand each loop a
# redactor that does not settle. The substitution is the published redactor's NAME inside
# one module for the duration of one test; no second redactor is added to the repository,
# and nothing production imports is changed.


def _never_settles(value: str) -> str:
    """A redactor that never reaches a fixed point, and removes nothing on the way.

    One trailing space per pass: the text differs on every pass, so the loop can never
    take its ``settled == text`` exit, and the credential is still in the string - which
    is what makes the fail-open mutation observable rather than merely different.
    """
    return f"{value} "


UNSETTLED_BODY: str = '{"error":{"message":"api_key 0000abcdef1234567890 was rejected"}}'
"""A body carrying a corpus-shaped credential, for the two loops to fail closed on."""

UNSETTLED_SECRET: str = "0000abcdef1234567890"


def test_a_diagnostic_body_that_never_settles_is_dropped_rather_than_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I4: the arm in ``openai_compatible._excerpt``."""
    monkeypatch.setattr(openai_compatible, "redact_provider_body", _never_settles)
    excerpt = openai_compatible._excerpt(UNSETTLED_BODY)
    assert excerpt == REDACTION_PLACEHOLDER
    assert UNSETTLED_SECRET not in excerpt


async def test_a_failing_status_whose_body_never_settles_echoes_no_body_at_all(
    monkeypatch: pytest.MonkeyPatch,
    cognition_request: CognitionRequest,
) -> None:
    """I4 through the public surface the arm exists to protect.

    ``ProviderHttpError`` quotes the failing body, so an unsettled body reaches a log
    line, a report and a stored resolution. The message keeps its shape - the status and
    the endpoint are still named - and carries the bare placeholder where the body was.
    """
    monkeypatch.setattr(openai_compatible, "redact_provider_body", _never_settles)
    provider = _network_provider(lambda _: httpx.Response(500, text=UNSETTLED_BODY))
    with pytest.raises(ProviderHttpError) as caught:
        await provider.evaluate(cognition_request)
    await provider.aclose()
    message = str(caught.value)
    assert UNSETTLED_SECRET not in message
    assert REDACTION_PLACEHOLDER in message
    assert "HTTP 500" in message


def test_a_repair_echo_that_never_settles_is_dropped_rather_than_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I8: the same arm in ``prompts._bounded_echo``."""
    monkeypatch.setattr(prompts, "redact_provider_body", _never_settles)
    echoed = prompts._bounded_echo(UNSETTLED_BODY)
    assert echoed == REDACTION_PLACEHOLDER
    assert UNSETTLED_SECRET not in echoed


def test_a_repair_prompt_sends_no_body_it_could_not_settle(
    monkeypatch: pytest.MonkeyPatch,
    cognition_request: CognitionRequest,
) -> None:
    """I8 through the public surface: this text is POSTED to a third party.

    Both echoes - the rejected answer quoted as an assistant turn and the failure text
    quoted in the repair instruction - go through the same loop, so both are asserted.
    """
    monkeypatch.setattr(prompts, "redact_provider_body", _never_settles)
    messages = prompts.build_repair_messages(
        cognition_request,
        invalid_content=UNSETTLED_BODY,
        error=f"schema rejected: {UNSETTLED_BODY}",
    )
    assert UNSETTLED_SECRET not in json.dumps(messages)
    assert messages[-2]["content"] == REDACTION_PLACEHOLDER
    assert REDACTION_PLACEHOLDER in messages[-1]["content"]


def test_a_body_that_does_settle_is_still_echoed_after_the_substitution_is_undone() -> None:
    """The two tests above must not be passing because the loop drops everything.

    With the real redactor the same body settles and is echoed, so the placeholder in
    those assertions is the fail-closed arm firing rather than the loop's ordinary
    behaviour.
    """
    assert openai_compatible._excerpt("a plain body with no credential in it") == (
        "a plain body with no credential in it"
    )
    assert prompts._bounded_echo("a plain body with no credential in it") == (
        "a plain body with no credential in it"
    )


# --- T11: the run artifact is a new place text can come to rest ------------------------
#
# Task 11 opens two new paths from an event payload to somewhere permanent: the SQLite run
# store writes it into results.sqlite3 AND events.jsonl, and the JSONL sink writes it into
# any file a caller points at. Both are screened at the boundary with the two rules this
# repository already publishes - the narrow campaign-copy screen and the structural
# labelled-member walk - and neither introduces a second redactor.
#
# WHAT THE STORAGE SCREEN CLAIMS, precisely. The BROAD raw-provider-body screen is
# deliberately NOT used here: it fires on "Cookie lovers unite: the fictional snack brand"
# and would refuse ordinary advertising copy, which is the product this simulator exists
# to model. Seven of the shapes below are therefore outside what the storage screen names
# on its own - they are raw provider error bodies, and the control that keeps THEM out of
# an artifact is upstream, where ``redact_provider_body`` empties a body before it can
# become any message at all. ``test_the_corpus_shape_never_reaches_a_run_artifact``
# exercises exactly that route end to end and reads every byte the run wrote.


def _storage_fixture(
    tmp_path: Path, run_manifest: RunManifest, valid_scenario: Scenario
) -> SQLiteRunStore:
    store = SQLiteRunStore(tmp_path)
    store.create_run(run_manifest, scenario=valid_scenario)
    return store


def _payload_event(run_manifest: RunManifest, payload: dict[str, object]) -> DomainEvent:
    return DomainEvent(
        event_id=f"{run_manifest.run_id}:event-00000000",
        run_id=run_manifest.run_id,
        simulated_minute=0,
        sequence=0,
        event_type=EventType.COGNITION_FALLBACK,
        payload=payload,
        source=EventSource.FALLBACK,
    )


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_is_refused_entry_to_a_persisted_document(shape: LeakShape) -> None:
    """A credential under a credential label never reaches an artifact, whatever it is."""
    assert persisted_text_objection({"api_key": shape.secret}, label="payload") is not None


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_a_run_artifact(
    shape: LeakShape,
    tmp_path: Path,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
) -> None:
    """Route a real provider body through the real upstream control into a real run."""
    store = _storage_fixture(tmp_path, run_manifest, valid_scenario)
    event = _payload_event(run_manifest, {"provider_note": redact_provider_body(shape.body)})

    with contextlib.suppress(InvalidEventBatch):
        store.append_events([event])

    directory = store.run_directory(run_manifest.run_id)
    survivors = [
        path.name
        for path in sorted(directory.rglob("*"))
        if path.is_file() and shape.secret.encode("utf-8") in path.read_bytes()
    ]
    assert survivors == []


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_the_portable_export(
    shape: LeakShape, tmp_path: Path, run_manifest: RunManifest
) -> None:
    path = tmp_path / "events.jsonl"
    event = _payload_event(run_manifest, {"provider_note": redact_provider_body(shape.body)})

    with JsonlEventSink(path) as sink, contextlib.suppress(EventSinkError):
        sink.append_many(run_manifest.run_id, [event])

    assert shape.secret.encode("utf-8") not in path.read_bytes()


@pytest.mark.parametrize("shape", LEAK_CORPUS, ids=_CORPUS_IDS)
def test_the_corpus_shape_never_reaches_a_human_readable_event_line(
    shape: LeakShape, run_manifest: RunManifest
) -> None:
    """The plain sink omits the payload structurally, so even a RAW body cannot print."""
    stream = io.StringIO()
    event = _payload_event(run_manifest, {"provider_note": shape.body})

    PlainEventSink(stream).append_many(run_manifest.run_id, [event])

    assert shape.secret not in stream.getvalue()


ARTIFACT_BOUNDARY_MODULES: tuple[Path, ...] = (
    *sorted((Path(adlife.__file__).resolve().parent / "adapters" / "storage").glob("*.py")),
    *sorted((Path(adlife.__file__).resolve().parent / "adapters" / "output").glob("*.py")),
    Path(adlife.__file__).resolve().parent / "core" / "ports" / "run_store.py",
    Path(adlife.__file__).resolve().parent / "core" / "ports" / "event_sink.py",
    Path(adlife.__file__).resolve().parent / "core" / "domain" / "serialization.py",
)
"""Every module that translates a failure over stored, campaign or provider-derived text."""


def test_no_artifact_boundary_failure_chains_the_exception_it_translates() -> None:
    """The same structural rule the cognition boundary carries, for the storage boundary.

    Everything these modules translate was built from material this repository does not
    control: a document pydantic rejected and therefore RENDERS, a SQLite error naming the
    row it refused, a campaign string a user wrote. Chaining any of them puts that material
    into ``__cause__``, which every formatted traceback and ``logger.exception`` call
    prints, downstream of the screens that filter the message itself. So inside these
    modules a ``raise`` may carry no cause but ``None``.
    """
    assert ARTIFACT_BOUNDARY_MODULES
    offenders: list[str] = []
    for path in ARTIFACT_BOUNDARY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.cause is None:
                continue
            cause = ast.unparse(node.cause)
            if cause != "None":
                offenders.append(f"{path.name}:{node.lineno}: raise ... from {cause}")
    assert offenders == []


def test_a_rejected_stored_document_does_not_chain_what_it_rejected(
    tmp_path: Path, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """The concrete case: a tampered event document renders in a pydantic error."""
    store = _storage_fixture(tmp_path, run_manifest, valid_scenario)
    store.append_events([_payload_event(run_manifest, {"message": "an ordinary fictional ad"})])
    with sqlite3.connect(store.database_path(run_manifest.run_id)) as connection:
        connection.execute(
            "UPDATE events SET event_json = ?",
            ('{"payload":{"api_key":"0000abcdef1234567890"},"broken":true}',),
        )

    with pytest.raises(CorruptRunArtifact) as raised:
        store.load_run(run_manifest.run_id)

    assert raised.value.__cause__ is None
    assert "0000abcdef1234567890" not in "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
