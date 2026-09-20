"""`adlife doctor`: the pre-flight report a new installation runs first."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Annotated

import typer

from adlife import __version__
from adlife.cli.errors import command_boundary, output_format
from adlife.cli.output import emit_json


def _check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail}


def _sqlite_json1_available() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        try:
            row = connection.execute("SELECT json('{\"a\": 1}')").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return row is not None and "a" in str(row[0])


def _providers_available() -> tuple[bool, str]:
    try:
        from adlife.adapters.cognition.mock import MockCognitionProvider
        from adlife.adapters.cognition.rules import RuleCognitionProvider

        MockCognitionProvider(seed=0)
        RuleCognitionProvider({})
        return True, "rule and mock providers construct offline"
    except Exception as error:
        return False, f"provider construction failed: {type(error).__name__}"


def _probe_provider(base_url: str) -> tuple[bool, str]:
    """A two-second health probe of a configured endpoint, with the URL redacted."""
    from urllib.parse import urlsplit, urlunsplit

    import httpx

    parts = urlsplit(base_url)
    redacted = urlunsplit(
        (
            parts.scheme,
            "redacted@" + parts.netloc if "@" in parts.netloc else parts.netloc,
            parts.path,
            "",
            "",
        )
    )
    try:
        response = httpx.get(base_url, timeout=2.0)
        return response.status_code < 500, f"probe of {redacted} returned {response.status_code}"
    except Exception as error:
        return False, f"probe of {redacted} failed: {type(error).__name__}"


@command_boundary
def command(
    path: Annotated[
        Path | None,
        typer.Argument(help="An optional study directory to check the schema of."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Perform no network access at all."),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option("--no-color", help="Disable colored output."),
    ] = False,
) -> None:
    """Report the installation's readiness: versions, resources, providers, storage."""
    from importlib import resources

    checks: list[dict[str, object]] = []

    checks.append(_check("package-version", True, f"adlife {__version__}"))
    checks.append(_check("python-version", True, sys.version.split()[0]))

    try:
        resources.files("adlife.resources.demo").joinpath("adlife.yaml").read_text(encoding="utf-8")
        checks.append(_check("resources", True, "demo and routine resources are present"))
    except (OSError, ModuleNotFoundError) as error:
        checks.append(
            _check("resources", False, f"package resources missing: {type(error).__name__}")
        )

    import os

    checks.append(
        _check(
            "writable",
            os.access(Path.cwd(), os.W_OK),
            f"{Path.cwd()} is writable",
        )
    )

    if path is not None:
        try:
            from adlife.cli.project import load_project

            project = load_project(path)
            checks.append(
                _check(
                    "project",
                    True,
                    f"scenario {project.scenario.scenario_id} with "
                    f"{len(project.scenario.population)} agents",
                )
            )
        except Exception as error:
            checks.append(_check("project", False, f"{type(error).__name__}: {error}"))

    checks.append(_check("sqlite-json1", _sqlite_json1_available(), "SQLite JSON1 extension"))

    stdout_encoding = sys.stdout.encoding
    utf8 = (stdout_encoding or "").lower().replace("-", "") in {"utf8", "utf8mb4"}
    utf8_detail = (
        f"stdout encoding {stdout_encoding!r}"
        if utf8
        else f"stdout encoding {stdout_encoding!r}; set PYTHONUTF8=1 for reliable UTF-8 output"
    )
    checks.append(_check("utf-8", utf8, utf8_detail))

    providers_ok, providers_detail = _providers_available()
    checks.append(_check("providers", providers_ok, providers_detail))

    if offline:
        checks.append(_check("offline", True, "no network access performed (--offline)"))
    else:
        checks.append(_check("offline", True, "no network access was required by these checks"))
        provider_url = _configured_base_url(path)
        if provider_url is not None:
            probed, probe_detail = _probe_provider(provider_url)
            checks.append(_check("provider-probe", probed, probe_detail))

    document = {"checks": checks, "all_ok": all(check["ok"] for check in checks)}
    if output_format() in {"json", "jsonl"}:
        emit_json(document)
    else:
        emit_human(checks)


def _configured_base_url(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        from adlife.cli.project import load_project

        project = load_project(path)
        base_url = project.config.provider.base_url
        return str(base_url) if base_url is not None else None
    except Exception:
        return None


def emit_human(checks: list[dict[str, object]]) -> None:
    for check in checks:
        mark = "PASS" if check["ok"] else "FAIL"
        print(f"{mark:>4}  {check['name']}: {check['detail']}")
    failed = sum(1 for check in checks if not check["ok"])
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")


__all__ = ["command"]
