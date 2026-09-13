from adlife.core.simulation.rng import RandomOracle


def test_random_oracle_is_keyed_not_sequence_dependent() -> None:
    oracle = RandomOracle(42)
    first = oracle.uniform("notice", "person-001", 15, 0)
    oracle.uniform("unrelated", "person-002", 30, 0)
    second = oracle.uniform("notice", "person-001", 15, 0)

    assert first == second


def test_different_key_changes_draw() -> None:
    oracle = RandomOracle(42)

    assert oracle.uniform("notice", "person-001", 15, 0) != oracle.uniform(
        "notice", "person-001", 30, 0
    )


def test_random_oracle_matches_fixed_sha256_stream() -> None:
    oracle = RandomOracle(42)

    assert oracle.uniform("notice", "person-001", 15, 0) == 0.04571242994378566
