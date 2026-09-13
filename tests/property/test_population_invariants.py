import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.simulation.population import generate_population, generate_relationships


@pytest.mark.parametrize("size", range(1, 31))
@settings(max_examples=5, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=2**63 - 1),
    locale=st.sampled_from(("fa-IR", "en-US")),
)
def test_profiles_remain_valid_for_every_supported_population_size(
    size: int,
    seed: int,
    locale: str,
) -> None:
    profiles = generate_population(size, seed, locale)

    agent_ids = tuple(profile.agent_id for profile in profiles)
    assert len(profiles) == size
    assert len(agent_ids) == len(set(agent_ids))
    assert all(18 <= profile.age <= 65 for profile in profiles)
    assert all(profile.fictional is True for profile in profiles)
    assert all(
        0 <= value <= 1
        for profile in profiles
        for value in profile.traits.model_dump(mode="python").values()
    )


@pytest.mark.parametrize("size", range(1, 31))
@settings(max_examples=5, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**63 - 1))
def test_relationship_invariants_hold_for_every_supported_population_size(
    size: int,
    seed: int,
) -> None:
    profiles = generate_population(size, seed, "fa-IR")
    edges = generate_relationships(profiles, seed)
    agent_ids = {profile.agent_id for profile in profiles}
    degree = dict.fromkeys(agent_ids, 0)
    adjacency = {agent_id: set() for agent_id in agent_ids}
    pairs: set[tuple[str, str]] = set()

    for edge in edges:
        pair = (edge.source_id, edge.target_id)
        assert edge.source_id < edge.target_id
        assert pair not in pairs
        assert 0.2 <= edge.strength <= 1.0
        pairs.add(pair)
        degree[edge.source_id] += 1
        degree[edge.target_id] += 1
        adjacency[edge.source_id].add(edge.target_id)
        adjacency[edge.target_id].add(edge.source_id)

    assert max(degree.values()) <= 8
    if size == 1:
        assert edges == ()
    else:
        assert min(degree.values()) >= 1

    if size >= 3:
        visited: set[str] = set()
        pending = [min(agent_ids)]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency[current] - visited)
        assert visited == agent_ids

    mean_degree = 2 * len(edges) / size
    if size == 3:
        assert mean_degree == 2
    elif size >= 4:
        assert 3 <= mean_degree <= 5
