"""Named saved runs for story, guide and report tests, built by the real pure pipeline.

``saved_story_run(workspace_root, name)`` publishes one verified run into a workbench
workspace and returns it as loaded back through ``CityRunStore``. No service, HTTP job,
provider, cache, network or storage shim is involved: terminal cognition records are
built with the production record builders, then validated, finalized and published.

Names:

* ``rules``, ``mock``, ``provider`` (local, eight people, so two requests go over the
  limit of 10), ``cache`` (remote), ``fallback`` (local), ``empty`` (nobody is shown the
  ad) - the shapes of ``tests/integration/test_city_cognition_v2_artifacts.py``;
* ``mixed`` - every applied status (answered, reused, unsure, warning, failed, over the
  limit, not chosen, not noticed), with hostile scenario, campaign and persona names;
* ``dense`` - more than 40 ad moments per person on a phone feed and a billboard;
* ``default-mock`` / ``default-provider`` - the guided default study (spec Appendix C):
  Lumo x4 and Veyra x4, Lumen Pocket, phone 07:00-21:00 at 0.01, cap 4, price 1.25;
  the provider variant uses an ``.invalid`` remote profile with model thinking turned
  off and an AI limit of 10;
* ``hostile`` - hostile persona name and background text on a mock run;
* ``v9`` - a schema-v9 run, which has no story.

Hostile strings use escapes, never Persian literals, as the CI rules require.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from adlife.city import runs
from adlife.city.provider_profiles import (
    ProviderProfileDraftV1,
    ProviderProfileRegistry,
    ProviderProfileRevisionV1,
)
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.workbench_input import WorkbenchRunDraftV4
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_validation import ValidatedWorkbenchRun, construct_workbench_run
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.simulation.city_cognition_v2 import (
    build_city_budget_skipped_result_v2,
    build_city_cognition_terminal_record_v2,
    build_city_fallback_result_v2,
    build_city_mock_result_v2,
    build_city_rules_result_v2,
    validate_city_cognition_evaluation_v2,
)
from tests.integration.test_city_cognition_v2_artifacts import _save_prepared, _saved_v10
from tests.unit.city.test_city_run_preparation import _v2_arguments, _validated_v4

StoryRunName = Literal[
    "rules",
    "mock",
    "provider",
    "cache",
    "fallback",
    "empty",
    "mixed",
    "dense",
    "default-mock",
    "default-provider",
    "hostile",
    "v9",
]
STORY_RUN_NAMES: tuple[StoryRunName, ...] = (
    "rules",
    "mock",
    "provider",
    "cache",
    "fallback",
    "empty",
    "mixed",
    "dense",
    "default-mock",
    "default-provider",
    "hostile",
    "v9",
)

# "Mina" in Persian script plus markup that would set window.pwned if it ever ran.
HOSTILE_PERSONA_NAME = "\u0645\u06cc\u0646\u0627 <img src=x onerror=window.pwned=1>"
HOSTILE_BACKGROUND = "</script><script>window.pwned=2</script> \u0632\u0646\u062f\u06af\u06cc"
HOSTILE_SCENARIO = "\u067e\u0648\u06cc\u0634 </bdi><script>window.pwned=3</script>"
HOSTILE_CAMPAIGN = "\u06a9\u0645\u067e\u06cc\u0646 <svg onload=window.pwned=4>"
DEFAULT_PROVIDER_MODEL = "gapgpt-qwen-3.6"
DEFAULT_PROVIDER_BASE_URL = "https://api.gapgpt.invalid/v1"

_Outcome = tuple[str, Any, int, str | None, dict[str, int]]
_Plan = Callable[[int, Any, Any], _Outcome]

# Mirrors the inspector's mixed plan: one entry per funded request, cycled.
_MIXED_PLAN: tuple[tuple[str, dict[str, str] | None, tuple[str, ...]], ...] = (
    (
        "provider",
        {"affect": "positive", "relevance": "high", "credibility": "high", "salience": "high"},
        (),
    ),
    (
        "provider",
        {"affect": "curious", "relevance": "uncertain", "credibility": "medium", "salience": "low"},
        (),
    ),
    (
        "provider",
        {"affect": "annoyed", "relevance": "low", "credibility": "low", "salience": "none"},
        ("stereotype-risk",),
    ),
    (
        "provider",
        {"affect": "uncertain", "relevance": "high", "credibility": "high", "salience": "medium"},
        ("unsupported-inference",),
    ),
    (
        "cache",
        {"affect": "skeptical", "relevance": "medium", "credibility": "high", "salience": "high"},
        (),
    ),
    ("fallback", None, ()),
)
# The guided default study's fake provider answers: varied, all usable.
_DEFAULT_PROVIDER_PLAN: tuple[dict[str, str], ...] = (
    {"affect": "skeptical", "relevance": "none", "credibility": "low", "salience": "low"},
    {"affect": "curious", "relevance": "medium", "credibility": "medium", "salience": "high"},
    {"affect": "annoyed", "relevance": "low", "credibility": "low", "salience": "medium"},
    {"affect": "positive", "relevance": "high", "credibility": "high", "salience": "high"},
    {"affect": "neutral", "relevance": "medium", "credibility": "medium", "salience": "medium"},
)


def _labelled(request: Any, vector: dict[str, str], flags: tuple[str, ...] = ()) -> Any:
    return build_city_rules_result_v2(request).model_copy(update={**vector, "safety_flags": flags})


def _publish(
    root: Path,
    validated: ValidatedWorkbenchRun,
    *,
    profile: ProviderProfileRevisionV1 | None,
    plan: _Plan,
) -> StoredCityRun:
    """Resolve each funded request with ``plan`` and publish the finalized run."""
    staged = runs.prepare_city_scientific_run_v2(
        validated.pack, provider_profile=profile, **_v2_arguments(validated)
    )
    frozen = staged.cognition_input
    records = []
    for index, request in enumerate(staged.cognition_requests):
        if not frozen.reservations[index].funded:
            source, result, attempts, reason, usage = (
                "budget-skipped",
                build_city_budget_skipped_result_v2(request),
                0,
                "budget-skipped",
                {},
            )
        else:
            source, result, attempts, reason, usage = plan(index, request, frozen)
        records.append(
            build_city_cognition_terminal_record_v2(
                request,
                frozen,
                selection_index=index,
                source=source,
                result=result,
                attempt_count=attempts,
                reason=reason,
                **usage,
            )
        )
    evaluation = validate_city_cognition_evaluation_v2(
        frozen,
        staged.cognition_requests,
        tuple(records),
        baseline_evaluation=staged.baseline_evaluation,
    )
    prepared = runs.finalize_city_run_v2(
        staged, cognition_input=frozen, cognition_evaluation=evaluation
    )
    _save_prepared(root, prepared)
    return CityRunStore(root).load(prepared.manifest.run_id)


def _mock_plan(_index: int, request: Any, frozen: Any) -> _Outcome:
    return (
        "mock",
        build_city_mock_result_v2(request, policy=frozen.causal_contract.mock_policy),
        0,
        None,
        {},
    )


def _mixed_plan(index: int, request: Any, _frozen: Any) -> _Outcome:
    source, vector, flags = _MIXED_PLAN[index % len(_MIXED_PLAN)]
    if vector is None:
        return (
            "fallback",
            build_city_fallback_result_v2(request, reason="timeout-exhausted"),
            2,
            "timeout-exhausted",
            {},
        )
    attempts = 1 if source == "provider" else 0
    usage = {"prompt_tokens": 900, "completion_tokens": 40}
    return source, _labelled(request, vector, flags), attempts, None, usage


def _default_provider_plan(index: int, request: Any, _frozen: Any) -> _Outcome:
    vector = _DEFAULT_PROVIDER_PLAN[index % len(_DEFAULT_PROVIDER_PLAN)]
    usage = {"prompt_tokens": 1_400, "completion_tokens": 130}
    return "provider", _labelled(request, vector), 1, None, usage


def story_provider_revision(
    root: Path, *, model_id: str = DEFAULT_PROVIDER_MODEL, disable_thinking: bool = True
) -> ProviderProfileRevisionV1:
    """A remote guided-style profile on an ``.invalid`` origin; nothing is ever contacted."""
    registry = ProviderProfileRegistry(
        prepare_workbench_workspace(root / "story-provider-registry")
    )
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "guide-gapgpt",
        "display_label": "Guided demo - gapgpt",
        "mode": "remote",
        "base_url": DEFAULT_PROVIDER_BASE_URL,
        "model_id": model_id,
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 256,
        "attempt_timeout_seconds": 30,
        "transient_retries": 1,
        "repair_attempts": 1,
        "origin_approval_id": "gapgpt",
    }
    if disable_thinking:
        document["disable_thinking"] = True
    return registry.create(ProviderProfileDraftV1.model_validate(document))


def default_study_draft(
    *,
    run_id: str,
    mode: str = "mock",
    profile: ProviderProfileRevisionV1 | None = None,
    funded_response_limit: int | None = None,
) -> dict[str, Any]:
    """The exact guided setup draft of spec 2.4 for the default study."""
    templates = {
        item["persona_id"]: item
        for item in load_persona_template_catalog().model_dump(mode="json")["templates"]
    }
    provider = mode in {"local", "remote"}
    limit = funded_response_limit or (10 if provider else 60)
    return {
        "schema_version": 4,
        "city_id": "fictional-grid-v2",
        "scenario": {
            "scenario_id": "guided-device",
            "name": "Guided demo study - Lumen Pocket",
            "campaign": {
                "campaign_id": "fictional-launch",
                "name": "Lumen Pocket campaign",
                "creative_template_id": "fictional-device-launch-v1",
                "target_interests": ["commuting", "technology"],
                "relative_price": 1.25,
            },
            "phone": {
                "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 1260}],
                "frequency_cap_per_agent_per_day": 4,
                "eligible_activities": ["commute", "home", "leisure"],
                "opportunity_probability_per_minute": 0.01,
            },
            "roadside": None,
        },
        "population": {
            "personas": [
                {**templates["persona-001"], "count": 4},
                {**templates["persona-002"], "count": 4},
            ]
        },
        "settings": {
            "run_id": run_id,
            "agent_count": 8,
            "days": 1,
            "seed": 42,
            "response_mode": "deterministic-rules",
        },
        "cognition": {
            "effect_scope": "bounded-signals-v1",
            "mode": mode,
            "provider_profile_id": None if profile is None else profile.profile_id,
            "provider_revision_sha256": None if profile is None else profile.revision_sha256,
            "funded_response_limit": limit,
        },
    }


def _validated_draft(document: dict[str, Any]) -> ValidatedWorkbenchRun:
    return construct_workbench_run(WorkbenchRunDraftV4.model_validate_json(json.dumps(document)))


def _hostile_names(document: dict[str, Any]) -> None:
    document["scenario"]["name"] = HOSTILE_SCENARIO
    document["scenario"]["campaign"]["name"] = HOSTILE_CAMPAIGN
    document["population"]["personas"][0]["display_name"] = HOSTILE_PERSONA_NAME


def _busy_phone(document: dict[str, Any]) -> None:
    document["scenario"]["phone"].update(
        opportunity_probability_per_minute=0.5,
        frequency_cap_per_agent_per_day=10,
        eligible_activities=["home", "commute", "work", "leisure"],
    )


def _dense(document: dict[str, Any]) -> None:
    document["settings"]["days"] = 2
    document["scenario"]["phone"].update(
        opportunity_probability_per_minute=1.0,
        frequency_cap_per_agent_per_day=30,
        eligible_activities=["home", "commute", "work", "leisure"],
    )
    document["scenario"]["roadside"] = {
        "active_windows": [
            {"day": 1, "start_minute": 420, "end_minute": 1140},
            {"day": 2, "start_minute": 420, "end_minute": 1140},
        ],
        "frequency_cap_per_agent_per_day": 2,
        "road_id": "middle-west",
        "travel_direction": "forward",
        "road_fraction": 0.5,
        "side": "right",
        "orientation_degrees": 90.0,
        "max_view_distance_meters": 120.0,
    }


def _hostile_persona(document: dict[str, Any]) -> None:
    persona = document["population"]["personas"][0]
    persona["display_name"] = HOSTILE_PERSONA_NAME
    persona["background"] = HOSTILE_BACKGROUND


def saved_story_run(
    workspace_root: Path, name: StoryRunName, *, run_id: str | None = None
) -> StoredCityRun:
    """Publish one named fixture run into the workbench workspace at ``workspace_root``."""
    root = prepare_workbench_workspace(workspace_root).root
    identifier = run_id or f"story-{name}"

    def renamed(mutation: Callable[[dict[str, Any]], None] | None = None) -> Any:
        def mutate(document: dict[str, Any]) -> None:
            document["settings"]["run_id"] = identifier
            if mutation is not None:
                mutation(document)

        return mutate

    simple: dict[str, dict[str, Any]] = {
        "rules": {"mode": "rules"},
        "mock": {"mode": "mock"},
        "provider": {"mode": "local", "source": "provider", "agents": 8},
        "cache": {"mode": "remote", "source": "cache"},
        "fallback": {"mode": "local", "source": "fallback"},
        "empty": {"mode": "mock", "empty": True},
        "hostile": {"mode": "mock", "mutation": _hostile_persona},
    }
    if name in simple:
        _saved_v10(root, run_id=identifier, **simple[name])
        return CityRunStore(root).load(identifier)
    if name == "mixed":
        from tests.unit.city.test_city_run_preparation import _provider_revision

        profile = _provider_revision(root, mode="local")

        def mixed(document: dict[str, Any]) -> None:
            document["settings"].update(run_id=identifier, days=2)
            _busy_phone(document)
            _hostile_names(document)

        validated = _validated_v4(mode="local", limit=10, profile=profile, agents=3, mutation=mixed)
        return _publish(root, validated, profile=profile, plan=_mixed_plan)
    if name == "dense":
        validated = _validated_v4(mode="mock", agents=2, mutation=renamed(_dense))
        return _publish(root, validated, profile=None, plan=_mock_plan)
    if name == "default-mock":
        validated = _validated_draft(default_study_draft(run_id=identifier))
        return _publish(root, validated, profile=None, plan=_mock_plan)
    if name == "default-provider":
        profile = story_provider_revision(root)
        validated = _validated_draft(
            default_study_draft(run_id=identifier, mode="remote", profile=profile)
        )
        return _publish(root, validated, profile=profile, plan=_default_provider_plan)
    if name == "v9":
        from tests.integration.test_city_run_replay import _create_v9_run

        stored: StoredCityRun = _create_v9_run(root, run_id=identifier)
        return stored
    raise ValueError(f"unknown story fixture {name!r}")


__all__ = [
    "DEFAULT_PROVIDER_BASE_URL",
    "DEFAULT_PROVIDER_MODEL",
    "HOSTILE_BACKGROUND",
    "HOSTILE_CAMPAIGN",
    "HOSTILE_PERSONA_NAME",
    "HOSTILE_SCENARIO",
    "STORY_RUN_NAMES",
    "StoryRunName",
    "default_study_draft",
    "saved_story_run",
    "story_provider_revision",
]
