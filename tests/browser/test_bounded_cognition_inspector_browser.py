"""Browser contract for the saved-run manifest-V10 bounded cognition inspector.

Every test drives a real server: the workbench inspector at ``/runs/{run_id}`` and, where
both surfaces matter, the standalone ``adlife city-view`` application at ``/``. Each run
is written by the production pipeline into a real workbench workspace and loaded back
through ``CityRunStore``. ``V10Projections`` is an independent oracle of the exact
documents the inspector consumes; tampering is injected only in the browser, after the
real server has answered.
"""

from __future__ import annotations

import copy
import json
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi import FastAPI
from playwright.sync_api import Browser, Page, Route, expect, sync_playwright

from adlife.city import runs
from adlife.city.analysis import metrics_for_stored_city_run, response_metrics_for_stored_city_run
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.web import create_city_app
from adlife.city.workbench_cognition_views import (
    build_workbench_cognition_records_view,
    build_workbench_cognition_summary_view,
    build_workbench_run_input_view_v4,
)
from adlife.city.workbench_transport import WorkbenchRunInputViewV4, WorkbenchRunSettingsView
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
from adlife.core.simulation.city_cognition_v2 import (
    build_city_budget_skipped_result_v2,
    build_city_cognition_terminal_record_v2,
    build_city_fallback_result_v2,
    build_city_rules_result_v2,
    validate_city_cognition_evaluation_v2,
)
from adlife.core.simulation.spatial_response_v2 import (
    spatial_response_state_document_v2,
    summarize_spatial_response_artifact_v2,
)
from tests.browser.test_city_places_browser import _artifact_hashes, _installed_browser, _serve
from tests.integration.test_city_cognition_v2_artifacts import _save_prepared, _saved_v10
from tests.unit.city.test_city_run_preparation import (
    _provider_revision,
    _v2_arguments,
    _validated_v4,
)

