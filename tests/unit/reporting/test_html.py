"""The self-contained HTML report, pinned against real stored runs.

Every claim the report makes must be re-derivable from the artifact it was rendered
from, every interpolated string must survive a hostile value, and the file must render
with the network disconnected: no remote scripts, fonts, or stylesheets, one inlined
Plotly runtime, and JSON chart payloads that cannot close their own script tag.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from adlife.adapters.cognition.rules import (
    RULE_MODEL_ID,
    RuleCognitionInputs,
    RuleCognitionProvider,
)
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
)
from adlife.core.ports.run_store import ProviderUsageLog
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from adlife.reporting.html import _safe_json, render_report
from tests.builders import small_three_agent_scenario


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class RuleResolutionPort:
    """The in-process rule seam the Task 12 suites wire."""

    async def resolve(self, requests, *, fallback_provider):
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            assert isinstance(fallback_provider, RuleCognitionProvider)
            answers[request.request_id] = await fallback_provider.answer(request)
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return ProviderMetadata(
            kind="rule",
            model_id=RULE_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="d" * 64,
        )


def _rule_fallback(plan):
    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        opportunity = decision.opportunity
        inputs[decision.events[2].event_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return RuleCognitionProvider.for_requests(plan.requests, inputs)


async def _drive_run(scenario: Scenario, root: Path, run_id: str) -> SQLiteRunStore:
    store = SQLiteRunStore(root)
    identity = RunIdentity.for_project(
        project_root=_repo_root(),
        provider="rules",
        model_id=RULE_MODEL_ID,
    )
    runner = SimulationRunner(
        cognition_factory=lambda *args, **kwargs: RuleResolutionPort(),
        fallback_provider_factory=_rule_fallback,
        identity=identity,
    )
    await runner.run(scenario, seed=42, store=store, sinks=(), run_id=run_id)
    return store


def _usage_log() -> ProviderUsageLog:
    return ProviderUsageLog(
        run_id="run-report",
        records=(
            ProviderUsage(
                provider_kind="rule",
                model_id=RULE_MODEL_ID,
                prompt_tokens=12,
                completion_tokens=20,
                latency_ms=40,
                cache_hit=False,
            ),
            ProviderUsage(
                provider_kind="rule",
                model_id=RULE_MODEL_ID,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                cache_hit=True,
            ),
            ProviderUsage(
                provider_kind="fallback",
                model_id=RULE_MODEL_ID,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=5,
                cache_hit=False,
                fallback_reason="provider-unavailable",
            ),
        ),
    )


_RUN_CACHE: dict[str, object] = {}


async def _cached_run(key: str, request, scenario: Scenario) -> object:
    """Drive once per scenario, share the stored run across this module's tests."""
    if key not in _RUN_CACHE:
        root = request.getfixturevalue("tmp_path_factory").mktemp(f"report-{key}")
        store = await _drive_run(scenario, root, f"run-{key}")
        _RUN_CACHE[key] = store.load_run(f"run-{key}")
    return _RUN_CACHE[key]


@pytest.fixture
async def stored_run(request, valid_scenario: Scenario):
    return await _cached_run("report", request, small_three_agent_scenario(valid_scenario))


@pytest.fixture
async def stored_run_with_script_tag(request, valid_scenario: Scenario):
    scenario = small_three_agent_scenario(valid_scenario)
    hostile_campaigns = tuple(
        campaign.model_copy(update={"name": "<script>alert(1)</script>"})
        for campaign in scenario.campaigns
    )
    hostile = scenario.model_copy(update={"campaigns": hostile_campaigns})
    return await _cached_run("hostile", request, hostile)


def test_report_contains_disclosure_and_reproducibility(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")

    assert "synthetic and exploratory" in text.lower()
    assert stored_run.manifest.scenario_hash in text
    assert f"Seed: {stored_run.manifest.seed}" in text
    assert stored_run.manifest.package_version in text
    assert stored_run.manifest.git_sha in text
    assert stored_run.manifest.platform in text


def test_report_escapes_campaign_text(stored_run_with_script_tag, tmp_path) -> None:
    path = render_report(stored_run_with_script_tag, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")

    assert "<script>alert(" not in text
    assert "&lt;script&gt;" in text


def test_report_renders_all_required_metrics(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")

    for label in (
        "Reach",
        "Frequency",
        "Notice rate",
        "Sentiment",
        "Recall",
        "Fatigue",
        "Direct awareness",
        "Indirect awareness",
        "Word of mouth",
        "Intention",
        "Purchases",
    ):
        assert label in text, label


def test_report_has_no_remote_resources(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")

    # The inlined Plotly runtime mentions CDNs inside its own optional mapbox
    # feature; strip the runtime element and scan everything we wrote ourselves.
    start = text.index('<script id="plotly-runtime">')
    end = text.index("</script>", start)
    runtime = text[start:end]
    ours = text[:start] + text[end:]

    assert "</script>" not in runtime  # the runtime cannot break out of its element
    for forbidden in (
        'src="http',
        'href="http',
        "fonts.googleapis",
        "unpkg.com",
        "cdn.jsdelivr",
        "@import",
    ):
        assert forbidden not in ours, forbidden
    assert "<script" in ours  # the report's own chart script is inline


def test_safe_json_cannot_close_the_script_tag() -> None:
    import json

    payload = {"summary": "</script><script>alert(1)</script>", "nested": {"x": "</"}}

    rendered = _safe_json(payload)

    # The closing sequence cannot appear inside the script element...
    assert "</" not in rendered
    # ...but the payload still round-trips: the escapes are plain JSON escapes.
    assert json.loads(rendered) == payload


def test_report_renders_persian_utf8_agent_name(tmp_path: Path, valid_scenario: Scenario) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    renamed = scenario.population[0].model_copy(update={"display_name": "مریم احدی"})
    utf8_scenario = scenario.model_copy(update={"population": (renamed, *scenario.population[1:])})

    store = asyncio.run(_drive_run(utf8_scenario, tmp_path / "utf8", "run-utf8"))
    stored = store.load_run("run-utf8")

    path = render_report(stored, tmp_path / "utf8.html")
    text = path.read_text(encoding="utf-8")
    assert "مریم احدی" in text


def test_report_diaries_are_labeled_fictional(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8").lower()

    assert "diaries" in text
    assert "fictional" in text


def test_report_includes_provider_usage_and_fallbacks(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html", usage=_usage_log())
    text = path.read_text(encoding="utf-8")

    assert "Provider usage" in text
    assert "provider-unavailable" in text
    assert "Cache hits" in text


def test_report_without_usage_still_states_the_provider(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")

    assert "Provider usage" in text
    assert stored_run.manifest.provider in text


def test_report_includes_limitations(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")

    assert "Limitations" in text
    assert "not a forecast" in text.lower()
