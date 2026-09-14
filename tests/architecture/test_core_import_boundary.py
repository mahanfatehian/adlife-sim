"""The core/adapter import boundary, enforced by walking the source rather than by prose.

The binding constraint is that ``src/adlife/core/**`` is the pure simulation: it may not
import a CLI framework, a TUI framework, a database driver, a plotting library, an HTTP
client, a model-provider SDK, or either of the outer layers (``adlife.cli``,
``adlife.adapters``) that depend on it. Task 9 is the commit that first creates both sides
of that boundary, so this is where the seam belongs.

The check parses every core module with :mod:`ast` instead of grepping, so a boundary
break cannot be hidden inside a string, a comment or an unusual import spelling, and the
dynamic-import escape hatches (``importlib.import_module``, ``__import__``) are refused in
core outright rather than inspected for their argument.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import adlife

CORE = Path(adlife.__file__).resolve().parent / "core"

FORBIDDEN_ROOTS: frozenset[str] = frozenset(
    {
        # The outer layers of this repository.
        "adlife.adapters",
        "adlife.cli",
        "adlife.tui",
        # CLI, TUI, storage, plotting and transport.
        "typer",
        "click",
        "textual",
        "rich",
        "sqlite3",
        "sqlalchemy",
        "plotly",
        "matplotlib",
        "httpx",
        "requests",
        "aiohttp",
        "urllib.request",
        "http.client",
        "socket",
        "jinja2",
        # Model-provider SDKs.
        "openai",
        "anthropic",
        "cohere",
        "mistralai",
        "google.generativeai",
        "ollama",
        "litellm",
        "transformers",
        "llama_cpp",
    }
)
"""Every name a pure simulation core must never reach for, prefix-matched."""

_DYNAMIC_IMPORTS: frozenset[str] = frozenset({"import_module", "__import__"})


def _core_modules() -> list[Path]:
    return sorted(path for path in CORE.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_names(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Yield every module name an import statement in this tree binds."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, node.lineno
            for alias in node.names:
                yield f"{node.module}.{alias.name}", node.lineno


def _is_forbidden(module: str) -> bool:
    return any(module == root or module.startswith(f"{root}.") for root in FORBIDDEN_ROOTS)


def test_the_core_package_is_present_and_non_trivial() -> None:
    """Guard the guard: an empty or mislocated scan would pass vacuously."""
    modules = _core_modules()
    assert CORE.is_dir()
    assert len(modules) >= 10


@pytest.mark.parametrize("module_path", _core_modules(), ids=lambda path: path.name)
def test_a_core_module_imports_nothing_outside_the_simulation_core(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    offenders = [
        f"{module_path.name}:{line} imports {name}"
        for name, line in _imported_names(tree)
        if _is_forbidden(name)
    ]
    assert not offenders, "the simulation core reached outside its boundary: " + "; ".join(
        offenders
    )


@pytest.mark.parametrize("module_path", _core_modules(), ids=lambda path: path.name)
def test_a_core_module_never_imports_dynamically(module_path: Path) -> None:
    """A dynamic import would make the boundary unverifiable, so core may not use one."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else target.id
            if isinstance(target, ast.Name)
            else ""
        )
        if name in _DYNAMIC_IMPORTS:
            offenders.append(f"{module_path.name}:{node.lineno} calls {name}")
    assert not offenders, "the simulation core used a dynamic import: " + "; ".join(offenders)


def test_the_boundary_check_actually_detects_a_violation(tmp_path: Path) -> None:
    """Prove the detector is not vacuous by running it over a deliberate violation."""
    offender = tmp_path / "leaky.py"
    offender.write_text(
        "import httpx\nfrom adlife.adapters.cognition import mock\nimport json\n",
        encoding="utf-8",
    )
    tree = ast.parse(offender.read_text(encoding="utf-8"), filename=str(offender))
    found = {name for name, _ in _imported_names(tree) if _is_forbidden(name)}
    assert "httpx" in found
    assert "adlife.adapters.cognition" in found
    assert not _is_forbidden("json")
    assert not _is_forbidden("adlife.core.domain.person")


def test_the_adapter_layer_is_where_the_forbidden_imports_are_allowed_to_live() -> None:
    """The boundary is a boundary, not a ban: the adapters may import what core may not."""
    adapters = Path(adlife.__file__).resolve().parent / "adapters"
    assert adapters.is_dir()
    assert not any(str(path).startswith(str(CORE)) for path in adapters.rglob("*.py"))
