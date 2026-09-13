from __future__ import annotations

import json
import re
import warnings
from collections.abc import Mapping
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import mesa  # type: ignore[import-untyped]  # Mesa does not publish a py.typed marker.
from pydantic import BaseModel, TypeAdapter

from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation.clock import SimClock
from adlife.core.simulation.rng import RandomOracle

if TYPE_CHECKING:
    from adlife.core.simulation.agent import ConsumerAgent
    from adlife.core.simulation.movement import Snapshot, TickOutcome, TickPlan

_JSON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)
_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _json_fallback(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonicalize_dump(source: object, dumped: object) -> object:
    if isinstance(source, BaseModel):
        if not isinstance(dumped, Mapping):
            raise TypeError("Pydantic model did not produce a JSON object")
        fields = type(source).model_fields
        return {
            key: _canonicalize_dump(getattr(source, key), item) if key in fields else item
            for key, item in dumped.items()
        }

    if isinstance(source, Mapping):
        if not isinstance(dumped, Mapping):
            raise TypeError("mapping did not produce a JSON object")
        canonical: dict[str, object] = {}
        for key, item in source.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            canonical[key] = _canonicalize_dump(item, dumped[key])
        return canonical

    if isinstance(source, (set, frozenset)):
        if not isinstance(dumped, list):
            raise TypeError("set did not produce a JSON array")
        items = [
            _canonicalize_dump(item, dumped_item)
            for item, dumped_item in zip(source, dumped, strict=True)
        ]
        return sorted(items, key=_canonical_json)

    if isinstance(source, (list, tuple)):
        if not isinstance(dumped, list):
            raise TypeError("sequence did not produce a JSON array")
        return [
            _canonicalize_dump(item, dumped_item)
            for item, dumped_item in zip(source, dumped, strict=True)
        ]

    return dumped


def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str:
    if isinstance(value, BaseModel):
        dumped: object = value.model_dump(mode="json")
    else:
        dumped = _JSON_ADAPTER.dump_python(value, mode="json", fallback=_json_fallback)
    canonical = _canonical_json(_canonicalize_dump(value, dumped)).encode("utf-8")
    return sha256(canonical).hexdigest()


def stable_event_id(run_id: str, sequence: int) -> str:
    return f"{run_id}:event-{sequence:08d}"


class StaleTickPlan(RuntimeError):
    """Raised when model state no longer matches a planned snapshot."""


class UnboundRun(RuntimeError):
    """Raised when a commit has no persistent run identity and sequence."""


class RunAlreadyBound(RuntimeError):
    """Raised when replacing an existing event-stream binding is attempted."""


class AdLifeModel(mesa.Model):  # type: ignore[misc]
    def __init__(
        self,
        *,
        scenario: Scenario,
        seed: int,
        run_id: str | None = None,
    ) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="The use of the `seed` keyword argument is deprecated",
                category=FutureWarning,
            )
            super().__init__(seed=seed)

        from adlife.core.simulation.agent import ConsumerAgent

        self._domain_scenario = scenario
        self.clock = SimClock(scenario.days * 1440, scenario.tick_minutes)
        self.oracle = RandomOracle(seed)
        self._run_id: str | None = None
        self._next_event_sequence = 0
        self._snapshot_version = 0
        states_by_id = {state.agent_id: state for state in scenario.initial_states}
        self.agent_by_id: dict[str, ConsumerAgent] = {}
        for profile in sorted(scenario.population, key=lambda item: item.agent_id):
            agent = ConsumerAgent(
                self,
                profile=profile,
                state=states_by_id[profile.agent_id],
                oracle=self.oracle,
            )
            self.agent_by_id[profile.agent_id] = agent
        if run_id is not None:
            self._bind_run(run_id, next_event_sequence=0, stale_existing_plans=False)

    def bind_run(self, run_id: str, *, next_event_sequence: int) -> None:
        """Bind the one persistent event stream before the first commit."""
        self._bind_run(
            run_id,
            next_event_sequence=next_event_sequence,
            stale_existing_plans=True,
        )

    def _bind_run(
        self,
        run_id: str,
        *,
        next_event_sequence: int,
        stale_existing_plans: bool,
    ) -> None:
        if self._run_id is not None:
            raise RunAlreadyBound("model event stream is already bound")
        if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ValueError("run_id must be a lowercase slug of at most 40 characters")
        if type(next_event_sequence) is not int or next_event_sequence < 0:
            raise ValueError("next_event_sequence must be a nonnegative integer")

        self._run_id = run_id
        self._next_event_sequence = next_event_sequence
        if stale_existing_plans:
            self._snapshot_version += 1

    def snapshot(self) -> Snapshot:
        from adlife.core.simulation.movement import Snapshot

        return Snapshot(
            agents={
                agent_id: (agent.profile, agent.state)
                for agent_id, agent in self.agent_by_id.items()
            },
            simulated_minute=self.clock.current_minute,
            run_id=self._run_id,
            next_event_sequence=self._next_event_sequence,
            version=self._snapshot_version,
        )

    def plan_tick(self) -> TickPlan:
        from adlife.core.simulation.movement import TickPlan

        snapshot = self.snapshot()
        intents = tuple(
            self.agent_by_id[agent_id].plan(snapshot) for agent_id in sorted(self.agent_by_id)
        )
        return TickPlan(snapshot=snapshot, intents=intents)

    def commit_tick(
        self,
        plan: TickPlan,
        cognition: Mapping[str, object],
    ) -> TickOutcome:
        from adlife.core.simulation.movement import InvalidTickPlan, TickPlan

        if self._run_id is None:
            raise UnboundRun("model event stream is unbound; call bind_run before commit_tick")
        if not isinstance(plan, TickPlan):
            raise TypeError("plan must be a TickPlan")
        checked_plan = TickPlan(snapshot=plan.snapshot, intents=plan.intents)
        if tuple(checked_plan.snapshot.agents) != tuple(self.agent_by_id):
            raise InvalidTickPlan("plan agent IDs must exactly match model agent IDs")
        if checked_plan.snapshot.fingerprint != self.snapshot().fingerprint:
            raise StaleTickPlan("state changed between plan and commit")
        return self._commit_in_stable_order(checked_plan, cognition)

    def _commit_in_stable_order(
        self,
        plan: TickPlan,
        cognition: Mapping[str, object],
    ) -> TickOutcome:
        from adlife.core.simulation.movement import Snapshot, TickOutcome, _resolve_movement

        del cognition
        resolution = _resolve_movement(
            plan.intents,
            self._domain_scenario.world,
            plan.snapshot,
        )
        successor_states: dict[str, ConsumerState] = {}
        for transition in resolution.transitions:
            agent = self.agent_by_id[transition.agent_id]
            successor_states[transition.agent_id] = agent.state.model_copy(
                update={
                    "location": transition.to_zone,
                    "activity": transition.to_activity,
                    "current_route_id": transition.to_route_id,
                }
            )

        next_event_sequence = self._next_event_sequence + len(resolution.events)
        next_snapshot_version = self._snapshot_version + 1
        outcome = TickOutcome(
            snapshot=Snapshot(
                agents={
                    agent_id: (agent.profile, successor_states[agent_id])
                    for agent_id, agent in self.agent_by_id.items()
                },
                simulated_minute=self.clock.current_minute,
                run_id=self._run_id,
                next_event_sequence=next_event_sequence,
                version=next_snapshot_version,
            ),
            events=resolution.events,
        )

        for agent_id, successor_state in successor_states.items():
            self.agent_by_id[agent_id]._replace_state(successor_state)
        self._next_event_sequence = next_event_sequence
        self._snapshot_version = next_snapshot_version
        return outcome


__all__ = [
    "AdLifeModel",
    "RunAlreadyBound",
    "StaleTickPlan",
    "UnboundRun",
    "canonical_sha256",
    "stable_event_id",
]