STATIC = Path(str(resources.files("adlife.city").joinpath("static")))
HELPER = STATIC / "app-cognition-v2.js"
CSRF_TOKEN = "inspector-browser-csrf-token"
ASSET_TYPES = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app-cognition-v2.js": "text/javascript; charset=utf-8",
    "app-cognition-v2.css": "text/css; charset=utf-8",
}
Server = Literal["workbench", "standalone"]
HOSTILE_SCENARIO = "پویش شناختی </script><img src=x onerror=window.inspectorPwned=1>"
HOSTILE_CAMPAIGN = "کمپین \u200fساختگی <svg onload=window.inspectorPwned=2>"  # noqa: RUF001
HOSTILE_MODEL = "مدل محلی <img src=x onerror=window.inspectorPwned=3>"
STAGES = ["baseline", "signal", "mapping", "applied", "dual"]
STAGE_TITLES = [
    "Deterministic baseline",
    None,
    "city-cognition-influence-v1 mapping",
    "Applied cognition delta",
    "Atomic rule/cognition state update",
]
SIGNAL_TITLES = {
    "provider": "Provider signal",
    "cache": "Cached provider signal",
    "mock": "Deterministic mock signal",
    "rules": "Rules mode",
    "fallback": "Deterministic fallback",
    "budget-skipped": "Budget skipped",
    "unselected": "Not selected",
}
NEUTRAL_STATUS = {
    "rules": "Neutral · rules identity",
    "fallback": "Neutral · fallback (timeout exhausted)",
    "budget-skipped": "Neutral · budget skipped",
    "unselected": "Neutral · not selected",
    "uncertain": "Neutralized · uncertain category",
}
SOURCE_BADGES = {
    "provider": "Provider",
    "cache": "Cache",
    "mock": "Mock",
    "rules": "Rules",
    "fallback": "Fallback",
    "budget-skipped": "Budget skipped",
    "unselected": "Not selected",
}
EFFECT_BADGES = {
    "mapped": "Changes cognition",
    "identity": "Equals baseline",
    "safety": "Neutralized · safety flag",
    "uncertain": "Neutralized · uncertain",
    "neutral": "Neutral · equals baseline",
}
NEUTRAL_VECTOR = ["neutral", "medium", "medium", "medium"]
CHIP_NOTES = {
    "applied": None,
    "set-aside": "Model answer recorded for audit · not applied to cognition",
    "neutral": "Fixed neutral vector · no model answer",
}
MIXED_PLAN: tuple[tuple[str, dict[str, str] | None, tuple[str, ...]], ...] = (
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


def _hostile_names(document: dict[str, Any]) -> None:
    document["scenario"]["name"] = HOSTILE_SCENARIO
    document["scenario"]["campaign"]["name"] = HOSTILE_CAMPAIGN


def _busy_phone(document: dict[str, Any]) -> None:
    document["scenario"]["phone"].update(
        opportunity_probability_per_minute=0.5,
        frequency_cap_per_agent_per_day=10,
        eligible_activities=["home", "commute", "work", "leisure"],
    )


def _mixed_v10(root: Path, *, run_id: str) -> None:
    """Exercise every applied status with the real pure pipeline and no storage shim."""
    root.mkdir(parents=True, exist_ok=True)
    profile = _provider_revision(root, mode="local")

    def mutate(document: dict[str, Any]) -> None:
        document["settings"].update(run_id=run_id, days=2)
        _busy_phone(document)
        _hostile_names(document)

    validated = _validated_v4(mode="local", limit=10, profile=profile, agents=3, mutation=mutate)
    staged = runs.prepare_city_scientific_run_v2(
        validated.pack, provider_profile=profile, **_v2_arguments(validated)
    )
    frozen = staged.cognition_input
    records = []
    for index, request in enumerate(staged.cognition_requests):
        reason = None
        attempts = 0
        usage: dict[str, int] = {}
        if not frozen.reservations[index].funded:
            source, reason = "budget-skipped", "budget-skipped"
            result = build_city_budget_skipped_result_v2(request)
        else:
            source, vector, flags = MIXED_PLAN[index % len(MIXED_PLAN)]
            if vector is None:
                reason, attempts = "timeout-exhausted", 2
                result = build_city_fallback_result_v2(request, reason=reason)
            else:
                attempts = 1 if source == "provider" else 0
                result = build_city_rules_result_v2(request).model_copy(
                    update={**vector, "safety_flags": flags}
                )
                usage = {"prompt_tokens": 900, "completion_tokens": 40}
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


def _build_run(name: str, root: Path, run_id: str) -> None:
    if name == "mixed":
        _mixed_v10(root, run_id=run_id)
        return
    options: dict[str, Any] = {
        "rules": {"mode": "rules"},
        "mock": {"mode": "mock"},
        "provider": {"mode": "local", "source": "provider", "agents": 8},
        "cache": {"mode": "remote", "source": "cache"},
        "fallback": {"mode": "local", "source": "fallback"},
        "empty": {"mode": "mock", "empty": True},
    }[name]
    _saved_v10(root, run_id=run_id, **options)


@dataclass(frozen=True)
class SavedRun:
    root: Path
    directory: Path
    stored: StoredCityRun


def saved_workspace_run(root: Path, name: str, run_id: str) -> SavedRun:
    """Publish one verified V10 run into a real workbench workspace."""
    workspace = prepare_workbench_workspace(root / "workspace").root
    _build_run(name, workspace, run_id)
    stored = CityRunStore(workspace).load(run_id)
    return SavedRun(workspace, workspace / "city-runs" / run_id, stored)


@pytest.fixture(scope="module")
def saved_runs(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], SavedRun]:
    cache: dict[str, SavedRun] = {}

    def saved(name: str) -> SavedRun:
        if name not in cache:
            root = tmp_path_factory.mktemp(f"v10-{name}")
            cache[name] = saved_workspace_run(root, name, f"inspector-{name}")
        return cache[name]

    return saved


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    executable = _installed_browser()
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(
            executable_path=str(executable),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        try:
            yield launched
        finally:
            launched.close()


def _subject(item: Any) -> Any:
    applied = getattr(item, "event_type", None) == "spatial.applied-response"
    return item.baseline if applied else item


def _counts(items: tuple[Any, ...], agents: tuple[str, ...]) -> dict[str, int]:
    return {
        agent: sum(_subject(item).agent_id == agent for item in items)
        for agent in agents
        if any(_subject(item).agent_id == agent for item in items)
    }


def _channels(items: tuple[Any, ...]) -> dict[str, int]:
    return {
        channel: sum(getattr(item, "channel", None) == channel for item in items)
        for channel in ("roadside-billboard", "mobile-feed")
    }


class V10Projections:
    """Read-only C3 projections derived from one verified StoredCityRun."""

    def __init__(self, stored: StoredCityRun, *, metrics: bool) -> None:
        self.stored = stored
        manifest = stored.manifest
        simulation = stored.mobility
        scenario = stored.spatial_scenario
        opportunities = stored.opportunity_evaluation
        attention = stored.attention_evaluation
        response = stored.response_evaluation
        cognition_input = stored.cognition_input
        cognition = stored.cognition_evaluation
        workbench = stored.workbench_input
        assert scenario and opportunities and attention and response and cognition_input
        assert cognition is not None and workbench is not None
        self.agents = tuple(agent.agent_id for agent in simulation.agents)
        self.days = simulation.days
        attention_metrics = derive_spatial_metrics(
            opportunities,
            attention,
            agent_ids=self.agents,
            agents_sha256=manifest.agents_sha256,
            trace_sha256=manifest.trace_sha256,
            days=manifest.days,
            source_run_schema_version=10,
        )
        response_metrics = None
        if metrics:
            from adlife.core.experiments.spatial_response_metrics_v2 import (
                derive_spatial_response_metrics_v2,
            )

            response_metrics = derive_spatial_response_metrics_v2(
                stored.response_input,
                response,
                scenario=scenario,
                opportunities=opportunities,
                attention=attention,
                agent_ids=self.agents,
                attention_metrics=attention_metrics,
                cognition_input=cognition_input,
                cognition=cognition,
            ).model_dump(mode="json")
        profile = cognition_input.provider_profile
        contract = cognition_input.causal_contract
        draft = workbench.draft
        settings = draft.settings
        meta = simulation.metadata()
        meta.update(
            saved=True,
            run_id=manifest.run_id,
            run_schema_version=10,
            workbench_input_available=True,
            workbench_input_sha256=manifest.workbench_input_sha256,
            spatial_opportunities=True,
            claim_scope="synthetic-opportunity-not-impression",
            opportunity_count=opportunities.counts.opportunity_count,
            spatial_attention=True,
            attention_model_id=attention.model_id,
            attention_claim_scope=attention.claim_scope,
            attention_notice_probability=attention.notice_probability,
            impression_count=attention.counts.impression_count,
            noticed_count=attention.counts.noticed_count,
            spatial_metrics=True,
            spatial_response=True,
            response_model_id=response.model_id,
            response_claim_scope=response.claim_scope,
            response_count=response.counts.baseline_response_count,
            state_update_count=response.counts.dual_state_update_count,
            response_campaign_count=response.counts.campaign_count,
            final_state_count=response.counts.final_rule_state_count,
            bounded_cognition=True,
            cognition_effect_scope="bounded-signals-v1",
        )
        if response_metrics is not None:
            meta["spatial_response_metrics"] = True
        self.static: dict[str, Any] = {
            "meta": meta,
            "city": stored.pack.model_dump(mode="json"),
            "agents": [asdict(agent) for agent in simulation.agents],
            "opportunity-summary": {
                "schema_version": 1,
                "scenario_id": scenario.scenario_id,
                "scenario_name": scenario.name,
                "scenario_sha256": scenario.fingerprint,
                "opportunity_model_id": opportunities.model_id,
                "claim_scope": "synthetic-opportunity-not-impression",
                "counts": opportunities.counts.model_dump(mode="json"),
                "campaigns": [item.model_dump(mode="json") for item in scenario.campaigns],
                "placements": [item.model_dump(mode="json") for item in scenario.placements],
            },
            "attention-summary": {
                "schema_version": 1,
                "scenario_id": scenario.scenario_id,
                "scenario_name": scenario.name,
                "scenario_sha256": scenario.fingerprint,
                "attention_model_id": attention.model_id,
                "claim_scope": attention.claim_scope,
                "notice_probability": attention.notice_probability,
                "counts": attention.counts.model_dump(mode="json"),
            },
            "spatial-metrics": attention_metrics.model_dump(mode="json"),
            "response-summary": summarize_spatial_response_artifact_v2(response).model_dump(
                mode="json"
            ),
            "workbench-input": WorkbenchRunInputViewV4.model_validate(
                {
                    "run_id": manifest.run_id,
                    "manifest_sha256": sha256(
                        (canonical_json(manifest) + "\n").encode("utf-8")
                    ).hexdigest(),
                    "workbench_input_sha256": workbench.fingerprint,
                    "city_sha256": stored.pack.fingerprint,
                    "scenario_sha256": scenario.fingerprint,
                    "creative_sha256": workbench.creative_template.fingerprint,
                    "scenario": draft.scenario,
                    "population": draft.population,
                    "settings": WorkbenchRunSettingsView(
                        run_id=settings.run_id,
                        agent_count=settings.agent_count,
                        days=settings.days,
                        seed=str(settings.seed),
                        response_mode=settings.response_mode,
                    ),
                    "cognition": draft.cognition,
                    "creative_template": workbench.creative_template,
                }
            ).model_dump(mode="json"),
            "cognition-summary": {
                "schema_version": 2,
                "run_id": manifest.run_id,
                "run_schema_version": 10,
                "effect_scope": cognition_input.effect_scope,
                "cognition_input_sha256": cognition_input.fingerprint,
                "mode": cognition_input.mode,
                "provider_profile_id": None if profile is None else profile.profile_id,
                "provider_revision_sha256": None if profile is None else profile.revision_sha256,
                "model_id": None if profile is None else profile.model_id,
                "funded_response_limit": cognition_input.funded_response_limit,
                "causal_contract_sha256": cognition_input.causal_contract_sha256,
                "prompt_contract_id": contract.prompt_contract_id,
                "prompt_contract_sha256": contract.prompt_contract_sha256,
                "result_schema_id": contract.result_schema_id,
                "result_schema_contract_sha256": contract.result_schema_contract_sha256,
                "selection_policy_sha256": contract.selection_policy_sha256,
                "fallback_policy_sha256": contract.fallback_policy_sha256,
                "mock_policy_sha256": contract.mock_policy_sha256,
                "influence_policy_id": contract.influence_policy.policy_id,
                "influence_policy_sha256": contract.influence_policy_sha256,
                "summary": cognition.summary.model_dump(mode="json"),
            },
            "cognition-records": {
                "schema_version": 2,
                "run_id": manifest.run_id,
                "run_schema_version": 10,
                "cognition_input_sha256": cognition_input.fingerprint,
                "records": [record.model_dump(mode="json") for record in cognition.records],
            },
        }
        if response_metrics is not None:
            self.static["spatial-response-metrics"] = response_metrics
        self.opportunities = opportunities.opportunities
        self.opportunity_minutes = tuple(item.model_minute for item in self.opportunities)
        self.attention = attention.events
        self.attention_minutes = tuple(item.model_minute for item in self.attention)
        self.response = response
        self.response_minutes = tuple(_subject(item).model_minute for item in response.records)
        self.state_document = spatial_response_state_document_v2(response)

    def _matching(self, items: tuple[Any, ...], minutes: tuple[int, ...], minute: int) -> Any:
        if minute >= self.days * 1440:
            raise ValueError("minute is outside the saved run")
        return items[bisect_left(minutes, minute) : bisect_right(minutes, minute)]

    def _window(self, matching: tuple[Any, ...], offset: int, limit: int) -> dict[str, Any]:
        page = matching[offset : offset + limit]
        consumed = offset + len(page)
        return {
            "offset": offset,
            "limit": limit,
            "total": len(matching),
            "next_offset": consumed if consumed < len(matching) else None,
            "items": [item.model_dump(mode="json") for item in page],
        }

    def opportunity_page(self, minute: int, offset: int, limit: int) -> dict[str, Any]:
        matching = self._matching(self.opportunities, self.opportunity_minutes, minute)
        return {
            "schema_version": 1,
            "claim_scope": "synthetic-opportunity-not-impression",
            "minute": minute,
            "agent_id": None,
            "channel_counts": _channels(matching),
            "agent_counts": _counts(matching, self.agents),
            **self._window(matching, offset, limit),
        }

    def attention_page(self, minute: int, offset: int, limit: int) -> dict[str, Any]:
        matching = self._matching(self.attention, self.attention_minutes, minute)
        return {
            "schema_version": 1,
            "attention_model_id": "spatial-attention-v1",
            "claim_scope": "synthetic-attention-not-observed-behavior",
            "minute": minute,
            "agent_id": None,
            "event_type_counts": {
                kind: sum(item.event_type == kind for item in matching)
                for kind in ("spatial.impression", "spatial.noticed")
            },
            "channel_counts": _channels(matching),
            "agent_counts": _counts(matching, self.agents),
            **self._window(matching, offset, limit),
        }

    def response_page(self, minute: int, offset: int, limit: int) -> dict[str, Any]:
        matching = self._matching(self.response.records, self.response_minutes, minute)
        applied = tuple(
            item.baseline for item in matching if item.event_type == "spatial.applied-response"
        )
        return {
            "schema_version": 2,
            "response_model_id": "spatial-response-v2",
            "claim_scope": self.response.claim_scope,
            "minute": minute,
            "agent_id": None,
            "event_type_counts": {
                kind: sum(item.event_type == kind for item in matching)
                for kind in ("spatial.applied-response", "spatial.dual-state-updated")
            },
            "channel_counts": _channels(applied),
            "agent_counts": _counts(matching, self.agents),
            **self._window(matching, offset, limit),
        }

    def state_page(self, agent_id: str, offset: int, limit: int) -> dict[str, Any]:
        document = self.state_document
        pairs = tuple(
            (rule, cognition)
            for rule, cognition in zip(document.rule.states, document.cognition.states, strict=True)
            if rule.agent_id == agent_id
        )
        page = pairs[offset : offset + limit]
        consumed = offset + len(page)
        return {
            "schema_version": 2,
            "model_id": document.model_id,
            "claim_scope": document.claim_scope,
            "response_input_sha256": document.response_input_sha256,
            "scenario_sha256": document.scenario_sha256,
            "city_sha256": document.city_sha256,
            "cognition_input_sha256": document.cognition_input_sha256,
            "rule_state_model_id": document.rule.model_id,
            "cognition_state_model_id": document.cognition.model_id,
            "state_scope": "final-end-of-run-not-scrubbed-minute",
            "agent_id": agent_id,
            "offset": offset,
            "limit": limit,
            "total": len(pairs),
            "next_offset": consumed if consumed < len(pairs) else None,
            "items": [
                {
                    "rule_state": rule.model_dump(mode="json"),
                    "cognition_state": cognition.model_dump(mode="json"),
                }
                for rule, cognition in page
            ],
        }

    def frame(self, minute: int, agent_id: str) -> dict[str, Any]:
        return self.stored.mobility.frame_document(minute, selected_agent_id=agent_id)

    def document(self, endpoint: str, query: Mapping[str, str]) -> dict[str, Any]:
        def number(name: str, default: int = 0) -> int:
            return int(query.get(name, default))

        limit = number("limit", 100)
        if endpoint in self.static:
            return copy.deepcopy(self.static[endpoint])
        if endpoint == "opportunities":
            return self.opportunity_page(number("minute"), number("offset"), limit)
        if endpoint == "attention-events":
            return self.attention_page(number("minute"), number("offset"), limit)
        if endpoint == "response-events":
            return self.response_page(number("minute"), number("offset"), limit)
        if endpoint == "response-state":
            return self.state_page(query["agent_id"], number("offset"), limit)
        if endpoint == "frame":
            return self.frame(number("minute"), query["agent_id"])
        if endpoint == "timeline":
            minute = number("minute")
            agent_id = query["agent_id"]
            return {
                "schema_version": 1,
                "minute": minute,
                "agent_id": agent_id,
                "frame": self.frame(minute, agent_id),
                "opportunity_page": self.opportunity_page(
                    minute, number("opportunity_offset"), limit
                ),
                "attention_page": self.attention_page(minute, number("attention_offset"), limit),
                "response_page": self.response_page(minute, number("response_offset"), limit),
                "response_state_page": self.state_page(
                    agent_id, number("response_state_offset"), limit
                ),
            }
        raise KeyError(endpoint)


Override = Callable[[dict[str, Any], Mapping[str, str]], dict[str, Any]]


def workbench_app(run: SavedRun) -> FastAPI:
    """The real workbench, which serves the inspector at /runs/{run_id}."""
    return create_city_workbench_app(prepare_workbench_workspace(run.root), csrf_token=CSRF_TOKEN)


def standalone_app(stored: StoredCityRun) -> FastAPI:
    """The real standalone viewer, built exactly as ``adlife city-view`` builds it."""
    return create_city_app(
        stored.mobility,
        run_id=stored.manifest.run_id,
        run_schema_version=stored.manifest.schema_version,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        spatial_metrics=metrics_for_stored_city_run(stored),
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
        spatial_response_metrics=response_metrics_for_stored_city_run(stored),
        cognition_input=stored.cognition_input,
        cognition_evaluation=stored.cognition_evaluation,
        workbench_input_view=build_workbench_run_input_view_v4(stored),
        cognition_summary_view=build_workbench_cognition_summary_view(stored),
        cognition_records_view=build_workbench_cognition_records_view(stored),
    )


def entry_path(server: Server, run: SavedRun) -> tuple[str, str]:
    """The inspector page and the API base the real server declares for it."""
    run_id = run.stored.manifest.run_id
    if server == "workbench":
        return f"/runs/{run_id}", f"/api/runs/{run_id}"
    return "/", "/api"


@contextmanager
def served(server: Server, run: SavedRun) -> Iterator[str]:
    app = workbench_app(run) if server == "workbench" else standalone_app(run.stored)
    with _serve(app) as base_url:
        yield base_url


@dataclass
class Session:
    page: Page
    base_url: str
    api_base: str
    run: SavedRun
    console_errors: list[str]
    page_errors: list[str]
    requests: list[tuple[str, str, dict[str, str]]]
    external: list[str]

    def minute(self, minute: int, agent: str | None = None) -> None:
        if agent is None:
            assert self.page.evaluate("minute => setMinute(minute)", minute) is True
        else:
            assert (
                self.page.evaluate("([minute, agent]) => setMinute(minute, agent)", [minute, agent])
                is True
            )
        self.page.wait_for_function("minute => state.minute === minute", arg=minute)

    def view_token(self) -> str:
        return str(
            self.page.evaluate(
                "() => document.querySelector('meta[name=\"adlife-view-token\"]').content"
            )
        )


@contextmanager
def inspector(
    browser: Browser,
    run: SavedRun,
    *,
    server: Server = "workbench",
    overrides: Mapping[str, Override] | None = None,
    helper: bool = True,
    viewport: dict[str, int] | None = None,
    init_script: str | None = None,
    wait: str = "networkidle",
    **context_options: Any,
) -> Iterator[Session]:
    """Open a real server's inspector; overrides tamper only with the browser's copy."""
    hooks = dict(overrides or {})
    page_path, api_base = entry_path(server, run)
    with served(server, run) as base_url:
        context = browser.new_context(
            viewport=viewport or {"width": 1440, "height": 900}, **context_options
        )
        page = context.new_page()
        session = Session(page, base_url, api_base, run, [], [], [], [])
        page.on(
            "console",
            lambda message: (
                session.console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: session.page_errors.append(str(error)))

        def route(handler: Route) -> None:
            request = handler.request
            if not request.url.startswith(base_url):
                session.external.append(request.url)
                handler.abort()
                return
            session.requests.append((request.method, request.url, dict(request.headers)))
            path, _, query = request.url[len(base_url) :].partition("?")
            if not helper and path == "/static/app-cognition-v2.js":
                handler.fulfill(status=404, body="")
                return
            endpoint = path[len(api_base) + 1 :] if path.startswith(f"{api_base}/") else None
            if endpoint in hooks:
                upstream = handler.fetch()
                if upstream.status != 200:
                    session.page_errors.append(f"override source {endpoint} {upstream.status}")
                    handler.fulfill(response=upstream)
                    return
                document = hooks[endpoint](upstream.json(), dict(parse_qsl(query)))
                handler.fulfill(response=upstream, body=json.dumps(document))
                return
            handler.continue_()

        page.route("**/*", route)
        if init_script is not None:
            page.add_init_script(init_script)
        try:
            page.goto(f"{base_url}{page_path}", wait_until=wait)
            yield session
        finally:
            context.close()


def applied_records(run: SavedRun) -> list[Any]:
    return [
        item
        for item in run.stored.response_evaluation.records
        if item.event_type == "spatial.applied-response"
    ]


def response_minutes(run: SavedRun) -> list[int]:
    return sorted({item.baseline.model_minute for item in applied_records(run)})


def documents(run: SavedRun) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    projections = V10Projections(run.stored, metrics=False)
    return (
        projections.document("cognition-summary", {}),
        projections.document("cognition-records", {}),
        [item.model_dump(mode="json") for item in applied_records(run)],
    )


@pytest.fixture(scope="module")
def helper_page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page()
    page.set_content("<!doctype html><html><head></head><body></body></html>")
    page.add_script_tag(path=str(HELPER))
    try:
        yield page
    finally:
        page.close()


EVALUATE_PIPELINE = """([summary, records, applied, forgeIndex]) => {
  const helper = window.AdLifeBoundedCognition;
  const attempt = (stage, operation) => {
    try { return { value: operation() }; }
    catch (error) { return { failedAt: stage, message: String(error.message) }; }
  };
  const s = attempt("summary", () => helper.validateSummary(summary));
  if (s.failedAt) return s;
  const r = attempt("records", () => helper.validateRecords(records, s.value));
  if (r.failedAt) return r;
  const index = attempt("index", () => forgeIndex
    ? Object.freeze({ get: () => null, has: () => false, size: 0 })
    : helper.indexTerminalRecords(r.value));
  if (index.failedAt) return index;
  const presentations = [];
  for (const item of applied) {
    const p = attempt(
      "presentation", () => helper.presentationForAppliedResponse(item, index.value),
    );
    if (p.failedAt) return p;
    presentations.push(p.value);
  }
  return { failedAt: null, presentations };
}"""


@pytest.mark.parametrize("name", ["rules", "mock", "provider", "cache", "fallback", "mixed"])
def test_pure_v2_helpers_validate_and_freeze_evidence(
    helper_page: Page, saved_runs: Callable[[str], SavedRun], name: str
) -> None:
    run = saved_runs(name)
    summary, records, applied = documents(run)
    exports = helper_page.evaluate(
        """() => ({
          keys: Object.keys(window.AdLifeBoundedCognition),
          frozen: Object.isFrozen(window.AdLifeBoundedCognition),
          writable: Object.getOwnPropertyDescriptor(window, "AdLifeBoundedCognition").writable,
        })"""
    )
    assert exports == {
        "keys": [
            "validateSummary",
            "validateRecords",
            "indexTerminalRecords",
            "presentationForAppliedResponse",
        ],
        "frozen": True,
        "writable": False,
    }
    result = helper_page.evaluate(EVALUATE_PIPELINE, [summary, records, applied, False])
    assert result["failedAt"] is None, result
    presentations = result["presentations"]
    assert len(presentations) == len(applied) == len(applied_records(run))
    terminals = {record["record_hash"]: record for record in records["records"]}
    for item, presentation in zip(applied, presentations, strict=True):
        assert [stage["key"] for stage in presentation["stages"]] == STAGES
        assert presentation["source"] == item["source"]
        assert presentation["neutralizationStatus"] == item["neutralization_status"]
        assert presentation["appliedEventId"] == item["event_id"]
        assert presentation["baselineEventId"] == item["baseline"]["event_id"]
        assert presentation["appliedDeltas"] == {
            "sentiment": item["applied_sentiment_delta"],
            "recall": item["applied_recall_delta"],
        }
        assert presentation["terminalRecordHash"] == item["terminal_record_hash"]
        status = item["neutralization_status"]
        kind = (
            None
            if item["source"] == "unselected"
            else "applied"
            if status == "none"
            else "set-aside"
            if status in {"safety", "uncertain"}
            else "neutral"
        )
        assert presentation["stages"][1]["chipsKind"] == kind
        assert presentation["stages"][1]["chipsNote"] == (
            None if kind is None else CHIP_NOTES[kind]
        )
        if item["source"] == "unselected":
            assert presentation["selected"] is False
            assert presentation["stages"][1]["chips"] == []
        else:
            terminal = terminals[item["terminal_record_hash"]]
            assert presentation["selectionIndex"] == terminal["selection_index"]
            assert presentation["requestId"] == terminal["request_id"]
            chips = {chip["key"]: chip["value"] for chip in presentation["stages"][1]["chips"]}
            assert chips == {
                key: terminal["result"][key]
                for key in ("affect", "relevance", "credibility", "salience")
            }
        if item["neutralization_status"] == "none" and presentation["factors"] is not None:
            assert presentation["stages"][2]["status"] == "Fixed mapping"
        else:
            assert presentation["stages"][2]["detail"].startswith("Neutral vector")
            assert presentation["appliedDeltas"] == {
                "sentiment": item["baseline"]["sentiment_delta"],
                "recall": item["baseline"]["recall_delta"],
            }

    immutability = helper_page.evaluate(
        """([summary, records, applied]) => {
          const helper = window.AdLifeBoundedCognition;
          const s = helper.validateSummary(summary);
          const r = helper.validateRecords(records, s);
          const index = helper.indexTerminalRecords(r);
          const deepFrozen = (value) => value === null || typeof value !== "object"
            || (Object.isFrozen(value) && Object.values(value).every(deepFrozen));
          const presentation = helper.presentationForAppliedResponse(applied[0], index);
          const originalGet = index.get;
          const before = JSON.stringify([s, r, presentation]);
          const firstHash = r.records.length ? r.records[0].record_hash : null;
          const attempts = [
            () => { s.summary.coverage.selected_request_count = 99; },
            () => { r.records.push(r.records[0]); },
            () => { if (r.records.length) r.records[0].record_hash = "0".repeat(64); },
            () => { presentation.stages[0].title = "rewritten"; },
            () => { index.get = () => r.records[0]; },
          ];
          for (const operation of attempts) {
            try { operation(); } catch (_) { /* strict-mode refusal is also acceptable */ }
          }
          const unchanged = JSON.stringify([s, r, presentation]) === before
            && index.get === originalGet;
          return {
            frozen: [s, r, index, presentation].every(deepFrozen),
            refused: unchanged ? attempts.length : 0,
            total: attempts.length,
            mutators: ["set", "delete", "clear", "add"].filter((name) => name in index),
            lookup: firstHash === null || index.get(firstHash) === r.records[0],
            unknown: index.get("f".repeat(64)),
            size: index.size,
            copied: summary !== s && records !== r,
          };
        }""",
        [summary, records, applied],
    )
    assert immutability == {
        "frozen": True,
        "refused": 5,
        "total": 5,
        "mutators": [],
        "lookup": True,
        "unknown": None,
        "size": len(records["records"]),
        "copied": True,
    }


def _rehash(record: dict[str, Any]) -> None:
    facts = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = sha256(canonical_json(facts).encode("utf-8")).hexdigest()


def _first(applied: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    return next(
        item for item in applied if all(item[key] == value for key, value in fields.items())
    )


Mutation = Callable[[dict[str, Any], dict[str, Any], list[dict[str, Any]]], None]


def _summary(**changes: Any) -> Mutation:
    def mutate(summary: dict[str, Any], _records: Any, _applied: Any) -> None:
        summary.update(changes)

    return mutate


def _record_zero(change: Callable[[dict[str, Any]], None], *, rehash: bool) -> Mutation:
    def mutate(_summary: Any, records: dict[str, Any], _applied: Any) -> None:
        change(records["records"][0])
        if rehash:
            _rehash(records["records"][0])

    return mutate


def _provider_applied(change: Callable[[dict[str, Any]], None]) -> Mutation:
    def mutate(_summary: Any, _records: Any, applied: list[dict[str, Any]]) -> None:
        target = _first(applied, source="provider", neutralization_status="none")
        change(target)
        applied[:] = [target]

    return mutate


def _set_result(**changes: Any) -> Callable[[dict[str, Any]], None]:
    return lambda record: record["result"].update(changes)


def _cross_terminal(applied: list[dict[str, Any]]) -> None:
    first = _first(applied, source="provider", neutralization_status="none")
    other = next(
        item
        for item in applied
        if item["terminal_record_hash"] not in (None, first["terminal_record_hash"])
    )
    first["terminal_record_hash"] = other["terminal_record_hash"]
    applied[:] = [first]


def _unselected_with_terminal(applied: list[dict[str, Any]]) -> None:
    target = _first(applied, source="provider", neutralization_status="none")
    target.update(
        source="unselected",
        neutralization_status="unselected",
        terminal_record_hash=None,
        unselected_marker="unselected-neutral",
        applied_sentiment_delta=target["baseline"]["sentiment_delta"],
        applied_recall_delta=target["baseline"]["recall_delta"],
    )
    applied[:] = [target]


REFUSALS: list[tuple[str, str, str, Mutation]] = [
    ("summary-extra-field", "provider", "summary", _summary(base_url="http://127.0.0.1:1/v1")),
    ("summary-missing-field", "provider", "summary", lambda s, _r, _a: s.pop("model_id")),
    ("summary-schema-version", "rules", "summary", _summary(schema_version=3)),
    ("summary-run-schema-boolean", "rules", "summary", _summary(run_schema_version=True)),
    ("summary-run-schema-string", "rules", "summary", _summary(run_schema_version="10")),
    ("summary-run-schema-unknown", "rules", "summary", _summary(run_schema_version=11)),
    ("summary-unknown-mode", "rules", "summary", _summary(mode="cloud")),
    ("summary-unknown-limit", "provider", "summary", _summary(funded_response_limit=15)),
    ("summary-wrong-influence", "rules", "summary", _summary(influence_policy_sha256="0" * 64)),
    ("summary-wrong-selection", "mock", "summary", _summary(selection_policy_sha256="0" * 64)),
    ("summary-wrong-contract", "mock", "summary", _summary(causal_contract_sha256="0" * 64)),
    (
        "summary-prompt-hash-shape",
        "provider",
        "summary",
        _summary(prompt_contract_sha256="A" * 64),
    ),
    (
        "summary-result-schema-hash-shape",
        "rules",
        "summary",
        _summary(result_schema_contract_sha256="0" * 63),
    ),
    (
        "summary-inner-contract-disagrees",
        "provider",
        "summary",
        lambda s, _r, _a: s["summary"].update(causal_contract_sha256="a" * 64),
    ),
    ("summary-bidi-model", "provider", "summary", _summary(model_id="model \u202e evil")),
    (
        "summary-nonfinite-attempts",
        "provider",
        "summary",
        lambda s, _r, _a: s["summary"].update(attempt_count=float("inf")),
    ),
    (
        "summary-coverage-equation",
        "provider",
        "summary",
        lambda s, _r, _a: s["summary"]["coverage"].update(
            eligible_response_count=s["summary"]["coverage"]["eligible_response_count"] + 1
        ),
    ),
    (
        "records-duplicate",
        "provider",
        "records",
        lambda _s, r, _a: r["records"].__setitem__(1, copy.deepcopy(r["records"][0])),
    ),
    (
        "records-reordered",
        "provider",
        "records",
        lambda _s, r, _a: r["records"].__setitem__(slice(0, 2), [r["records"][1], r["records"][0]]),
    ),
    (
        "records-extra-field",
        "provider",
        "records",
        _record_zero(lambda record: record.update(prompt="raw provider body"), rehash=True),
    ),
    (
        "records-missing-field",
        "provider",
        "records",
        _record_zero(lambda record: record.pop("reason"), rehash=True),
    ),
    (
        "records-unknown-enum",
        "provider",
        "records",
        _record_zero(_set_result(affect="ecstatic"), rehash=True),
    ),
    (
        "records-tampered-hash",
        "provider",
        "records",
        _record_zero(lambda record: record.update(record_hash="f" * 64), rehash=False),
    ),
    ("records-cross-run", "provider", "records", lambda _s, r, _a: r.update(run_id="other-run")),
    (
        "records-cross-input",
        "provider",
        "records",
        lambda _s, r, _a: r.update(cognition_input_sha256="f" * 64),
    ),
    (
        "records-cross-policy",
        "provider",
        "records",
        _record_zero(lambda record: record.update(influence_policy_sha256="e" * 64), rehash=True),
    ),
    (
        "records-cross-provider",
        "provider",
        "records",
        _record_zero(lambda record: record.update(provider_profile_id="other"), rehash=True),
    ),
    (
        "records-cross-prompt-contract",
        "provider",
        "records",
        _record_zero(lambda record: record.update(prompt_contract_sha256="a" * 64), rehash=True),
    ),
    (
        "records-cross-causal-contract",
        "mock",
        "records",
        _record_zero(lambda record: record.update(causal_contract_sha256="b" * 64), rehash=True),
    ),
    (
        "records-cross-result-schema",
        "rules",
        "records",
        _record_zero(
            lambda record: record.update(result_schema_contract_sha256="c" * 64), rehash=True
        ),
    ),
    (
        "records-mock-vector",
        "mock",
        "records",
        _record_zero(
            lambda record: record["result"].update(
                affect="positive" if record["result"]["affect"] != "positive" else "annoyed"
            ),
            rehash=True,
        ),
    ),
    (
        "records-stream-hash",
        "provider",
        "records",
        lambda s, _r, _a: s["summary"].update(terminal_stream_sha256="0" * 64),
    ),
    (
        "binding-orphan",
        "provider",
        "presentation",
        _provider_applied(lambda item: item.update(terminal_record_hash="9" * 64)),
    ),
    ("binding-request-id-only", "provider", "presentation", lambda _s, _r, a: _cross_terminal(a)),
    (
        "binding-cross-agent",
        "provider",
        "presentation",
        _provider_applied(
            lambda item: item["baseline"].update(
                agent_id="person-001"
                if item["baseline"]["agent_id"] != "person-001"
                else "person-002"
            )
        ),
    ),
    (
        "binding-cross-campaign",
        "provider",
        "presentation",
        _provider_applied(lambda item: item["baseline"].update(campaign_id="other-campaign")),
    ),
    (
        "binding-cross-minute",
        "provider",
        "presentation",
        _provider_applied(
            lambda item: item["baseline"].update(model_minute=item["baseline"]["model_minute"] + 1)
        ),
    ),
    (
        "binding-cross-policy",
        "provider",
        "presentation",
        _provider_applied(lambda item: item.update(influence_policy_sha256="e" * 64)),
    ),
    (
        "binding-cross-run",
        "provider",
        "presentation",
        _provider_applied(lambda item: item.update(cognition_input_sha256="f" * 64)),
    ),
    (
        "binding-unselected-with-terminal",
        "provider",
        "presentation",
        lambda _s, _r, a: _unselected_with_terminal(a),
    ),
    (
        "binding-status-mismatch",
        "provider",
        "presentation",
        _provider_applied(
            lambda item: item.update(
                neutralization_status="uncertain",
                applied_sentiment_delta=item["baseline"]["sentiment_delta"],
                applied_recall_delta=item["baseline"]["recall_delta"],
            )
        ),
    ),
    (
        "binding-tampered-delta",
        "provider",
        "presentation",
        _provider_applied(
            lambda item: item.update(applied_sentiment_delta=item["applied_sentiment_delta"] - 1e-9)
        ),
    ),
    (
        "binding-extra-field",
        "provider",
        "presentation",
        _provider_applied(lambda item: item.update(explanation="free text")),
    ),
    (
        "binding-nonfinite-delta",
        "provider",
        "presentation",
        _provider_applied(lambda item: item.update(applied_recall_delta=float("nan"))),
    ),
]


@pytest.mark.parametrize(
    ("case", "name", "stage", "mutate"),
    REFUSALS,
    ids=[case for case, *_ in REFUSALS],
)
def test_helpers_refuse_malformed_or_cross_bound_evidence(
    helper_page: Page,
    saved_runs: Callable[[str], SavedRun],
    case: str,
    name: str,
    stage: str,
    mutate: Mutation,
) -> None:
    summary, records, applied = documents(saved_runs(name))
    mutate(summary, records, applied)
    result = helper_page.evaluate(EVALUATE_PIPELINE, [summary, records, applied, False])
    assert result["failedAt"] == stage, (case, result)
    assert result["message"].endswith("is invalid"), result


def _rebind_contract(
    summary: dict[str, Any], records: dict[str, Any], hashes: Mapping[str, str]
) -> None:
    """Rebind a run to another self-consistent prompt/result/causal contract identity."""
    summary.update(hashes)
    summary["summary"]["causal_contract_sha256"] = hashes["causal_contract_sha256"]
    stream = ""
    for record in records["records"]:
        record.update(hashes)
        _rehash(record)
        stream += canonical_json(record) + "\n"
    encoded = stream.encode("utf-8")
    summary["summary"].update(
        terminal_stream_sha256=sha256(encoded).hexdigest(), terminal_stream_bytes=len(encoded)
    )


@pytest.mark.parametrize("name", ["rules", "mock", "provider", "mixed"])
def test_helpers_bind_the_run_contract_not_a_browser_constant(
    helper_page: Page, saved_runs: Callable[[str], SavedRun], name: str
) -> None:
    """A revised prompt contract needs no browser change; disagreement is still refused."""
    summary, records, _applied = documents(saved_runs(name))
    revised = {
        "prompt_contract_sha256": sha256(b"revised prompt contract").hexdigest(),
        "result_schema_contract_sha256": sha256(b"revised result schema").hexdigest(),
        "causal_contract_sha256": sha256(b"revised causal contract").hexdigest(),
    }
    assert all(summary[field] != value for field, value in revised.items())
    _rebind_contract(summary, records, revised)
    accepted = helper_page.evaluate(EVALUATE_PIPELINE, [summary, records, [], False])
    assert accepted == {"failedAt": None, "presentations": []}

    if records["records"]:
        records["records"][-1]["prompt_contract_sha256"] = "d" * 64
        _rehash(records["records"][-1])
        refused = helper_page.evaluate(EVALUATE_PIPELINE, [summary, records, [], False])
        assert refused == {
            "failedAt": "records",
            "message": "Saved bounded cognition record is invalid",
        }


def test_helpers_refuse_forged_lookup_and_unvalidated_inputs(
    helper_page: Page, saved_runs: Callable[[str], SavedRun]
) -> None:
    summary, records, applied = documents(saved_runs("provider"))
    forged = helper_page.evaluate(EVALUATE_PIPELINE, [summary, records, applied, True])
    assert forged["failedAt"] == "presentation"
    refusals = helper_page.evaluate(
        """([summary, records]) => {
          const helper = window.AdLifeBoundedCognition;
          const outcomes = [];
          for (const operation of [
            () => helper.validateRecords(records, summary),
            () => helper.indexTerminalRecords(records),
            () => helper.indexTerminalRecords(helper.validateSummary(summary)),
          ]) {
            try { operation(); outcomes.push("accepted"); }
            catch (error) { outcomes.push(error.message); }
          }
          return outcomes;
        }""",
        [summary, records],
    )
    assert refusals == [
        "Saved bounded cognition records is invalid",
        "Bounded cognition record index is invalid",
        "Bounded cognition record index is invalid",
    ]


def _stage_texts(page: Page, selector: str) -> list[dict[str, Any]]:
    return page.evaluate(
        """selector => Array.from(document.querySelectorAll(selector)).map((item) => ({
          applied: item.dataset.appliedEventId,
          effect: item.dataset.effect,
          source: item.querySelector('.bounded-applied-card').dataset.source,
          neutralization: item.querySelector('.bounded-applied-card').dataset.neutralization,
          badges: Array.from(item.querySelectorAll('.bounded-badge')).map(
            (badge) => [badge.dataset.badge, badge.dataset.value, badge.textContent],
          ),
          stages: Array.from(item.querySelectorAll('.bounded-stage')).map((stage) => ({
            key: stage.dataset.stage,
            title: stage.querySelector('.bounded-stage-title').textContent,
            status: stage.querySelector('.bounded-stage-status').textContent,
            detail: stage.querySelector('.bounded-stage-detail').textContent,
            chips: Array.from(stage.querySelectorAll('.bounded-chip[data-dimension]')).map(
              (chip) => [chip.dataset.dimension, chip.dataset.value, chip.textContent],
            ),
            flags: Array.from(stage.querySelectorAll('.bounded-chip[data-flag]')).map(
              (chip) => chip.dataset.flag,
            ),
            chipsKind: stage.querySelector('.bounded-chips')
              ? stage.querySelector('.bounded-chips').dataset.kind
              : null,
            note: stage.querySelector('.bounded-chips-note')
              ? stage.querySelector('.bounded-chips-note').textContent
              : null,
            formula: Array.from(stage.querySelectorAll('.bounded-stage-formula')).map(
              (line) => line.textContent,
            ),
            visible: stage.getBoundingClientRect().height > 0,
          })),
        }))""",
        selector,
    )


MIXED_STATUSES = {"none", "uncertain", "safety", "fallback", "budget-skipped", "unselected"}


@pytest.mark.parametrize(
    ("name", "statuses", "server"),
    [
        ("mixed", MIXED_STATUSES, "workbench"),
        ("mixed", MIXED_STATUSES, "standalone"),
        ("rules", {"rules"}, "workbench"),
        ("mock", {"none"}, "standalone"),
    ],
)
def test_v10_inspector_traces_every_eligible_response_in_order(
    browser: Browser,
    saved_runs: Callable[[str], SavedRun],
    name: str,
    statuses: set[str],
    server: Server,
) -> None:
    run = saved_runs(name)
    expected = {item.event_id: item for item in applied_records(run)}
    assert {item.neutralization_status for item in expected.values()} == statuses
    with inspector(browser, run, server=server) as session:
        page = session.page
        expect(page.locator("#error-banner")).to_be_hidden()
        expect(page.locator("#bounded-cognition-panel")).to_be_visible()
        seen: dict[str, dict[str, Any]] = {}
        for minute in response_minutes(run):
            session.minute(minute)
            for item in _stage_texts(page, "#response-list .bounded-applied-item"):
                seen[item["applied"]] = item
            updates = page.locator("#response-list .bounded-dual-item")
            assert updates.count() >= 1
            for index in range(updates.count()):
                tracks = updates.nth(index).locator(".bounded-track")
                assert tracks.evaluate_all("nodes => nodes.map((node) => node.dataset.track)") == [
                    "rule",
                    "cognition",
                ]
        assert set(seen) == set(expected)
        terminals = {
            record.record_hash: record for record in run.stored.cognition_evaluation.records
        }
        for applied_id, rendered in seen.items():
            record = expected[applied_id]
            source = record.source
            status = record.neutralization_status
            assert (rendered["source"], rendered["neutralization"]) == (source, status)
            assert [stage["key"] for stage in rendered["stages"]] == STAGES
            assert all(stage["visible"] for stage in rendered["stages"])
            titles = [stage["title"] for stage in rendered["stages"]]
            assert titles[0] == STAGE_TITLES[0] and titles[2:] == STAGE_TITLES[2:]
            assert titles[1] == SIGNAL_TITLES[source]
            assert all(stage["status"].strip() for stage in rendered["stages"])
            changed = (
                record.applied_sentiment_delta != record.baseline.sentiment_delta
                or record.applied_recall_delta != record.baseline.recall_delta
            )
            if status == "none":
                effect = "mapped" if changed else "identity"
            else:
                assert not changed
                effect = status if status in {"safety", "uncertain"} else "neutral"
            # Text badges name the source and the effect; shape and color only repeat them.
            assert rendered["effect"] == effect
            assert rendered["badges"] == [
                ["source", source, f"Source{SOURCE_BADGES[source]}"],
                ["effect", effect, f"Effect{EFFECT_BADGES[effect]}"],
            ]
            signal = rendered["stages"][1]
            vector: list[str] = []
            if source == "unselected":
                assert signal["chips"] == [] and signal["flags"] == []
                assert signal["chipsKind"] is None and signal["note"] is None
            else:
                result = terminals[record.terminal_record_hash].result
                vector = [result.affect, result.relevance, result.credibility, result.salience]
                assert [chip[:2] for chip in signal["chips"]] == [
                    ["affect", result.affect],
                    ["relevance", result.relevance],
                    ["credibility", result.credibility],
                    ["salience", result.salience],
                ]
                assert signal["flags"] == list(result.safety_flags)
                # An applied answer, an audited set-aside answer, and the fixed neutral
                # vector (applied as identity) are three different things.
                kind = (
                    "applied"
                    if status == "none"
                    else "set-aside"
                    if status in {"safety", "uncertain"}
                    else "neutral"
                )
                assert signal["chipsKind"] == kind
                assert signal["note"] == CHIP_NOTES[kind]
                if kind == "neutral":
                    assert vector == NEUTRAL_VECTOR
            if status in NEUTRAL_STATUS:
                assert signal["status"] == NEUTRAL_STATUS[status]
            if status == "safety":
                assert signal["status"].startswith("Neutralized · safety flag (")
            if status in {"safety", "uncertain"}:
                # The neutralization is explained in plain words, naming its cause.
                assert "sets the whole answer aside" in signal["detail"]
                cause = "safety flag" if status == "safety" else "“uncertain”"
                assert cause in signal["detail"]
            mapping = rendered["stages"][2]
            if status == "none":
                assert signal["status"] == "Signal applied" or signal["status"].endswith(
                    "signal applied"
                )
                identity = vector == NEUTRAL_VECTOR
                assert mapping["detail"].startswith("Neutral vector" if identity else "Affect ")
                assert [line.split(" = ")[0] for line in mapping["formula"]] == (
                    [] if identity else ["Sentiment Δ", "Recall Δ"]
                )
            else:
                assert mapping["detail"].startswith("Neutral vector")
                assert mapping["formula"] == []
            assert rendered["stages"][4]["detail"].startswith("Committed atomically")
        assert session.page_errors == [] and session.console_errors == []
        assert session.external == []


@pytest.mark.parametrize("name", ["rules", "mixed"])
def test_v10_purchase_intention_stays_on_rule_cards(
    browser: Browser, saved_runs: Callable[[str], SavedRun], name: str
) -> None:
    run = saved_runs(name)
    first = applied_records(run)[0].baseline
    with inspector(browser, run) as session:
        page = session.page
        session.minute(first.model_minute, first.agent_id)
        cards = page.evaluate(
            """() => ({
              rule: Array.from(document.querySelectorAll(
                '.bounded-rule-card, .bounded-rule-track',
              )).map((node) => node.textContent),
              cognition: Array.from(document.querySelectorAll(
                '.bounded-cognition-card, .bounded-cognition-track',
              )).map((node) => node.textContent),
            })"""
        )
        assert cards["rule"] and cards["cognition"]
        assert all(
            "INTENTION PROXY" in text or "Purchase-intention" in text for text in cards["rule"]
        )
        for text in cards["cognition"]:
            assert not re.search(r"purchase|intention", text, re.IGNORECASE), text
            assert re.search(r"sentiment", text, re.IGNORECASE)
            assert re.search(r"recall", text, re.IGNORECASE)
            assert re.search(r"responses", text, re.IGNORECASE)
        final = page.locator("#response-state-list .bounded-state-pair").first
        expect(final.locator(".bounded-rule-card")).to_contain_text("RULE TRACK · RULE-OWNED")
        expect(final.locator(".bounded-cognition-card")).to_contain_text(
            "COGNITION TRACK · SYNTHETIC"
        )
        expect(final.locator(".bounded-cognition-card")).to_contain_text("LAST RESPONSE")
        expect(page.locator("#response-state-claim")).to_contain_text("rule-owned")


@pytest.mark.parametrize(
    ("name", "title", "boundary"),
    [
        ("rules", "Bounded cognition · rules identity", "neutral bounded cognition identity"),
        ("mock", "Bounded cognition · mock signals", "deterministic mock cognition signals"),
        ("provider", "Bounded AI cognition signals", "bounded AI cognition signals"),
        ("cache", "Bounded AI cognition signals", "bounded AI cognition signals"),
        ("fallback", "Bounded AI cognition · neutral outcomes", "neutral outcomes"),
        ("mixed", "Bounded AI cognition signals", "bounded AI cognition signals"),
    ],
)
@pytest.mark.parametrize("server", ["workbench", "standalone"])
def test_v10_panel_shows_exact_provenance_coverage_and_offline_replay(
    browser: Browser,
    saved_runs: Callable[[str], SavedRun],
    name: str,
    title: str,
    boundary: str,
    server: Server,
) -> None:
    run = saved_runs(name)
    summary = run.stored.cognition_evaluation.summary
    cognition_input = run.stored.cognition_input
    profile = cognition_input.provider_profile
    contract = cognition_input.causal_contract
    with inspector(browser, run, server=server) as session:
        page = session.page
        panel = page.locator("#bounded-cognition-panel")
        expect(panel).to_be_visible()
        expect(page.locator("#bounded-cognition-title")).to_have_text(title)
        expect(page.locator("#model-boundary-label")).to_contain_text(boundary)
        if name in {"rules", "mock"}:
            assert "AI" not in page.locator("#bounded-cognition-title").inner_text()
        tiles = page.evaluate(
            """() => Object.fromEntries(Array.from(
              document.querySelectorAll('#bounded-cognition-coverage .bounded-tile'),
            ).map((tile) => [
              tile.dataset.count, Number(tile.querySelector('strong').textContent),
            ]))"""
        )
        coverage = summary.coverage
        outcomes = summary.outcomes
        assert {
            key: tiles[key]
            for key in (
                "eligible",
                "selected",
                "funded",
                "unselected",
                "provider",
                "cache",
                "fallback",
                "budget-skipped",
            )
        } == {
            "eligible": coverage.eligible_response_count,
            "selected": coverage.selected_request_count,
            "funded": coverage.funded_request_count,
            "unselected": coverage.unselected_response_count,
            "provider": outcomes.provider,
            "cache": outcomes.cache,
            "fallback": outcomes.fallback,
            "budget-skipped": outcomes.budget_skipped,
        }
        captions = page.locator("#bounded-cognition-coverage .bounded-tile small").all_inner_texts()
        assert len(captions) == len(tiles) and all(text.strip() for text in captions)
        neutral = summary.neutralization
        expect(page.locator("#bounded-cognition-neutralization")).to_contain_text(
            f"uncertain results {neutral.uncertain_result_count} · safety-neutralized results "
            f"{neutral.safety_neutralized_result_count} (these two diagnostic counts may overlap)"
        )
        # Every eligible response resolves to exactly one applied status.
        statuses = [
            item.neutralization_status
            for item in run.stored.response_evaluation.records
            if item.event_type == "spatial.applied-response"
        ]
        partition = {
            "signal": statuses.count("none"),
            **{key: statuses.count(key) for key in ("safety", "uncertain", "fallback")},
            "budget-skipped": statuses.count("budget-skipped"),
            "rules": statuses.count("rules"),
            "unselected": statuses.count("unselected"),
        }
        rows = page.evaluate(
            """() => Object.fromEntries(Array.from(
              document.querySelectorAll('#bounded-cognition-outcome-list > div'),
            ).map((row) => [row.dataset.outcome, Number(row.querySelector('dd').textContent)]))"""
        )
        assert coverage.eligible_response_count > 0
        assert rows == {
            key: count
            for key, count in partition.items()
            if count > 0 or (key == "signal" and cognition_input.mode != "rules")
        }
        assert sum(rows.values()) == coverage.eligible_response_count
        expect(page.locator("#bounded-cognition-outcomes-title")).to_have_text(
            f"How the {coverage.eligible_response_count} eligible "
            f"{'response was' if coverage.eligible_response_count == 1 else 'responses were'} "
            "resolved"
        )
        facts = page.evaluate(
            """() => Object.fromEntries(Array.from(
              document.querySelectorAll('#bounded-cognition-provenance > div'),
            ).map((row) => [row.dataset.fact, row.querySelector('dd').textContent]))"""
        )
        assert (
            facts["prompt"] == f"{contract.prompt_contract_id} · {contract.prompt_contract_sha256}"
        )
        assert facts["result-schema"] == (
            f"{contract.result_schema_id} · {contract.result_schema_contract_sha256}"
        )
        assert facts["influence"] == (
            f"city-cognition-influence-v1 · {contract.influence_policy_sha256}"
        )
        assert facts["cognition-input"] == cognition_input.fingerprint
        assert facts["causal-contract"] == cognition_input.causal_contract_sha256
        limit = cognition_input.funded_response_limit
        model_line = page.locator("#bounded-cognition-model")
        if profile is None:
            assert facts["profile"] == "Not used · offline"
            assert facts["model"] == "Not used"
            assert facts["revision"] == "Not used"
            assert facts["funded-limit"] == f"{limit} · resolved locally, no provider tokens"
            expect(model_line).to_have_text(
                "No model · offline deterministic mock signals"
                if cognition_input.mode == "mock"
                else "No model · rules identity, no signal requests"
            )
        else:
            assert facts["profile"] == profile.profile_id
            assert facts["model"] == profile.model_id
            assert facts["revision"] == profile.revision_sha256
            # The output budget comes from the run's frozen input, never a browser constant.
            assert facts["funded-limit"] == (
                f"{limit} · {cognition_input.aggregate_output_token_ceiling} reserved output "
                f"tokens ({cognition_input.reservation_tokens_per_request} per request, at most "
                f"{cognition_input.max_output_tokens_per_attempt} per attempt)"
            )
            expect(model_line).to_have_text(
                f"Model{profile.model_id}profile {profile.profile_id} · revision "
                f"{profile.revision_sha256[:12]}…"
            )
            expect(page.locator("#bounded-cognition-shortcut")).to_contain_text(
                f"Model {profile.model_id}"
            )
            assert profile.base_url not in panel.inner_text()
            assert profile.credential_slot_id not in page.content()
        expect(page.locator("#bounded-cognition-replay")).to_have_text(
            "Saved replay is frozen and offline: this inspector reads the verified saved "
            "signals and never calls a provider. A fresh live run is not guaranteed to "
            "reproduce them."
        )
        expect(page.locator("#bounded-cognition-disclosure")).to_contain_text(
            "Disclosure: synthetic-bounded-cognition-not-observed-behavior."
        )
        ownership = page.locator("#bounded-cognition-authority").inner_text()
        assert "Movement, exposure, and attention" in ownership
        assert "rule-owned uncalibrated proxy · purchases not modeled" in ownership
        assert "frozen and offline" in ownership
        expect(page.locator("#saved-run-label")).to_contain_text(" · V10")
        shortcut = page.locator("#bounded-cognition-shortcut")
        expect(shortcut).to_contain_text(
            f"{coverage.selected_request_count} saved "
            f"{'request' if coverage.selected_request_count == 1 else 'requests'}"
            f" · {coverage.eligible_response_count} eligible responses"
        )
        shortcut.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.activeElement"
            " && document.activeElement.id === 'bounded-cognition-panel'"
        )
        assert page.evaluate(
            """() => {
              const panel = document.querySelector('#bounded-cognition-panel');
              const rect = panel.getBoundingClientRect();
              return rect.top >= 0 && rect.top < window.innerHeight;
            }"""
        )
        assert session.page_errors == [] and session.console_errors == []


HOLD_TIMELINE = """(() => {
  const originalFetch = window.fetch.bind(window);
  window.heldMinute = null;
  window.heldFetches = [];
  window.fetch = async (input, options = {}) => {
    const url = String(input);
    const match = /\\/timeline\\?minute=(\\d+)/.exec(url);
    if (match && Number(match[1]) === window.heldMinute) {
      const { signal: _ignored, ...rest } = options;
      const response = await originalFetch(input, rest);
      await new Promise((resolve) => window.heldFetches.push(resolve));
      return response;
    }
    if (url.endsWith('/cognition-records') && window.holdCognition) {
      await new Promise((resolve) => { window.releaseCognition = resolve; });
    }
    return originalFetch(input, options);
  };
})();"""


def test_v10_late_pages_never_replace_a_newer_minute_or_agent(
    browser: Browser, saved_runs: Callable[[str], SavedRun]
) -> None:
    run = saved_runs("mixed")
    minutes = response_minutes(run)
    early, late = minutes[0], minutes[-1]
    with inspector(browser, run, init_script=HOLD_TIMELINE) as session:
        page = session.page
        page.evaluate("minute => { window.heldMinute = minute; }", early)
        page.evaluate("minute => { window.staleResult = setMinute(minute); }", early)
        page.wait_for_function("() => window.heldFetches.length === 1")
        session.minute(late, "person-002")
        newer = page.evaluate(
            """() => Array.from(document.querySelectorAll('.bounded-applied-item'))
              .map((item) => item.dataset.appliedEventId)"""
        )
        page.evaluate("() => window.heldFetches.forEach((release) => release())")
        assert page.evaluate("() => window.staleResult") is False
        assert page.evaluate("() => [state.minute, state.selected]") == [late, "person-002"]
        assert (
            page.evaluate(
                """() => Array.from(document.querySelectorAll('.bounded-applied-item'))
                  .map((item) => item.dataset.appliedEventId)"""
            )
            == newer
        )
        expected = {
            item.event_id for item in applied_records(run) if item.baseline.model_minute == late
        }
        assert set(newer) == expected
        expect(page.locator("#error-banner")).to_be_hidden()


def test_v10_delayed_cognition_evidence_binds_to_the_current_selection(
    browser: Browser, saved_runs: Callable[[str], SavedRun]
) -> None:
    run = saved_runs("provider")
    target = applied_records(run)[0].baseline
    with inspector(
        browser,
        run,
        init_script="window.holdCognition = true;\n" + HOLD_TIMELINE,
        wait="domcontentloaded",
    ) as session:
        page = session.page
        page.wait_for_function("() => typeof window.releaseCognition === 'function'")
        session.minute(target.model_minute, target.agent_id)
        expect(page.locator(".bounded-trace-pending").first).to_be_visible()
        assert page.locator(".bounded-trace").count() == 0
        page.evaluate("() => window.releaseCognition()")
        expect(page.locator("#bounded-cognition-panel")).to_be_visible()
        page.wait_for_function("() => state.boundedCognition !== null")
        expect(page.locator(".bounded-trace").first).to_be_visible()
        assert page.evaluate("() => [state.minute, state.selected]") == [
            target.model_minute,
            target.agent_id,
        ]
        assert page.locator(".bounded-trace-pending").count() == 0


def model_label_overrides(model_id: str) -> dict[str, Override]:
    """Relabel the provider model consistently in every document that names it."""

    def summary(document: dict[str, Any], _query: Mapping[str, str]) -> dict[str, Any]:
        document["model_id"] = model_id
        return document

    def metrics(document: dict[str, Any], _query: Mapping[str, str]) -> dict[str, Any]:
        document["provider_dependent_cognition"]["provider_model_id"] = model_id
        return document

    return {"cognition-summary": summary, "spatial-response-metrics": metrics}


@pytest.mark.parametrize(
    ("viewport", "forced_colors", "scale"),
    [
        ({"width": 390, "height": 844}, "none", "100%"),
        ({"width": 390, "height": 844}, "active", "200%"),
        ({"width": 1440, "height": 900}, "none", "100%"),
    ],
    ids=["mobile", "mobile-forced-colors-200", "projector"],
)
def test_v10_hostile_text_stays_inert_and_controls_stay_accessible(
    browser: Browser,
    saved_runs: Callable[[str], SavedRun],
    viewport: dict[str, int],
    forced_colors: str,
    scale: str,
) -> None:
    run = saved_runs("mixed")
    with inspector(
        browser,
        run,
        overrides=model_label_overrides(HOSTILE_MODEL),
        viewport=viewport,
        reduced_motion="reduce",
        forced_colors=forced_colors,
    ) as session:
        page = session.page
        expect(page.locator("#bounded-cognition-panel")).to_be_visible()
        page.evaluate(f"() => {{ document.documentElement.style.fontSize = '{scale}'; }}")
        model = page.locator('#bounded-cognition-provenance [data-fact="model"] bdi')
        expect(model).to_have_text(HOSTILE_MODEL)
        assert model.get_attribute("dir") == "auto"
        expect(page.locator("#opportunity-scenario")).to_have_text(HOSTILE_SCENARIO)
        summary = page.locator("#bounded-signal-index > summary")
        summary.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#bounded-signal-index")).to_have_attribute("open", "")
        filter_select = page.locator("#bounded-signal-filter")
        filter_select.focus()
        page.keyboard.press("ArrowDown")
        expect(filter_select).to_have_value("provider")
        provider_rows = page.locator("#bounded-signal-list .bounded-signal-jump")
        assert provider_rows.count() == run.stored.cognition_evaluation.summary.outcomes.provider
        assert provider_rows.evaluate_all(
            "rows => rows.every((row) => row.dataset.source === 'provider')"
        )
        first = run.stored.cognition_evaluation.records[0]
        provider_rows.first.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "([minute, agent]) => state.minute === minute && state.selected === agent",
            arg=[first.model_minute, first.agent_id],
        )
        page.wait_for_function(
            "hash => document.activeElement && document.activeElement.dataset"
            " && document.activeElement.dataset.terminalRecordHash === hash",
            arg=first.record_hash,
        )
        assert page.evaluate("() => state.selectedEvidence && state.selectedEvidence.kind") == (
            "response"
        )
        # The jump starts the selected response at the top, so its trace begins in view.
        assert page.evaluate(
            """hash => {
              const card = document.querySelector(`[data-terminal-record-hash="${hash}"]`);
              const item = card.closest('.bounded-applied-item');
              const first = item.querySelector('.bounded-stage.stage-baseline');
              const cardRect = card.getBoundingClientRect();
              const stageRect = first.getBoundingClientRect();
              return cardRect.top >= -0.5 && stageRect.bottom <= window.innerHeight + 0.5;
            }""",
            first.record_hash,
        )
        layout = page.evaluate(
            """() => ({
              documentWidth: document.documentElement.scrollWidth,
              viewportWidth: window.innerWidth,
              outside: Array.from(document.querySelectorAll(
                '#bounded-cognition-panel *, .bounded-trace *, .bounded-dual-tracks *',
              )).filter((node) => {
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && (rect.left < -0.5 || rect.right > window.innerWidth + 0.5);
              }).map((node) => node.className).slice(0, 5),
              statuses: Array.from(document.querySelectorAll('.bounded-stage-status'))
                .every((node) => node.textContent.trim().length > 0),
              labelled: Array.from(document.querySelectorAll('.bounded-trace'))
                .every((node) => /^Bounded cognition trace for PERSON-\\d{3} at \\d\\d:\\d\\d$/
                  .test(node.getAttribute('aria-label'))),
              executable: document.querySelectorAll(
                '#bounded-cognition-panel img, #bounded-cognition-panel svg, '
                + '#bounded-cognition-panel script, .bounded-trace img, .bounded-trace script',
              ).length,
              pwned: window.inspectorPwned === undefined,
            })"""
        )
        assert layout == {
            "documentWidth": layout["viewportWidth"],
            "viewportWidth": layout["viewportWidth"],
            "outside": [],
            "statuses": True,
            "labelled": True,
            "executable": 0,
            "pwned": True,
        }
        assert session.page_errors == [] and session.console_errors == []


@pytest.mark.parametrize("server", ["workbench", "standalone"])
def test_v10_inspector_is_read_only_and_same_origin(
    browser: Browser, saved_runs: Callable[[str], SavedRun], server: Server
) -> None:
    run = saved_runs("provider")
    before = _artifact_hashes(run.directory)
    with inspector(browser, run, server=server) as session:
        for minute in response_minutes(run)[:3]:
            session.minute(minute)
        token = session.view_token()
        methods = {method for method, _url, _headers in session.requests}
        paths = {
            re.sub(r"\?.*$", "", url[len(session.base_url) :]) for _m, url, _h in session.requests
        }
        tokens = {
            headers.get("x-adlife-view-token")
            for _method, url, headers in session.requests
            if f"{session.base_url}/api/" in url
        }
        page_path, api_base = entry_path(server, run)
    assert methods == {"GET"}
    assert {
        page_path,
        "/static/app.js",
        "/static/app.css",
        "/static/app-cognition-v2.js",
        "/static/app-cognition-v2.css",
    } <= paths
    assert paths <= {
        page_path,
        "/static/app.js",
        "/static/app.css",
        "/static/app-cognition-v2.js",
        "/static/app-cognition-v2.css",
        *(
            f"{api_base}/{endpoint}"
            for endpoint in (
                "meta",
                "city",
                "agents",
                "workbench-input",
                "opportunity-summary",
                "attention-summary",
                "response-summary",
                "cognition-summary",
                "cognition-records",
                "spatial-metrics",
                "spatial-response-metrics",
                "timeline",
            )
        ),
    }
    assert f"{api_base}/cognition-records" in paths
    # The workbench pins one verified snapshot per page; the standalone viewer has one run.
    if server == "workbench":
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
        assert tokens == {token}
    else:
        assert token == ""
        assert tokens == {None}
    assert session.external == []
    assert _artifact_hashes(run.directory) == before


def _tamper_terminal_binding(document: dict[str, Any], _query: Mapping[str, str]) -> dict[str, Any]:
    hashes = [
        item["terminal_record_hash"]
        for item in document["response_page"]["items"]
        if item.get("terminal_record_hash")
    ]
    for item in document["response_page"]["items"]:
        if item.get("terminal_record_hash"):
            item["terminal_record_hash"] = "9" * 64 if len(hashes) == 1 else hashes[-1]
            break
    return document


@pytest.mark.parametrize(
    ("endpoint", "mutate", "message"),
    [
        (
            "meta",
            lambda document, _q: {**document, "bounded_cognition": False},
            "Saved bounded cognition run metadata is invalid",
        ),
        (
            "cognition-summary",
            lambda document, _q: {**document, "mode": "mock"},
            "Saved bounded cognition summary is invalid",
        ),
        (
            "cognition-records",
            lambda document, _q: {
                **document,
                "records": [
                    {**document["records"][0], "record_hash": "f" * 64},
                    *document["records"][1:],
                ],
            },
            "Saved bounded cognition record is invalid",
        ),
        (
            "response-summary",
            lambda document, _q: {**document, "cognition_input_sha256": "f" * 64},
            "Saved cognition evidence is invalid",
        ),
        (
            "workbench-input",
            lambda document, _q: {
                **document,
                "cognition": {**document["cognition"], "rule_state_noninterference": False},
            },
            "Frozen workbench input is invalid",
        ),
        (
            "workbench-input",
            lambda document, _q: {
                **document,
                "cognition": {
                    **document["cognition"],
                    "reservation_tokens_per_request": (
                        document["cognition"]["reservation_tokens_per_request"] + 1
                    ),
                },
            },
            "Frozen workbench input is invalid",
        ),
        (
            "workbench-input",
            lambda document, _q: {
                **document,
                "cognition": {
                    **document["cognition"],
                    "causal_contract": {
                        **document["cognition"]["causal_contract"],
                        "prompt_contract_text": (
                            document["cognition"]["causal_contract"]["prompt_contract_text"] + " "
                        ),
                    },
                },
            },
            "Saved cognition evidence is invalid",
        ),
        (
            "workbench-input",
            lambda document, _q: {
                **document,
                "cognition": {
                    **document["cognition"],
                    "causal_contract": {
                        **document["cognition"]["causal_contract"],
                        "prompt_contract_sha256": "a" * 64,
                    },
                },
            },
            "Saved cognition evidence is invalid",
        ),
    ],
    ids=[
        "meta",
        "summary",
        "records",
        "response-summary",
        "workbench-input",
        "workbench-input-reservation",
        "workbench-input-prompt-text",
        "workbench-input-prompt-hash",
    ],
)
def test_v10_refuses_tampered_projections_before_rendering(
    browser: Browser,
    saved_runs: Callable[[str], SavedRun],
    endpoint: str,
    mutate: Override,
    message: str,
) -> None:
    with inspector(browser, saved_runs("provider"), overrides={endpoint: mutate}) as session:
        page = session.page
        expect(page.locator("#error-banner")).to_contain_text(message)
        assert page.locator(".bounded-trace").count() == 0
        assert page.locator("#bounded-signal-list .bounded-signal-jump").count() == 0


def test_v10_cross_bound_response_page_keeps_the_last_committed_frame(
    browser: Browser, saved_runs: Callable[[str], SavedRun]
) -> None:
    run = saved_runs("provider")
    minute = response_minutes(run)[0]

    def tamper(document: dict[str, Any], query: Mapping[str, str]) -> dict[str, Any]:
        return (
            _tamper_terminal_binding(document, query)
            if int(query["minute"]) == minute
            else document
        )

    with inspector(browser, run, overrides={"timeline": tamper}) as session:
        page = session.page
        expect(page.locator("#bounded-cognition-panel")).to_be_visible()
        assert page.evaluate("minute => setMinute(minute)", minute) is False
        expect(page.locator("#error-banner")).to_contain_text(
            "Applied bounded cognition binding is invalid"
        )
        assert page.evaluate("() => state.minute") == 0
        assert page.locator(".bounded-trace").count() == 0


def test_v10_without_its_helper_asset_fails_closed(
    browser: Browser, saved_runs: Callable[[str], SavedRun]
) -> None:
    with inspector(browser, saved_runs("rules"), helper=False) as session:
        expect(session.page.locator("#error-banner")).to_contain_text(
            "Bounded cognition inspector assets are unavailable"
        )
        assert session.page.locator("#bounded-cognition-panel").count() == 0
        assert not any("/cognition-" in url for _method, url, _h in session.requests)


@pytest.mark.parametrize(
    ("name", "server"), [("provider", "workbench"), ("rules", "standalone"), ("mixed", "workbench")]
)
def test_v10_response_metrics_keep_the_rule_baseline_separate_from_cognition(
    browser: Browser, saved_runs: Callable[[str], SavedRun], name: str, server: Server
) -> None:
    run = saved_runs(name)
    with inspector(browser, run, server=server) as session:
        page = session.page
        expect(page.locator("#error-banner")).to_be_hidden()
        panel = page.locator("#response-metrics-panel")
        expect(panel).to_be_visible()
        expect(page.locator("#response-metrics-claim")).to_have_text(
            "Rule baseline · modeled response metrics · not observed outcomes"
        )
        state_rows = page.locator("#response-metrics-state-body th").all_inner_texts()
        assert state_rows == ["Brand sentiment", "Recall strength", "Purchase intention proxy"]
        cognition = page.locator("#bounded-cognition-metrics")
        expect(cognition).to_be_visible()
        expect(cognition.locator("h3")).to_have_text("Provider-dependent cognition · exploratory")
        expect(cognition.locator(".bounded-metrics-claim")).to_have_text(
            "Synthetic cognition-track measurements · not a campaign effect · no purchase quantity"
        )
        rows = cognition.locator("tbody tr").evaluate_all(
            "rows => rows.map((row) => row.dataset.metric)"
        )
        assert rows == [
            "funded-coverage",
            "mean-applied-sentiment",
            "mean-applied-recall",
            "synthetic-sentiment",
            "synthetic-recall",
        ]
        coverage = run.stored.cognition_evaluation.summary.coverage
        expect(cognition.locator('[data-metric="funded-coverage"] small')).to_have_text(
            f"{coverage.funded_request_count} funded / {coverage.eligible_response_count} eligible"
        )
        text = cognition.inner_text()
        assert not re.search(r"purchase(?! quantity)|intention|lift|effect estimate", text, re.I)
        families = cognition.locator(".bounded-category-counts > div").evaluate_all(
            "rows => rows.map((row) => row.dataset.family)"
        )
        assert families == ["affect", "relevance", "credibility", "salience"]
        # Category counts include fixed neutral vectors; the caption says so plainly.
        selected = coverage.selected_request_count
        expect(cognition.locator(".bounded-category-note")).to_have_text(
            f"Recorded categories across all {selected} selected "
            f"{'request' if selected == 1 else 'requests'}, including the fixed neutral vector "
            "of rules, fallback, and budget-skipped requests and answers later set aside as "
            "uncertain or safety-flagged."
        )
        assert session.page_errors == [] and session.console_errors == []


def _groups(run: SavedRun) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in run.stored.response_evaluation.records:
        current.append(item.model_dump(mode="json"))
        if item.event_type == "spatial.dual-state-updated":
            groups.append(current)
            current = []
    return groups


def test_v10_page_boundaries_may_split_but_never_reorder_a_canonical_group(
    browser: Browser, saved_runs: Callable[[str], SavedRun]
) -> None:
    run = saved_runs("mixed")
    groups = _groups(run)
    pair = (groups[0], groups[1])
    group = pair[0]
    applied, update = group[:-1], group[-1]
    other_update = pair[1][-1]
    cases = {
        "whole-groups": ([*group, *pair[1]], 0, None, True),
        "page-ends-before-update": (applied, 0, len(applied), True),
        "page-starts-at-update": ([update, *pair[1]], len(applied), None, True),
        "update-without-causes-on-first-page": ([update], 0, None, False),
        "update-before-its-response": ([update, *applied], 0, None, False),
        "trailing-response-on-last-page": (applied, 0, None, False),
        "response-bound-to-another-group": ([*applied, other_update], 0, None, False),
        "second-update-missing-causes": ([*group, pair[1][-1]], 0, None, False),
    }
    with inspector(browser, run) as session:
        outcomes = session.page.evaluate(
            """cases => Object.fromEntries(Object.entries(cases).map(([name, values]) => {
              const [items, offset, next] = values;
              try {
                validateBoundedPageGrammar({ items, offset, next_offset: next });
                return [name, true];
              } catch (_) {
                return [name, false];
              }
            }))""",
            {name: list(case[:3]) for name, case in cases.items()},
        )
    assert outcomes == {name: case[3] for name, case in cases.items()}


@pytest.mark.parametrize("server", ["workbench", "standalone"])
def test_v10_run_without_eligible_responses_renders_an_honest_empty_state(
    browser: Browser, saved_runs: Callable[[str], SavedRun], server: Server
) -> None:
    run = saved_runs("empty")
    coverage = run.stored.cognition_evaluation.summary.coverage
    assert coverage.eligible_response_count == coverage.selected_request_count == 0
    with inspector(browser, run, server=server) as session:
        page = session.page
        expect(page.locator("#bounded-cognition-panel")).to_be_visible()
        expect(page.locator("#error-banner")).to_be_hidden()
        expect(page.locator("#bounded-cognition-outcomes-title")).to_have_text(
            "No ad was noticed, so no response needed a signal"
        )
        assert page.locator("#bounded-cognition-outcome-list > div").count() == 0
        expect(page.locator("#bounded-signal-count")).to_have_text("0 of 0 requests")
        expect(page.locator("#bounded-cognition-shortcut")).to_contain_text(
            "0 saved requests · 0 eligible responses"
        )
        session.minute(600)
        assert page.locator(".bounded-trace, .bounded-applied-item").count() == 0
        expect(page.locator("#bounded-cognition-metrics")).to_be_visible()
        expect(
            page.locator('#bounded-cognition-metrics [data-metric="funded-coverage"] small')
        ).to_have_text("0 funded / 0 eligible")
        assert session.page_errors == [] and session.console_errors == []


@pytest.mark.parametrize("server", ["workbench", "standalone"])
def test_real_servers_serve_the_exact_inspector_assets_before_app_js(
    saved_runs: Callable[[str], SavedRun], server: Server
) -> None:
    run = saved_runs("rules")
    page_path, api_base = entry_path(server, run)
    with (
        served(server, run) as base_url,
        httpx.Client(base_url=base_url, trust_env=False) as client,
    ):
        page = client.get(page_path)
        assets = {name: client.get(f"/static/{name}") for name in ASSET_TYPES}
        unknown = client.get("/static/app-cognition-v3.js")
    assert page.status_code == 200
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert f'<meta name="adlife-api-base" content="{api_base}">' in page.text
    for name, response in assets.items():
        assert response.status_code == 200, f"expected 200, got {response.status_code} for {name}"
        assert response.headers["content-type"] == ASSET_TYPES[name]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.content == (STATIC / name).read_bytes()
    assert unknown.status_code == 404
    # The pure helper loads before app.js, and its stylesheet after the shared one.
    tags = re.findall(r'<(?:script src|link rel="stylesheet" href)="(/static/[^"]+)"', page.text)
    assert tags == [
        "/static/app.css",
        "/static/app-cognition-v2.css",
        "/static/app-cognition-v2.js",
        "/static/app.js",
    ]
    assert page.text.count("<script") == 2
    assert '<script src="/static/app-cognition-v2.js" defer></script>' in page.text


def _fetch_documents(
    server: Server, run: SavedRun, minute: int, agent: str
) -> dict[str, dict[str, Any]]:
    page_path, api_base = entry_path(server, run)
    timeline = (
        f"timeline?minute={minute}&agent_id={agent}&opportunity_offset=0&attention_offset=0"
        f"&response_offset=0&response_state_offset=0&limit=100"
    )
    endpoints = {
        "meta": "meta",
        "workbench-input": "workbench-input",
        "cognition-summary": "cognition-summary",
        "cognition-records": "cognition-records",
        "response-summary": "response-summary",
        "spatial-response-metrics": "spatial-response-metrics",
        "response-events": f"response-events?minute={minute}&offset=0&limit=100",
        "response-state": f"response-state?agent_id={agent}&offset=0&limit=100",
        "timeline": timeline,
    }
    # Loopback only: never route through a system or environment proxy.
    with (
        served(server, run) as base_url,
        httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client,
    ):
        page = client.get(page_path)
        assert page.status_code == 200
        match = re.search(r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]*)">', page.text)
        assert match is not None
        headers = {"X-AdLife-View-Token": match.group(1)} if match.group(1) else {}
        documents: dict[str, dict[str, Any]] = {}
        for key, suffix in endpoints.items():
            response = client.get(f"{api_base}/{suffix}", headers=headers)
            assert response.status_code == 200, (key, response.status_code)
            documents[key] = response.json()
    return documents


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("rules", "workbench"),
        ("mock", "standalone"),
        ("provider", "workbench"),
        ("provider", "standalone"),
        ("mixed", "workbench"),
    ],
)
def test_real_v10_projections_match_the_inspector_oracle(
    saved_runs: Callable[[str], SavedRun], name: str, server: Server
) -> None:
    """The documents the inspector consumes are exactly the C3 contract it validates."""
    run = saved_runs(name)
    target = applied_records(run)[0].baseline
    minute, agent = target.model_minute, target.agent_id
    actual = _fetch_documents(server, run, minute, agent)
    oracle = V10Projections(run.stored, metrics=True)
    for key in (
        "workbench-input",
        "cognition-summary",
        "cognition-records",
        "response-summary",
        "spatial-response-metrics",
    ):
        assert actual[key] == oracle.document(key, {}), key
    assert actual["response-events"] == oracle.response_page(minute, 0, 100)
    assert actual["response-state"] == oracle.state_page(agent, 0, 100)
    expected_timeline = oracle.document(
        "timeline", {"minute": str(minute), "agent_id": agent, "limit": "100"}
    )
    for key in ("schema_version", "minute", "agent_id", "response_page", "response_state_page"):
        assert actual["timeline"][key] == expected_timeline[key], key
    expected_meta = oracle.document("meta", {})
    for key in (
        "saved",
        "run_id",
        "run_schema_version",
        "bounded_cognition",
        "cognition_effect_scope",
        "workbench_input_available",
        "workbench_input_sha256",
        "spatial_opportunities",
        "spatial_attention",
        "spatial_response",
        "response_model_id",
        "spatial_response_metrics",
    ):
        assert actual["meta"][key] == expected_meta[key], key
    # No provider transport, credential slot, prompt text, or local path reaches a document.
    profile = run.stored.cognition_input.provider_profile
    encoded = json.dumps({key: actual[key] for key in actual if key != "workbench-input"})
    everything = json.dumps(actual, ensure_ascii=False)
    if profile is not None:
        assert profile.base_url not in everything
        assert profile.credential_slot_id not in everything
    assert run.stored.cognition_input.causal_contract.prompt_contract_text not in encoded
    for form in (str(run.root), json.dumps(str(run.root))[1:-1], run.root.as_posix()):
        assert form not in everything
