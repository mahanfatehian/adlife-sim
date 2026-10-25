"""Ordinary life-story and campaign prose passes the user-text screens; credentials do not.

Persona and campaign text is written by a person, and with prompt v3 a background is the
life story the AI answers from, so authors are invited to write prose. That text used to be
screened with the raw-provider-body screen, whose prose heuristics refuse "basic" before a
long word, a credential or header label up to 120 characters before a colon, and the
interest "cookies" as a transport header name. These tests pin both sides of the narrower
user-text screens: the prose is accepted at every layer that sees it (the draft models,
``/api/scenarios/validate``, the job's canonical re-read and the v3 persona card), and every
credential shape the old screen refused is still refused.

The phone screen is pinned here too: the dotted, slash and no-break-space numbers prompt v3
would send verbatim to a remote provider are refused where a new draft is accepted, saved
runs holding one still open, and dates, prices and versions are not numbers anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.city.workbench_input import (
    WorkbenchCampaignDraft,
    WorkbenchPersonaDraftV2,
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV4,
    parse_workbench_run_input_json,
)
from adlife.city.workbench_security import WorkbenchJsonContentError, strict_json_object
from adlife.city.workbench_validation import WorkbenchValidationError, construct_workbench_run
from adlife.core.domain import city_cognition_v2 as domain
from adlife.core.domain import person
from adlife.core.domain.city_cognition import CityCognitionCreativeSnapshotV1
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_response import SpatialResponseTraits
from tests.security.test_redaction_corpus import (
    ORDINARY_COPY_NOT_A_VENDOR_KEY,
    REALISTIC_VENDOR_KEYS_STILL_REFUSED,
)
from tests.unit.city import test_workbench_web as web
from tests.unit.city.run_story_fixtures import default_study_draft
from tests.unit.city.test_workbench_input import draft_v2_data, persona_data

LIFE_STORY_PROSE = (
    "She has only basic computer skills and a basic smartphone.",
    "After basic training she joined the fire brigade.",
    "Buys only basic necessities and saves the rest.",
    "Bakes cookies every Sunday; favourite: oatmeal.",
    "Her secret dream: to open a bakery by the sea.",
    "Has a password manager she never uses: too fiddly.",
)
"""Each sentence was refused as a credential before the user-text screen existed."""

CAMPAIGN_PROSE = (
    "Basic Essentials Box",
    "Cookie Crunch: 2 for 1",
    "Basic smartphone launch study",
    "Basic training instructor",
)

CREDENTIAL_TEXT = (
    "person@example.org",
    "Authorization: Bearer secret-token-1234567890",
    "authorization: Bearer 0000abcdef1234567890",
    "Proxy-Authorization: Basic 0000abcdef1234567890",
    "Bearer secret-token-1234567890",
    "Basic dXNlcjpwYXNzd29yZA==",
    "Cookie: session=0000abcdef1234567890",
    # Built at run time, so the source holds no credential-shaped literal.
    "x-api-key: " + "0" * 20,
    "api_key=secret-token-1234567890",
    "ADLIFE_API_KEY=" + "0" * 20,
    '{"api_key":"AAAABBBBCCCC"}',
    "Password: " + "hunter2-but-longer",
    "sk-" + "a" * 32,
    "ghp_" + "0" * 34,
)
"""Every credential shape the pinned persona and campaign tests refuse, and a few more."""

PHONE_NUMBERS = (
    "Reach her at 555.123.4567 after six.",
    "Call 555.123.4567.",
    "Call 01.23.45.67.89 in the evening.",
    "Call 0151/2345678 after work.",
    "Call 030/1234567.",
    "Call 555\N{NO-BREAK SPACE}123\N{NO-BREAK SPACE}4567 after work.",
    "Call 555\N{NARROW NO-BREAK SPACE}123\N{NARROW NO-BREAK SPACE}4567 after work.",
    "Call 555\N{FIGURE SPACE}123\N{FIGURE SPACE}4567 after work.",
    "Call +49\N{NO-BREAK SPACE}151\N{NO-BREAK SPACE}2345678 after work.",
)

NOT_PHONE_NUMBERS = (
    "Born 12.03.1990 in a small fictional town.",
    "The fictional fair opens on 2026.10.25 this year.",
    "Moved in 2015/2016 and stayed.",
    "Started her job in 01/2026.",
    "Her town has 1.234.567 residents.",
    "Her savings: 12.500.000 rials.",
    "She works 24/7 at a fictional bakery.",
    "She can recite pi as 3.14159265.",
    "Her router answers at 192.168.100.10.",
    "Zip 123-4567 in a fictional prefecture.",
)


def _persona(**updates: object) -> WorkbenchPersonaDraftV2:
    return WorkbenchPersonaDraftV2.model_validate_json(json.dumps(persona_data() | updates))


def _campaign(**updates: object) -> WorkbenchCampaignDraft:
    campaign = draft_v2_data()["scenario"]["campaign"]
    return WorkbenchCampaignDraft.model_validate_json(json.dumps(campaign | updates))


def _person_card(**updates: object) -> domain.CityCognitionPersonProfileV1:
    card: dict[str, object] = {
        "schema_version": 1,
        "agent_id": "person-001",
        "name": "Fictional Mina",
        "age": 28,
        "occupation": "Fictional designer",
        "background": "An invented person who enjoys synthetic city scenarios.",
        "personality_type": "INTJ",
    }
    return domain.CityCognitionPersonProfileV1.model_validate(card | updates)


# --- the text screen -------------------------------------------------------------------


@pytest.mark.parametrize("text", LIFE_STORY_PROSE + CAMPAIGN_PROSE + ORDINARY_COPY_NOT_A_VENDOR_KEY)
def test_life_story_and_campaign_prose_is_not_user_credential_text(text: str) -> None:
    assert not person.contains_user_secret_text(text)


@pytest.mark.parametrize("text", LIFE_STORY_PROSE + CAMPAIGN_PROSE)
def test_a_raw_provider_body_keeps_the_broad_screen_that_refused_the_prose(text: str) -> None:
    # Only person-written text moved to the narrow screen; a provider body did not.
    assert person.contains_provider_secret_text(text)


@pytest.mark.parametrize("text", CREDENTIAL_TEXT + REALISTIC_VENDOR_KEYS_STILL_REFUSED)
def test_every_credential_shape_is_still_user_credential_text(text: str) -> None:
    assert person.contains_user_secret_text(text)


@pytest.mark.parametrize(
    "text",
    LIFE_STORY_PROSE + CAMPAIGN_PROSE + CREDENTIAL_TEXT + REALISTIC_VENDOR_KEYS_STILL_REFUSED,
)
def test_the_user_text_screen_never_refuses_what_the_raw_body_screen_admits(text: str) -> None:
    # Stored inputs and saved runs were screened with the broad screen, so every one of
    # them still passes the narrower screen.
    assert not person.contains_user_secret_text(text) or person.contains_provider_secret_text(text)


@pytest.mark.parametrize(
    ("text", "credential"),
    [
        ("Basic dXNlcjpwYXNzd29yZA==", True),
        ("Bearer abcdefgh12345678", True),
        ("bearer abc/defgh+ijk", True),
        ("Basic QWxhZGRpbjpvcGVu", True),
        ("Basic Essentials Box", False),
        ("a basic smartphone", False),
        ("basic well-being routines", False),
        ("Bearer bonds and basic necessities", False),
    ],
)
def test_basic_and_bearer_count_only_before_a_credential_shaped_token(
    text: str, credential: bool
) -> None:
    assert person.contains_user_secret_text(text) is credential


@pytest.mark.parametrize(
    "text",
    [
        "Her secret dream: to open a bakery by the sea.",
        "Cookie Crunch: 2 for 1",
        "Has a password manager she never uses: too fiddly.",
    ],
)
def test_a_label_counts_only_when_an_assignment_follows_it_at_once(text: str) -> None:
    assert not person.contains_user_secret_text(text)
    squeezed = text.replace(" dream:", ":").replace(" Crunch:", ":").replace(" manager", ":")
    assert person.contains_user_secret_text(squeezed), squeezed


# --- the structural walk ---------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        ["cookies", "music"],
        ["music", "cookies"],
        ["tea", "baking", "cookies"],
        ["cookie", "session=opaque-private-marker"],
        {"interests": ["set-cookie", "authorization", "music"]},
        ["api_key"],
    ],
)
def test_transport_header_words_are_ordinary_string_values_in_a_user_draft(
    document: object,
) -> None:
    assert not person.contains_user_labelled_secret_member(document)


@pytest.mark.parametrize(
    "document",
    [
        {"api_key": "opaque-private-marker"},
        {"Cookie": "session=opaque-private-marker"},
        {"nested": [{"Authorization": "opaque-private-marker"}]},
        {"traits": {"x-api-key": {"nested": "opaque-private-marker"}}},
        ["api_key", "opaque-private-marker"],
        ["x-api-key", "opaque-private-marker"],
        {"interests": ["music", "password", "opaque-private-marker"]},
    ],
)
def test_user_draft_keys_keep_the_whole_label_table_and_values_the_credential_labels(
    document: object,
) -> None:
    assert person.contains_user_labelled_secret_member(document)


def test_the_draft_json_boundary_admits_cookie_interests_but_not_a_header_key() -> None:
    interests = json.dumps({"population": {"personas": [{"interests": ["cookies", "music"]}]}})
    assert strict_json_object(interests.encode(), user_draft=True)
    with pytest.raises(WorkbenchJsonContentError):
        strict_json_object(interests.encode())
    header = json.dumps({"nested": [{"Authorization": "opaque-private-marker"}]}).encode()
    with pytest.raises(WorkbenchJsonContentError):
        strict_json_object(header, user_draft=True)


# --- the draft models ------------------------------------------------------------------


@pytest.mark.parametrize("sentence", LIFE_STORY_PROSE)
def test_a_persona_background_may_be_an_ordinary_life_story(sentence: str) -> None:
    assert _persona(background=sentence).background == sentence


@pytest.mark.parametrize("field", ["display_name", "occupation"])
@pytest.mark.parametrize("text", CAMPAIGN_PROSE)
def test_short_persona_fields_accept_basic_and_cookie_prose(field: str, text: str) -> None:
    assert getattr(_persona(**{field: text}), field) == text


@pytest.mark.parametrize("interests", [["cookies", "music"], ["music", "cookies"]])
def test_a_persona_may_like_cookies(interests: list[str]) -> None:
    assert _persona(interests=interests).interests == frozenset({"cookies", "music"})


@pytest.mark.parametrize("name", CAMPAIGN_PROSE)
def test_a_campaign_and_its_scenario_may_have_ordinary_names(name: str) -> None:
    assert _campaign(name=name).name == name
    data = draft_v2_data()
    data["scenario"]["name"] = name
    assert WorkbenchRunDraftV2.model_validate_json(json.dumps(data)).scenario.name == name


def test_a_campaign_may_target_cookie_lovers() -> None:
    campaign = _campaign(target_interests=["tea", "baking", "cookies"])
    assert campaign.target_interests == frozenset({"tea", "baking", "cookies"})


@pytest.mark.parametrize("field", ["display_name", "occupation", "background", "interests"])
@pytest.mark.parametrize("text", CREDENTIAL_TEXT)
def test_every_persona_string_still_refuses_a_credential(field: str, text: str) -> None:
    with pytest.raises(ValidationError):
        _persona(**{field: [text] if field == "interests" else text})


@pytest.mark.parametrize("field", ["name", "target_interests"])
@pytest.mark.parametrize("text", CREDENTIAL_TEXT)
def test_campaign_text_still_refuses_a_credential(field: str, text: str) -> None:
    with pytest.raises(ValidationError):
        _campaign(**{field: [text] if field == "target_interests" else text})


# --- the v3 persona card ---------------------------------------------------------------


@pytest.mark.parametrize("sentence", LIFE_STORY_PROSE)
def test_the_v3_persona_card_accepts_the_same_life_story(sentence: str) -> None:
    card = _person_card(background=sentence, occupation="Basic training instructor")
    assert card.background == sentence


def test_the_v3_person_may_like_cookies_and_its_ad_may_target_them() -> None:
    traits = SpatialResponseTraits(**persona_data()["traits"])
    person_v1 = domain.CityCognitionPersonV1(
        name="Fictional Mina",
        age=28,
        occupation="Basic training instructor",
        background=" ".join(LIFE_STORY_PROSE),
        personality_type="INTJ",
        hobbies_and_interests=("music", "cookies"),
        temperament=domain.city_cognition_persona_temperament_v1(traits),
    )
    assert person_v1.hobbies_and_interests == ("cookies", "music")
    creative = CityCognitionCreativeSnapshotV1(
        template_id="fictional-bakery-v1",
        product_name="Cookie Crunch",
        product_category="Basic groceries",
        message="Basic Essentials Box: fresh cookies every Sunday.",
        call_to_action="Cookie Crunch: 2 for 1",
    )
    campaign = domain.CityCognitionCausalCampaignV2(
        schema_version=2,
        campaign_id="fictional-launch",
        creative_sha256=creative.fingerprint,
        creative=creative,
        target_interests=("tea", "baking", "cookies"),
        relative_price=1.25,
    )
    assert campaign.target_interests == ("baking", "cookies", "tea")


@pytest.mark.parametrize("text", CREDENTIAL_TEXT)
def test_the_v3_persona_card_still_refuses_a_credential(text: str) -> None:
    with pytest.raises(ValidationError):
        _person_card(background=text)


# --- the phone screen: new drafts meet the newer shapes, saved runs still open ----------


@pytest.mark.parametrize("text", PHONE_NUMBERS)
def test_dotted_slash_and_no_break_space_numbers_are_refused_in_a_new_draft(text: str) -> None:
    assert person.contains_written_identifier_text(text)
    document = _rules_draft("phone-background")
    document["settings"]["seed"] = (
        42  # the HTTP transport turns the browser's string seed into this
    )
    document["population"]["personas"][0]["background"] = text
    with pytest.raises(WorkbenchValidationError) as refused:
        construct_workbench_run(
            WorkbenchRunDraftV4.model_validate_json(json.dumps(document)),
            causal_contract=domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
        )
    assert refused.value.code == "invalid-persona-text"
    assert refused.value.field.endswith(".background")
    assert text not in refused.value.safe_message


@pytest.mark.parametrize("text", PHONE_NUMBERS)
def test_a_saved_input_holding_a_newer_number_shape_still_opens(text: str) -> None:
    # Stored inputs re-run contains_sensitive_text whenever a saved run loads, so its
    # patterns stay those every saved run was accepted under.
    assert not person.contains_sensitive_text(text)
    assert _persona(background=text).background == text
    assert _person_card(background=text).background == text


@pytest.mark.parametrize("text", NOT_PHONE_NUMBERS)
def test_dates_amounts_versions_and_short_codes_are_not_phone_numbers(text: str) -> None:
    assert not person.contains_written_identifier_text(text)
    assert not person.contains_sensitive_text(text)
    assert _persona(background=text).background == text
    assert _person_card(background=text).background == text


@pytest.mark.parametrize(
    "separator", [" ", "\N{NO-BREAK SPACE}", "\N{FIGURE SPACE}", "\N{NARROW NO-BREAK SPACE}"]
)
def test_a_labelled_identifier_with_no_break_spaces_is_refused_in_a_new_draft(
    separator: str,
) -> None:
    assert person.contains_written_identifier_text(f"SSN: 12{separator}3456")
    # A plain space was always refused, saved runs included.
    assert person.contains_sensitive_text("SSN: 12 3456")


# --- through the HTTP API and the job's canonical re-read ------------------------------


def _offline_limit() -> int:
    current = domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT.selection_policy_version
    return domain.CITY_COGNITION_SELECTION_LIMITS[current].offline_funded_response_limit


def _rules_draft(run_id: str) -> dict[str, Any]:
    """A small schema-4 No-AI draft; every new run freezes the current V4 contract."""
    document = default_study_draft(
        run_id=run_id, mode="rules", funded_response_limit=_offline_limit()
    )
    for item in document["population"]["personas"]:
        item["count"] = 1
    document["settings"].update({"agent_count": 2, "seed": "42"})
    return document


def _prose_draft(run_id: str) -> dict[str, Any]:
    document = _rules_draft(run_id)
    persona = document["population"]["personas"][0]
    persona["background"] = " ".join(LIFE_STORY_PROSE)
    persona["occupation"] = "Basic training instructor"
    persona["interests"] = ["cookies", "music"]
    document["scenario"]["name"] = "Basic smartphone launch study"
    document["scenario"]["campaign"]["name"] = "Basic Essentials Box"
    document["scenario"]["campaign"]["target_interests"] = ["tea", "baking", "cookies"]
    return document


@pytest.mark.parametrize("sentence", LIFE_STORY_PROSE)
def test_validate_accepts_each_life_story_sentence(tmp_path: Path, sentence: str) -> None:
    client, _root = web._client(tmp_path)
    payload = _rules_draft("prose-sentence")
    payload["population"]["personas"][0]["background"] = sentence
    with client:
        response = web._secure_post(client, "/api/scenarios/validate", payload)
    assert response.status_code == 200, response.json()


def test_the_job_re_reads_the_prose_draft_it_accepted() -> None:
    document = _prose_draft("prose-reparse")
    document["settings"]["seed"] = 42
    validated = construct_workbench_run(
        WorkbenchRunDraftV4.model_validate_json(json.dumps(document)),
        causal_contract=domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    )
    reread = parse_workbench_run_input_json(canonical_json(validated.workbench_input))
    assert reread == validated.workbench_input
    campaign = reread.draft.scenario.campaign
    assert campaign.target_interests == frozenset({"tea", "baking", "cookies"})


def test_a_prose_persona_who_likes_cookies_runs_to_completion(tmp_path: Path) -> None:
    client, _root = web._client(tmp_path)
    payload = _prose_draft("prose-job")
    with client:
        validated = web._secure_post(client, "/api/scenarios/validate", payload)
        assert validated.status_code == 200, validated.json()
        submitted = web._secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202, submitted.json()
        terminal = web._await_job(client, submitted.json()["status_url"], timeout=180.0)
    # Planning builds the v3 persona card and re-reads the canonical input.
    assert terminal["phase"] == "completed", terminal.get("error")


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer secret-token-1234567890",
        "Cookie: session=0000abcdef1234567890",
        "Basic dXNlcjpwYXNzd29yZA==",
        "x-api-key: " + "0" * 20,
    ],
)
def test_validate_still_refuses_credential_background_without_echo(
    tmp_path: Path, text: str
) -> None:
    client, root = web._client(tmp_path)
    payload = _rules_draft("credential-background")
    payload["population"]["personas"][0]["background"] = text
    before = web._snapshot(root)
    with client:
        response = web._secure_post(client, "/api/scenarios/validate", payload)
        assert client.get("/api/workbench").json()["active_job"] is None
    assert response.status_code == 422
    assert text not in response.text
    assert text.split()[-1] not in response.text
    assert web._snapshot(root) == before


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize("interests", [["travel", "secrets"], ["secrets", "travel"]])
def test_a_label_target_interest_is_refused_up_front_in_either_order(
    tmp_path: Path, path: str, interests: list[str]
) -> None:
    # The job re-reads the canonical, sorted input, so the order a client sends must not
    # decide whether a draft is accepted and then fails as an evaluation error.
    client, root = web._client(tmp_path)
    payload = _rules_draft("label-target")
    payload["scenario"]["campaign"]["target_interests"] = interests
    before = web._snapshot(root)
    with client:
        response = web._secure_post(client, path, payload)
        assert client.get("/api/workbench").json()["active_job"] is None
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == {
        "request": "Request data cannot contain credential fields."
    }
    assert web._snapshot(root) == before
