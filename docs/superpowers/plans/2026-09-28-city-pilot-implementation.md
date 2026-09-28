# City mobility pilot implementation plan

1. Add strict core city-pack models, canonical order/hash, graph validation, and
   regression tests for malformed data and order invariance. RED first.
2. Add deterministic directed shortest-path routing and day trace generation.
   Tests cover same-seed equality, input permutation, route membership, one-way
   enforcement, weekends, agent bounds, and invalid/disconnected graph behavior.
   RED first.
3. Add a local JSON city-pack loader with file-size and UTF-8 limits, plus a
   small fictional offline demo pack. Tests reject malformed and oversized input.
4. Add FastAPI read-only city endpoints and CLI startup command. Tests cover
   endpoint schemas, no arbitrary file reads, default loopback binding, and static
   asset availability from installed resources. Add dependencies with `uv`.
5. Build a vanilla responsive city dashboard with time scrub, day/night styling,
   visible attribution and scientific disclosure. Test API/UI asset contracts.
6. Update README and architecture/CLI docs with honest capability boundaries.
   Run narrow tests after each behavior change, then Ruff, mypy, full pytest,
   multiple hash seeds, coverage, wheel build and installed-wheel smoke.
7. Inspect diff and Git status; commit on `main` with existing repository identity.
   Do not push or modify `defense-ready`.
