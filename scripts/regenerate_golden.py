"""Regenerate the committed golden scenario evidence.

The ONLY sanctioned way to move the golden records. Run it after a deliberate,
reviewed behaviour change:

    uv run python scripts/regenerate_golden.py
    git diff tests/golden/scenarios   # review what moved

The script re-executes the two canonical runs and rewrites
``tests/golden/scenarios/<scenario>/record.json``. The diff you are reviewing is the
behaviour change, event by event.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from adlife.adapters.cognition.mock import MOCK_MODEL_ID, MockCognitionProvider  # noqa: E402
from adlife.adapters.output.jsonl import JsonlEventSink  # noqa: E402
from adlife.adapters.storage.sqlite_store import SQLiteRunStore  # noqa: E402
from adlife.core.ports.cognition import (  # noqa: E402
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner  # noqa: E402
from tests.builders import small_three_agent_scenario, strip_campaigns  # noqa: E402
from tests.conftest import (  # noqa: E402
    consumer_state,
    valid_campaign,
    valid_profile,
    valid_scenario,
)

SEED = 42
GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "scenarios"


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


def _canonical_scenario() -> tuple[object, bool]:
    profile = valid_profile.__wrapped__()
    campaign = valid_campaign.__wrapped__()
    state = consumer_state.__wrapped__(profile)
    base = valid_scenario.__wrapped__(profile, campaign, state)
    return base, True


async def _record(*, mock: bool) -> bytes:
    base, _ = _canonical_scenario()
    scenario = (
        small_three_agent_scenario(base)
        if mock
        else strip_campaigns(small_three_agent_scenario(base))
    )
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        run_id = "run-golden"
        store = SQLiteRunStore(root)
        sinks = [JsonlEventSink(root / f"{run_id}.jsonl")]
        if mock:
            port: object = _MockPort(seed=0)
            factory = lambda model: port  # noqa: E731
            fallback = lambda plan: None  # noqa: E731
            provider_name, model_id = "mock", MOCK_MODEL_ID
        else:
            from adlife.cli.cognition import RuleCognitionPort, rule_fallback_for

            factory = lambda model: RuleCognitionPort()  # noqa: E731
            fallback = rule_fallback_for
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
        stored = store.load_run(run_id)
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
        metrics = json.loads(store.metrics_json_path(run_id).read_text(encoding="utf-8"))
        record = {"events": events, "metrics": metrics}
        return (
            json.dumps(record, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
        )


async def main() -> int:
    for name, mock in (("rule-small", False), ("mock-small", True)):
        destination = GOLDEN_DIR / name / "record.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(await _record(mock=mock))
        print(f"regenerated {destination.relative_to(REPO_ROOT)}")
    return 0


from collections.abc import Sequence  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
