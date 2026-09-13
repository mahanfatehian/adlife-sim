# Repository Instructions

- Read `docs/superpowers/specs/2026-09-13-adlife-cli-simulator-design.md` and
  `docs/superpowers/plans/2026-09-13-adlife-cli-simulator-implementation.md` before making
  changes.
- Use `uv` for Python versions, dependency management, and project commands.
- Keep `src/adlife/core` adapter-free: it must not import Typer, Textual, SQLite, Plotly, or
  provider-specific adapters.
- Follow test-driven development and run the relevant tests and checks before claiming work
  is complete.
- Never add a Git remote or push without explicit authorization from the repository owner.
