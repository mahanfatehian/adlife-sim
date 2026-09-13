from __future__ import annotations

from typing import TYPE_CHECKING

import mesa  # type: ignore[import-untyped]  # Mesa does not publish a py.typed marker.

from adlife.core.domain.person import PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation.movement import MovementIntent, Snapshot
from adlife.core.simulation.rng import RandomOracle
from adlife.core.simulation.routines import routine_at

if TYPE_CHECKING:
    from adlife.core.simulation.engine import AdLifeModel


class ConsumerAgent(mesa.Agent):  # type: ignore[misc]
    def __init__(
        self,
        model: AdLifeModel,
        *,
        profile: PersonProfile,
        state: ConsumerState,
        oracle: RandomOracle,
    ) -> None:
        super().__init__(model)
        self.unique_id = profile.agent_id
        self._profile = profile
        self._state = state
        self._oracle = oracle

    @property
    def profile(self) -> PersonProfile:
        return self._profile

    @property
    def state(self) -> ConsumerState:
        return self._state

    def plan_movement(self, snapshot: Snapshot) -> MovementIntent:
        try:
            profile, state = snapshot.agents[self.profile.agent_id]
        except KeyError as error:
            raise ValueError(f"agent {self.profile.agent_id} is missing from snapshot") from error
        block = routine_at(profile, snapshot.simulated_minute, self._oracle)
        return MovementIntent(
            agent_id=profile.agent_id,
            from_zone=state.location,
            to_zone=block.zone_id,
            activity=block.activity,
            route_id=block.route_id,
        )

    def plan(self, snapshot: Snapshot) -> MovementIntent:
        return self.plan_movement(snapshot)

    def _replace_state(self, state: ConsumerState) -> None:
        if state.agent_id != self.profile.agent_id:
            raise ValueError("state agent_id does not match consumer agent")
        self._state = state


__all__ = ["ConsumerAgent"]
