"""The committed golden scenarios: run, normalize, compare against evidence.

The ``rule-small`` and ``mock-small`` directories hold the normalized event stream and
metrics of one canonical run each, committed as evidence. Every test run re-executes
that run and requires the normalized record to match byte-for-byte; the
``scripts/regenerate_golden.py`` script is the only sanctioned way to move the
evidence, so a behaviour change is a reviewed, deliberate commit and never a quiet
drift. What is compared is deliberately normalized: run identifiers and event
identifiers name the run that produced them, so the evidence pins BEHAVIOUR - every
event, in order, with its payload - not incidental labels.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from adlife.adapters.cognition.mock import MOCK_MODEL_ID, MockCognitionProvider
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from tests.builders import small_three_agent_scenario

GOLDEN_DIR = Path(__file__).parent / "scenarios"
SEED = 42


class _MockPort:
    def __init__(self, seed: int) -> None:
        self.provider = MockCognitionProvider(seed=seed)

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        del fallback_provider
        return {request.request_id: await self.provider.answer(request) for request in requests}

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


def _rule_port():
    from adlife.cli.cognition import RuleCognitionPort, rule_fallback_for

    return RuleCognitionPort(), rule_fallback_for


async def _drive(scenario: Scenario, root: Path, *, mock: bool) -> tuple[SQLiteRunStore, Path]:
    run_id = "run-golden"
    store = SQLiteRunStore(root)
    sinks = [JsonlEventSink(root / f"{run_id}.jsonl")]
    if mock:
        port: object = _MockPort(seed=0)
        factory = lambda model: port  # noqa: E731
        fallback = lambda plan: None  # noqa: E731
        provider_name, model_id = "mock", MOCK_MODEL_ID
    else:
        rule_port, rule_fallback = _rule_port()
        factory = lambda model: rule_port  # noqa: E731
        fallback = rule_fallback
        provider_name, model_id = "rules", "rule-baseline"
    runner = SimulationRunner(
        cognition_factory=factory,
        fallback_provider_factory=fallback,
        identity=RunIdentity.for_project(
            project_root=root,
            provider=provider_name,
            model_id=model_id,
            overrides={"git_sha": "uncommitted", "platform": "golden-scenario-platform"},
        ),
    )
    await runner.run(
        scenario,
        seed=SEED,
        store=store,
        sinks=sinks,
        run_id=run_id,
        provider_name=provider_name,
        model_id=model_id,
    )
    return store, root / f"{run_id}.jsonl"


def _normalized_record(store: SQLiteRunStore, events_path: Path) -> bytes:
    """The evidence record: every event's behaviour plus the metrics, canonicalized.

    Event and run identifiers are run-local labels, so they are normalized away;
    everything a behaviour change could move - order, type, agent, campaign, channel,
    payload, causes - stays.
    """
    stored = store.load_run("run-golden")
    events = [
        {
            "sequence": event.sequence,
            "event_type": event.event_type.value,
            "simulated_minute": event.simulated_minute,
            "agent_id": event.agent_id,
            "campaign_id": event.campaign_id,
            "channel": event.channel,
            "payload": json.loads(json.dumps(dict(event.payload), sort_keys=True)),
            "caused_by": list(event.caused_by_event_ids),
        }
        for event in stored.events
    ]
    metrics = json.loads(store.metrics_json_path("run-golden").read_text(encoding="utf-8"))
    record = {"events": events, "metrics": metrics}
    return json.dumps(record, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"


async def test_rule_small_matches_committed_evidence(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    from tests.builders import strip_campaigns

    scenario = strip_campaigns(small_three_agent_scenario(valid_scenario))
    store, events_path = await _drive(scenario, tmp_path, mock=False)
    actual = _normalized_record(store, events_path)

    evidence = GOLDEN_DIR / "rule-small" / "record.json"
    if not evidence.is_file():  # pragma: no cover - regenerate script writes it
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(actual)
    expected = evidence.read_bytes()
    # .gitattributes pins the record to LF, but a checkout made before that pin may
    # still hold CRLF; the comparison is about BEHAVIOUR, not a checkout artifact.
    expected = expected.replace(b"\r\n", b"\n")
    assert actual == expected, (
        "the rule-small golden record changed; if this change is intended, run "
        "`uv run python scripts/regenerate_golden.py` and review the diff"
    )


async def test_mock_small_matches_committed_evidence(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    store, events_path = await _drive(scenario, tmp_path, mock=True)
    actual = _normalized_record(store, events_path)

    evidence = GOLDEN_DIR / "mock-small" / "record.json"
    if not evidence.is_file():  # pragma: no cover - regenerate script writes it
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(actual)
    expected = evidence.read_bytes()
    expected = expected.replace(b"\r\n", b"\n")
    assert actual == expected, (
        "the mock-small golden record changed; if this change is intended, run "
        "`uv run python scripts/regenerate_golden.py` and review the diff"
    )
