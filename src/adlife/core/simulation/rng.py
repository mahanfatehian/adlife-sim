import random
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class RandomOracle:
    root_seed: int

    def _seed(
        self,
        namespace: str,
        agent_id: str,
        tick: int,
        decision_index: int,
    ) -> int:
        material = f"{self.root_seed}|{namespace}|{agent_id}|{tick}|{decision_index}"
        return int.from_bytes(sha256(material.encode("utf-8")).digest()[:8], "big")

    def uniform(
        self,
        namespace: str,
        agent_id: str,
        tick: int,
        decision_index: int,
    ) -> float:
        return random.Random(self._seed(namespace, agent_id, tick, decision_index)).random()


__all__ = ["RandomOracle"]
