"""Inner thoughts end to end: a setup that asks for them freezes the thoughts contract,
and the saved run keeps every thought through load, replay, the story and the report.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from adlife.city import runs
from adlife.city.run_story import build_run_story, story_json_bytes
from adlife.city.workbench_input import WorkbenchRunDraftV4
from adlife.city.workbench_validation import ValidatedWorkbenchRun, construct_workbench_run
from adlife.core.domain import city_cognition_v2 as domain
from adlife.reporting.run_report import render_run_report_html
from adlife.reporting.run_story_model import RunStoryV1
from tests.unit.city.run_story_fixtures import (
    THOUGHTS_PLAN,
    default_study_draft,
    saved_story_run,
    story_provider_revision,
)


def _draft(root: Path, *, languages: tuple[str, ...], mode: str = "remote") -> dict[str, Any]:
    provider = mode in {"local", "remote"}
    document = default_study_draft(
        run_id="thoughts",
        mode=mode,
        profile=story_provider_revision(root) if provider else None,
        funded_response_limit=60 if provider else 1000,
    )
    document["cognition"]["thought_languages"] = list(languages)
    return document


def _construct(document: dict[str, Any]) -> ValidatedWorkbenchRun:
    # The server freezes the current contract for every new run.
    return construct_workbench_run(
        WorkbenchRunDraftV4.model_validate_json(json.dumps(document)),
        causal_contract=domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    )


@pytest.mark.parametrize("languages", [("en",), ("fa",), ("de", "fa"), ("en", "de", "fa")])
def test_any_language_choice_freezes_the_thoughts_contract(
    tmp_path: Path, languages: tuple[str, ...]
) -> None:
    cognition = _construct(_draft(tmp_path, languages=languages)).workbench_input.draft.cognition

    assert cognition.causal_contract is not None
    assert (
        cognition.causal_contract.fingerprint
        == domain.CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT.fingerprint
    )
    assert cognition.thought_languages == languages
    # Every answer may now carry up to three thoughts, so it gets a fixed, larger budget.
    assert cognition.max_output_tokens_per_attempt == 640
    assert cognition.reservation_tokens_per_request == 6 * 640


@pytest.mark.parametrize("absent", [True, False])
def test_no_language_keeps_the_current_contract(tmp_path: Path, absent: bool) -> None:
    document = _draft(tmp_path, languages=())
    if absent:
        del document["cognition"]["thought_languages"]
    cognition = _construct(document).workbench_input.draft.cognition

    assert cognition.causal_contract is not None
    assert (
        cognition.causal_contract.fingerprint
        == domain.CURRENT_CITY_COGNITION_CAUSAL_CONTRACT.fingerprint
    )
    assert cognition.thought_languages == ()
    assert "thought_languages" not in cognition.model_dump(mode="json")


@pytest.mark.parametrize("mode", ["mock", "rules"])
def test_practice_and_rules_runs_cannot_ask_for_thoughts(tmp_path: Path, mode: str) -> None:
    with pytest.raises(ValueError):
        _construct(_draft(tmp_path, languages=("en",), mode=mode))


@pytest.mark.parametrize("languages", [("fa", "en"), ("en", "en"), ("es",)])
def test_thought_languages_must_be_known_distinct_and_in_order(
    tmp_path: Path, languages: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        _construct(_draft(tmp_path, languages=languages))


def test_a_saved_thoughts_run_keeps_every_thought_through_replay_story_and_report(
    tmp_path: Path,
) -> None:
    stored = saved_story_run(tmp_path, "thoughts-provider")
    records = stored.cognition_evaluation.records
    answered = [record for record in records if record.source in {"provider", "cache"}]
    plan = {row["en"]: row for row in THOUGHTS_PLAN}

    assert answered
    for record in answered:
        assert record.thoughts is not None
        assert record.thoughts.languages == ("en", "de", "fa")
        assert record.thoughts.model_dump(mode="json") == plan[record.thoughts.en]
    assert all(record.thoughts is None for record in records if record not in answered)
    # Replay recomputes every minute from the saved records, thoughts included.
    assert runs.replay_city_run(stored).identical is True

    story = build_run_story(stored)
    assert story.ai.thought_languages == ("en", "de", "fa")
    assert story.ai.contract.prompt_contract_id == domain.CITY_COGNITION_PROMPT_CONTRACT_V4_ID
    assert story.ai.contract.result_schema_id == domain.CITY_COGNITION_RESULT_SCHEMA_V3_ID
    moments = [moment for person in story.people for moment in person.moments]
    with_thoughts = [moment for moment in moments if moment.thoughts is not None]
    assert with_thoughts
    assert {moment.status for moment in with_thoughts} <= {"answered", "reused"}
    assert all(moment.thoughts is not None for moment in moments if moment.status == "answered")
    # The moment line still says the AI answered as the person.
    assert {moment.ai_clause.key for moment in with_thoughts if moment.ai_clause} == {
        "story.ai.answered_as"
    }
    document = json.loads(story_json_bytes(story))
    first = next(
        moment
        for person in document["people"]
        for moment in person["moments"]
        if "thoughts" in moment
    )
    assert first["thoughts"] == plan[first["thoughts"]["en"]]

    report = render_run_report_html(story, language="fa", links=None).content.decode("utf-8")
    shown = with_thoughts[0].thoughts
    assert shown is not None and shown.fa is not None and shown.de is not None
    assert shown.fa in report and shown.de in report
    assert '<blockquote lang="fa" dir="rtl">' in report
    assert '<blockquote lang="en" dir="ltr">' in report


def test_the_story_refuses_thoughts_it_cannot_account_for(tmp_path: Path) -> None:
    document = json.loads(
        story_json_bytes(build_run_story(saved_story_run(tmp_path, "thoughts-provider")))
    )
    RunStoryV1.model_validate_json(json.dumps(document))

    # A language the run never asked for.
    narrowed = copy.deepcopy(document)
    narrowed["ai"]["thought_languages"] = ["en"]
    # A thoughts run that names no language.
    unnamed = copy.deepcopy(document)
    del unnamed["ai"]["thought_languages"]
    # Thoughts on a moment with no usable AI answer.
    misplaced = copy.deepcopy(document)
    moment = next(
        moment
        for person in misplaced["people"]
        for moment in person["moments"]
        if moment["status"] not in {"answered", "reused"}
    )
    moment["thoughts"] = {"en": "Nothing to see here."}
    for broken in (narrowed, unnamed, misplaced):
        with pytest.raises(ValueError):
            RunStoryV1.model_validate_json(json.dumps(broken))


def test_a_run_without_thoughts_keeps_its_story_free_of_them(tmp_path: Path) -> None:
    stored = saved_story_run(tmp_path, "persona-provider")
    text = story_json_bytes(build_run_story(stored)).decode("utf-8")

    assert all(record.thoughts is None for record in stored.cognition_evaluation.records)
    assert '"thoughts"' not in text
    assert '"thought_languages"' not in text
