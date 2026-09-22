<!--
  Thank you for contributing! CONTRIBUTING.md is the contract; the checklist below
  is its quick form. All commits must carry a DCO sign-off
  (`git commit -s`: "Signed-off-by: Your Name <you@example.com>").
-->

## Why

<!-- What does this change make true for a user or researcher? Not just *what* changed. -->

## How

<!-- The approach, and any trade-offs worth recording. -->

## Checklist

- [ ] Tests: a test fails before this change and passes after it (test-driven).
- [ ] `uv run pytest -q` passes.
- [ ] `uv run ruff check src tests` and `uv run ruff format --check src tests` pass.
- [ ] `uv run mypy src` passes.
- [ ] All data in this change is fictional and generated from templates (no real persons).
- [ ] Scientific disclosure text is preserved, not weakened.
- [ ] If a limitation changed, `docs/methodology/limitations.md` was updated.
- [ ] Commits carry `Signed-off-by` (DCO).

## Determinism check (core changes only)

- [ ] Suite run under two `PYTHONHASHSEED` values with identical results.
- [ ] Golden/fingerprint changes are intentional and explained.
