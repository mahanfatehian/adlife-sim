# Contributing to AdLife Lab

Thank you for considering a contribution. This is a research instrument with a strict
scientific posture, and four rules keep it trustworthy.

## 1. Fictional data only

Every persona, name, routine, interest, and campaign in this repository — and in any
contribution — must be **fictional and generated from templates**. The domain validators
enforce this in code: persona fields reject national identifiers, phone numbers, email
addresses, and free-form secrets. Do not add real people, real datasets, scraped
content, or any data that could identify a real person. If your contribution needs a
"realistic" population, generate one with the packaged templates.

## 2. Tests are required

Contributions are test-driven: a behavioural change lands with a test that **fails
before the change and passes after it**. The enforced gates are:

```bash
uv run pytest -q                      # the full suite
uv run ruff check src tests           # lint
uv run ruff format --check src tests  # formatting
uv run mypy src                       # strict typing
```

Determinism is part of correctness: if you touch the core, also run the suite under
varying `PYTHONHASHSEED` values to catch iteration-order leaks:

```bash
PYTHONHASHSEED=0 uv run pytest -q
PYTHONHASHSEED=12345 uv run pytest -q
```

Golden/fingerprint tests pin byte-identical output; if your change legitimately alters
recorded fingerprints, say so explicitly in your pull request and update them in the
same commit.

## 3. Preserve the disclosures

The scientific disclosure language — "synthetic and exploratory", "not a representative
survey", the report banner, the model card's external-validity status — is part of the
product, protected by tests. Do not weaken, reword, or remove it. If your change makes
an existing limitation worse or creates a new one, extend
`docs/methodology/limitations.md` in the same change.

## 4. Sign your work (DCO)

All commits must carry a **Developer Certificate of Origin** sign-off, certifying that
you have the right to submit the code under the project's AGPL-3.0-only license:

```
Signed-off-by: Your Name <your-email@example.com>
```

The easiest way is `git commit -s` (adds the trailer automatically with your configured
identity). Pull requests containing unsigned commits cannot be merged.

## Process

1. Fork / branch from `main`.
2. Make your change with tests (see rule 2).
3. Run all gates (rule 2) and update documentation when behaviour or claims change.
4. Open a pull request describing the *why*, not just the *what*.
5. Keep commits signed off (rule 4).

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the
[AGPL-3.0-only](LICENSE) terms of this repository, with the sign-off above certifying
your right to do so.
